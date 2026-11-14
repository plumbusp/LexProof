"""Context for the map's recent events: one Exa /contents call per article, saved in full.

For each event published in the last N days (default 92), Exa returns in ONE call:
  - text        the full article text (kept locally, so the demo never fetches)
  - highlights  query = the event's quote -> the passage around it on the page
  - summary     query = a fixed English question -> 2-3 sentences of context, in English

Files (all local, gitignored except what --export puts in the snapshot):
  data/threatmap/exa_raw/contents/<sha1>.json   Exa's full response body, unmodified, + the request
  data/threatmap/enrich.jsonl                   {url, summary, context, fetched_at, cost_usd}

A hard budget stops the run (default $5). Resumable: a URL already in enrich.jsonl is never paid for twice.

  python -m app.threatenrich --run [--days 92] [--budget 5]
"""
import argparse
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from . import threatmap as tm

FILE = tm.OUT_DIR / "enrich.jsonl"
RAW = tm.OUT_DIR / "exa_raw" / "contents"
SUMMARY_Q = ("In 2-3 short sentences in English: what happened, where, which institutions were targeted, "
             "how many, and who is blamed or suspected. Only facts stated in the article.")


def _read() -> dict[str, dict]:
    out = {}
    if FILE.exists():
        with FILE.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out[r["url"]] = r
                except (ValueError, KeyError):
                    pass
    return out


@lru_cache(maxsize=2)
def _cached(stamp: float) -> dict[str, dict]:
    return _read()


def table() -> dict[str, dict]:
    return _cached(FILE.stat().st_mtime if FILE.exists() else 0.0)


# One-line headlines per event (id -> text), written by an AI from the article title,
# Exa's summary and the quote. Display only: the quote stays the proof.
HEADLINES = tm.OUT_DIR / "headlines.jsonl"


@lru_cache(maxsize=2)
def _headlines(stamp: float) -> dict[str, str]:
    out = {}
    if HEADLINES.exists():
        for line in HEADLINES.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                out[r["id"]] = r["headline"]
            except (ValueError, KeyError):
                pass
    return out


def clean_summary(s: str) -> str:
    """Exa summaries sometimes start with 'Summary:' or come as '- What happened: ...' bullets.
    Returns plain prose, at most three sentences."""
    s = re.sub(r"^\s*summary\s*:\s*", "", s or "", flags=re.I)
    s = re.sub(r"(?m)^\s*-\s*(what happened|where|institutions targeted|who is blamed[^:]*|how many)\s*:\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", s)
    out = parts[0]
    for p in parts[1:3]:           # 2-3 lines on the card: whole sentences up to ~260 characters
        if len(out) + len(p) > 260:
            break
        out += " " + p
    return out


def attach(e: dict) -> dict:
    r = table().get(e.get("url"))
    if r:
        if r.get("summary"):
            e["summary"] = clean_summary(r["summary"])
        if r.get("context"):
            e["context"] = r["context"]
    h = _headlines(HEADLINES.stat().st_mtime if HEADLINES.exists() else 0.0).get(e.get("id"))
    if h:
        e["headline"] = h
    return e


def request_body(url: str, quote: str) -> dict:
    return {"urls": [url], "text": {"maxCharacters": 40000},
            "highlights": {"query": quote or SUMMARY_Q, "maxCharacters": 1200},
            "summary": {"query": SUMMARY_Q}}


def fetch_one(client, url: str, quote: str) -> dict:
    body = request_body(url, quote)
    resp = client.request("/contents", body)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")).write_text(
        json.dumps({"request": body, "response": resp, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    res = (resp.get("results") or [{}])[0]
    return {"url": url, "summary": (res.get("summary") or "").strip(),
            "context": " … ".join(h.strip() for h in res.get("highlights") or [] if h.strip()),
            "text_chars": len(res.get("text") or ""),
            "status": [s.get("status") for s in resp.get("statuses") or []],
            "cost_usd": (resp.get("costDollars") or {}).get("total"),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def run(days: int = 92, budget: float = 5.0, workers: int = 4) -> None:
    from . import threatstore as ts
    from .sources.exa_src import _exa
    since = (date.today() - timedelta(days=days)).isoformat()
    done = table()
    todo = {}
    for e in ts.events():
        if e["date_start"] >= since and e["url"] not in done:
            todo.setdefault(e["url"], e.get("quote", ""))
    print(f"{len(todo)} articles since {since} to fetch (budget ${budget:.2f})", flush=True)
    client, lock, spent, n = _exa(), threading.Lock(), 0.0, 0
    stop = threading.Event()
    FILE.parent.mkdir(parents=True, exist_ok=True)

    def job(url, quote):
        if stop.is_set():
            return None
        return fetch_one(client, url, quote)

    with ThreadPoolExecutor(workers) as pool, FILE.open("a", encoding="utf-8") as out:
        futs = {pool.submit(job, u, q): u for u, q in todo.items()}
        for f in as_completed(futs):
            try:
                row = f.result()
            except Exception as ex:  # not saved, so a re-run retries it
                print(f"  ERROR {futs[f][:70]}: {str(ex)[:160]}", flush=True)
                continue
            if row is None:
                continue
            with lock:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                n += 1
                spent += row["cost_usd"] or 0
                if spent >= budget * 0.95 and not stop.is_set():
                    stop.set()
                    print(f"  budget reached (${spent:.3f}); stopping", flush=True)
                if n % 25 == 0:
                    print(f"  {n}/{len(todo)} fetched, ${spent:.3f}", flush=True)
    _cached.cache_clear()
    print(f"done: {n} articles saved, Exa cost ${spent:.3f} -> {FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--days", type=int, default=92)
    ap.add_argument("--budget", type=float, default=5.0)
    a = ap.parse_args()
    run(a.days, a.budget) if a.run else ap.print_help()

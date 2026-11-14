"""One-time pass: saved Exa articles -> verified event fields (data/threatmap/extractions.jsonl).

Gemini only READS text and fills fields (plan rule 2). Code then
  - drops any event whose quote is not really in the saved article text (no quote, no dot),
  - keeps only in-scope countries,
and later sets the evidence level itself (threatmap.level).
Resumable: an article already in extractions.jsonl is never sent again.

  python -m app.threatextract --sample          one batch, printed, nothing saved
  python -m app.threatextract --run             all articles not yet done
"""
import argparse
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ValidationError

from . import threatmap as tm
from . import threatstore as ts
from .config import settings

PV = 1          # prompt version, stored with every row
BATCH = 10      # articles per Gemini call

# NOTE: the plan's taxonomy file (T1-T6) is not in the repo. These three are a working
# reading of the plan's seed table (T3 Latvia, T5 Bulgaria/Hungary/NI, T6 newsrooms);
# T1, T2 and T4 are left unused until the real taxonomy is added.
PROMPT = f"""You read short news excerpts and fill fixed fields. You do not judge or score.

For each article decide whether it reports (or officially warns of) public institutions or media
being flooded with mass submissions in one of these countries: {", ".join(sorted(tm.SCOPE))}.

layer "hostile": a coordinated or suspicious flood aimed at institutions. type:
  T3 = mass complaints, requests, petitions or emails to public bodies (ministries, police, councils, regulators, courts)
  T5 = mass threat or hoax messages (e.g. bomb-threat emails) to schools, courts, hospitals, ports and similar
  T6 = flooding of newsrooms, fact-checkers or media regulators (complaints, fake verification requests)
layer "precursor": a rise in complaints written with AI by ordinary people. This is NOT hostile. type "AI".
is_event = false for anything else: single incidents, scams aimed at individuals, general AI commentary,
opinion, other countries, no sign of volume or coordination.

Fields (use only what the text says; never guess):
- countries: the in-scope countries whose institutions or media were targeted (one event per country). Use exact names from the list.
- date_start: YYYY-MM-DD when the events happened or were reported; if unclear use the article's published date.
- target: the bodies targeted, a few words. volume: the size as written ("70+ schools"), or "".
- voice: who says it is hostile or coordinated. "official" = a named government body, minister, police or security service;
  "researchers" = two or more independent researchers or a named monitoring body; "expert" = one expert or commentator;
  "nobody" = nobody says it is hostile (always "nobody" for layer precursor).
- attribution: "named" = a named state or group is blamed by official or multiple sources; "suspected" = suspicion or a hypothesis; "none".
- ai_use: "yes" if AI-written content is mentioned, "no" if stated not, else "unknown".
- kind: "incident" = it happened; "warning" = authorities warn or expect it; "forecast" = projection or trend.
- weak_source: true if the site is an aggregator, social media, or the claim is flagged unverified.
- quote: ONE short passage (under 220 characters) copied EXACTLY, in its original language, from the title or excerpt that supports the event.
  Do not translate or rephrase. If you cannot copy one, set is_event false.

Return JSON only: {{"items": [{{"i": <article number>, "is_event": bool, "layer": "hostile"|"precursor", "type": "T3"|"T5"|"T6"|"AI",
"countries": [..], "date_start": "", "target": "", "volume": "", "voice": "", "attribution": "", "ai_use": "", "kind": "",
"weak_source": bool, "quote": ""}}]}}. One item per article that is an event; omit articles that are not."""


class Item(BaseModel):
    i: int
    is_event: bool = True
    layer: Literal["hostile", "precursor"] = "hostile"
    type: Literal["T3", "T5", "T6", "AI"]
    countries: list[str]
    date_start: str = ""
    target: str = ""
    volume: str = ""
    voice: Literal["nobody", "expert", "researchers", "official"] = "nobody"
    attribution: Literal["none", "suspected", "named"] = "none"
    ai_use: Literal["yes", "no", "unknown"] = "unknown"
    kind: Literal["incident", "warning", "forecast"] = "incident"
    weak_source: bool = False
    quote: str = ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"“”„«»'’‘`]", "", s or "")).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    """The quote must be really there. A quote with '…' / '...' gaps is accepted if every piece is."""
    hay = _norm(text)
    pieces = [p for p in re.split(r"…|\.\.\.", _norm(quote)) if p.strip()]
    return bool(pieces) and all(p.strip() in hay for p in pieces if len(p.strip()) >= 12) and any(len(p.strip()) >= 12 for p in pieces)


def verify(item: Item, article: dict) -> dict | None:
    """Keeps an event only if it is in scope, typed consistently, and its quote is in the saved text."""
    if not item.is_event:
        return None
    countries = [c for c in dict.fromkeys(item.countries) if c in tm.SCOPE]
    text = article["title"] + "\n" + "\n".join(article["highlights"])
    if not countries or not quote_in_text(item.quote, text):
        return None
    if (item.layer == "precursor") != (item.type == "AI"):
        return None
    d = item.model_dump(exclude={"i", "is_event", "countries"})
    if item.layer == "precursor":
        d["voice"], d["attribution"] = "nobody", "none"   # a surge of complaints alone is never hostile (plan rule 6)
    return {**d, "countries": countries}


def _client():
    from openai import OpenAI
    kw = {"api_key": settings.ai_api_key, "timeout": 150, "max_retries": 3}
    if settings.ai_base_url:
        kw["base_url"] = settings.ai_base_url
    return OpenAI(**kw)


def _numbered(batch: list[dict]) -> str:
    return "\n\n".join(f"[{n}] published {a['published_date']} | {tm.domain(a['url'])}\n{a['title']}\n" + " … ".join(a["highlights"])[:1500]
                       for n, a in enumerate(batch, 1))


def extract_batch(client, batch: list[dict]) -> tuple[list[dict], int]:
    """Returns one row per article in the batch, and how many raw items the model returned."""
    resp = client.chat.completions.create(
        model=settings.ai_model, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": _numbered(batch)}])
    data = json.loads(resp.choices[0].message.content or "{}")
    raw = data.get("items", []) if isinstance(data, dict) else []
    by_article: dict[int, list[dict]] = {n: [] for n in range(1, len(batch) + 1)}
    for r in raw:
        try:
            it = Item.model_validate(r)
        except ValidationError:
            continue
        if 1 <= it.i <= len(batch) and (ev := verify(it, batch[it.i - 1])):
            by_article[it.i].append(ev)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [{"url": a["url"], "pv": PV, "items": by_article[n], "extracted_at": now}
            for n, a in enumerate(batch, 1)], len(raw)


def pending() -> list[dict]:
    done = {r["url"] for r in ts.read_jsonl(ts.EXTRACTIONS) if r.get("pv") == PV}
    return [a for u, a in sorted(ts.unique_articles().items()) if u not in done]


def run(limit: int | None = None, workers: int = 2) -> None:
    """Few workers on purpose: 6 in parallel hit Gemini's 429 quota error after 5 calls."""
    if not settings.ai_enabled:
        raise SystemExit("AI_API_KEY and AI_MODEL must be set in .env")
    todo = pending()[:limit] if limit else pending()
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    print(f"{len(todo)} articles in {len(batches)} Gemini calls (model {settings.ai_model})", flush=True)
    client, lock, ok, bad, events = _client(), threading.Lock(), 0, 0, 0
    ts.EXTRACTIONS.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool, ts.EXTRACTIONS.open("a", encoding="utf-8") as out:
        futs = {pool.submit(extract_batch, client, b): b for b in batches}
        for f in as_completed(futs):
            try:
                rows, _ = f.result()
            except Exception as e:  # not stored, so a re-run retries this batch
                bad += 1
                print(f"  batch failed: {str(e)[:160]}", flush=True)
                continue
            with lock:
                for r in rows:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                out.flush()
                ok += 1
                events += sum(len(r["items"]) for r in rows)
                if ok % 10 == 0:
                    print(f"  {ok}/{len(batches)} calls done, {events} verified events so far", flush=True)
    print(f"done: {ok} calls ok, {bad} failed (re-run to retry), {events} verified events -> {ts.EXTRACTIONS}")


def sample() -> None:
    arts = [a for a in pending() if a["published_date"].startswith("2026-10")][:BATCH]
    rows, n_raw = extract_batch(_client(), arts)
    print(f"model returned {n_raw} items; {sum(len(r['items']) for r in rows)} survived verification\n")
    for a, r in zip(arts, rows):
        print(f"- {a['title'][:90]}\n  {a['url'][:80]}")
        for it in r["items"]:
            print("   ->", json.dumps(it, ensure_ascii=False)[:420])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="one batch of October articles, printed, nothing saved")
    ap.add_argument("--run", action="store_true", help="extract every article not yet done and save")
    ap.add_argument("--limit", type=int, help="with --run: only the first N articles")
    a = ap.parse_args()
    sample() if a.sample else run(a.limit) if a.run else ap.print_help()

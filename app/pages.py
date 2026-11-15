"""Every article page the map uses, fetched from Exa /contents ONCE per URL, ever.

One call per new URL returns two content types ($0.002):
  text     up to 20,000 characters: the quote check runs on this ORIGINAL text, in code
  summary  with a JSON schema, answered in English: lang, title_en, headline_en, quote_en, summary_en

Exa's English fields are display only (headline, summary, translation "by Exa"); they never set a level
and never pass a quote check.

Files (gitignored, paid data):
  data/threatmap/pages.jsonl                 one line per URL: the English fields, context, cost
  data/threatmap/pages_text/<key>.txt        the page text
  data/threatmap/exa_raw/contents/<h>.json   Exa's raw response (written by exacall)
Pages fetched before this module (exa_raw/contents/<sha1(url)>.json from threatenrich) count as known:
their text is reused and they are never paid for again.

  python -m app.pages --backfill [--days 92] [--budget 0.5]   English fields for recent events that lack them
"""
import argparse
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import exacall
from . import threatmap as tm

TEXT_CHARS = 20000
FIELDS = ("lang", "title_en", "headline_en", "quote_en", "summary_en")
SUMMARY_SCHEMA = {"type": "object", "properties": {
    "lang": {"type": "string", "description": "ISO 639-1 code of the article's language, e.g. lv, de, en"},
    "title_en": {"type": "string", "description": "the article's title translated into English"},
    "headline_en": {"type": "string", "description": "one plain factual line in English, max 90 characters: what happened, to whom, where"},
    "quote_en": {"type": "string", "description": "a faithful English translation of the quoted sentence, nothing added; the sentence itself if it is already English"},
    "summary_en": {"type": "string", "description": "2-3 short English sentences: what happened, where, which bodies, how many, who is blamed"}},
    "required": list(FIELDS)}


def summary_query(quote: str) -> str:
    q = (quote or "").strip()
    return ("Answer in English about this article. " + (f"The quoted sentence is: «{q[:300]}». " if q else "")
            + "Only facts stated in the article.")


def norm(u: str) -> str:
    """Lowercase, no fragment, no trailing slash, no utm_* / amp variants: one key per article."""
    u = (u or "").strip()
    try:
        p = urlsplit(u)
    except ValueError:
        return u.split("#")[0].rstrip("/").lower()
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in ("amp", "outputtype", "fbclid", "gclid")]
    path = re.sub(r"/amp/?$|\.amp$", "", p.path)
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), path, urlencode(q), "")).rstrip("/").lower()


def key(u: str) -> str:
    return hashlib.sha1(norm(u).encode("utf-8")).hexdigest()[:16]


def _file():
    return tm.OUT_DIR / "pages.jsonl"


def _text_dir():
    return tm.OUT_DIR / "pages_text"


def _stamp(p) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


@lru_cache(maxsize=4)
def _records(path: str, stamp: float) -> dict[str, dict]:
    out = {}
    f = _file()
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                out[r["key"]] = r
            except (ValueError, KeyError):
                pass
    return out


def records() -> dict[str, dict]:
    """key -> page record (no text)."""
    f = _file()
    return _records(str(f), _stamp(f))


@lru_cache(maxsize=4)
def _legacy(path: str, stamp: float) -> dict[str, str]:
    """norm url -> legacy raw file (threatenrich), whose text and summary were already paid for."""
    out = {}
    d = exacall.raw_dir() / "contents"
    if d.exists():
        for f in d.glob("*.json"):
            try:
                body = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if "endpoint" in body:          # written by exacall: already in pages.jsonl
                continue
            for u in (body.get("request") or {}).get("urls") or []:
                out[norm(u)] = str(f)
    return out


def legacy() -> dict[str, str]:
    d = exacall.raw_dir() / "contents"
    return _legacy(str(d), _stamp(d))


def known(u: str) -> bool:
    return key(u) in records() or norm(u) in legacy()


def text(u: str) -> dict | None:
    """{title, text, published_date} for a known URL (new or legacy), else None. Never calls Exa."""
    r = records().get(key(u))
    if r:
        f = _text_dir() / f"{r['key']}.txt"
        return {"title": r.get("title", ""), "published_date": r.get("published_date", ""),
                "text": f.read_text(encoding="utf-8") if f.exists() else ""}
    lf = legacy().get(norm(u))
    if lf:
        try:
            res = (json.loads(open(lf, encoding="utf-8").read())["response"].get("results") or [{}])[0]
        except (ValueError, KeyError, OSError):
            return None
        return {"title": res.get("title") or "", "text": res.get("text") or "",
                "published_date": (res.get("publishedDate") or "")[:10]}
    return None


def parse_summary(s) -> dict:
    """Exa's summary is a string; with a schema it should be JSON. Falls back to the plain text as summary_en."""
    if isinstance(s, dict):
        d = s
    else:
        s = (s or "").strip()
        m = re.search(r"\{.*\}", s, re.S)
        try:
            d = json.loads(m.group(0)) if m else None
        except ValueError:
            d = None
        if not isinstance(d, dict):
            return {"summary_en": s} if s else {}
    out = {k: str(d.get(k) or "").strip() for k in FIELDS if d.get(k)}
    if "lang" in out:
        out["lang"] = out["lang"][:5].lower()
    if "headline_en" in out:
        out["headline_en"] = out["headline_en"][:120]
    return out


def context_around(text: str, quote: str, width: int = 500) -> str:
    """The passage around the quote, cut by code from the saved text (no paid highlights)."""
    from .textutil import find_span
    span = find_span(text or "", (quote or "").split("…")[0].split("...")[0].strip())
    if not span:
        return ""
    a, b = max(0, span[0] - width), min(len(text), span[1] + width)
    return ("… " if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + (" …" if b < len(text) else "")


_write = threading.Lock()


def _store(u: str, quote: str, resp: dict) -> dict:
    res = (resp.get("results") or [{}])[0] if isinstance(resp, dict) else {}
    st = [s.get("status") for s in (resp.get("statuses") or [])] if isinstance(resp, dict) else []
    txt = res.get("text") or ""
    rec = {"key": key(u), "url": u, "title": res.get("title") or "", "published_date": (res.get("publishedDate") or "")[:10],
           "quote_src": quote, **parse_summary(res.get("summary")), "context": context_around(txt, quote),
           "text_chars": len(txt), "status": st, "cost_usd": (resp.get("costDollars") or {}).get("total"),
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "by": "exa"}
    with _write:
        _text_dir().mkdir(parents=True, exist_ok=True)
        (_text_dir() / f"{rec['key']}.txt").write_text(txt, encoding="utf-8")
        with _file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _records.cache_clear()
    return rec


def body(u: str, quote: str = "", with_summary: bool = True) -> dict:
    b = {"urls": [u], "text": {"maxCharacters": TEXT_CHARS}}
    if with_summary:
        b["summary"] = {"query": summary_query(quote), "schema": SUMMARY_SCHEMA}
    return b


def get(urls, quotes: dict | None = None, run: exacall.Run | None = None, with_summary: bool = True,
        workers: int = 4, client=None) -> dict[str, dict]:
    """norm url -> {title, text, published_date}. Unknown URLs are fetched once (text + summary schema);
    known ones (new or legacy) cost nothing. A URL that fails or is over budget is simply missing."""
    quotes = quotes or {}
    want = {}
    for u in urls:
        if u and str(u).startswith("http"):
            want.setdefault(norm(u), u)
    out, todo = {}, []
    for n, u in want.items():
        t = text(u)
        if t is not None:
            out[n] = t
        else:
            todo.append(u)

    def one(u):
        q = quotes.get(u) or quotes.get(norm(u)) or ""
        try:
            resp = exacall.call("contents", "/contents", body(u, q, with_summary),
                                est=exacall.PRICE["contents_type"] * (2 if with_summary else 1), run=run, client=client)
        except exacall.BudgetExceeded:
            raise
        except Exception as e:  # one failed page only means its events cannot be verified now
            print(f"  contents failed {u[:70]}: {str(e)[:120]}", flush=True)
            return
        _store(u, q, resp)
        t = text(u)
        if t is not None:
            out[norm(u)] = t

    if todo:
        with ThreadPoolExecutor(min(workers, len(todo))) as pool:
            for f in [pool.submit(one, u) for u in todo]:
                try:
                    f.result()
                except exacall.BudgetExceeded as e:
                    print(f"  {e}; page skipped", flush=True)
    return out


def _flat(s) -> str:
    return re.sub(r"\W+", " ", str(s or "")).strip().lower()


def attach(e: dict) -> dict:
    """Adds Exa's English fields where nothing saved earlier (headlines.jsonl, translations, enrich) gave one."""
    r = records().get(key(e.get("url", "")))
    if not r:
        return e
    if not e.get("headline") and r.get("headline_en"):
        e["headline"], e["headline_by"] = r["headline_en"], "exa"
    if not e.get("summary") and r.get("summary_en"):
        from .threatenrich import clean_summary
        e["summary"] = clean_summary(r["summary_en"])
    if not e.get("context") and r.get("context") and r.get("quote_src") == e.get("quote"):
        e["context"] = r["context"]
    lang = r.get("lang") or ""
    if lang and lang != "en":
        same = _flat(r.get("quote_en")) == _flat(e.get("quote"))   # Exa sometimes hands the original back
        if not e.get("quote_en") and r.get("quote_en") and not same and r.get("quote_src", "").strip() == (e.get("quote") or "").strip():
            e["quote_en"], e["lang"], e["tr_by"] = r["quote_en"], lang, "exa"
        if not e.get("title_en") and r.get("title_en"):
            e["title_en"] = r["title_en"]
    return e


def backfill(days: int = 92, budget: float = 0.5) -> dict:
    """pages.get for every recent event that lacks a headline or a summary and whose page is not known yet."""
    from . import threatstore as ts
    since = (date.today() - timedelta(days=days)).isoformat()
    todo = {}
    for e in ts.events():
        if e["date_start"] >= since and (not e.get("headline") or not e.get("summary")) and not known(e["url"]):
            todo.setdefault(e["url"], e.get("quote", ""))
    run = exacall.Run(budget)
    print(f"{len(todo)} pages to fetch (budget ${budget:.2f})", flush=True)
    got = get(list(todo), todo, run)
    print(exacall.summary(run))
    return {"asked": len(todo), "fetched": len(got), "cost": run.spent}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--days", type=int, default=92)
    ap.add_argument("--budget", type=float, default=0.5)
    a = ap.parse_args()
    print(backfill(a.days, a.budget)) if a.backfill else ap.print_help()

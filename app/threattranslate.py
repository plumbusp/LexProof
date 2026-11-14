"""English translations of the map's quotes and titles, for display only.

The original text stays the proof (the quote check runs on it); the translation is shown
by default and the page offers "Show original". Translated once, saved, never re-sent:

data/threatmap/translations.jsonl   {"h": sha1 of the original, "lang": "lv", "en": "..."}

  python -m app.threattranslate --run      translate every quote/title not yet done (Gemini)
"""
import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from . import threatmap as tm
from .config import settings

FILE = tm.OUT_DIR / "translations.jsonl"
BATCH = 25

PROMPT = """You translate short news snippets into English for a map of reported incidents.
For each numbered snippet return its ISO 639-1 language code and a faithful English translation.
Keep names, numbers, dates and quoted words exact. Do not summarise, soften or add anything.
If a snippet is already English, return lang "en" and the snippet unchanged.
Return JSON only: {"items": [{"i": <number>, "lang": "xx", "en": "..."}]}"""


def key(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _read() -> dict[str, dict]:
    out = {}
    if FILE.exists():
        with FILE.open(encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    out[r["h"]] = r
                except (ValueError, KeyError):
                    pass  # half-written last line
    return out


@lru_cache(maxsize=2)
def _cached(stamp: float) -> dict[str, dict]:
    return _read()


def table() -> dict[str, dict]:
    return _cached(FILE.stat().st_mtime if FILE.exists() else 0.0)


def attach(e: dict) -> dict:
    """Adds quote_en / title_en / lang when a translation exists and differs from the original."""
    t = table()
    q, ti = t.get(key(e.get("quote", ""))), t.get(key(e.get("title", "")))
    if q and q.get("lang") != "en" and q.get("en"):
        e["quote_en"], e["lang"], e["tr_by"] = q["en"], q["lang"], q.get("by", "gemini")
    if ti and ti.get("lang") != "en" and ti.get("en"):
        e["title_en"] = ti["en"]
    return e


def translate_batch(client, texts: list[str]) -> list[dict]:
    body = "\n\n".join(f"[{n}] {t}" for n, t in enumerate(texts, 1))
    resp = client.chat.completions.create(
        model=settings.ai_model, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": body}])
    data = json.loads(resp.choices[0].message.content or "{}")
    out = []
    for r in data.get("items", []) if isinstance(data, dict) else []:
        i = r.get("i")
        if isinstance(i, int) and 1 <= i <= len(texts) and isinstance(r.get("en"), str) and r["en"].strip():
            out.append({"h": key(texts[i - 1]), "lang": str(r.get("lang") or "")[:5].lower(), "en": r["en"].strip(), "by": "gemini"})
    return out


def run(workers: int = 2) -> None:
    from . import threatstore as ts
    from .threatextract import _client
    if not settings.ai_enabled:
        raise SystemExit("AI_API_KEY and AI_MODEL must be set in .env")
    done = table()
    texts = list(dict.fromkeys(t for e in ts.events() for t in (e.get("quote"), e.get("title"))
                               if t and t.strip() and key(t) not in done))
    batches = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)]
    print(f"{len(texts)} snippets to translate in {len(batches)} Gemini calls", flush=True)
    client, lock, n = _client(), threading.Lock(), 0
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool, FILE.open("a", encoding="utf-8") as out:
        futs = [pool.submit(translate_batch, client, b) for b in batches]
        for f in as_completed(futs):
            try:
                rows = f.result()
            except Exception as e:  # not saved, so a re-run retries it
                print(f"  batch failed: {str(e)[:160]}", flush=True)
                continue
            with lock:
                for r in rows:
                    out.write(json.dumps(r, ensure_ascii=False) + "\n")
                out.flush()
                n += len(rows)
    _cached.cache_clear()
    print(f"done: {n} translations saved -> {FILE}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    run() if a.run else ap.print_help()

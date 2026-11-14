"""Local data behind the Regional Abuse Map. Reads files only; never calls Exa or Gemini.

data/threatmap/exa_results.jsonl   raw Exa answers (one line per search)      written by threatmap --run
data/threatmap/extractions.jsonl   verified event fields per article URL      written by threatextract

The map is a query over these two files, so changing the time window costs nothing.
"""
import hashlib
import json
from datetime import date, timedelta
from functools import lru_cache

from . import threatmap as tm

EXTRACTIONS = tm.OUT_DIR / "extractions.jsonl"


def read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a half-written last line from an interrupted run
    return out


def _stamp(path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@lru_cache(maxsize=2)
def _results_index(stamp: float) -> dict:
    """One pass over the saved Exa answers, cached until the file changes. Treat the result as read-only."""
    arts: dict[str, dict] = {}
    cov: dict[str, set[str]] = {}
    saved = ""
    for row in read_jsonl(tm.RESULTS):
        saved = max(saved, row.get("fetched_at", ""))
        if row.get("country"):
            cov.setdefault(row["country"], set()).add(row["month"])
        for r in row["results"]:  # the same article comes back from many searches
            arts.setdefault(r["url"], {k: r[k] for k in ("url", "title", "published_date", "highlights")})
    return {"articles": arts, "coverage": cov, "saved_at": saved[:10]}


def _index() -> dict:
    return _results_index(_stamp(tm.RESULTS))


def unique_articles() -> dict[str, dict]:
    """url -> {url, title, published_date, highlights}."""
    return _index()["articles"]


def coverage() -> dict[str, set[str]]:
    """country -> months that were actually searched. A country outside this is 'not searched', not 'nothing found'."""
    return _index()["coverage"]


def _day(s) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _event_date(item: dict, published: str) -> str:
    """The event date Gemini read, unless it is missing, malformed or later than the article itself."""
    pub, d = _day(published), _day(item.get("date_start"))
    if d and not (pub and d > pub + timedelta(days=1)):
        return d.isoformat()
    return pub.isoformat() if pub else ""


def _build_events(_stamp_key: float) -> list[dict]:
    arts = unique_articles()
    events = []
    for row in read_jsonl(EXTRACTIONS):
        art = arts.get(row["url"])
        if not art:
            continue
        for it in row.get("items", []):
            for country in it.get("countries", []):
                e = {**it, "country": country, "url": art["url"], "title": art["title"],
                     "published": art["published_date"], "domain": tm.domain(art["url"]),
                     "date_start": _event_date(it, art["published_date"])}
                e["id"] = hashlib.sha1(f"{e['url']}|{country}|{it.get('type')}".encode()).hexdigest()[:10]
                if e["date_start"] and tm.valid(e):
                    events.append(e)
    # plan step 4: same country + type + source domain within 7 days is one event (hostile layer only)
    hostile = tm.dedupe([e for e in events if e["layer"] == "hostile"])
    return sorted(hostile + [e for e in events if e["layer"] != "hostile"], key=lambda e: e["date_start"], reverse=True)


@lru_cache(maxsize=2)
def _cached(stamp: float) -> list[dict]:
    return _build_events(stamp)


def events() -> list[dict]:
    return [dict(e) for e in _cached(_stamp(EXTRACTIONS))]


def months_available() -> list[str]:
    """Months with at least one searched country, oldest first."""
    return sorted({m for ms in coverage().values() for m in ms})


def saved_at() -> str:
    """When the newest saved Exa answer was fetched (the data is a snapshot, not live)."""
    return _index()["saved_at"]


@lru_cache(maxsize=2)
def _extracted_count(stamp: float) -> int:
    return len(read_jsonl(EXTRACTIONS))


def query(frm: str, to: str, layer: str = "hostile", types: list[str] | None = None) -> dict:
    """Everything the map page needs for the months frm..to (inclusive, 'YYYY-MM').
    types=None means every type; an empty list means none (an empty map, not a broken one)."""
    months = months_available()
    cov = coverage()
    wanted = [m for m in months if frm <= m <= to]
    in_window = [e for e in events() if e["layer"] == layer and frm <= e["date_start"][:7] <= to]
    all_types = sorted({e["type"] for e in in_window})
    evs = in_window if types is None else [e for e in in_window if e["type"] in types]
    countries = {}
    for c in sorted(tm.SCOPE):
        searched = bool(cov.get(c, set()) & set(wanted))
        countries[c] = {"searched": searched, "level": 0, "media": 0, "events": []}
    for c, lv in tm.country_levels(evs, layer).items():
        countries[c]["level"] = lv
    for e in tm.with_levels(evs, layer):
        slot = countries[e["country"]]
        slot["events"].append(e)
        slot["media"] += e["type"] == "T6"
    return {"layer": layer, "from": frm, "to": to, "months": months, "searched_months": wanted,
            "types": all_types, "countries": countries, "n_events": len(evs),
            "extracted": _extracted_count(_stamp(EXTRACTIONS)), "articles": len(unique_articles()), "saved_at": saved_at()}

"""Regional Abuse Map: live refresh (plan step 4) and level rules (plan section 5).

Dry run (no Exa or Gemini calls):  python -m app.threatmap --dry-run

Code sets the level, never Gemini (plan rule 2). Gemini would only fill the
event fields from a result's highlight text.
"""
import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

# EU-27 + UK + Switzerland (plan rule 4). Ukraine, Kosovo, Serbia, Moldova are out.
SCOPE = {"Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czechia", "Denmark", "Estonia", "Finland",
         "France", "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg",
         "Malta", "Netherlands", "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden",
         "United Kingdom", "Switzerland"}

# DRAFT queries. The plan's queries file (exa-threat-map-queries.md) is not in the repo;
# replace these with its sentences when it is added.
QUERIES = {
    "incident": [
        "News report of a coordinated flood of complaints or requests sent to a government agency in Europe",
        "Authorities warn that mass emails or submissions overwhelming public institutions are part of a hybrid operation",
        "Schools, courts or municipalities in Europe receive hundreds of identical threatening emails",
        "Newsrooms and fact-checkers flooded with fake verification requests from a coordinated campaign",
    ],
    "precursor": [
        "Council reports a sharp rise in complaints written with AI chatbots",
        "Ombudsman or court overwhelmed by AI-generated complaints and objections",
    ],
}

# Fields Gemini fills per result (plan step 1). The last three are added because the
# level rules need them and the step 1 list does not carry them.
EVENT_FIELDS = {
    "is_event": "bool: false when the three tests in the taxonomy are not met",
    "type": "T1..T6", "country": "one of SCOPE", "date_start": "YYYY-MM-DD", "date_end": "YYYY-MM-DD",
    "target": "body targeted", "volume": "volume as written", "voice": "nobody | expert | researchers | official",
    "attribution": "none | suspected | named", "ai_use": "yes | no | unknown",
    "quote": "short quote copied from the page", "language": "source language",
    "kind": "incident | warning | forecast", "weak_source": "bool: aggregator-only or unverified",
}


def _kwargs(q: str, start: date, end: date) -> dict:
    return {"type": "auto", "category": "news", "num_results": 10,
            "start_published_date": f"{start.isoformat()}T00:00:00.000Z",
            "end_published_date": f"{end.isoformat()}T23:59:59.999Z",
            "contents": {"highlights": {"query": q, "maxCharacters": 600}}}


def build_requests(days: int = 7, today: date | None = None) -> list[dict]:
    """The Exa search calls a 'last N days' refresh would make. Dates go in the date fields (plan rule 5)."""
    today = today or date.today()
    start = today - timedelta(days=days)
    return [{"group": g, "query": q, "kwargs": _kwargs(q, start, today)} for g, qs in QUERIES.items() for q in qs]


# ---------- historical backfill: monthly periods, stored locally so credits are spent once

# One query per country and month, per layer. {c} is the country name.
COUNTRY_QUERIES = {
    "incident": "News report about public institutions in {c} being flooded with coordinated mass complaints, requests or threatening emails",
    "precursor": "News report about councils, courts or agencies in {c} receiving a surge of complaints written with AI",
}
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "threatmap"
RESULTS = OUT_DIR / "exa_results.jsonl"
PLAN_FILE = OUT_DIR / "requests.json"


def months(first: date = date(2024, 1, 1), today: date | None = None) -> list[tuple[date, date]]:
    """Calendar months from `first` up to today; the current month ends today."""
    today = today or date.today()
    out, d = [], first
    while d <= today:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        out.append((d, min(nxt - timedelta(days=1), today)))
        d = nxt
    return out


def build_plan(scope: str = "countries", today: date | None = None) -> list[dict]:
    """scope: 'europe' = the Europe-wide QUERIES per month;
    'countries' = one incident + one precursor query per country per month;
    'full' = both."""
    plan = []
    for start, end in months(today=today):
        m = start.strftime("%Y-%m")
        if scope in ("europe", "full"):
            for g, qs in QUERIES.items():
                for i, q in enumerate(qs):
                    plan.append({"id": f"{m}|europe|{g}{i}", "month": m, "country": None, "group": g,
                                 "query": q, "kwargs": _kwargs(q, start, end)})
        if scope in ("countries", "full"):
            for c in sorted(SCOPE):
                for g, tpl in COUNTRY_QUERIES.items():
                    q = tpl.format(c=c)
                    plan.append({"id": f"{m}|{c}|{g}", "month": m, "country": c, "group": g,
                                 "query": q, "kwargs": _kwargs(q, start, end)})
    return plan


def done_ids() -> set[str]:
    if not RESULTS.exists():
        return set()
    with RESULTS.open(encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f if line.strip()}


def run_plan(plan: list[dict], workers: int = 4) -> None:
    """Calls Exa for every request not already in RESULTS and appends each answer as one JSON line.
    Safe to stop and restart: finished requests are never paid for twice."""
    from .sources.exa_src import _exa, _res
    client = _exa()
    todo = [r for r in plan if r["id"] not in done_ids()]
    print(f"{len(plan) - len(todo)} already stored, {len(todo)} to fetch", flush=True)
    lock, n_ok, n_err, cost = threading.Lock(), 0, 0, 0.0

    def one(r):
        resp = client.search(r["query"], **r["kwargs"])
        return {**{k: r[k] for k in ("id", "month", "country", "group", "query")}, "params": r["kwargs"],
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "cost_usd": getattr(getattr(resp, "cost_dollars", None), "total", None),
                "results": [_res(x) for x in resp.results]}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool, RESULTS.open("a", encoding="utf-8") as out:
        futs = {pool.submit(one, r): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                row = f.result()
            except Exception as e:  # leave it out of RESULTS so the next run retries it
                n_err += 1
                print(f"  ERROR {r['id']}: {str(e)[:200]}", flush=True)
                continue
            with lock:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                n_ok += 1
                cost += row["cost_usd"] or 0
                if n_ok % 50 == 0:
                    print(f"  {n_ok}/{len(todo)} stored, ${cost:.3f} so far", flush=True)
    print(f"done: {n_ok} stored, {n_err} failed (re-run to retry the failed ones), Exa cost ${cost:.3f} -> {RESULTS}")


def domain(url: str) -> str:
    d = urlparse(url or "").netloc.lower()
    return d[4:] if d.startswith("www.") else d


def valid(e: dict) -> bool:
    """No source, no dot (plan rule 1); in-scope countries only (rule 4)."""
    return bool(e.get("is_event", True) and e.get("url") and (e.get("quote") or "").strip()
                and e.get("country") in SCOPE)


def level(e: dict) -> int:
    """Evidence level 0-4 for the hostile layer, from plain rules (plan section 5).

    Reading of the plan where it is ambiguous:
    - voice "nobody" is 0: a rise in complaints alone is not hostile (rule 6, test 9).
    - level 3 needs an expert or official voice and a suspected attribution, so it
      stays above level 2 (an official warning with no attribution at all).
    """
    voice, attr = e.get("voice", "nobody"), e.get("attribution", "none")
    if voice == "nobody":
        return 0
    if e.get("weak_source") or e.get("kind") == "forecast":
        return 1
    if voice in ("official", "researchers") and attr == "named" and len(set(e.get("domains") or [domain(e.get("url"))])) >= 2:
        return 4
    if e.get("kind") == "incident" and attr == "suspected":
        return 3
    if voice == "official":
        return 2
    return 1


SAME_INCIDENT_DAYS = 7


def _day(s):
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def with_levels(events: list[dict], layer: str = "hostile") -> list[dict]:
    """Copies of the events with `level` set. Level 4 needs two source sites reporting the SAME
    incident: same country, same type, dates within SAME_INCIDENT_DAYS of each other. (Pooling
    every site in a country made level 4 too easy: two unrelated stories counted as corroboration.)
    The AI-complaint layer has no hostile level (always 0)."""
    def site(x):
        return x.get("domain") or domain(x["url"])

    out = []
    for e in events:
        d = _day(e.get("date_start"))
        same = {site(o) for o in events
                if o["country"] == e["country"] and o.get("type") == e.get("type")
                and (d is None or (_day(o.get("date_start")) is not None
                                   and abs((_day(o.get("date_start")) - d).days) <= SAME_INCIDENT_DAYS))}
        c = {**e, "domains": sorted(same | {site(e)})}
        c["level"] = level(c) if layer == "hostile" else 0
        out.append(c)
    return out


def _bucket(n: int) -> int:
    return 1 if n <= 1 else 2 if n == 2 else 3 if n <= 4 else 4


def country_levels(events: list[dict], layer: str = "hostile") -> dict[str, int]:
    """Level per country with at least one valid event.
    hostile: highest event level; T6 (media targets) never raises a country (plan section 5).
    precursor (AI complaints by real people): NOT hostile. Provisional shading by the number of
    different source sites (1, 2, 3-4, 5+); the plan gives this layer no ladder."""
    evs = [e for e in with_levels([e for e in events if valid(e)], layer)]
    out: dict[str, int] = {}
    if layer == "precursor":
        sites: dict[str, set] = {}
        for e in evs:
            sites.setdefault(e["country"], set()).add(e.get("domain") or domain(e["url"]))
        return {c: _bucket(len(s)) for c, s in sites.items()}
    for e in evs:
        if e.get("type") != "T6":
            out[e["country"]] = max(out.get(e["country"], 0), e["level"])
    return out


def dedupe(events: list[dict]) -> list[dict]:
    """Same country, type and source domain within 7 days counts once (plan step 4)."""
    kept: list[dict] = []
    for e in sorted(events, key=lambda x: x.get("date_start", "")):
        dup = any(k["country"] == e["country"] and k["type"] == e["type"] and domain(k["url"]) == domain(e["url"])
                  and abs((date.fromisoformat(e["date_start"]) - date.fromisoformat(k["date_start"])).days) <= 7
                  for k in kept)
        if not dup:
            kept.append(e)
    return kept


# Made-up events that only exercise the rules. Not real sources; never put on the map.
_FIXTURES = [
    {"id": "fx-official-warning", "type": "T3", "country": "Latvia", "voice": "official", "attribution": "none",
     "kind": "warning", "url": "https://example.test/a", "quote": "x", "date_start": "2026-09-07"},
    {"id": "fx-weak-source", "type": "T5", "country": "Hungary", "voice": "expert", "attribution": "suspected",
     "kind": "incident", "weak_source": True, "url": "https://example.test/b", "quote": "x", "date_start": "2026-10-19"},
    {"id": "fx-nobody", "type": "T3", "country": "Germany", "voice": "nobody", "attribution": "none",
     "kind": "incident", "url": "https://example.test/c", "quote": "x", "date_start": "2026-10-01"},
    {"id": "fx-no-quote", "type": "T3", "country": "Poland", "voice": "official", "attribution": "named",
     "kind": "incident", "url": "https://example.test/d", "quote": "", "date_start": "2026-10-01"},
    {"id": "fx-out-of-scope", "type": "T3", "country": "Serbia", "voice": "official", "attribution": "none",
     "kind": "warning", "url": "https://example.test/e", "quote": "x", "date_start": "2026-10-01"},
    {"id": "fx-media-only", "type": "T6", "country": "Sweden", "voice": "researchers", "attribution": "named",
     "kind": "incident", "url": "https://example.test/f", "quote": "x", "date_start": "2026-08-20",
     "domains": ["example.test", "other.test"]},
]


def dry_run(days: int) -> None:
    reqs = build_requests(days)
    print(f"DRY RUN: nothing is sent to Exa or Gemini.\n\n{len(reqs)} Exa searches would run:")
    for i, r in enumerate(reqs, 1):
        k = r["kwargs"]
        print(f"  {i}. [{r['group']}] {r['query']}\n     {k['category']}, {k['num_results']} results, "
              f"published {k['start_published_date']} to {k['end_published_date']}, highlights on")
    n = sum(r["kwargs"]["num_results"] for r in reqs)
    print(f"\nUp to {n} results -> up to {n} Gemini calls, each returning:\n  " + json.dumps(EVENT_FIELDS, indent=2).replace("\n", "\n  "))
    print("\nThen in code: drop is_event=false, no URL or quote, or out of scope; dedupe; set level; mark 'new, unreviewed'.")
    print("\nRule check on made-up events (not real sources):")
    for e in _FIXTURES:
        print(f"  {e['id']:<20} valid={str(valid(e)):<5} level={level(e) if valid(e) else '-'}")
    print(f"  country levels: {country_levels(_FIXTURES)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the 'last N days' refresh calls; call nothing")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--plan", choices=["europe", "countries", "full"], help="write the monthly 2024-2026 request plan; call nothing")
    ap.add_argument("--run", choices=["europe", "countries", "full"], help="call Exa for the monthly plan and store results (spends credits)")
    ap.add_argument("--month", help="only this month, e.g. 2026-10")
    a = ap.parse_args()
    if a.plan or a.run:
        plan = build_plan(a.plan or a.run)
        if a.month:
            plan = [r for r in plan if r["month"] == a.month]
            if not plan:
                raise SystemExit(f"no requests for month {a.month}")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
        ms = sorted({r["month"] for r in plan})
        print(f"{len(plan)} Exa searches ({len(ms)} months, {ms[0]} to {ms[-1]}), 10 results each -> {PLAN_FILE}")
        if a.run:
            run_plan(plan)
    elif a.dry_run:
        dry_run(a.days)
    else:
        ap.print_help()

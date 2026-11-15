"""Relevance to one country, 1-5: how much each saved signal matters to that country's public bodies.

Today the country is the United Kingdom (the demo). Another country gets its own profile and its own
agent run, so the same signal can be level 5 for one country and level 2 for another.

How a level is made:
1. Shared signal pool (exists): Exa Deep Search sweep -> quote check -> Exa summary. Same for every country.
2. Per country (this module): ONE Exa Agent run reads the recent signals and returns FACTS per signal:
     same_channel   does it hit a channel public bodies in this country run?
     reached        is the same wave/campaign/tool reported in this country? (+ a source URL)
   A "reached" fact counts only if the cited page is fetched with Exa Contents and names the country.
3. Code turns facts into the level (rel_level). AI never sets the level.

  python -m app.relevance --dry-run     what the agent would get, and levels from code-only facts
  python -m app.relevance --run         one Exa Agent run for the profile (spends credits; budget-capped)
"""
import argparse
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from . import threatmap as tm

PROFILES = {
    "GB": {"code": "GB", "country": "United Kingdom", "the": "the United Kingdom", "name": "the UK", "neighbours": ["Ireland"],
           "names": re.compile(r"\b(united kingdom|uk|britain|british|england|scotland|wales|northern ireland|london)\b", re.I),
           "channels": ("complaints to councils, schools, the NHS, ombudsmen or regulators; Freedom of Information or "
                        "subject access requests; planning or other objections and appeals; emails to council, school "
                        "or court inboxes; petitions to public bodies")},
}
RECENT_DAYS = 90
NAMES = {5: "Act now", 4: "Prepare", 3: "Watch", 2: "Background", 1: "Not relevant"}

# Code-only fallback for "same channel" when the agent did not see a signal (older than RECENT_DAYS).
CHANNEL_WORDS = re.compile(r"council|municipal|local authorit|school|ombuds|complain|request|freedom of information|"
                           r"objection|appeal|data protection|public bod|city hall|town hall|petition|court", re.I)


def file_for(code: str):
    return tm.OUT_DIR / f"relevance_{code}.json"


@lru_cache(maxsize=4)
def _facts(code: str, stamp: float) -> dict:
    f = file_for(code)
    return json.loads(f.read_text(encoding="utf-8")).get("facts", {}) if f.exists() else {}


def facts(code: str = "GB") -> dict:
    f = file_for(code)
    return _facts(code, f.stat().st_mtime if f.exists() else 0.0)


def trend_urls(code: str = "GB") -> set:
    """Evidence pages the agent cites for the AI-complaint TREND. Citing one of these for a threat or
    complaint WAVE only shows the trend is here, not that the same wave reached this country."""
    return {f["evidence_url"] for f in facts(code).values() if f.get("signal_type") == "AI" and f.get("evidence_url")}


def attach(e: dict, code: str = "GB") -> dict:
    fx = facts(code).get(e.get("id"))
    if fx:
        e["rel_facts"] = fx
    return e


def campaign(e: dict) -> bool:
    """The old evidence ladder, folded in as one signal: someone credible calls it coordinated or blames an actor."""
    return e.get("layer") == "hostile" and (e.get("voice") in ("official", "researchers") or e.get("attribution") in ("suspected", "named"))


def signals(e: dict, profile: dict, today: date | None = None) -> dict:
    today = today or date.today()
    fx = e.get("rel_facts") or {}
    text = " ".join(str(e.get(k) or "") for k in ("headline", "summary", "target", "title_en", "title"))
    same_country = e["country"] == profile["country"]
    trend_only = e.get("layer") == "precursor" or (fx.get("evidence_url") in trend_urls(profile["code"]))
    return {
        "same_country": same_country,
        "neighbour": e["country"] in profile["neighbours"],
        "same_channel": fx["same_channel"] if "same_channel" in fx else (e.get("type") in ("T3", "AI") or bool(CHANNEL_WORDS.search(text))),
        "reached": same_country or bool(fx.get("reached_verified")),
        "campaign": campaign(e),
        "recent": (today - date.fromisoformat(e["date_start"])).days <= RECENT_DAYS,
        "media_only": e.get("type") == "T6",
        "trend": trend_only,   # AI complaints, or "reached" only via a trend page: a trend, not one wave
        "agent": bool(fx),
    }


def rel_level(s: dict) -> int:
    if s["media_only"] and not s["same_country"]:
        return 1
    if s["same_country"] and s["same_channel"] and s["recent"]:
        return 5     # hits the channels public bodies here run, here, in the last 90 days
    if s["same_country"] or (s["reached"] and s["recent"] and not s["trend"]):
        return 4     # here but another channel or older; or a specific wave abroad that has reached this country
    if s["same_channel"] and s["recent"] and (s["neighbour"] or s["campaign"] or s["reached"]):
        return 3     # same channel, now: next door, a coordinated campaign, or a trend already seen here
    if s["same_channel"]:
        return 2     # the same kind of pressure elsewhere: background
    return 1


def why(s: dict, profile: dict) -> list[str]:
    """Short reasons shown on the card (at most 4)."""
    out = []
    if s["same_country"]:
        out.append(f"In {profile['name']}")
    elif s["reached"]:
        out.append(f"Trend seen in {profile['name']}" if s["trend"] else f"Same wave hit {profile['name']}")
    elif s["neighbour"]:
        out.append("Next door")
    out.append("Same channel" if s["same_channel"] else "Other channel")
    if s["campaign"]:
        out.append("Campaign signal")
    out.append("Last 90 days" if s["recent"] else "Older")
    return out[:4]


def score(e: dict, profile: dict, today: date | None = None) -> dict:
    s = signals(e, profile, today)
    lv = rel_level(s)
    return {**e, "rel": lv, "rel_name": NAMES[lv], "rel_why": why(s, profile), "rel_by_agent": s["agent"]}


# ---------- the agent run

SCHEMA = {"type": "object", "required": ["signals"], "properties": {"signals": {"type": "array", "items": {
    "type": "object", "required": ["id", "same_channel", "reached"], "properties": {
        "id": {"type": "string"},
        "same_channel": {"type": "boolean", "description": "true if it hits a channel public bodies in the target country run (listed in the query)"},
        "reached": {"type": "boolean", "description": "true only if a published report says the same wave, campaign, template or tool also hit public bodies in the target country"},
        "evidence_url": {"type": "string", "description": "URL of that report; empty if reached is false"},
        "note": {"type": "string", "description": "one short sentence in English: why"}}}}}}


def agent_input(evs: list[dict], profile: dict, today: date | None = None) -> list[dict]:
    today = today or date.today()
    since = (today - timedelta(days=RECENT_DAYS)).isoformat()
    return [{"id": e["id"], "country": e["country"], "type": e["type"], "date": e["date_start"],
             "headline": e.get("headline") or e.get("title_en") or e.get("title"),
             "summary": (e.get("summary") or "")[:300], "url": e["url"]}
            for e in evs if e["date_start"] >= since and e["country"] != profile["country"]]


def agent_query(profile: dict) -> str:
    c = profile.get("the", profile["country"])
    return (f"You assess news signals for public bodies in {c}. For EACH signal in the input data, decide:\n"
            f"1) same_channel: does it hit a channel that public bodies in {c} also run? Channels: {profile['channels']}.\n"
            f"2) reached: search the web. Is the SAME wave, campaign, email template or tool reported to have also hit public bodies "
            f"in {c}? Only true with a published report; give its URL in evidence_url.\n"
            f"Return one item per input signal, using its id. Be conservative.")


def run(code: str = "GB", effort: str = "low", max_cost: float = 1.0) -> dict:
    from . import threatstore as ts
    from .sources.exa_src import _exa
    from .threatdeep import RAW_DIR, _plain, fetch_pages, _norm_url
    profile = PROFILES[code]
    data = agent_input(ts.events(), profile)
    client = _exa()
    kw = {"query": agent_query(profile), "input": {"data": data}, "output_schema": SCHEMA, "effort": effort,
          "metadata": {"app": "lexproof", "profile": code}}
    if effort in ("auto", "ultra"):
        kw["budget"] = {"maxCostDollars": max(1.0, max_cost)}
    t0 = time.time()
    r = client.agent.runs.create(**kw)
    print(f"agent run {r.id} started with {len(data)} signals", flush=True)
    while r.status in ("queued", "running") and time.time() - t0 < 900:
        time.sleep(5)
        r = client.agent.runs.get(r.id)
        print(f"  {r.status} {int(time.time() - t0)}s", flush=True)
    d = RAW_DIR / "agent"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{r.id}.json").write_text(json.dumps({"request": {**kw, "input": {"data": data}}, "response": _plain(r)},
                                               ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if r.status != "completed":
        raise SystemExit(f"agent run {r.status}: {getattr(r.error, 'message', '')}")
    out = (r.output.structured or {}) if r.output else {}
    items = {str(x.get("id")): x for x in out.get("signals", []) if isinstance(x, dict)}
    known = {x["id"] for x in data}
    types = {x["id"]: x["type"] for x in data}
    # "reached" is only kept if the cited page is fetched and names the country (no source, no claim).
    urls = sorted({x.get("evidence_url") for x in items.values() if x.get("reached") and str(x.get("evidence_url", "")).startswith("http")})
    pages = fetch_pages(client, urls) if urls else {}
    facts_out = {}
    for i, x in items.items():
        if i not in known:
            continue
        page = pages.get(_norm_url(x.get("evidence_url", "")), {})
        ok = bool(x.get("reached") and profile["names"].search(page.get("title", "") + " " + page.get("text", "")))
        facts_out[i] = {"signal_type": types.get(i), "same_channel": bool(x.get("same_channel")), "reached_claimed": bool(x.get("reached")),
                        "reached_verified": ok, "evidence_url": x.get("evidence_url") if ok else "",
                        "note": str(x.get("note") or "")[:200]}
    cost = getattr(r.cost_dollars, "total", None) if r.cost_dollars else None
    file_for(code).write_text(json.dumps({"profile": code, "run_id": r.id, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "cost_usd": cost, "signals_sent": len(data), "facts": facts_out},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    _facts.cache_clear()
    res = {"run_id": r.id, "sent": len(data), "answered": len(facts_out), "cost_usd": cost, "seconds": int(time.time() - t0),
           "reached_claimed": sum(f["reached_claimed"] for f in facts_out.values()),
           "reached_verified": sum(f["reached_verified"] for f in facts_out.values())}
    print(res)
    return res


def dry_run(code: str = "GB") -> None:
    from . import threatstore as ts
    import collections
    profile = PROFILES[code]
    evs = ts.events()
    data = agent_input(evs, profile)
    print(f"DRY RUN for {profile['country']}: the agent would get {len(data)} signals ({len(json.dumps(data)):,} chars).")
    print("Query:\n" + agent_query(profile))
    lv = collections.Counter(score(e, profile)["rel"] for e in evs)
    print("\nLevels with code-only facts:", {k: lv[k] for k in (5, 4, 3, 2, 1)})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profile", default="GB")
    ap.add_argument("--effort", default="low")
    a = ap.parse_args()
    run(a.profile, a.effort) if a.run else dry_run(a.profile) if a.dry_run else ap.print_help()

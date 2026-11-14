"""Orchestration. Output is one self-describing JSON document; the UI is a renderer."""
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import ai, deadlines, registry, taxonomy
from .config import settings
from .extract_rules import case_facts, extract
from .sources import exa_src
from .textutil import find_span, is_verbatim
from .verify import verify

SEVERITY = {s["code"]: s["severity"] for s in taxonomy.STATUSES}


def _date(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _precedents(text: str, facts: dict) -> list[dict]:
    keys = ("payment", "passcode", "fraud", "scam", "authoris", "refund", "transfer", "call")
    narrative = " ".join(s for s in re.split(r"(?<=[.!?])\s+", text) if any(k in s.lower() for k in keys))[:600]
    query = "Financial Ombudsman decision: " + (narrative or text[:600])
    res = exa_src.search(query, include_domains=[registry.OMBUDSMAN_DECISIONS_DOMAIN], num_results=5,
                         highlights_query="ombudsman decision outcome reasons gross negligence refund interest")
    return [{"title": r["title"], "url": r["url"], "published_date": r["published_date"],
             "highlight": (r["highlights"] or [""])[0][:500]} for r in res]


def _source_link(c: dict) -> dict:
    """What a caseworker should click. Never a dead link presented as the answer."""
    law = c["law"]
    if c["status"] == "NOT_FOUND":
        tried = [e["url"] for e in c["evidence"] if e["url"] and not e["ok"]]
        if c["nearest_real"]:
            n = c["nearest_real"][0]
            return {"kind": "nearest", "label": f"Nearest real rule: {n['title']}", "url": n["url"],
                    "evidence_url": tried[-1] if tried else ""}
        return {"kind": "evidence", "label": "Checked - does not exist" + (" (HTTP 404)" if tried else ""),
                "url": "", "evidence_url": tried[-1] if tried else ""}
    if law["url"]:
        label = (law["title"] + (f" - {law['heading']}" if law["heading"] else "")).strip(" -")
        return {"kind": "source", "label": label or law["url"], "url": law.get("as_of_url") or law["url"],
                "as_of": law.get("as_of", ""), "evidence_url": ""}
    return {"kind": "none", "label": "No source", "url": "", "evidence_url": ""}


def run(text: str, params: dict, progress=lambda step: None) -> dict:
    t0 = time.time()
    warnings = []
    received = _date(params.get("received_date")) or date.today()
    facts = case_facts(text)
    as_of = params.get("event_date") or facts["event_date_text"] or received.isoformat()
    opts = {"exa": settings.exa_enabled, "snapshots": bool(params.get("exa_snapshots", True)),
            "provenance": bool(params.get("exa_provenance", True))}
    if not settings.exa_enabled:
        warnings.append("EXA_API_KEY not set: discovery, regulator pages, snapshots, provenance and precedent are off.")

    progress("Extracting citations (rules, no AI)")
    cits = extract(text)

    progress(f"Verifying {len(cits)} citations against sources as at {as_of}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda ic: verify(ic[1], text, ic[0], facts, as_of, opts), enumerate(cits, 1)))

    precedents = []
    if settings.exa_enabled and params.get("exa_precedents", True):
        progress("Exa: finding similar Ombudsman decisions")
        try:
            precedents = _precedents(text, facts)
        except Exception as e:
            warnings.append(f"Precedent search failed: {str(e)[:200]}")

    core = None
    if params.get("ai_validate", True) and settings.ai_enabled and claims:
        progress("AI second opinion")
        try:
            v = ai.validate(text, claims)
            by_id = {c["id"]: c for c in claims}
            for row in v.rows:
                c = by_id.get(row.id)
                if not c:
                    continue
                sources = c["law"]["text"] + "\n" + "\n".join(r["text"] for r in c["law"]["related"])
                quote_ok = is_verbatim(row.law_quote, sources)
                c["ai"] = {"agree": row.agree, "suggested_status": row.suggested_status, "reason": row.reason,
                           "implication": row.implication, "law_quote": row.law_quote if quote_ok else "",
                           "law_quote_rejected": bool(row.law_quote) and not quote_ok,
                           "verdict": ("Agrees" if row.agree and row.suggested_status == c["status"]
                                       else f"Suggests {row.suggested_status}: {row.reason}")}
                if row.short_claim:
                    c["short_claim"], c["short_claim_source"] = row.short_claim, "ai"
                if not row.agree or row.suggested_status != c["status"]:
                    c["tags"].append("AI_DISAGREES")
                if row.weak_factual_basis:
                    c["tags"].append("WEAK_FACTUAL_BASIS")
                if not c["law"]["quote"] and quote_ok:
                    c["law"]["quote"], c["law"]["quote_source"] = row.law_quote, "ai (verified verbatim)"
                c["tags"] = sorted(set(c["tags"]))
            core = {"strength": v.core.strength, "reason": v.core.reason, "source": "ai"}
            for m in v.missed_citations:
                span = find_span(text, m.quote)
                if not span or any(c["span"][0] <= span[0] < c["clause_span"][1] for c in claims):
                    continue
                claims.append({
                    "id": f"ai{len(claims) + 1}", "n": len(claims) + 1, "kind": "ai_detected",
                    "citation_as_written": text[span[0]:span[1]], "span": list(span), "clause_span": list(span),
                    "original_text": text[span[0]:span[1]], "short_claim": m.why, "short_claim_source": "ai",
                    "claimed_limits": [], "status": "UNVERIFIED", "reason": "Found by the AI layer only; not verified.",
                    "tags": ["AI_DETECTED"], "implication": "", "nearest_real": [],
                    "law": {"title": "", "heading": "", "url": "", "as_of_url": "", "as_of": "", "quote": "",
                            "quote_source": "", "text": "", "changed_since_event": False, "related": []},
                    "exa": {"snapshot": None, "provenance": [], "discovery": []}, "evidence": [], "ai": None})
        except Exception as e:
            warnings.append(f"AI layer failed, showing deterministic results only: {str(e)[:200]}")
    elif params.get("ai_validate", True) and not settings.ai_enabled:
        warnings.append("AI second opinion off: set AI_API_KEY and AI_MODEL.")

    # ---- case-level tags
    counts = {}
    for c in claims:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    checked = [c for c in claims if c["kind"] != "ai_detected"]
    wrong = sum(1 for c in checked if c["status"] in ("NOT_FOUND", "MISQUOTED"))
    case_tags = []

    def tag(code, reason, ids=()):
        case_tags.append({"code": code, **taxonomy.TAGS[code], "reason": reason, "claim_ids": list(ids)})

    for code in ("FABRICATED_AUTHORITY", "MISSTATED_TIME_LIMIT", "OMITTED_CONDITION", "NO_SOURCE_NAMED",
                 "NOT_STATUTE", "LAW_CHANGED_SINCE_EVENT", "OVERSTATED_CLAIM", "WEAK_FACTUAL_BASIS", "AI_DISAGREES"):
        ids = [c["id"] for c in claims if code in c["tags"]]
        if ids:
            tag(code, f"{len(ids)} claim(s)", ids)
    if checked and wrong / len(checked) >= 0.5:
        tag("HIGH_CITATION_ERROR_RATE", f"{wrong} of {len(checked)} citations are not found or misquoted")
    if core and core["strength"] == "strong":
        tag("CORE_COMPLAINT_STRONG", core["reason"])
    if facts["payment_services"]:
        tag("PAYMENT_SERVICES_COMPLAINT", "Mentions payments/fraud: DISP 1.6.2A 15-business-day deadline applies")
    if facts["data_access_request"]:
        tag("DATA_ACCESS_REQUEST", "Separate UK GDPR deadline")
    if facts["possible_vulnerability"]:
        tag("POSSIBLE_VULNERABILITY", f"Age {facts['age']} stated" if facts["age"] else "Vulnerability indicator")

    for c in claims:
        c["source_link"] = _source_link(c)

    highlights = []
    for c in claims:
        sev = SEVERITY.get(c["status"], "muted")
        highlights.append({"claim_id": c["id"], "kind": "claim", "start": c["clause_span"][0],
                           "end": c["clause_span"][1], "severity": sev, "status": c["status"]})
        highlights.append({"claim_id": c["id"], "kind": "citation", "start": c["span"][0], "end": c["span"][1],
                           "severity": sev, "status": c["status"]})

    n_total = len(checked)
    headline = (f"{n_total} legal references: " + ", ".join(
        f"{counts[s['code']]} {s['label'].lower()}" for s in taxonomy.STATUSES if counts.get(s["code"]))) if claims \
        else "No legal references found."
    return {
        "document": {"text": text},
        "params": {**params, "received_date": received.isoformat(), "as_of": as_of},
        "facts": facts,
        "summary": {"headline": headline, "counts": counts, "core": core},
        "tags": case_tags,
        "highlights": highlights,
        "claims": claims,
        "deadlines": deadlines.compute(received, facts, bool(params.get("fos_eligible", True)),
                                       _date(params.get("reported_date"))),
        "precedents": precedents,
        "view": taxonomy.view_schema(settings.ai_enabled, settings.exa_enabled),
        "meta": {"seconds": round(time.time() - t0, 1), "warnings": warnings,
                 "engines": {"exa": settings.exa_enabled, "ai": settings.ai_enabled,
                             "ai_model": settings.ai_model if settings.ai_enabled else ""}},
    }


def run_safe(text, params, progress):
    try:
        return {"ok": True, "result": run(text, params, progress)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}

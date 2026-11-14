"""Request text -> officer view (citations + proof + deadline + draft reply)."""
import os
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlparse

from . import ai, deadlines
from .check import STATUSES, check
from .config import settings
from .extract_rules import extract as rule_extract
from .textutil import find_span

ORG_NAME = os.getenv("ORG_NAME", "Camden Council")
SEV = {s["code"]: s["severity"] for s in STATUSES}
PARAMETERS = [{"key": "received_date", "label": "Received", "type": "date", "default": "today"}]


def view():
    return {"statuses": STATUSES, "parameters": PARAMETERS, "org_name": ORG_NAME,
            "capabilities": {"exa": settings.exa_enabled, "ai": settings.ai_enabled}}


def _refs(text, warnings):
    """Returns (references, asks, extractor)."""
    if settings.ai_enabled:
        try:
            r = ai.extract(text)
            return [x.model_dump() for x in r.references], r.asks[:3], "Gemini"
        except Exception as e:
            warnings.append(f"{e} - using the rule-based fallback")
    else:
        warnings.append("No Gemini key: rule-based fallback finds explicit references only.")
    refs = []
    for c in rule_extract(text):
        clause = text[c.clause_span[0]:c.clause_span[1]].strip()
        if c.kind == "legislation":
            refs.append({"quote": c.citation_as_written, "kind": "explicit", "act_name": c.instrument_title,
                         "provision": f"{c.provision_kind} {c.provision_id}".strip(), "claim": clause})
        else:
            refs.append({"quote": c.citation_as_written, "kind": "implicit", "act_name": "", "provision": "",
                         "claim": c.citation_as_written})
    return refs, [], "rules"


def request_type(text: str) -> str:
    t = text.lower()
    if re.search(r"subject access|my personal data|data (?:you|the council) hold[s]? about me|article 15", t):
        return "SAR"
    if re.search(r"environmental information|\beirs?\b", t):
        return "EIR"
    return "FOI"


def requester_name(text: str) -> str:
    m = re.search(r"(?:Yours (?:faithfully|sincerely)|Kind regards|Best regards|Regards),?\s*\n\s*([^\n]{2,60})", text, re.I)
    return m.group(1).strip() if m else ""


def draft_reply(received: date, rows, dls, rtype, name) -> str:
    act = {"FOI": "Freedom of Information Act 2000", "EIR": "Environmental Information Regulations 2004",
           "SAR": "UK General Data Protection Regulation"}[rtype]
    due = date.fromisoformat(dls[0]["due"])
    p = [f"Dear {name}," if name else "Dear Sir or Madam,",
         f"{act} - request received {received.day} {received:%B %Y}",
         f"Thank you for your request, which we received on {received.day} {received:%B %Y}. We are handling it under the {act}."]
    for r in rows:
        pr = r.get("proof") or {}
        src = f"{pr.get('title', '')}{' (' + pr['heading'] + ')' if pr.get('heading') else ''}"
        if r["status"] == "MISQUOTED" and r.get("diff"):
            p.append(f"Please note that {r['cited'] or src} requires a response no later than the "
                     f"{r['diff']['law']}, rather than the {r['diff']['claim']} mentioned in your request."
                     if "day" in r["diff"]["law"] else
                     f"Please note that {r['cited'] or src} provides \"{pr.get('quote', '')}\", which differs from "
                     f"your description.")
        elif r["status"] == "MISQUOTED":
            p.append(f"Please note that {r['cited'] or src} provides: \"{pr.get('quote', '')}\"")
        elif r["status"] == "NOT_AS_CITED":
            p.append(f"We could not find {r['cited'] or 'the provision'} you cited. The closest provision appears "
                     f"to be {src}.")
        elif r["status"] == "NOT_FOUND" and r["kind"] == "explicit":
            first = r["note"].split(".")[0] if " has no " in r["note"] else ""
            p.append(f"Please note that the {first.removeprefix('The ')}, so we have not relied on it."
                     if first else f"We could not identify any law matching \"{r['quote']}\".")
    p.append(f"We will respond no later than {due:%A} {due.day} {due:%B %Y}.")
    p.append("If any information is withheld, we will tell you which exemption applies and why, as required by "
             "section 17 of the Freedom of Information Act 2000." if rtype == "FOI" else
             "If any information is withheld, we will explain the reasons.")
    p.append(f"Yours sincerely,\nInformation Governance Team\n{ORG_NAME}")
    return "\n\n".join(p)


def run(text: str, params: dict, progress=lambda s: None) -> dict:
    t0, warnings = time.time(), []
    try:
        received = date.fromisoformat(params.get("received_date") or "")
    except ValueError:
        received = date.today()
    if not settings.exa_enabled:
        warnings.append("No Exa key: unknown laws, 404s and implicit claims can't be looked up.")

    progress("Finding legal references" + (" with Gemini" if settings.ai_enabled else " (rules)"))
    refs, asks, extractor = _refs(text, warnings)

    rtype = request_type(text)
    hint = {"FOI": "Freedom of Information Act 2000, public authority", "EIR": "Environmental Information Regulations 2004",
            "SAR": "UK GDPR data subject rights"}[rtype]
    for r in refs:
        if r["kind"] == "implicit":
            r["hint"] = hint
    progress(f"Checking {len(refs)} references against legislation.gov.uk and Exa")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, refs))

    rows, highlights = [], []
    for i, (ref, res) in enumerate(zip(refs, results), 1):
        span = find_span(text, ref["quote"])
        url = (res.get("proof") or {}).get("url", "")
        row = {"id": f"r{i}", "n": i, **ref, **res, "span": list(span) if span else None,
               "source": urlparse(url).netloc.removeprefix("www.") if url else ""}
        rows.append(row)
        if span:
            highlights.append({"claim_id": row["id"], "start": span[0], "end": span[1], "status": row["status"]})

    dls = deadlines.compute(received, text)
    if rtype == "SAR" and len(dls) > 1:
        dls = [dls[1], dls[0]]
    hols = deadlines.uk_bank_holidays()
    due = date.fromisoformat(dls[0]["due"])
    today = date.today()
    left = deadlines.working_days_between(today, due, hols) if due >= today else -deadlines.working_days_between(due, today, hols)
    counts = {s["code"]: sum(r["status"] == s["code"] for r in rows) for s in STATUSES}
    return {
        "document": {"text": text},
        "params": {"received_date": received.isoformat()},
        "request": {"type": rtype, "requester": requester_name(text), "asks": asks},
        "summary": {"total": len(rows), "counts": counts, "verified": counts.get("MATCH", 0)},
        "highlights": highlights,
        "claims": rows,
        "deadline": {**dls[0], "working_days_left": left},
        "deadlines": dls,
        "draft_reply": draft_reply(received, rows, dls, rtype, requester_name(text)),
        "view": view(),
        "meta": {"seconds": round(time.time() - t0, 1), "warnings": warnings, "extractor": extractor},
    }


def run_safe(text, params, progress):
    try:
        return {"ok": True, "result": run(text, params, progress)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}

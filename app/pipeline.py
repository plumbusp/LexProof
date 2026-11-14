"""Request text -> officer view (citations + proof + deadline + draft reply)."""
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import ai, deadlines
from .check import STATUSES, check
from .config import settings
from .extract_rules import extract as rule_extract
from .textutil import find_span

SEV = {s["code"]: s["severity"] for s in STATUSES}
COLUMNS = [
    {"key": "n", "label": "#"},
    {"key": "quote", "label": "Original text"},
    {"key": "kind", "label": "Type"},
    {"key": "status", "label": "Status"},
    {"key": "proof", "label": "Real text (proof)"},
    {"key": "note", "label": "Note"},
]
PARAMETERS = [{"key": "received_date", "label": "Date received", "type": "date", "default": "today"}]


def view():
    return {"statuses": STATUSES, "grid_columns": COLUMNS, "parameters": PARAMETERS,
            "capabilities": {"exa": settings.exa_enabled, "ai": settings.ai_enabled}}


def _refs(text, warnings):
    if settings.ai_enabled:
        try:
            return [r.model_dump() for r in ai.extract(text)], "gemini"
        except Exception as e:
            warnings.append(f"{e} - using the rule-based fallback")
    else:
        warnings.append("No Gemini key: rule-based fallback finds explicit references only.")
    refs = []
    for c in rule_extract(text):
        clause = text[c.clause_span[0]:c.clause_span[1]].strip()
        if c.kind == "legislation":
            prov = f"{c.provision_kind} {c.provision_id}".strip()
            refs.append({"quote": c.citation_as_written, "kind": "explicit", "act_name": c.instrument_title,
                         "provision": prov, "claim": clause})
        else:
            refs.append({"quote": c.citation_as_written, "kind": "implicit", "act_name": "", "provision": "",
                         "claim": clause})
    return refs, "rules"


def draft_reply(received: date, rows: list[dict], dls: list[dict]) -> str:
    lines = [f"Thank you for your request, which we received on {received:%d %B %Y}.",
             f"We will respond no later than {date.fromisoformat(dls[0]['due']):%d %B %Y} "
             f"(20 working days, Freedom of Information Act 2000 s.10).", ""]
    notes = []
    for r in rows:
        p = r.get("proof") or {}
        src = f"{p.get('title', '')}{' - ' + p['heading'] if p.get('heading') else ''}"
        if r["status"] == "MISQUOTED":
            notes.append(f"- You refer to \"{r['quote']}\". {src} actually provides: \"{p.get('quote', '')}\"")
        elif r["status"] == "NOT_AS_CITED":
            notes.append(f"- We could not find \"{r['quote']}\" as cited. The closest provision appears to be {src}.")
        elif r["status"] == "NOT_FOUND":
            notes.append(f"- We could not identify any law matching \"{r['quote']}\".")
    if notes:
        lines += ["To help us handle your request correctly, please note:", *notes, ""]
    lines += ["We will be in touch if we need any clarification.", "", "Yours sincerely,", "[Officer name]"]
    return "\n".join(lines)


def run(text: str, params: dict, progress=lambda s: None) -> dict:
    t0, warnings = time.time(), []
    try:
        received = date.fromisoformat(params.get("received_date") or "")
    except ValueError:
        received = date.today()
    if not settings.exa_enabled:
        warnings.append("No Exa key: unknown laws, 404s and implicit claims can't be looked up.")

    progress("Finding legal references" + (" (Gemini)" if settings.ai_enabled else " (rules)"))
    refs, extractor = _refs(text, warnings)

    progress(f"Checking {len(refs)} references (dictionary → legislation.gov.uk → Exa)")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, refs))

    rows, highlights = [], []
    for i, (ref, res) in enumerate(zip(refs, results), 1):
        span = find_span(text, ref["quote"])
        row = {"id": f"r{i}", "n": i, **ref, **res, "span": list(span) if span else None}
        rows.append(row)
        if span:
            highlights.append({"claim_id": row["id"], "start": span[0], "end": span[1],
                               "severity": SEV[row["status"]], "status": row["status"]})

    dls = deadlines.compute(received, text)
    counts = {s["code"]: sum(r["status"] == s["code"] for r in rows) for s in STATUSES}
    headline = f"{len(rows)} legal references: " + ", ".join(
        f"{counts[s['code']]} {s['label'].lower()}" for s in STATUSES if counts[s["code"]]) if rows \
        else "No legal references found."
    return {
        "document": {"text": text},
        "params": {"received_date": received.isoformat()},
        "summary": {"headline": headline, "counts": counts},
        "tags": [{"code": s["code"], "label": s["label"], "severity": s["severity"], "reason": s["rule"],
                  "claim_ids": [r["id"] for r in rows if r["status"] == s["code"]]}
                 for s in STATUSES if counts[s["code"]]],
        "highlights": highlights,
        "claims": rows,
        "deadlines": dls,
        "draft_reply": draft_reply(received, rows, dls),
        "view": view(),
        "meta": {"seconds": round(time.time() - t0, 1), "warnings": warnings, "extractor": extractor},
    }


def run_safe(text, params, progress):
    try:
        return {"ok": True, "result": run(text, params, progress)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}

"""The flowchart, step by step.

explicit: dict -> legislation.gov.uk -> compare  => MATCH | MISQUOTED
          unknown name or 404 -> Exa nearest -> fetch real text => NOT_AS_CITED | NOT_FOUND
implicit: Exa "which law says this?" -> fetch real text      => POSSIBLE_RULE | NOT_FOUND
"""
import re

from . import laws
from .sources import exa_src, legislation
from .textutil import normalise
from .timeexpr import compare, parse_durations

STATUSES = [
    {"code": "MATCH", "label": "Match", "severity": "ok",
     "rule": "The law exists and says what the text claims. Real text shown as proof."},
    {"code": "MISQUOTED", "label": "Misquoted", "severity": "warn",
     "rule": "The law exists but says something different. Real text shown as proof."},
    {"code": "NOT_AS_CITED", "label": "Not as cited", "severity": "alert",
     "rule": "The cited law/section doesn't exist as cited. Nearest real rule shown: maybe a wrong number, "
             "a misquote, or invented."},
    {"code": "POSSIBLE_RULE", "label": "Possible rule", "severity": "info",
     "rule": "No law was named. This is the rule that most likely applies, with its real text."},
    {"code": "NOT_FOUND", "label": "Not found", "severity": "alert",
     "rule": "No matching law exists or could be found."},
]


def _best_line(law_text: str, claim: str) -> str:
    words = set(re.findall(r"[a-z]{4,}", normalise(claim)))
    best, score = "", -1
    for line in law_text.splitlines():
        s = len(words & set(re.findall(r"[a-z]{4,}", normalise(line))))
        if line.strip() and s > score:
            best, score = line.strip(), s
    return best


def _fetch(type_, year, number, kind, pid):
    r = legislation.fetch_provision(type_, year, number, kind, pid)
    return r if r["status"] == "ok" else None


def _proof(r, claim_text):
    return {"title": r["act_title"], "heading": r["heading"].rstrip(". "), "url": r["url"], "text": r["text"],
            "quote": _best_line(r["text"], claim_text)}


def _compare(claim_text: str, proof: dict):
    """Code comparison: time limits in the claim vs the real text. Returns (ok, note)."""
    law = parse_durations(proof["text"])
    for d in parse_durations(claim_text):
        res = compare(d, law)
        if res == "differs":
            real = next((x.text for x in law if x.unit == d.unit), law[0].text)
            proof["quote"] = next((l.strip() for l in proof["text"].splitlines() if real in l), proof["quote"])
            return False, f"Says '{d.text}'; the law says '{real}'."
        if res == "approx":
            real = next(x.text for x in law if compare(d, [x]) == "approx")
            return True, f"Says '{d.text}'; the law says '{real}' (close)."
    return True, ""


def _exa_rule(query: str, ev: list):
    """Exa: find a legislation.gov.uk provision, then fetch its real text from the API."""
    try:
        hits = exa_src.search(query, include_domains=["legislation.gov.uk"], num_results=6)
    except exa_src.ExaUnavailable:
        ev.append("Exa not configured")
        return None
    except Exception as e:
        ev.append(f"Exa error: {str(e)[:120]}")
        return None
    ev.append(f"Exa: {len(hits)} results on legislation.gov.uk")
    parsed = [(h, laws.parse_url(h["url"])) for h in hits]
    for h, p in parsed:  # prefer a specific section we can fetch
        if p and p["pid"]:
            r = _fetch(p["type"], p["year"], p["number"], p["kind"], p["pid"])
            if r:
                ev.append(f"Fetched real text: {r['url']}")
                return {"proof": _proof(r, query), "exa_title": h["title"]}
    for h, p in parsed:  # otherwise an Act-level hit, proof = Exa highlight
        if p:
            return {"proof": {"title": h["title"], "heading": "", "url": h["url"],
                              "text": "\n".join(h["highlights"]), "quote": (h["highlights"] or [""])[0]},
                    "exa_title": h["title"]}
    return None


def check(ref: dict) -> dict:
    """ref: {quote, kind, act_name, provision, claim}. Returns the row for the officer view."""
    ev, out = [], {"status": "NOT_FOUND", "note": "", "proof": None}
    claim_text = f"{ref['claim']} {ref['quote']}"

    if ref["kind"] == "explicit":
        law = laws.lookup(ref["act_name"]) or laws.lookup(ref["quote"])
        prov = laws.parse_provision(ref["provision"] or ref["quote"], law["type"] if law else "")
        if law:
            ev.append(f"Dictionary: {ref['act_name'] or ref['quote']} -> {law['type']}/{law['year']}/{law['number']}")
            if prov:
                r = _fetch(law["type"], law["year"], law["number"], *prov)
                if r:
                    ev.append(f"Fetched {r['url']}")
                    proof = _proof(r, claim_text)
                    ok, note = _compare(claim_text, proof)
                    return {**out, "status": "MATCH" if ok else "MISQUOTED", "note": note, "proof": proof,
                            "evidence": ev}
                ev.append(f"404: {law['title']} has no {prov[0]} {prov[1]}")
            else:  # Act named without a section: the Act exists
                ex = legislation.act_exists(law["type"], law["year"], law["number"])
                if ex["exists"]:
                    return {**out, "status": "MATCH", "note": "The Act exists; no section was cited.",
                            "proof": {"title": law["title"], "heading": "", "url": ex["url"], "text": "", "quote": ""},
                            "evidence": ev + [f"Act exists: {ex['url']}"]}
        else:
            ev.append(f"Dictionary: '{ref['act_name'] or ref['quote']}' unknown")
        found = _exa_rule(f"{ref['act_name']} {ref['provision']}: {ref['claim']}", ev)
        if found:
            p = found["proof"]
            label = f"{p['title']}{' - ' + p['heading'] if p['heading'] else ''}"
            return {**out, "status": "NOT_AS_CITED", "proof": p, "evidence": ev,
                    "note": f"Not as cited. Nearest real rule: {label}. Maybe a wrong number, misquoted or invented."}
        return {**out, "note": "No such law found.", "evidence": ev}

    # implicit
    found = _exa_rule(ref["claim"] or ref["quote"], ev)
    if found:
        ok, note = _compare(claim_text, found["proof"])
        return {**out, "status": "POSSIBLE_RULE", "proof": found["proof"], "evidence": ev,
                "note": ("Likely rule. " + note).strip() if note else "Likely rule - check it applies."}
    return {**out, "note": "No law found that says this.", "evidence": ev}

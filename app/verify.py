"""Deterministic verification of each citation, with Exa for discovery and reading.

Status rules live in taxonomy.STATUSES. Lessons from the A/B test are encoded:
- read the provisions where a rule's detail lives (RELATED), not just the cited one
- a claim that names no authority is NOT_FOUND unless a source is identified (row 9)
- regulator rules are first-class citations, read via Exa (row 10)
- never present a 404 URL as the answer: keep it as evidence, offer the nearest real rule
"""
import re
from urllib.parse import urlparse

from . import registry
from .extract_rules import Citation
from .sources import exa_src, legislation
from .textutil import is_verbatim, normalise
from .timeexpr import compare, parse_durations

LIABILITY_TERMS = {"fraudulently": ["fraud"], "gross negligence": ["gross negligence", "grossly negligent"],
                   "with intent": ["intent", "deliberately"]}
LEG_URL_RE = re.compile(r"legislation\.gov\.uk/(ukpga|uksi|eur|asp|wsi|ssi|nia)/([^/]+(?:/[^/]+)?)/(\d+)")


def _log(ev, step, detail="", url="", ok=True):
    ev.append({"step": step, "detail": detail, "url": url, "ok": ok})


def _best_line(law_text: str, clause: str) -> str:
    words = {w for w in re.findall(r"[a-z]{4,}", normalise(clause))}
    best, score = "", -1
    for line in law_text.splitlines():
        s = len(words & set(re.findall(r"[a-z]{4,}", normalise(line))))
        if s > score and line.strip():
            best, score = line.strip(), s
    return best


def _line_with(law_text: str, needle: str) -> str:
    for line in law_text.splitlines():
        if needle.lower() in line.lower():
            return line.strip()
    return ""


def _short(clause: str, n=170) -> str:
    c = re.sub(r"\s+", " ", clause).strip(" ,;")
    return c if len(c) <= n else c[: n - 1].rsplit(" ", 1)[0] + "…"


def _rule_short(c: Citation, clause: str) -> str:
    """Short claim without AI: the authority + what is asserted about it."""
    who = c.citation_as_written
    if (c.kind == "legislation" and c.provision_id and c.instrument_title
            and registry.normalise_title(c.instrument_title) not in registry.normalise_title(who)):
        who = f"{who} ({c.instrument_title})"
    parts = []
    if c.claimed_durations:
        parts.append("time limit " + ", ".join(d.text for d in c.claimed_durations))
    if c.only_if:
        parts.append("liable only if " + _short(c.only_if, 60))
    if c.overstated:
        parts.append(", ".join(c.overstated))
    if not parts:
        tail = clause[clause.find(c.citation_as_written) + len(c.citation_as_written):] if c.citation_as_written in clause else clause
        parts.append(_short(tail, 90))
    body = "; ".join(p for p in parts if p.strip(" ,."))
    return _short(f"{who}: {body}" if body else who, 200)


def _new_claim(c: Citation, text: str, n: int) -> dict:
    clause = text[c.clause_span[0]:c.clause_span[1]]
    return {
        "id": c.id, "n": n, "kind": c.kind, "citation_as_written": c.citation_as_written,
        "span": list(c.span), "clause_span": list(c.clause_span), "original_text": clause.strip(),
        "short_claim": _rule_short(c, clause), "short_claim_source": "rule",
        "claimed_limits": [d.text for d in c.claimed_durations],
        "status": "UNVERIFIED", "reason": "", "tags": [], "implication": "",
        "law": {"title": "", "heading": "", "url": "", "as_of_url": "", "as_of": "", "quote": "",
                "quote_source": "", "text": "", "changed_since_event": False, "related": []},
        "nearest_real": [], "exa": {"snapshot": None, "provenance": [], "discovery": []},
        "evidence": [], "ai": None,
    }


# ---------------------------------------------------------------- resolution
def _resolve_instrument(c: Citation, ev, use_exa: bool):
    if c.leg_type and c.number:
        _log(ev, "Resolved instrument from registry", f"{c.instrument_title} -> {c.leg_type}/{c.year}/{c.number}")
        return True
    hits = legislation.title_search(c.instrument_title)
    want = registry.normalise_title(c.instrument_title)
    for h in hits:
        if registry.normalise_title(h["title"]) == want:
            c.leg_type, c.year, c.number, c.resolution = h["type"], h["year"], h["number"], "title_search"
            _log(ev, "Resolved via legislation.gov.uk title search", h["title"], h["url"])
            return True
    _log(ev, "legislation.gov.uk title search", f"no Act titled '{c.instrument_title}' ({len(hits)} fuzzy results)",
         ok=False)
    if use_exa:
        try:
            res = exa_src.search(f"{c.instrument_title} legislation", include_domains=["legislation.gov.uk"],
                                 num_results=5)
        except exa_src.ExaUnavailable:
            return False
        for r in res:
            m = LEG_URL_RE.search(r["url"])
            if m and registry.normalise_title(r["title"]).startswith(want):
                c.leg_type, c.year, c.number, c.resolution = m.group(1), m.group(2), m.group(3), "exa"
                _log(ev, "Resolved via Exa search (legislation.gov.uk)", r["title"], r["url"])
                return True
        _log(ev, "Exa search of legislation.gov.uk", f"no instrument matching '{c.instrument_title}'", ok=False)
    return False


def _nearest_real(query: str, ev, domains=None, n=3):
    """Exa semantic search for the closest REAL rule to what the complaint claims."""
    try:
        res = exa_src.search(query, include_domains=domains or ["legislation.gov.uk", "psr.org.uk",
                                                                "handbook.fca.org.uk"], num_results=n)
    except exa_src.ExaUnavailable:
        _log(ev, "Nearest real rule", "Exa not configured", ok=False)
        return []
    out = [{"title": r["title"], "url": r["url"], "why": (r["highlights"] or [""])[0][:400]} for r in res]
    _log(ev, "Exa: nearest real rules", f"{len(out)} candidates", ok=bool(out))
    return out


# ---------------------------------------------------------------- checks
def _apply_text_checks(claim, c: Citation, law_text: str, main_text: str, prov_label: str):
    law_durs = parse_durations(law_text)
    status, reasons = "VERIFIED", []
    for cd in c.claimed_durations:
        res = compare(cd, law_durs)
        if res == "differs":
            status = "MISQUOTED"
            claim["tags"].append("MISSTATED_TIME_LIMIT")
            ld = law_durs[0]
            claim["law"]["quote"] = _line_with(main_text, ld.text) or _line_with(law_text, ld.text)
            reasons.append(f"Complaint says '{cd.text}'; {prov_label} says '{ld.text}'.")
            claim["implication"] = f"The real limit is '{ld.text}', not '{cd.text}'."
        elif res == "approx":
            status = "VERIFIED_WITH_CAVEAT" if status == "VERIFIED" else status
            claim["tags"].append("MISSTATED_TIME_LIMIT")
            ld = next(d for d in law_durs if compare(cd, [d]) == "approx")
            claim["law"]["quote"] = claim["law"]["quote"] or _line_with(law_text, ld.text)
            reasons.append(f"Complaint says '{cd.text}'; the law says '{ld.text}' (close, not identical).")
            claim["implication"] = claim["implication"] or f"Deadline is '{ld.text}', not exactly '{cd.text}'."
    if c.only_if:
        claimed = normalise(c.only_if)
        missing = [t for t, syn in LIABILITY_TERMS.items()
                   if t in normalise(main_text) and not any(s in claimed for s in syn)]
        if missing:
            status = "MISQUOTED"
            claim["tags"].append("OMITTED_CONDITION")
            claim["law"]["quote"] = claim["law"]["quote"] or _line_with(main_text, missing[0])
            reasons.append(f"Complaint says liability only if '{c.only_if}'; {prov_label} also covers: "
                           + ", ".join(missing) + ".")
            claim["implication"] = claim["implication"] or (
                "The bank can also hold the customer liable for " + ", ".join(missing)
                + " - but it must prove it (PSRs reg 75).")
    if c.overstated:
        claim["tags"].append("OVERSTATED_CLAIM")
        reasons.append("Overstated wording: " + ", ".join(f"'{o}'" for o in c.overstated) + ".")
        if status == "VERIFIED":
            status = "VERIFIED_WITH_CAVEAT"
    if not claim["law"]["quote"]:
        claim["law"]["quote"] = _best_line(main_text, claim["original_text"])
    claim["law"]["quote_source"] = "rule"
    return status, reasons


def _provenance(claim, ev):
    try:
        res = exa_src.search(claim["original_text"], exclude_domains=registry.OFFICIAL_DOMAINS, num_results=5)
    except exa_src.ExaUnavailable:
        return
    res.sort(key=lambda r: r["published_date"] or "9999")
    claim["exa"]["provenance"] = [{"title": r["title"], "url": r["url"], "published_date": r["published_date"],
                                   "highlight": (r["highlights"] or [""])[0][:300]} for r in res]
    _log(ev, "Exa: where this claim appears online", f"{len(res)} pages (official sites excluded)")


# ---------------------------------------------------------------- per kind
def _verify_legislation(claim, c, as_of, opts, ev):
    use_exa = opts["exa"]
    if not _resolve_instrument(c, ev, use_exa):
        claim["status"] = "NOT_FOUND"
        claim["tags"].append("FABRICATED_AUTHORITY")
        claim["reason"] = f"No instrument titled '{c.instrument_title}' exists on legislation.gov.uk."
        claim["nearest_real"] = _nearest_real(claim["original_text"], ev) if use_exa else []
        claim["implication"] = ("This law does not exist. Closest real rule: " + claim["nearest_real"][0]["title"]
                                if claim["nearest_real"] else "This law does not exist; do not rely on it.")
        return
    base = f"{c.leg_type}/{c.year}/{c.number}"
    claim["law"]["title"] = c.instrument_title
    if not c.provision_id:  # act-level citation ("Equality Act 2010")
        ex = legislation.act_exists(c.leg_type, c.year, c.number)
        _log(ev, "Act exists check", ex["page_title"], ex["url"], ex["exists"])
        if not ex["exists"]:
            claim["status"], claim["reason"] = "NOT_FOUND", "Act not found on legislation.gov.uk."
            claim["tags"].append("FABRICATED_AUTHORITY")
            return
        claim["law"]["url"] = ex["url"]
        texts = []
        for rel in registry.RELATED.get((base, None), []):
            kind, pid = rel.split("/")
            r = legislation.fetch_provision(c.leg_type, c.year, c.number, kind, pid, as_of)
            if r["status"] in ("ok", "not_in_force_on_date"):
                claim["law"]["related"].append({"label": f"{kind} {pid} - {r['heading']}", "url": r["url"],
                                                "text": r["text"]})
                texts.append(r["text"])
        claim["law"]["text"] = "\n".join(texts)
        claim["law"]["quote"] = _best_line(claim["law"]["text"], claim["original_text"]) if texts else ""
        claim["law"]["quote_source"] = "rule"
        claim["status"] = "VERIFIED_WITH_CAVEAT"
        claim["reason"] = ("The Act exists, but no provision is cited; whether it applies depends on the facts "
                           "alleged, which the law text alone cannot confirm.")
        claim["implication"] = "Check the facts against the relevant sections listed."
        return

    label = f"{c.provision_kind} {c.provision_id}"
    r = legislation.fetch_provision(c.leg_type, c.year, c.number, c.provision_kind, c.provision_id, as_of)
    for a in r["attempts"]:
        _log(ev, "Fetch legislation.gov.uk", f"HTTP {a['http_status']}", a["url"], a["http_status"] == 200)
    if r["status"] in ("provision_not_found", "act_not_found"):
        claim["status"] = "NOT_FOUND"
        claim["tags"].append("FABRICATED_AUTHORITY")
        claim["reason"] = (f"{c.instrument_title} has no {label} (legislation.gov.uk returned "
                           f"{r['attempts'][-1]['http_status']}).")
        if use_exa:  # search inside the same Act first (Exa domain+path prefix), then everywhere
            claim["nearest_real"] = _nearest_real(claim["original_text"], ev,
                                                  domains=[f"legislation.gov.uk/{base}"], n=3)
            claim["nearest_real"] += _nearest_real(claim["original_text"], ev, n=2)
        claim["implication"] = "This provision does not exist; do not rely on it."
        return
    if r["status"] == "error":
        claim["status"], claim["reason"] = "UNVERIFIED", "legislation.gov.uk could not be reached."
        return
    claim["law"].update({"title": r["act_title"] or c.instrument_title, "heading": r["heading"], "url": r["url"],
                         "as_of_url": r["as_of_url"], "as_of": r["as_of"], "text": r["text"],
                         "changed_since_event": r["changed_since_event"],
                         "version_start": r["version_start"], "versions": r["versions"]})
    if r["status"] == "not_in_force_on_date":
        claim["reason"] = f"{label} exists now but was not in force on {as_of}."
    related_text = []
    for rel in registry.RELATED.get((base, c.provision_id), []):
        kind, pid = rel.split("/")
        rr = legislation.fetch_provision(c.leg_type, c.year, c.number, kind, pid, as_of)
        if rr["status"] in ("ok", "not_in_force_on_date"):
            claim["law"]["related"].append({"label": f"{kind} {pid} - {rr['heading']}", "url": rr["url"],
                                            "text": rr["text"], "changed_since_event": rr["changed_since_event"]})
            related_text.append(rr["text"])
            if rr["changed_since_event"]:
                claim["tags"].append("LAW_CHANGED_SINCE_EVENT")
    if r["changed_since_event"]:
        claim["tags"].append("LAW_CHANGED_SINCE_EVENT")
    status, reasons = _apply_text_checks(claim, c, "\n".join([r["text"], *related_text]), r["text"],
                                         f"{c.instrument_title} {label}")
    claim["status"] = status if r["status"] == "ok" else "UNVERIFIED"
    claim["reason"] = " ".join([claim["reason"], *reasons]).strip() or f"{label} exists and supports the claim."
    if status == "VERIFIED" and not claim["implication"]:
        claim["implication"] = "Supports the complaint as stated."


def _verify_regulator(claim, c, as_of, opts, facts, ev):
    rule = registry.REGULATOR_RULES.get(c.regulator_rule or "DISP_1_6")
    claim["status"] = "NOT_LEGISLATION"
    claim["tags"].append("NOT_STATUTE")
    claim["law"]["title"], claim["law"]["url"] = rule["title"], rule["url"]
    if not opts["exa"]:
        claim["status"], claim["reason"] = "UNVERIFIED", "Regulator rule; Exa needed to read the FCA Handbook."
        return
    found = exa_src.search(rule["exa_query"], include_domains=rule["domains"], num_results=3)
    url = next((f["url"] for f in found if urlparse(f["url"]).path.rstrip("/") == urlparse(rule["url"]).path.rstrip("/")),
               found[0]["url"] if found else rule["url"])
    _log(ev, "Exa: found regulator page", url, url)
    live = exa_src.contents(url, highlights_query=claim["original_text"])
    _log(ev, "Exa: read JS-rendered page (plain GET returns an empty shell)", live.get("error", "ok"), url,
         live["ok"])
    if not live["ok"]:
        claim["status"], claim["reason"] = "UNVERIFIED", "Could not read the regulator page."
        return
    text = live["text"]
    if opts["snapshots"] and as_of:
        snap = exa_src.contents(url, snapshot_as_of=as_of)
        if snap["ok"]:
            window = lambda t: normalise(" ".join(l for l in t.splitlines() if any(a in l for a in rule["anchors"])))
            changed = window(snap["text"]) != window(text)
            claim["exa"]["snapshot"] = {"as_of": as_of, "snapshot_at": snap["snapshot_at"], "changed": changed,
                                        "url": url}
            text = snap["text"]
            _log(ev, "Exa Snapshot: page as stored on/before incident date",
                 f"snapshot {snap['snapshot_at'] or 'n/a'}; relevant rules {'CHANGED' if changed else 'unchanged'} since")
            if changed:
                claim["tags"].append("LAW_CHANGED_SINCE_EVENT")
        else:
            claim["exa"]["snapshot"] = {"as_of": as_of, "error": snap.get("error", "")}
            _log(ev, "Exa Snapshot", snap.get("error", "unavailable"), url, False)
    claim["law"]["url"], claim["law"]["text"] = url, text
    claim["law"]["as_of"] = as_of if claim["exa"]["snapshot"] and claim["exa"]["snapshot"].get("snapshot_at") else ""
    reasons = ["This is an FCA rule (DISP), not legislation."]
    if facts.get("payment_services"):
        q = _line_with(text, "15 business days")
        claimed = c.claimed_durations[0].text if c.claimed_durations else ""
        if q:
            claim["law"]["quote"], claim["law"]["quote_source"] = q, "rule"
            if claimed and compare(c.claimed_durations[0], parse_durations(q)) != "match":
                claim["tags"].append("MISSTATED_TIME_LIMIT")
                reasons.append(f"For payment services complaints DISP 1.6.2A R sets 15 business days, not '{claimed}'.")
                claim["implication"] = "The bank's deadline is 15 business days (35 in exceptional cases)."
    if not claim["law"]["quote"]:
        claim["law"]["quote"] = _line_with(text, "eight weeks") or _best_line(text, claim["original_text"])
        claim["law"]["quote_source"] = "rule"
    claim["reason"] = " ".join(reasons)


def _verify_unnamed(claim, c, opts, ev):
    claim["status"] = "NOT_FOUND"
    claim["tags"].append("NO_SOURCE_NAMED")
    claim["reason"] = "No law is named, and no statutory rule giving this was identified."
    if opts["exa"]:
        hits = exa_src.search(claim["original_text"] + " statutory right", include_domains=["legislation.gov.uk"],
                              num_results=3)
        claim["exa"]["discovery"] = [{"title": h["title"], "url": h["url"], "highlight": (h["highlights"] or [""])[0][:300]}
                                     for h in hits]
        _log(ev, "Exa: searched legislation.gov.uk for a source", f"{len(hits)} related provisions (shown, not matched)")
        claim["nearest_real"] = [{"title": h["title"], "url": h["url"], "why": "Related provision - check scope"}
                                 for h in hits[:2]]
    claim["implication"] = "Treat as a request, not a legal entitlement, unless a source is shown."


def verify(c: Citation, text: str, n: int, facts: dict, as_of: str, opts: dict) -> dict:
    claim = _new_claim(c, text, n)
    ev = claim["evidence"]
    try:
        if c.kind == "legislation":
            _verify_legislation(claim, c, as_of, opts, ev)
        elif c.kind == "regulator_rule":
            _verify_regulator(claim, c, as_of, opts, facts, ev)
        else:
            _verify_unnamed(claim, c, opts, ev)
        if opts["exa"] and opts["provenance"] and claim["status"] in ("MISQUOTED", "NOT_FOUND", "VERIFIED_WITH_CAVEAT"):
            _provenance(claim, ev)
    except exa_src.ExaUnavailable as e:
        _log(ev, "Exa", str(e), ok=False)
    except Exception as e:  # one bad source must not sink the whole analysis
        _log(ev, "Error", f"{type(e).__name__}: {str(e)[:200]}", ok=False)
        if claim["status"] == "UNVERIFIED":
            claim["reason"] = claim["reason"] or "Checker error; see evidence log."
    # A quote is only shown if it is literally in the fetched source
    if claim["law"]["quote"] and not is_verbatim(claim["law"]["quote"], claim["law"]["text"] + "\n" +
                                                 "\n".join(r["text"] for r in claim["law"]["related"])):
        claim["law"]["quote"] = ""
    claim["tags"] = sorted(set(claim["tags"]))
    return claim

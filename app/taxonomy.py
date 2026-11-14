"""Backend-owned vocabulary. The UI renders whatever this says, so the UI can
change freely without re-encoding any of these rules."""

# Status rules (fixes the ambiguity we hit in testing: rows 7-10 of the answer key)
STATUSES = [
    {"code": "VERIFIED", "label": "Verified", "severity": "ok",
     "rule": "The provision exists in force on the incident date and its text supports the claim."},
    {"code": "VERIFIED_WITH_CAVEAT", "label": "Verified, with caveat", "severity": "info",
     "rule": "The provision exists, but a detail is slightly off (e.g. '30 days' vs 'one month') or the claim "
             "rests on facts the law alone can't confirm."},
    {"code": "MISQUOTED", "label": "Misquoted", "severity": "warn",
     "rule": "The provision exists but its text contradicts the claim (different time limit, omitted condition)."},
    {"code": "NOT_FOUND", "label": "Not found", "severity": "alert",
     "rule": "The instrument or provision does not exist (HTTP 404/400, no title match), or no statutory source "
             "for the claim could be identified."},
    {"code": "NOT_LEGISLATION", "label": "Regulator rule / practice", "severity": "info",
     "rule": "The authority is real but is a regulator rule or ombudsman practice, not legislation."},
    {"code": "UNVERIFIED", "label": "Unverified", "severity": "muted",
     "rule": "The checker could not fetch or match a source. Needs a human."},
]

TAGS = {
    # claim-level (assigned by deterministic rules)
    "FABRICATED_AUTHORITY": {"label": "Fabricated authority", "severity": "alert"},
    "MISSTATED_TIME_LIMIT": {"label": "Wrong time limit", "severity": "warn"},
    "OMITTED_CONDITION": {"label": "Omitted condition", "severity": "warn"},
    "OVERSTATED_CLAIM": {"label": "Overstated claim", "severity": "warn"},
    "NOT_STATUTE": {"label": "Not a statute", "severity": "info"},
    "NO_SOURCE_NAMED": {"label": "No source named", "severity": "info"},
    "LAW_CHANGED_SINCE_EVENT": {"label": "Law changed since incident", "severity": "info"},
    "AI_DISAGREES": {"label": "AI second opinion differs", "severity": "warn"},
    "AI_DETECTED": {"label": "Found by AI only", "severity": "muted"},
    "WEAK_FACTUAL_BASIS": {"label": "Weak factual basis", "severity": "info"},
    # case-level
    "HIGH_CITATION_ERROR_RATE": {"label": "Most citations wrong", "severity": "alert"},
    "CORE_COMPLAINT_STRONG": {"label": "Core complaint looks strong", "severity": "ok"},
    "PAYMENT_SERVICES_COMPLAINT": {"label": "Payment services complaint", "severity": "info"},
    "DATA_ACCESS_REQUEST": {"label": "Contains data access request", "severity": "info"},
    "POSSIBLE_VULNERABILITY": {"label": "Possible vulnerability", "severity": "warn"},
}

GRID_COLUMNS = [
    {"key": "n", "label": "#"},
    {"key": "original_text", "label": "Original text"},
    {"key": "short_claim", "label": "Claim (short)"},
    {"key": "status", "label": "Status"},
    {"key": "law.quote", "label": "What the source says"},
    {"key": "source_link", "label": "Source to double-check"},
    {"key": "implication", "label": "What it means for this case"},
    {"key": "ai.verdict", "label": "AI second opinion"},
]

PARAMETERS = [
    {"key": "received_date", "label": "Complaint received", "type": "date", "default": "today"},
    {"key": "event_date", "label": "Incident date", "type": "date", "default": "",
     "help": "Law is checked as in force on this date. Blank = detect from text."},
    {"key": "reported_date", "label": "Customer reported it", "type": "date", "default": ""},
    {"key": "fos_eligible", "label": "Eligible for Ombudsman", "type": "bool", "default": True},
    {"key": "exa_snapshots", "label": "Rules as at incident date (Exa Snapshot)", "type": "bool", "default": True},
    {"key": "exa_provenance", "label": "Trace claims on the web (Exa)", "type": "bool", "default": True},
    {"key": "exa_precedents", "label": "Similar Ombudsman decisions (Exa)", "type": "bool", "default": True},
    {"key": "ai_validate", "label": "AI second opinion", "type": "bool", "default": True},
]


def view_schema(ai_enabled: bool, exa_enabled: bool) -> dict:
    return {
        "statuses": STATUSES,
        "tags": [{"code": k, **v} for k, v in TAGS.items()],
        "grid_columns": GRID_COLUMNS,
        "parameters": PARAMETERS,
        "capabilities": {"ai": ai_enabled, "exa": exa_enabled},
    }

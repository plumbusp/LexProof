"""Name -> legislation.gov.uk ID dictionary, plus provision/URL parsing.
Unknown names go to Exa (see check.py)."""
import re

# (alias regex, (official title, type, year, number)). Most specific first.
INSTRUMENT_ALIASES = [
    (r"Freedom of Information \(Scotland\) Act\s*2002|\bFOISA\b",
     ("Freedom of Information (Scotland) Act 2002", "asp", "2002", "13")),
    (r"Freedom of Information Act\s*2000|\bFOIA\b|\bFoI Act\b|\bFOI Act\b",
     ("Freedom of Information Act 2000", "ukpga", "2000", "36")),
    (r"Environmental Information Regulations\s*2004|\bEIRs?\b",
     ("The Environmental Information Regulations 2004", "uksi", "2004", "3391")),
    (r"\bUK\s+GDPR\b|\bGDPR\b|General Data Protection Regulation", ("UK GDPR", "eur", "2016", "679")),
    (r"Data Protection Act\s*2018|\bDPA\s*2018\b", ("Data Protection Act 2018", "ukpga", "2018", "12")),
    (r"Local Government \(Access to Information\) Act\s*1985",
     ("Local Government (Access to Information) Act 1985", "ukpga", "1985", "43")),
    (r"Local Government Act\s*1972", ("Local Government Act 1972", "ukpga", "1972", "70")),
    (r"Local Government Act\s*2000", ("Local Government Act 2000", "ukpga", "2000", "22")),
    (r"Localism Act\s*2011", ("Localism Act 2011", "ukpga", "2011", "20")),
    (r"Human Rights Act\s*1998", ("Human Rights Act 1998", "ukpga", "1998", "42")),
    (r"Equality Act\s*2010", ("Equality Act 2010", "ukpga", "2010", "15")),
    (r"Re-use of Public Sector Information Regulations\s*2015",
     ("The Re-use of Public Sector Information Regulations 2015", "uksi", "2015", "1415")),
    (r"Housing Act\s*1996", ("Housing Act 1996", "ukpga", "1996", "52")),
    (r"Highways Act\s*1980", ("Highways Act 1980", "ukpga", "1980", "66")),
    (r"Town and Country Planning Act\s*1990", ("Town and Country Planning Act 1990", "ukpga", "1990", "8")),
    (r"Payment Services Regulations\s*2017|\bPSRs\b", ("The Payment Services Regulations 2017", "uksi", "2017", "752")),
    (r"Consumer Rights Act\s*2015", ("Consumer Rights Act 2015", "ukpga", "2015", "15")),
    (r"Financial Services and Markets Act\s*2000|\bFSMA\b",
     ("Financial Services and Markets Act 2000", "ukpga", "2000", "8")),
]

PROVISION_KIND_FOR_TYPE = {"ukpga": "section", "asp": "section", "nia": "section",
                           "uksi": "regulation", "ssi": "regulation", "wsi": "regulation", "eur": "article"}

_PROV_RE = re.compile(r"\b(section|s\.?|ss\.?|regulation|reg\.?|regs?\.?|article|art\.?|schedule|sch\.?)\s*"
                      r"(\d+[A-Z]{0,2})", re.I)
_URL_RE = re.compile(r"legislation\.gov\.uk/(ukpga|uksi|asp|ssi|wsi|nia|eur)/(\d{4})/(\d+)"
                     r"(?:/(section|regulation|article|schedule)/([0-9A-Za-z.]+))?")


def match_alias(text: str):
    for pattern, info in INSTRUMENT_ALIASES:
        if re.search(pattern, text or "", re.IGNORECASE):
            return info
    return None


def lookup(name: str):
    info = match_alias(name)
    if not info:
        return None
    return {"title": info[0], "type": info[1], "year": info[2], "number": info[3]}


def parse_provision(text: str, leg_type: str):
    """'section 10(1)' -> ('section', '10'); kind follows the instrument type."""
    m = _PROV_RE.search(text or "")
    if not m:
        return None
    word = m.group(1).lower().rstrip(".")
    kind = "schedule" if word.startswith("sch") else PROVISION_KIND_FOR_TYPE.get(leg_type, "section")
    return kind, m.group(2)


def parse_url(url: str):
    m = _URL_RE.search(url or "")
    if not m:
        return None
    return {"type": m.group(1), "year": m.group(2), "number": m.group(3),
            "kind": m.group(4) or "", "pid": (m.group(5) or "").rstrip(".") if m.group(5) else ""}


def normalise_title(t: str) -> str:
    t = re.sub(r"^\s*the\s+", "", (t or "").strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

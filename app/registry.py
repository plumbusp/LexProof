"""Curated knowledge the deterministic layer relies on.

Anything not listed here is resolved live (legislation.gov.uk title search,
then Exa). The registry only speeds up common instruments and encodes
*where the detail of a rule actually lives* - the gap that made a pure
keyword check mislabel UK GDPR Art 15 in testing (its deadline is in Art 12/12A).
"""
import re

# alias regex -> (canonical title, legislation.gov.uk type, year, number)
INSTRUMENT_ALIASES = [
    (r"\bUK\s+GDPR\b|\bU\.K\.\s*GDPR\b|\bGDPR\b|General Data Protection Regulation",
     ("UK GDPR", "eur", "2016", "679")),
    (r"\bPSRs\b(?:\s*2017)?|Payment Services Regulations\s+2017",
     ("The Payment Services Regulations 2017", "uksi", "2017", "752")),
    (r"\bFSMA\b(?:\s*2000)?|Financial Services and Markets Act\s+2000",
     ("Financial Services and Markets Act 2000", "ukpga", "2000", "8")),
    (r"Financial Services and Markets Act\s+2023",
     ("Financial Services and Markets Act 2023", "ukpga", "2023", "29")),
    (r"\bCRA\s*2015\b|Consumer Rights Act\s+2015", ("Consumer Rights Act 2015", "ukpga", "2015", "15")),
    (r"Equality Act\s+2010", ("Equality Act 2010", "ukpga", "2010", "15")),
    (r"\bDPA\s*2018\b|Data Protection Act\s+2018", ("Data Protection Act 2018", "ukpga", "2018", "12")),
    (r"Consumer Credit Act\s+1974", ("Consumer Credit Act 1974", "ukpga", "1974", "39")),
    (r"County Courts Act\s+1984", ("County Courts Act 1984", "ukpga", "1984", "28")),
    (r"Senior Courts Act\s+1981", ("Senior Courts Act 1981", "ukpga", "1981", "54")),
    (r"Late Payment of Commercial Debts \(Interest\) Act\s+1998",
     ("Late Payment of Commercial Debts (Interest) Act 1998", "ukpga", "1998", "20")),
]

PROVISION_KIND_FOR_TYPE = {"ukpga": "section", "uksi": "regulation", "eur": "article",
                           "asp": "section", "wsi": "regulation", "ssi": "regulation", "nia": "section"}

# (type/year/number, provision or None) -> extra provisions to read with it
RELATED = {
    ("eur/2016/679", "15"): ["article/12", "article/12A"],
    ("uksi/2017/752", "76"): ["regulation/75"],
    ("uksi/2017/752", "77"): ["regulation/75", "regulation/72"],
    ("ukpga/2010/15", None): ["section/13", "section/29", "section/5"],
}

# Regulator rules (not on legislation.gov.uk). Fetched through Exa because the
# FCA Handbook renders with JavaScript and returns an empty shell to plain GETs.
REGULATOR_RULES = {
    "DISP_1_6": {
        "title": "FCA Handbook DISP 1.6 - Complaints time limit rules",
        "url": "https://handbook.fca.org.uk/handbook/disp1/disp1s6",
        "exa_query": "FCA Handbook DISP 1.6 complaints time limit rules final response eight weeks 15 business days",
        "domains": ["handbook.fca.org.uk"],
        "anchors": ["1.6.2", "eight weeks", "15 business days"],
    },
    "PSR_APP_REIMBURSEMENT": {
        "title": "PSR APP scam reimbursement requirement (Specific Requirement 1)",
        "url": "https://www.psr.org.uk/information-for-consumers/app-fraud-reimbursement-protections/",
        "exa_query": "PSR APP scam reimbursement five business days stop the clock £85,000",
        "domains": ["psr.org.uk"],
        "anchors": ["five business days", "5 business days", "85,000"],
    },
}

OMBUDSMAN_DECISIONS_DOMAIN = "financial-ombudsman.org.uk"
OFFICIAL_DOMAINS = ["legislation.gov.uk", "handbook.fca.org.uk", "fca.org.uk", "psr.org.uk",
                    "financial-ombudsman.org.uk", "gov.uk", "ico.org.uk"]


def match_alias(text: str):
    for pattern, info in INSTRUMENT_ALIASES:
        if re.search(pattern, text, re.IGNORECASE):
            return info
    return None


def normalise_title(t: str) -> str:
    t = re.sub(r"^\s*the\s+", "", t.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

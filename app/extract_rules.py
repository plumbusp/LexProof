"""Deterministic citation extraction (no AI). The AI layer later checks
whether anything was missed; it never replaces this step."""
import re
from dataclasses import dataclass, field

from . import laws as registry
from .textutil import sentence_spans
from .timeexpr import parse_durations

_CONNECT = r"(?:and|of|for|the|in|on|to|&)"
_CAPWORD = r"(?:[A-Z][A-Za-z'’-]*|\([A-Z][A-Za-z]*\))"
TITLE_RE = re.compile(
    rf"\b({_CAPWORD}(?:\s+(?:{_CAPWORD}|{_CONNECT}))*?\s+(Act|Regulations|Order|Rules)\s+(\d{{4}}))\b")

PROV_RE = re.compile(
    r"\b(section|sections|s\.|ss\.|regulation|regulations|reg\.?|article|art\.?|rule|paragraph|para\.?|schedule)"
    r"\s*(\d+[A-Z]{0,2}(?:\(\d+[A-Za-z]?\))*)", re.I)
FCA_RE = re.compile(r"\b(DISP|PRIN|CONC|BCOBS|COBS|SYSC|CONRED)\s*(\d+(?:\.\d+){0,3}[A-Z]?)\s*(R|G)?\b")
SAME_ACT_RE = re.compile(r"^\s*of\s+(?:the\s+same|that|this|the)\s+(Act|Regulations)\b", re.I)
OF_THE_RE = re.compile(r"^\s*of\s+(?:the\s+)?", re.I)

INTEREST_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:simple\s+|statutory\s+|compound\s+)?interest", re.I)
OMBUDSMAN_WAIT_RE = re.compile(
    r"within\s+(\d+|eight|six)\s+weeks[^.]{0,160}?(Ombudsman|FOS)|(Ombudsman|FOS)[^.]{0,160}?(\d+|eight)\s+weeks", re.I)
ONLY_IF_RE = re.compile(r"\bonly\s+(?:be\s+)?(?:held\s+)?(?:liable\s+)?if\s+([^.;]+)", re.I)
LIST_ITEM_RE = re.compile(r",?\s*\(\d+\)\s")
LIMIT_CTX_RE = re.compile(r"(within|no later than|by|in|after|up to|inside)\s+(?:the\s+)?$", re.I)


def _is_limit(clause: str, dur_text: str) -> bool:
    i = clause.find(dur_text)
    return i >= 0 and bool(LIMIT_CTX_RE.search(clause[max(0, i - 25):i]))


OVERSTATE_RE = re.compile(r"\b(absolute duty|all scam victims|direct right|automatic(?:ally)? entitled|"
                          r"legally obliged|must in all cases)\b", re.I)


@dataclass
class Citation:
    id: str
    kind: str                     # legislation | regulator_rule | unnamed
    citation_as_written: str
    span: tuple                   # citation phrase in the complaint
    clause_span: tuple            # the claim made about it
    instrument_title: str = ""
    leg_type: str = ""
    year: str = ""
    number: str = ""
    provision_kind: str = ""
    provision_id: str = ""
    regulator_rule: str = ""
    claimed_durations: list = field(default_factory=list)
    only_if: str = ""
    overstated: list = field(default_factory=list)
    resolution: str = ""          # registry | title_search | exa | unresolved


def _instrument_at(text, start):
    """Instrument named right after 'of the' at text[start:]."""
    tail = text[start:start + 160]
    m = OF_THE_RE.match(tail)
    if not m:
        return None
    rest = tail[m.end():]
    for pattern, info in registry.INSTRUMENT_ALIASES:
        am = re.match(pattern, rest, re.I)
        if am:
            return {"title": info[0], "type": info[1], "year": info[2], "number": info[3],
                    "end": start + m.end() + am.end(), "resolution": "registry"}
    tm = TITLE_RE.match(rest)
    if tm:
        return {"title": tm.group(1), "type": "", "year": tm.group(3), "number": "",
                "end": start + m.end() + tm.end(), "resolution": "unresolved"}
    return None


def _inst_from_title(title):
    info = registry.match_alias(title)
    if info:
        return {"title": info[0], "type": info[1], "year": info[2], "number": info[3], "resolution": "registry"}
    y = re.search(r"(\d{4})\s*$", title)
    return {"title": title, "type": "", "year": y.group(1) if y else "", "number": "", "resolution": "unresolved"}


def _kind(word, leg_type):
    w = word.lower().rstrip(".")
    k = {"s": "section", "ss": "section", "sections": "section", "reg": "regulation", "regulations": "regulation",
         "art": "article", "para": "paragraph"}.get(w, w)
    if leg_type and k in ("section", "regulation", "article"):
        k = registry.PROVISION_KIND_FOR_TYPE.get(leg_type, k)
    return k


def extract(text: str):
    sentences = sentence_spans(text)
    raw = []   # (start, end, dict)
    taken = []

    def overlaps(a, b):
        return any(not (b <= s or a >= e) for s, e in taken)

    last_inst = None
    # 1) provision + instrument ("Regulation 76 of the PSRs 2017", "section 49 of the same Act")
    events = sorted([(m.start(), "prov", m) for m in PROV_RE.finditer(text)] +
                    [(m.start(), "title", m) for m in TITLE_RE.finditer(text)] +
                    [(m.start(), "alias", m) for p, _ in registry.INSTRUMENT_ALIASES for m in re.finditer(p, text, re.I)],
                    key=lambda x: x[0])
    for pos, typ, m in events:
        if overlaps(m.start(), m.end()):
            continue
        if typ == "prov":
            inst = _instrument_at(text, m.end())
            end = m.end()
            if inst:
                end = inst["end"]
                last_inst = inst
            elif SAME_ACT_RE.match(text[m.end():]) and last_inst:
                sm = SAME_ACT_RE.match(text[m.end():])
                inst, end = last_inst, m.end() + sm.end()
            elif last_inst:
                inst = last_inst  # "Under Regulation 77, ..." continues the previous instrument
            if not inst:
                continue
            raw.append((m.start(), end, {"kind": "legislation", "inst": inst,
                                          "pkind": m.group(1), "pid": m.group(2)}))
            taken.append((m.start(), end))
        else:
            title = m.group(1) if typ == "title" else m.group(0)
            inst = _inst_from_title(title)
            last_inst = inst
            raw.append((m.start(), m.end(), {"kind": "legislation", "inst": inst, "pkind": "", "pid": ""}))
            taken.append((m.start(), m.end()))

    # 2) FCA handbook references
    for m in FCA_RE.finditer(text):
        if not overlaps(m.start(), m.end()):
            raw.append((m.start(), m.end(), {"kind": "regulator_rule", "rule": "DISP_1_6" if m.group(1) == "DISP" else "",
                                              "label": m.group(0)}))
            taken.append((m.start(), m.end()))
    # 3) claims with no named authority
    for m in INTEREST_RE.finditer(text):
        if not overlaps(m.start(), m.end()):
            raw.append((m.start(), m.end(), {"kind": "unnamed", "label": m.group(0)}))
            taken.append((m.start(), m.end()))
    for m in OMBUDSMAN_WAIT_RE.finditer(text):
        if not overlaps(m.start(), m.end()):
            raw.append((m.start(), m.end(), {"kind": "regulator_rule", "rule": "DISP_1_6", "label": m.group(0)}))
            taken.append((m.start(), m.end()))

    raw.sort(key=lambda r: r[0])
    out = []
    for i, (s, e, d) in enumerate(raw, 1):
        sent = next(((a, b) for a, b in sentences if a <= s < b), (s, e))
        # clause = this citation up to the next citation in the same sentence
        nxt = [r[0] for r in raw if sent[0] <= r[0] < sent[1] and r[0] > s]
        first_in_sentence = not [r for r in raw if sent[0] <= r[0] < s]
        cs = sent[0] if first_in_sentence else s
        ce = min(nxt) if nxt else sent[1]
        item_end = LIST_ITEM_RE.search(text, e, ce)  # "(1) ..., (2) ..." lists
        if item_end:
            ce = item_end.start()
        clause = text[cs:ce]
        c = Citation(id=f"c{i}", kind=d["kind"], citation_as_written=text[s:e], span=(s, e), clause_span=(cs, ce))
        if d["kind"] == "legislation":
            inst = d["inst"]
            c.instrument_title, c.leg_type, c.year, c.number = inst["title"], inst["type"], inst["year"], inst["number"]
            c.resolution = inst["resolution"]
            if d["pid"]:
                c.provision_kind, c.provision_id = _kind(d["pkind"], inst["type"]), re.sub(r"\(.*", "", d["pid"])
        elif d["kind"] == "regulator_rule":
            c.regulator_rule = d.get("rule", "")
        c.claimed_durations = [d for d in parse_durations(clause) if _is_limit(clause, d.text)]
        oi = ONLY_IF_RE.search(clause)
        c.only_if = oi.group(1).strip() if oi else ""
        c.overstated = [m.group(0) for m in OVERSTATE_RE.finditer(clause)]
        out.append(c)
    return out


def case_facts(text: str) -> dict:
    t = text.lower()
    age = re.search(r"\bI am (\d{2,3}) years old\b|\baged (\d{2,3})\b", text, re.I)
    age_v = int(next(g for g in age.groups() if g)) if age else None
    payment = any(k in t for k in ("unauthorised", "unauthorized", "payment", "transfer", "card", "passcode",
                                    "fraud", "scam"))
    return {
        "payment_services": payment,
        "app_scam_signals": any(k in t for k in ("persuaded", "tricked into", "i transferred", "i authorised", "safe account")),
        "unauthorised_signals": any(k in t for k in ("without my authorisation", "unauthorised", "did not authorise",
                                                     "without my authorization", "unauthorized")),
        "data_access_request": bool(re.search(r"article 15|subject access|copy of (all )?(the )?personal data", t)),
        "age": age_v,
        "possible_vulnerability": bool(age_v and age_v >= 70) or any(k in t for k in ("bereave", "disab", "dementia", "carer")),
        "event_date_text": _first_date(text),
        "amount": (re.search(r"£\s?[\d,]+(?:\.\d{2})?", text) or [None])[0],
    }


_MONTHS = "january february march april may june july august september october november december".split()


def _first_date(text):
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", text, re.I)
    if not m:
        return ""
    return f"{int(m.group(3)):04d}-{_MONTHS.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"

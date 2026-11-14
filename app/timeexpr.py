"""Parse and compare time limits ('within 24 hours', 'one month',
'end of the business day following ...')."""
import re
from dataclasses import dataclass

_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_WORDS.update({"thirty": 30, "thirty-five": 35, "forty": 40, "sixty": 60, "a": 1, "an": 1})

_NUM = r"(\d+|" + "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True)) + r")"
_DUR = re.compile(_NUM + r"(?:\s*\(\d+\))?\s+(business\s+|working\s+|calendar\s+|clear\s+)?(hours?|days?|weeks?|months?|years?)\b",
                  re.I)
_NEXT_BD = re.compile(r"end of the business day following|next business day|following business day", re.I)


@dataclass(frozen=True)
class Duration:
    value: int
    unit: str          # hour | day | week | month | year | next_business_day
    business: bool
    text: str

    def label(self) -> str:
        return self.text


def parse_durations(text: str):
    found = []
    for m in _DUR.finditer(text):
        raw = m.group(1).lower()
        val = int(raw) if raw.isdigit() else _WORDS.get(raw)
        if val is None:
            continue
        unit = m.group(3).lower().rstrip("s")
        business = bool(m.group(2) and m.group(2).strip().lower() in ("business", "working"))
        found.append(Duration(val, unit, business, m.group(0)))
    for m in _NEXT_BD.finditer(text):
        found.append(Duration(1, "next_business_day", True, m.group(0)))
    return found


def _approx_days(d: Duration):
    return {"hour": d.value / 24, "day": d.value, "week": d.value * 7, "month": d.value * 30.4,
            "year": d.value * 365, "next_business_day": None}.get(d.unit)


def compare(claim: Duration, law: list[Duration]) -> str:
    """'match' | 'approx' | 'differs' | 'no_law_limit'"""
    if not law:
        return "no_law_limit"
    for d in law:
        if d.unit == claim.unit and d.value == claim.value and d.business == claim.business:
            return "match"
    for d in law:
        if d.unit == claim.unit and d.value == claim.value:
            return "approx"  # business vs calendar days
        a, b = _approx_days(d), _approx_days(claim)
        if a and b and {d.unit, claim.unit} == {"day", "month"} and abs(a - b) <= 2:
            return "approx"  # '30 days' vs 'one month'
    return "differs"

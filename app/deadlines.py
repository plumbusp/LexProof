"""Deadline maths (runs separately from the legal check)."""
import calendar
import json
import re
from datetime import date, timedelta

from .sources import http

# Fallback if gov.uk is unreachable: bank holidays in any part of the UK, 2026-2027
_FALLBACK = {"2026-01-01", "2026-01-02", "2026-03-17", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25",
             "2026-07-13", "2026-08-03", "2026-08-31", "2026-11-30", "2026-12-25", "2026-12-28",
             "2027-01-01", "2027-01-04", "2027-03-17", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31",
             "2027-07-12", "2027-08-02", "2027-08-30", "2027-11-30", "2027-12-27", "2027-12-28"}


def uk_bank_holidays() -> set[str]:
    """FOIA s.10(6): a working day excludes a bank holiday in ANY part of the UK."""
    r = http.get("https://www.gov.uk/bank-holidays.json", max_age_s=86400 * 7, throttle=False)
    if r["status"] == 200:
        try:
            return {e["date"] for div in json.loads(r["body"]).values() for e in div["events"]}
        except (ValueError, KeyError, TypeError):
            pass
    return set(_FALLBACK)


def add_working_days(start: date, n: int, hols: set[str]) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in hols:
            n -= 1
    return d


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def compute(received: date, text: str) -> list[dict]:
    hols = uk_bank_holidays()
    out = [{"label": "Information request (FOIA / EIR)",
            "due": add_working_days(received, 20, hols).isoformat(),
            "basis": "FOIA s.10(1): no later than the twentieth working day following receipt "
                     "(EIR reg 5(2): 20 working days). Bank holidays anywhere in the UK excluded.",
            "url": "https://www.legislation.gov.uk/ukpga/2000/36/section/10"}]
    if re.search(r"subject access|my personal data|data you hold about me|article 15", text, re.I):
        out.append({"label": "Subject access request (UK GDPR)", "due": add_months(received, 1).isoformat(),
                    "basis": "UK GDPR Art 12A: one month beginning with receipt (extendable by two months)",
                    "url": "https://www.legislation.gov.uk/eur/2016/679/article/12A."})
    return out

"""Deterministic deadline calculation (England & Wales business days)."""
import calendar
import json
from datetime import date, timedelta

from .sources import http

# Fallback if gov.uk is unreachable (England and Wales, from gov.uk/bank-holidays.json)
_FALLBACK = {"2025-01-01", "2025-04-18", "2025-04-21", "2025-05-05", "2025-05-26", "2025-08-25", "2025-12-25",
             "2025-12-26", "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25", "2026-08-31",
             "2026-12-25", "2026-12-28", "2027-01-01", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31",
             "2027-08-30", "2027-12-27", "2027-12-28"}


def bank_holidays(division="england-and-wales") -> set[str]:
    r = http.get("https://www.gov.uk/bank-holidays.json", max_age_s=86400 * 7, throttle=False)
    if r["status"] == 200:
        try:
            return {e["date"] for e in json.loads(r["body"])[division]["events"]}
        except (ValueError, KeyError):
            pass
    return set(_FALLBACK)


def is_business_day(d: date, hols: set[str]) -> bool:
    return d.weekday() < 5 and d.isoformat() not in hols


def add_business_days(start: date, n: int, hols: set[str]) -> date:
    """End of the n-th business day after `start` (start itself not counted)."""
    d = start
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d, hols):
            n -= 1
    return d


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def next_business_day_on_or_after(d: date, hols) -> date:
    while not is_business_day(d, hols):
        d += timedelta(days=1)
    return d


def compute(received: date, facts: dict, fos_eligible: bool, reported: date | None) -> list[dict]:
    hols = bank_holidays()
    out = []
    psd = facts.get("payment_services")
    if psd:
        out.append({
            "code": "DISP_1_6_2A", "label": "Final response (payment services complaint)",
            "due": add_business_days(received, 15, hols).isoformat(),
            "extended_due": add_business_days(received, 35, hols).isoformat(),
            "basis": "DISP 1.6.2A R: end of 15 business days after receipt; 35 business days in exceptional "
                     "circumstances with a holding reply" + ("" if fos_eligible else " (PSRs 2017 reg 101 for non-eligible complainants)"),
            "source_url": "https://handbook.fca.org.uk/handbook/disp1/disp1s6" if fos_eligible
            else "https://www.legislation.gov.uk/uksi/2017/752/regulation/101",
        })
    else:
        out.append({
            "code": "DISP_1_6_2", "label": "Final response",
            "due": (received + timedelta(weeks=8)).isoformat(), "extended_due": "",
            "basis": "DISP 1.6.2 R: by the end of eight weeks after receipt",
            "source_url": "https://handbook.fca.org.uk/handbook/disp1/disp1s6",
        })
    if facts.get("data_access_request"):
        due = add_months(received, 1)
        out.append({
            "code": "UK_GDPR_12A", "label": "Data access request response",
            "due": next_business_day_on_or_after(due, hols).isoformat(),
            "extended_due": next_business_day_on_or_after(add_months(received, 3), hols).isoformat(),
            "basis": "UK GDPR Art 12(3) and 12A: one month beginning with receipt (extendable by two further "
                     "months); ICO guidance moves a deadline on a non-working day to the next working day",
            "source_url": "https://www.legislation.gov.uk/eur/2016/679/article/12A.",
        })
    if facts.get("unauthorised_signals") and reported:
        out.append({
            "code": "PSRS_REG_76", "label": "Refund of unauthorised payment",
            "due": add_business_days(reported, 1, hols).isoformat(), "extended_due": "",
            "basis": "PSRs 2017 reg 76(2): end of the business day following the day the bank becomes aware "
                     "(unless reasonable grounds to suspect fraud are notified, reg 76(3))",
            "source_url": "https://www.legislation.gov.uk/uksi/2017/752/regulation/76",
        })
    return out

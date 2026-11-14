"""Offline tests for the deterministic layer (no network)."""
from datetime import date
from pathlib import Path

from app import deadlines
from app.extract_rules import case_facts, extract
from app.textutil import find_span, is_verbatim
from app.timeexpr import compare, parse_durations

SAMPLE = (Path(__file__).resolve().parent.parent / "samples" / "margaret_ellis.txt").read_text(encoding="utf-8")


def test_extracts_all_ten_citations():
    cits = extract(SAMPLE)
    got = [(c.kind, c.leg_type, c.number, c.provision_id) for c in cits]
    assert got == [
        ("legislation", "uksi", "752", "76"),
        ("legislation", "uksi", "752", "77"),        # "Under Regulation 77" inherits the PSRs
        ("legislation", "ukpga", "15", "112A"),
        ("legislation", "ukpga", "15", "49"),        # "of the same Act"
        ("legislation", "", "", ""),                  # Banking Consumer Protection Act 2019 (unresolved)
        ("legislation", "ukpga", "8", "404Z"),
        ("legislation", "eur", "679", "15"),
        ("legislation", "ukpga", "15", ""),          # Equality Act 2010, act-level
        ("unnamed", "", "", ""),                      # 8% statutory interest
        ("regulator_rule", "", "", ""),               # 8 weeks -> DISP
    ]


def test_claim_details():
    c = {x.id: x for x in extract(SAMPLE)}
    assert [d.text for d in c["c1"].claimed_durations] == ["24 hours"]
    assert "fraudulently" in c["c2"].only_if
    assert c["c4"].overstated == []                  # clause doesn't bleed from c3
    assert c["c8"].claimed_durations == []           # "71 years old" is not a time limit


def test_case_facts():
    f = case_facts(SAMPLE)
    assert f["payment_services"] and f["data_access_request"] and f["possible_vulnerability"]
    assert f["event_date_text"] == "2026-09-03" and f["age"] == 71


def test_time_compare():
    law = parse_durations("no later than the end of the business day following the day on which it becomes aware")
    assert compare(parse_durations("within 24 hours")[0], law) == "differs"
    month = parse_durations("the period of one month beginning with the relevant time")
    assert compare(parse_durations("within 30 days")[0], month) == "approx"
    assert compare(parse_durations("within eight weeks")[0], parse_durations("8 weeks")) == "match"


def test_find_span_tolerates_quotes_and_case():
    span = find_span(SAMPLE, '"THE BANK NEVER REFUNDS THESE"')
    assert span and SAMPLE[span[0]:span[1]].lower().startswith("“the bank")
    assert is_verbatim("with intent or  gross negligence", "has with intent or gross negligence failed")
    assert not is_verbatim("within 24 hours", "end of the business day following")


def test_business_days_over_christmas():
    hols = deadlines._FALLBACK
    assert deadlines.add_business_days(date(2026, 11, 14), 15, hols) == date(2026, 12, 4)
    assert deadlines.add_business_days(date(2026, 11, 14), 35, hols) == date(2027, 1, 6)
    assert deadlines.add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)

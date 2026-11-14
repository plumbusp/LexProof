from datetime import date

from app import threatmap as tm


def ev(**kw):
    base = {"type": "T3", "country": "Latvia", "voice": "official", "attribution": "none", "kind": "warning",
            "url": "https://example.test/a", "quote": "x", "date_start": "2026-10-01"}
    return {**base, **kw}


def test_official_warning_no_attribution_is_2():  # plan acceptance 1 and 2
    assert tm.level(ev()) == 2


def test_weak_source_is_1():  # acceptance 3
    assert tm.level(ev(voice="expert", attribution="suspected", kind="incident", weak_source=True)) == 1


def test_nobody_and_no_attribution_cannot_reach_2():  # acceptance 9
    for kind in ("incident", "warning", "forecast"):
        assert tm.level(ev(voice="nobody", attribution="none", kind=kind)) < 2


def test_level_4_needs_named_state_and_two_domains():
    one = ev(voice="official", attribution="named", kind="incident")
    assert tm.level(one) != 4
    assert tm.level({**one, "domains": ["a.test", "b.test"]}) == 4


def test_no_source_no_dot_and_scope():  # acceptance 4 and 6
    assert not tm.valid(ev(quote=""))
    assert not tm.valid(ev(url=""))
    assert not tm.valid(ev(country="Serbia"))
    assert not tm.valid(ev(is_event=False))


def test_t6_never_raises_country():
    assert tm.country_levels([ev(type="T6", country="Sweden")]) == {}


def test_dedupe_same_country_type_domain_within_7_days():
    a, b = ev(date_start="2026-10-01"), ev(date_start="2026-10-05", url="https://www.example.test/b")
    c = ev(date_start="2026-10-20")
    assert len(tm.dedupe([a, b, c])) == 2


def test_requests_use_date_fields_not_query_words():  # plan rule 5
    reqs = tm.build_requests(7, today=date(2026, 11, 14))
    assert all(r["kwargs"]["start_published_date"].startswith("2026-11-07") for r in reqs)
    assert all("2026" not in r["query"] for r in reqs)


def test_monthly_plan_covers_jan_2024_to_today_once_per_country():
    ms = tm.months(today=date(2026, 11, 14))
    assert len(ms) == 35 and ms[0] == (date(2024, 1, 1), date(2024, 1, 31))
    assert ms[1][1] == date(2024, 2, 29) and ms[-1] == (date(2026, 11, 1), date(2026, 11, 14))
    plan = tm.build_plan("countries", today=date(2026, 11, 14))
    assert len(plan) == 35 * 29 * 2 and len({r["id"] for r in plan}) == len(plan)
    assert not any(c in r["query"] for r in plan for c in ("Serbia", "Ukraine", "Kosovo", "Moldova"))

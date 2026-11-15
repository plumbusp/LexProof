"""Relevance to the UK, 1-5: fixed rules over facts. The agent's facts never set a level by themselves."""
from datetime import date

from app import relevance as rv

UK = rv.PROFILES["GB"]
TODAY = date(2026, 11, 15)


def ev(**kw):
    base = {"id": "x", "country": "France", "type": "T5", "layer": "hostile", "date_start": "2026-11-10",
            "voice": "nobody", "attribution": "none", "headline": "Schools got threat emails", "url": "https://a.test/1"}
    return {**base, **kw}


def lvl(e):
    return rv.score(e, UK, TODAY)["rel"]


def test_uk_same_channel_recent_is_5():
    assert lvl(ev(country="United Kingdom")) == 5


def test_uk_but_old_is_4():
    assert lvl(ev(country="United Kingdom", date_start="2026-01-10")) == 4


def test_wave_abroad_that_reached_the_uk_is_4_only_when_verified():
    claimed = {"same_channel": True, "reached_claimed": True, "reached_verified": False, "evidence_url": ""}
    assert lvl(ev(rel_facts=claimed)) == 2                      # a claim without a checked source counts for nothing
    ok = {**claimed, "reached_verified": True, "evidence_url": "https://bbc.test/wave"}
    assert lvl(ev(rel_facts=ok)) == 4


def test_ai_trend_seen_in_uk_is_watch_not_prepare(monkeypatch):
    fx = {"same_channel": True, "reached_verified": True, "evidence_url": "https://guardian.test/ai-clog", "signal_type": "AI"}
    assert lvl(ev(type="AI", layer="precursor", rel_facts=fx)) == 3


def test_wave_evidence_that_is_only_the_ai_trend_page_counts_as_trend(monkeypatch):
    monkeypatch.setattr(rv, "trend_urls", lambda code="GB": {"https://guardian.test/ai-clog"})
    fx = {"same_channel": True, "reached_verified": True, "evidence_url": "https://guardian.test/ai-clog"}
    assert lvl(ev(type="T3", rel_facts=fx)) == 3                 # Czech letters != the UK AI-complaint trend


def test_campaign_or_neighbour_makes_it_watch():
    assert lvl(ev(voice="official")) == 3
    assert lvl(ev(country="Ireland")) == 3
    assert lvl(ev()) == 2


def test_media_only_abroad_is_1_and_old_is_background():
    assert lvl(ev(type="T6", voice="official")) == 1
    assert lvl(ev(voice="official", date_start="2025-03-01")) == 2


def test_why_has_at_most_four_reasons():
    r = rv.score(ev(voice="official", country="Ireland"), UK, TODAY)
    assert 1 <= len(r["rel_why"]) <= 4 and "Next door" in r["rel_why"]

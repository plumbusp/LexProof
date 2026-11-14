"""The map query: time window, searched vs not searched, type filter, dates, layers. Saved files only."""
from conftest import art, exa_row, ext_row, item

from app import threatstore as ts

A, B, C = "https://a.test/1", "https://b.test/2", "https://c.test/3"


def seed(store):
    store.exa(
        exa_row("Italy", "2026-09", [art(A, "2026-09-12")]),
        exa_row("Italy", "2026-10", [art(B, "2026-10-21"), art(C, "2026-10-22")]),
        exa_row("Latvia", "2026-10", []))
    store.ext(
        ext_row(A, item(countries=["Italy"], date_start="2026-09-12", type="T3", voice="expert", kind="warning")),
        ext_row(B, item(countries=["Italy"], date_start="2026-10-21", type="T5", voice="official")),
        ext_row(C, item(countries=["Latvia"], date_start="2026-10-22", type="T6", voice="official")))


def test_months_come_from_what_was_searched(store):
    seed(store)
    assert ts.months_available() == ["2026-09", "2026-10"]


def test_time_window_filters_events_and_levels(store):
    seed(store)
    both = ts.query("2026-09", "2026-10")
    assert both["n_events"] == 3 and both["countries"]["Italy"]["level"] == 2
    sep = ts.query("2026-09", "2026-09")
    assert sep["n_events"] == 1 and sep["countries"]["Italy"]["level"] == 1   # only the expert warning is in view
    assert ts.query("2026-10", "2026-10")["countries"]["Italy"]["level"] == 2
    assert sep["searched_months"] == ["2026-09"]


def test_searched_vs_not_searched(store):
    seed(store)
    r = ts.query("2026-09", "2026-10")["countries"]
    assert r["Italy"]["searched"] and r["Latvia"]["searched"]
    assert not r["Poland"]["searched"]                         # never searched: not the same as "nothing found"
    sep = ts.query("2026-09", "2026-09")["countries"]
    assert sep["Italy"]["searched"] and not sep["Latvia"]["searched"]   # Latvia was only searched in October


def test_type_filter_none_means_empty_not_broken(store):
    seed(store)
    none = ts.query("2026-09", "2026-10", types=[])
    assert none["n_events"] == 0 and all(c["level"] == 0 and not c["events"] for c in none["countries"].values())
    assert none["types"] == ["T3", "T5", "T6"]                  # chips stay available so they can be turned back on
    only = ts.query("2026-09", "2026-10", types=["T3"])
    assert only["n_events"] == 1 and only["countries"]["Italy"]["level"] == 1


def test_media_target_never_raises_level_but_is_counted(store):
    seed(store)
    lv = ts.query("2026-09", "2026-10")["countries"]["Latvia"]
    assert lv["level"] == 0 and lv["media"] == 1 and len(lv["events"]) == 1


def test_layers_are_kept_apart(store):
    store.exa(exa_row("Germany", "2026-10", [art(A, "2026-10-20")]))
    store.ext(ext_row(A, item(layer="precursor", type="AI", countries=["Germany"], voice="nobody")))
    assert ts.query("2026-10", "2026-10", "hostile")["n_events"] == 0
    p = ts.query("2026-10", "2026-10", "precursor")
    assert p["n_events"] == 1 and p["countries"]["Germany"]["level"] == 1


def test_ai_complaint_events_never_carry_a_hostile_level(store):
    # even a hand-edited saved row that claims an official + named state must not turn a complaint surge hostile (plan rule 6)
    store.exa(exa_row("Germany", "2026-10", [art(A, "2026-10-20"), art(B, "2026-10-20")]))
    bad = item(layer="precursor", type="AI", countries=["Germany"], voice="official", attribution="named")
    store.ext(ext_row(A, bad), ext_row(B, bad))
    evs = ts.query("2026-10", "2026-10", "precursor")["countries"]["Germany"]["events"]
    assert evs and all(e["level"] == 0 for e in evs)


def test_event_date_falls_back_to_publish_date(store):
    store.exa(exa_row("Italy", "2026-10", [art(A, "2026-10-21"), art(B, "2026-10-21"), art(C, "2026-10-21")]))
    store.ext(ext_row(A, item(date_start="")),                       # missing
              ext_row(B, item(date_start="not a date")),             # malformed
              ext_row(C, item(date_start="2026-12-31")))             # later than the article itself
    assert {e["date_start"] for e in ts.events()} == {"2026-10-21"}


def test_event_date_kept_when_earlier_than_article(store):
    store.exa(exa_row("Italy", "2026-10", [art(A, "2026-10-21")]))
    store.ext(ext_row(A, item(date_start="2026-10-05")))
    assert ts.events()[0]["date_start"] == "2026-10-05"


def test_same_site_same_type_within_a_week_is_one_event(store):
    u1, u2 = "https://news.test/a", "https://www.news.test/b"
    store.exa(exa_row("Italy", "2026-10", [art(u1), art(u2)]))
    store.ext(ext_row(u1, item(date_start="2026-10-10")), ext_row(u2, item(date_start="2026-10-13")))
    assert len(ts.events()) == 1


def test_level_4_pools_source_sites_across_articles(store):
    store.exa(exa_row("Latvia", "2026-10", [art(A), art(B)]))
    one = item(countries=["Latvia"], voice="official", attribution="named", type="T3")
    store.ext(ext_row(A, one))
    assert ts.query("2026-10", "2026-10")["countries"]["Latvia"]["level"] != 4        # one site only
    store.ext(ext_row(A, one), ext_row(B, {**one, "date_start": "2026-10-20"}))
    assert ts.query("2026-10", "2026-10")["countries"]["Latvia"]["level"] == 4         # two different sites


def test_events_without_a_matching_article_or_with_old_prompt_rows_are_ignored(store):
    store.exa(exa_row("Italy", "2026-10", [art(A)]))
    store.ext(ext_row("https://not-in-store.test/x", item()), ext_row(A, item()))
    assert [e["url"] for e in ts.events()] == [A]


def test_deploy_without_raw_files_serves_the_snapshot_identically(store):
    seed(store)
    before = ts.query("2026-09", "2026-10")
    snap = ts.export()
    text = ts.SNAPSHOT.read_text(encoding="utf-8")
    assert "highlights" not in text and "An excerpt." not in text        # full article excerpts never leave the PC
    assert len(snap["events"]) == 3
    ts.EXTRACTIONS.unlink()
    ts.SNAPSHOT.parent.joinpath("exa_results.jsonl").unlink()            # what a deploy looks like: no raw data
    for f in (ts._results_index, ts._extracted_count, ts._cached):
        f.cache_clear()
    assert ts.query("2026-09", "2026-10") == before
    assert ts.months_available() == ["2026-09", "2026-10"]


def test_no_raw_files_and_no_snapshot_is_empty_not_a_crash(store):
    assert ts.months_available() == [] and ts.events() == [] and ts.saved_at() == ""


def test_empty_store_and_half_written_last_line(store):
    assert ts.months_available() == [] and ts.events() == []
    store.exa(exa_row("Italy", "2026-10", [art(A)]))
    with ts.EXTRACTIONS.open("w", encoding="utf-8") as f:
        f.write('{"url": "' + A + '", "pv": 1, "items": []}\n{"url": "broken')   # run was killed mid-write
    ts._cached.cache_clear()
    assert ts.events() == []

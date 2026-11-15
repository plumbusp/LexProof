"""The live map's Exa gateway, pages, the 24 h check, investigate and replay. Offline: a fake Exa serves
recorded or hand-made responses and counts every paid call."""
import dataclasses
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import art, exa_row, ext_row, item
from fastapi.testclient import TestClient

from app import exacall, pages, relevance
from app import threatdeep as td
from app import threatstore as ts

FIX = Path(__file__).parent / "fixtures" / "exa"
RECORDED = json.loads((FIX / "contents_summary_schema.json").read_text(encoding="utf-8"))
QUOTE_OK = "Two hundred schools received the same bomb threat email on Monday morning."
QUOTE_BAD = "This sentence is not on the page at all, it was made up by the model."


def page_text(quote):
    return f"Intro paragraph. {quote} More text about the police response."


class FakeExa:
    """client.request(endpoint, data, method) like exa_py. Pages: url -> text; search/agent: canned answers."""

    def __init__(self, pages_=None, search=None, agent=None, fail=False):
        self.pages, self.search, self.agent, self.fail = pages_ or {}, search, agent, fail
        self.calls = []

    def request(self, endpoint, data=None, method="POST", **kw):
        self.calls.append((endpoint, data))
        if self.fail:
            raise RuntimeError("Exa is down")
        if endpoint == "/contents":
            u = data["urls"][0]
            summ = json.dumps({"lang": "lv", "title_en": "Schools get threats", "headline_en": "Bomb threat emails hit 200 schools in Riga",
                               "quote_en": "English quote", "summary_en": "Schools in Riga got threat emails. Police are checking."})
            return {"requestId": "c1", "results": [{"url": u, "title": "Skolas", "publishedDate": "2026-11-15T08:00:00.000Z",
                                                    "text": self.pages.get(u, ""), "summary": summ}],
                    "statuses": [{"id": u, "status": "success"}], "costDollars": {"total": 0.002}}
        if endpoint == "/search":
            return self.search
        if endpoint == "/agent/runs" and method == "POST":
            return {"id": "run1", "status": "running"}
        if endpoint.startswith("/agent/runs/"):
            return self.agent
        raise AssertionError(endpoint)

    def n(self, endpoint):
        return sum(1 for e, _ in self.calls if e == endpoint)


def search_answer(events, urls):
    return {"requestId": "s1", "results": [{"url": u, "title": "t", "publishedDate": "2026-11-15T07:00:00.000Z", "highlights": []}
                                           for u in urls],
            "output": {"content": {"events": events}, "grounding": []}, "costDollars": {"total": 0.012}}


def ev(url, quote, country="Latvia", typ="T5"):
    return {"url": url, "quote": quote, "type": typ, "country": country, "date": "2026-11-15", "target": "schools",
            "voice": "official", "attribution": "none"}


@pytest.fixture
def live(store, monkeypatch):
    monkeypatch.setattr(exacall, "SESSION_CAP", 6.0)
    monkeypatch.setattr(exacall, "SESSION_START", "2000-01-01")
    for f in (pages._records, pages._legacy):
        f.cache_clear()
    yield store
    for f in (pages._records, pages._legacy):
        f.cache_clear()


# ---------- the gateway

def test_same_body_twice_is_one_paid_call_and_one_ledger_row(live):
    fake = FakeExa({"https://a.test/1": "x"})
    for _ in range(2):
        exacall.call("contents", "/contents", {"urls": ["https://a.test/1"]}, est=0.002, client=fake)
    assert fake.n("/contents") == 1 and len(exacall.ledger()) == 1 and exacall.ledger()[0]["cost_usd"] == 0.002


def test_session_cap_refuses_before_any_call(live, monkeypatch):
    monkeypatch.setattr(exacall, "SESSION_CAP", 0.003)
    fake = FakeExa({"https://a.test/1": "x", "https://a.test/2": "y"})
    exacall.call("contents", "/contents", {"urls": ["https://a.test/1"]}, est=0.002, client=fake)
    with pytest.raises(exacall.BudgetExceeded):
        exacall.call("contents", "/contents", {"urls": ["https://a.test/2"]}, est=0.002, client=fake)
    assert fake.n("/contents") == 1 and len(exacall.ledger()) == 1


def test_run_click_and_daily_click_caps(live, monkeypatch):
    fake = FakeExa({f"https://a.test/{i}": "x" for i in range(10)})
    run = exacall.Run(0.003)
    exacall.call("contents", "/contents", {"urls": ["https://a.test/0"]}, est=0.002, run=run, client=fake)
    with pytest.raises(exacall.BudgetExceeded, match="run budget"):
        exacall.call("contents", "/contents", {"urls": ["https://a.test/1"]}, est=0.002, run=run, client=fake)
    with pytest.raises(exacall.BudgetExceeded, match="per-click"):
        exacall.call("search", "/search", {"q": 1}, est=0.06, run=exacall.Run(1.0, "click"), client=fake)
    monkeypatch.setattr(exacall, "CLICK_DAILY_CAP", 0.003)
    exacall.call("contents", "/contents", {"urls": ["https://a.test/2"]}, est=0.002, run=exacall.Run(0.05, "click"), client=fake)
    with pytest.raises(exacall.BudgetExceeded, match="daily"):
        exacall.call("contents", "/contents", {"urls": ["https://a.test/3"]}, est=0.002, run=exacall.Run(0.05, "click"), client=fake)
    assert fake.n("/contents") == 2


# ---------- pages: one /contents per URL, ever

def test_pages_fetch_a_url_once_whatever_its_spelling(live):
    fake = FakeExa({"https://www.a.test/x/": page_text(QUOTE_OK)})
    got = pages.get(["https://www.a.test/x/", "https://a.test/x?utm_source=fb", "https://A.test/x#top"], client=fake)
    assert fake.n("/contents") == 1 and len(got) == 1
    pages.get(["https://a.test/x"], client=fake)
    assert fake.n("/contents") == 1                       # known now: $0
    rec = pages.records()[pages.key("https://a.test/x")]
    assert rec["headline_en"].startswith("Bomb threat") and rec["lang"] == "lv"
    assert pages.text("https://a.test/x")["text"] == page_text(QUOTE_OK)


def test_legacy_contents_files_count_as_known(live):
    d = live.dir / "exa_raw" / "contents"
    d.mkdir(parents=True)
    (d / "abc.json").write_text(json.dumps({"request": {"urls": ["https://old.test/a"]},
                                            "response": {"results": [{"text": "old text", "title": "Old"}]}}), encoding="utf-8")
    fake = FakeExa()
    got = pages.get(["https://old.test/a/"], client=fake)
    assert fake.calls == [] and got[pages.norm("https://old.test/a")]["text"] == "old text"


def test_recorded_summary_schema_is_parsed():
    s = RECORDED["response"]["results"][0]["summary"]
    d = pages.parse_summary(s)
    assert d["lang"] == "fi" and d["headline_en"] and d["quote_en"] and d["summary_en"]
    assert pages.parse_summary("Just a plain sentence.") == {"summary_en": "Just a plain sentence."}


def test_exa_fields_fill_only_what_is_missing(live):
    fake = FakeExa({"https://a.test/1": page_text(QUOTE_OK)})
    pages.get(["https://a.test/1"], {"https://a.test/1": QUOTE_OK}, client=fake)
    e = pages.attach({"url": "https://a.test/1", "quote": QUOTE_OK})
    assert e["headline_by"] == "exa" and e["tr_by"] == "exa" and e["quote_en"] == "English quote" and e["summary"]
    kept = pages.attach({"url": "https://a.test/1", "quote": QUOTE_OK, "headline": "Saved", "quote_en": "Claude's", "tr_by": "claude"})
    assert kept["headline"] == "Saved" and kept["tr_by"] == "claude"


# ---------- the 24 h check and investigate

def test_refresh_keeps_only_quotes_found_on_the_page(live):
    u1, u2 = "https://lv.test/1", "https://lv.test/2"
    fake = FakeExa({u1: page_text(QUOTE_OK), u2: page_text("other text")},
                   search=search_answer([ev(u1, QUOTE_OK), ev(u2, QUOTE_BAD), ev(u1, QUOTE_OK, "Narnia")], [u1, u2]))
    out = td.refresh(client=fake, run=exacall.Run(0.05, "click"))
    assert out["proposed"] == 3 and out["passed"] == 1
    assert {d["reason"] for d in out["dropped"]} == {"quote not on the page", "outside the 42 countries"}
    assert fake.n("/search") == 1 and fake.n("/contents") == 2           # one page call per URL
    e = [e for e in ts.events() if e["url"] == u1][0]
    assert e["via"] == "exa-24h" and e["new"] and e["headline"].startswith("Bomb threat") and e["country"] == "Latvia"
    assert td.last_live("refresh")["passed"] == 1
    # same hour, second check: the search is a cache hit and the known URL is not opened again
    td.refresh(client=fake, run=exacall.Run(0.05, "click"))
    assert fake.n("/search") == 1 and fake.n("/contents") == 2
    assert abs(exacall.spent(trigger="click") - 0.016) < 1e-9


def test_investigate_excludes_known_urls_and_is_reused_for_6_hours(live):
    known = "https://lv.test/known"
    live.exa(exa_row("Latvia", "2026-10", [art(known)]))
    live.ext(ext_row(known, item(countries=["Latvia"])))
    u = "https://lv.test/new"
    agent = {"id": "run1", "status": "completed", "output": {"structured": {"events": [ev(u, QUOTE_OK), ev(known, "a verbatim quote")]}},
             "costDollars": {"total": 0.025}}
    fake = FakeExa({u: page_text(QUOTE_OK)}, agent=agent)
    import app.exacall as ex
    orig = ex.time.sleep
    ex.time.sleep = lambda s: None
    try:
        out = td.investigate("Latvia", client=fake)
        again = td.investigate("Latvia", client=fake)
    finally:
        ex.time.sleep = orig
    body = [d for e, d in fake.calls if e == "/agent/runs"][0]
    assert body["effort"] == "low" and body["input"]["exclusion"] == [{"url": known}]
    assert out["passed"] == 1 and fake.n("/contents") == 1 and {d["reason"] for d in out["dropped"]} == {"already on the map"}
    assert again["reused"] and again["cost_usd"] == 0.0 and fake.n("/agent/runs") == 1


# ---------- the HTTP buttons: reuse, replay

@pytest.fixture
def api(live, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, exa_api_key="test"))
    main._jobs.clear()
    return main, TestClient(main.app)


def _wait(c, jid):
    for _ in range(100):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("job never finished")


def test_refresh_reuses_a_check_from_the_last_15_minutes(api, monkeypatch):
    main, c = api
    calls = []

    def fake_refresh(progress):
        calls.append(1)
        out = {"status": "done", "proposed": 0, "passed": 0, "dropped": [], "events": [], "cost_usd": 0.012,
               "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        td.save_live("refresh", out)
        return out
    monkeypatch.setattr(td, "refresh", fake_refresh)
    j1 = _wait(c, c.post("/api/threatmap/refresh").json()["job_id"])
    j2 = _wait(c, c.post("/api/threatmap/refresh").json()["job_id"])
    assert len(calls) == 1 and j1["status"] == "done" and j2["result"]["reused"] and j2["result"]["cost_usd"] == 0.0


def test_failure_replays_the_newest_real_run_never_invents(api, monkeypatch):
    main, c = api
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    td.save_live("refresh", {"status": "done", "passed": 2, "events": [{"id": "x"}], "finished_at": old, "cost_usd": 0.02})

    def boom(progress):
        raise RuntimeError("Exa 500")
    monkeypatch.setattr(td, "refresh", boom)
    j = _wait(c, c.post("/api/threatmap/refresh").json()["job_id"])
    assert j["status"] == "replay" and j["result"]["finished_at"] == old and j["result"]["replay"]
    assert "Exa" in j["result"]["reason"] and "500" not in j["result"]["reason"]


def test_no_key_replays_and_with_nothing_saved_says_so(api, monkeypatch):
    main, c = api
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, exa_api_key=""))
    j = _wait(c, c.post("/api/threatmap/investigate", json={"country": "Latvia"}).json()["job_id"])
    assert j["status"] == "error" and "No earlier live check" in j["error"] and "Traceback" not in j["error"]


def test_budget_refusal_becomes_a_replay(api, monkeypatch):
    main, c = api
    td.save_live("investigate", {"status": "done", "country": "Latvia", "passed": 1, "events": [],
                                 "finished_at": "2026-11-15T01:00:00+00:00"})

    def refuse(country, progress):
        raise exacall.BudgetExceeded("daily")
    monkeypatch.setattr(td, "investigate", refuse)
    j = _wait(c, c.post("/api/threatmap/investigate", json={"country": "Latvia"}).json()["job_id"])
    assert j["status"] == "replay" and "budget" in j["result"]["reason"]


def test_live_endpoint_reports_spend_and_last_check(api):
    main, c = api
    td.save_live("refresh", {"status": "done", "finished_at": "2026-11-15T10:00:00+00:00", "cost_usd": 0.012})
    d = c.get("/api/threatmap/live").json()
    assert d["last_checked"] == "2026-11-15T10:00:00+00:00" and d["cap_today_usd"] == 1.0 and d["spent_today_usd"] == 0


# ---------- incremental sweep and agent

def test_incremental_sweep_windows_start_at_the_last_run(live):
    today = datetime.now(timezone.utc).date()
    row = {**exa_row("Latvia", "2026-11", []), "id": "deep|2025-01|Latvia|hostile", "group": "hostile",
           "fetched_at": (today - timedelta(days=3)).isoformat() + "T10:00:00+00:00"}
    live.exa(row)
    plan = {r["id"]: r for r in td.incremental_plan(["Latvia"], today=today)}
    inc = [r for r in plan.values() if r["group"] == "hostile"][0]
    assert inc["id"] == f"inc|{(today - timedelta(days=4)).isoformat()}|Latvia|hostile" and inc["kwargs"]["num_results"] == 10
    assert "deep|2025-01|Latvia|precursor" in plan                        # never swept: full back-fill


def test_incremental_agent_skips_when_nothing_is_new(live, monkeypatch):
    monkeypatch.setattr(relevance, "new_signals", lambda code, evs=None: [])
    fake = FakeExa()
    assert relevance.run("GB", client=fake)["skipped"] and fake.calls == []


def test_incremental_agent_merges_new_facts(live, monkeypatch):
    relevance.file_for("GB").write_text(json.dumps({"run_id": "old", "facts": {"a": {"same_channel": True}}}), encoding="utf-8")
    relevance._facts.cache_clear()
    monkeypatch.setattr(relevance, "new_signals", lambda code, evs=None: [{"id": "b", "type": "T5"}])
    agent = {"id": "run2", "status": "completed", "costDollars": {"total": 0.025},
             "output": {"structured": {"signals": [{"id": "b", "same_channel": False, "reached": False}]}}}
    fake = FakeExa(agent=agent)
    import app.exacall as ex
    monkeypatch.setattr(ex.time, "sleep", lambda s: None)
    res = relevance.run("GB", client=fake)
    f = json.loads(relevance.file_for("GB").read_text(encoding="utf-8"))
    assert res["sent"] == 1 and set(f["facts"]) == {"a", "b"} and [r["run_id"] for r in f["runs"]] == ["old", "run2"]
    assert [d for e, d in fake.calls if e == "/agent/runs"][0]["input"]["data"] == [{"id": "b", "type": "T5"}]


def test_snapshot_carries_live_runs_and_new_events(live):
    u = "https://lv.test/1"
    fake = FakeExa({u: page_text(QUOTE_OK)}, search=search_answer([ev(u, QUOTE_OK)], [u]))
    td.refresh(client=fake, run=exacall.Run(0.05, "click"))
    snap = ts.export()
    assert any(e["url"] == u and e.get("headline") for e in snap["events"]) and snap["live_runs"][0]["kind"] == "refresh"


def test_recorded_24h_answer_parses_into_in_scope_events():
    d = json.loads((FIX / "deep_24h.json").read_text(encoding="utf-8"))
    raw, _ = td.parse_output(d["response"]["output"])
    assert raw and all(e["country"] in td.ISO2 for e in raw)
    assert d["request"]["outputSchema"]["properties"]["events"]["items"]["properties"]["country"]["enum"] == sorted(td.ISO2)
    results = {pages.norm(r["url"]) for r in d["response"]["results"]}
    assert sum(pages.norm(e["url"]) in results for e in raw) >= 1

"""The two paid/slow steps, run against fake Exa and Gemini clients: resume, retry, verification. No network."""
import json
from types import SimpleNamespace

import pytest
from conftest import art, exa_row

from app import threatextract as tx
from app import threatmap as tm
from app import threatstore as ts

QUOTE = "a verbatim quote"


# ---------- Exa: stored searches are never paid for twice

class FakeExa:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def search(self, query, **kw):
        self.calls.append(query)
        if query in self.fail:
            raise RuntimeError("boom")
        r = SimpleNamespace(url=f"https://x.test/{len(self.calls)}", title="t", published_date="2026-10-01T00:00:00Z",
                            highlights=["h"], text=None, score=1, snapshot_at=None)
        return SimpleNamespace(results=[r], cost_dollars=SimpleNamespace(total=0.007))


def plan(*qs):
    return [{"id": f"2026-10|Italy|{q}", "month": "2026-10", "country": "Italy", "group": "incident", "query": q,
             "kwargs": {"num_results": 10}} for q in qs]


def stored_ids():
    return [json.loads(line)["id"] for line in tm.RESULTS.read_text(encoding="utf-8").splitlines()]


def test_exa_run_resumes_and_retries_only_failures(store, monkeypatch):
    fake = FakeExa(fail={"q2"})
    monkeypatch.setattr("app.sources.exa_src._exa", lambda: fake)
    p = plan("q1", "q2", "q3")
    tm.run_plan(p)
    assert sorted(stored_ids()) == ["2026-10|Italy|q1", "2026-10|Italy|q3"]      # the failed one is not stored
    first_calls = len(fake.calls)
    fake.fail.clear()
    tm.run_plan(p)
    assert len(fake.calls) - first_calls == 1 and fake.calls[-1] == "q2"           # only the failure is retried
    tm.run_plan(p)
    assert len(fake.calls) == first_calls + 1                                      # nothing left: zero calls, zero credits
    assert sorted(stored_ids()) == ["2026-10|Italy|q1", "2026-10|Italy|q2", "2026-10|Italy|q3"]


def test_exa_run_records_cost_and_search_params(store, monkeypatch):
    monkeypatch.setattr("app.sources.exa_src._exa", lambda: FakeExa())
    tm.run_plan(plan("q1"))
    row = json.loads(tm.RESULTS.read_text(encoding="utf-8"))
    assert row["cost_usd"] == 0.007 and row["params"] == {"num_results": 10} and row["results"][0]["published_date"] == "2026-10-01"


def test_exa_run_without_a_key_stores_nothing(store, monkeypatch):
    from app.sources.exa_src import ExaUnavailable

    def no_key():
        raise ExaUnavailable("EXA_API_KEY not set")
    monkeypatch.setattr("app.sources.exa_src._exa", no_key)
    with pytest.raises(ExaUnavailable):
        tm.run_plan(plan("q1"))
    assert not tm.RESULTS.exists()


def test_month_filter_of_the_plan_is_exact():
    p = tm.build_plan("countries")
    assert {r["month"] for r in p if r["month"] == "2026-10"} == {"2026-10"} and len([r for r in p if r["month"] == "2026-10"]) == 84


# ---------- Gemini: verification, bad output, resume

def reply(*items):
    return json.dumps({"items": list(items)})


def raw(i=1, **kw):
    return {"i": i, "is_event": True, "layer": "hostile", "type": "T5", "countries": ["Italy"], "date_start": "2026-10-19",
            "voice": "official", "attribution": "none", "kind": "incident", "quote": QUOTE, **kw}


def fake_client(fn):
    def create(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=fn(kw["messages"][-1]["content"])))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def articles(*titles):
    return [art(f"https://x.test/{n}", "2026-10-20", t, [f"{QUOTE} appears here"]) for n, t in enumerate(titles, 1)]


def test_batch_keeps_only_verified_items_and_survives_junk(monkeypatch):
    monkeypatch.setattr(tx, "settings", SimpleNamespace(ai_model="m"))
    junk = [raw(1), raw(1, quote="invented, not in the text"), raw(2, type="T9"), raw(7), "not a dict", raw(2, countries=["Ukraine"])]
    rows, n_raw = tx.extract_batch(fake_client(lambda _: reply(*junk)), articles("one", "two"))
    assert n_raw == 6 and [len(r["items"]) for r in rows] == [1, 0]
    assert rows[0]["url"] == "https://x.test/1" and rows[0]["pv"] == tx.PV and rows[0]["items"][0]["countries"] == ["Italy"]


def test_batch_with_non_object_json_gives_empty_rows_but_one_per_article(monkeypatch):
    monkeypatch.setattr(tx, "settings", SimpleNamespace(ai_model="m"))
    rows, n_raw = tx.extract_batch(fake_client(lambda _: "[]"), articles("one", "two"))
    assert n_raw == 0 and [r["items"] for r in rows] == [[], []]


def test_batch_with_invalid_json_raises_so_it_is_retried(monkeypatch):
    monkeypatch.setattr(tx, "settings", SimpleNamespace(ai_model="m"))
    with pytest.raises(json.JSONDecodeError):
        tx.extract_batch(fake_client(lambda _: "I cannot help with that"), articles("one"))


def test_pending_skips_done_articles_but_redoes_old_prompt_versions(store):
    store.exa(exa_row("Italy", "2026-10", articles("one", "two", "three")))
    store.ext({"url": "https://x.test/1", "pv": tx.PV, "items": []}, {"url": "https://x.test/2", "pv": tx.PV - 1, "items": []})
    assert [a["url"] for a in tx.pending()] == ["https://x.test/2", "https://x.test/3"]


def test_run_stores_good_batches_skips_failed_ones_and_resumes(store, monkeypatch):
    store.exa(exa_row("Italy", "2026-10", articles("ok one", "FAILME", "ok three")))
    monkeypatch.setattr(tx, "settings", SimpleNamespace(ai_enabled=True, ai_model="m", ai_base_url=None, ai_api_key="k"))
    monkeypatch.setattr(tx, "BATCH", 1)
    sent = []
    state = {"fail": True}

    def answer(prompt):
        sent.append(prompt)
        if state["fail"] and "FAILME" in prompt:
            raise RuntimeError("503 high demand")
        return reply(raw(1))
    monkeypatch.setattr(tx, "_client", lambda: fake_client(answer))

    tx.run(workers=1)
    done = {r["url"] for r in ts.read_jsonl(ts.EXTRACTIONS)}
    assert done == {"https://x.test/1", "https://x.test/3"} and len(sent) == 3          # the failed article is not marked done
    state["fail"] = False
    sent.clear()
    tx.run(workers=1)
    assert len(sent) == 1 and "FAILME" in sent[0]                                        # resume: only the failed one is sent again
    assert len(ts.read_jsonl(ts.EXTRACTIONS)) == 3
    tx.run(workers=1)
    assert len(sent) == 1                                                                # nothing left, no Gemini calls


def test_run_refuses_without_a_model(store, monkeypatch):
    monkeypatch.setattr(tx, "settings", SimpleNamespace(ai_enabled=False))
    with pytest.raises(SystemExit):
        tx.run()

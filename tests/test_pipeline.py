"""The whole check, end to end: letter -> rows, highlights, deadline, draft reply; and the job API.
Gemini off, the verdict step and bank holidays recorded, so nothing touches the network."""
import re
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import cache, deadlines, generate, main, pipeline
from app.config import settings

LETTER = generate.generate("foi", preset="foi-1")["text"]     # FOIA s.1, s.10 (10 days), s.64Z, fee claim, s.17
PEOPLE_LABELS = re.compile(r"\b(bot|bots|spam|spammer|ai slop|attack|attacker)\b", re.I)


def verdict(ref):
    """Recorded verdicts keyed on what the letter cites."""
    q = ref["quote"].lower()
    proof = lambda path, heading, text: {"title": "Freedom of Information Act 2000", "heading": heading,  # noqa: E731
                                         "url": f"https://www.legislation.gov.uk/ukpga/2000/36/{path}",
                                         "text": text, "quote": text}
    if "section 10" in q:
        return {"status": "MISQUOTED", "note": "Says '10 working days'; the law says 'twentieth working day'.",
                "diff": {"claim": "10 working days", "law": "twentieth working day"}, "cited": "FOIA s.10",
                "proof": proof("section/10", "Time for compliance with request", "not later than the twentieth working day"),
                "evidence": []}
    if "64z" in q:
        return {"status": "NOT_FOUND", "note": "Freedom of Information Act 2000 has no section 64Z. No similar rule found.",
                "diff": None, "cited": "FOIA s.64Z", "proof": None, "evidence": []}
    if "charge" in q:
        return {"status": "POSSIBLE_RULE", "note": "Likely rule - check it applies.", "diff": None, "cited": "",
                "proof": proof("section/9", "Fees", "may charge a fee"), "evidence": []}
    return {"status": "MATCH", "note": "", "diff": None, "cited": "FOIA",
            "proof": proof("section/1", "General right of access", "is entitled to be informed"), "evidence": []}


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(type(settings), "ai_enabled", property(lambda self: False))
    monkeypatch.setattr(type(settings), "exa_enabled", property(lambda self: False))
    monkeypatch.setattr(pipeline, "check", verdict)
    monkeypatch.setattr(deadlines, "uk_bank_holidays", lambda: set(deadlines._FALLBACK))


def test_run_end_to_end(offline):
    steps = []
    res = pipeline.run(LETTER, {"received_date": "2026-11-16"}, steps.append)
    rows = res["claims"]
    assert [r["status"] for r in rows] == ["MATCH", "MISQUOTED", "NOT_FOUND", "POSSIBLE_RULE", "MATCH"]
    assert res["summary"]["counts"]["MISQUOTED"] == 1 and res["summary"]["total"] == 5
    for h in res["highlights"]:                    # every highlight points at the words it claims to
        quote = next(r["quote"] for r in rows if r["id"] == h["claim_id"])
        assert LETTER[h["start"]:h["end"]].lower() == quote.lower()
    assert res["request"]["type"] == "FOI" and res["request"]["requester"] == "Daniel Okafor"
    assert res["deadline"]["due"] == "2026-12-15"   # 20 working days; St Andrew's Day 30 Nov is skipped
    assert any("No Gemini key" in w for w in res["meta"]["warnings"])
    assert any("No Exa key" in w for w in res["meta"]["warnings"])
    assert steps and steps[0].startswith("Finding legal references")


def test_draft_reply_corrects_the_letter_without_labelling_anyone(offline):
    reply = pipeline.run(LETTER, {"received_date": "2026-11-16"})["draft_reply"]
    assert reply.startswith("Dear Daniel Okafor,")
    assert "no later than the twentieth working day, rather than the 10 working days" in reply
    assert "Freedom of Information Act 2000 has no section 64Z, so we have not relied on it" in reply
    assert "Tuesday 15 December 2026" in reply
    assert "section 17 of the Freedom of Information Act 2000" in reply
    assert reply.rstrip().endswith(pipeline.ORG_NAME)
    assert not PEOPLE_LABELS.search(reply)


def test_draft_reply_for_not_as_cited_and_unknown_law():
    rows = [{"status": "NOT_AS_CITED", "cited": "FOIA s.99", "kind": "explicit", "note": "", "quote": "section 99",
             "proof": {"title": "Freedom of Information Act 2000", "heading": "Exemption where cost exceeds limit"}},
            {"status": "NOT_FOUND", "cited": "", "kind": "explicit", "note": "No such law found.",
             "quote": "Banking Consumer Protection Act 2019", "proof": None}]
    reply = pipeline.draft_reply(date(2026, 11, 16), rows, [{"due": "2026-12-14"}], "SAR", "")
    assert reply.startswith("Dear Sir or Madam,")
    assert "We could not find FOIA s.99 you cited. The closest provision appears to be Freedom of Information Act " \
           "2000 (Exemption where cost exceeds limit)." in reply
    assert 'We could not identify any law matching "Banking Consumer Protection Act 2019".' in reply
    assert "UK General Data Protection Regulation" in reply and "section 17" not in reply


def test_request_type_and_requester():
    assert pipeline.request_type("I make a subject access request for my personal data") == "SAR"
    assert pipeline.request_type("under the Environmental Information Regulations") == "EIR"
    assert pipeline.request_type("Please send me the contract") == "FOI"
    assert pipeline.requester_name("Thanks.\n\nKind regards,\nAisha Bello\n") == "Aisha Bello"
    assert pipeline.requester_name("no sign-off here") == ""


def test_run_safe_turns_a_crash_into_an_error(monkeypatch):
    monkeypatch.setattr(pipeline, "run", lambda *a: 1 / 0)
    out = pipeline.run_safe("text", {}, lambda s: None)
    assert out["ok"] is False and out["error"].startswith("ZeroDivisionError")


def test_bad_received_date_falls_back_to_today(offline):
    res = pipeline.run(LETTER, {"received_date": "not a date"})
    assert res["params"]["received_date"] == date.today().isoformat()


@pytest.fixture
def jobs(offline, monkeypatch):
    saved = {}
    monkeypatch.setattr(cache, "save_job", lambda job_id, v: saved.__setitem__(job_id, v))
    monkeypatch.setattr(cache, "load_job", lambda job_id: saved.get(job_id))
    monkeypatch.setattr(main, "_jobs", {})
    return saved


def test_analyze_job_runs_and_is_saved(jobs):
    c = TestClient(main.app)
    job_id = c.post("/api/analyze", json={"text": LETTER, "params": {"received_date": "2026-11-16"}}).json()["job_id"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done" and j["result"]["summary"]["total"] == 5
    assert j["progress"] and job_id in jobs
    main._jobs.clear()                               # after a restart the saved copy is served
    assert c.get(f"/api/jobs/{job_id}").json()["status"] == "done"


def test_job_api_errors(jobs, monkeypatch):
    c = TestClient(main.app)
    assert c.post("/api/analyze", json={"text": "too short"}).status_code == 422
    assert c.get("/api/jobs/nope").status_code == 404
    monkeypatch.setattr(pipeline, "run", lambda *a: 1 / 0)
    job_id = c.post("/api/analyze", json={"text": LETTER}).json()["job_id"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "error" and "ZeroDivisionError" in j["error"]

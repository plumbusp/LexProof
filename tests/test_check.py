"""The verdict logic (app/check.py): every status, with legislation.gov.uk and Exa replaced by
recorded answers, so these run offline. Code sets the status; proof is always fetched text."""
import pytest

from app import check
from app.sources import exa_src

LAW = "https://www.legislation.gov.uk"
TEXTS = {
    ("ukpga", "2000", "36", "section", "1"): ("General right of access to information held by public authorities",
        "(1) Any person making a request for information to a public authority is entitled\n"
        "(a) to be informed in writing by the public authority whether it holds information of the description "
        "specified in the request, and\n(b) if that is the case, to have that information communicated to him."),
    ("ukpga", "2000", "36", "section", "10"): ("Time for compliance with request",
        "(1) Subject to subsections (2) and (3), a public authority must comply with section 1(1) promptly and in "
        "any event not later than the twentieth working day following the date of receipt."),
    ("ukpga", "2000", "36", "section", "12"): ("Exemption where cost of compliance exceeds appropriate limit",
        "(1) Section 1(1) does not oblige a public authority to comply with a request for information if the "
        "authority estimates that the cost of complying with the request would exceed the appropriate limit."),
}


@pytest.fixture
def world(monkeypatch):
    """Recorded legislation.gov.uk + Exa. Set world.exa to the hits the next search returns."""
    class W:
        exa = []
        searches = []

    def fetch(leg_type, year, number, kind, pid, as_of=None):
        hit = TEXTS.get((leg_type, year, number, kind, pid))
        if not hit:
            return {"status": "provision_not_found", "attempts": []}
        return {"status": "ok", "url": f"{LAW}/{leg_type}/{year}/{number}/{kind}/{pid}",
                "act_title": "Freedom of Information Act 2000", "heading": hit[0], "text": hit[1]}

    def search(query, **kw):
        W.searches.append(query)
        if W.exa is None:
            raise exa_src.ExaUnavailable("EXA_API_KEY not set")
        return W.exa

    monkeypatch.setattr(check.legislation, "fetch_provision", fetch)
    monkeypatch.setattr(check.legislation, "act_exists",
                        lambda t, y, n: {"exists": True, "url": f"{LAW}/{t}/{y}/{n}", "http_status": 200})
    monkeypatch.setattr(exa_src, "search", search)
    return W


def ref(provision="", act="Freedom of Information Act 2000", claim="", kind="explicit", quote=None):
    return {"kind": kind, "act_name": act if kind == "explicit" else "", "provision": provision, "claim": claim,
            "quote": quote or (f"{provision} of the {act}" if kind == "explicit" else claim)}


def hit(path, title="Freedom of Information Act 2000"):
    return {"url": f"{LAW}/{path}", "title": title, "highlights": ["an Exa highlight"], "text": ""}


def test_match_real_section_and_true_claim(world):
    r = check.check(ref("section 1", claim="I am entitled to be told whether the council holds the information"))
    assert r["status"] == "MATCH"
    assert r["proof"]["url"].endswith("/ukpga/2000/36/section/1") and r["proof"]["text"]
    assert world.searches == []          # a known law never needs Exa


def test_misquoted_wrong_deadline(world):
    r = check.check(ref("section 10", claim="you must respond within 10 working days"))
    assert r["status"] == "MISQUOTED"
    assert r["diff"]["claim"] == "10 working days" and "twentieth working day" in r["diff"]["law"]


def test_not_as_cited_points_to_a_real_fetched_section(world):
    world.exa = [hit("ukpga/2000/36/section/12")]
    r = check.check(ref("section 64Z", claim="councils may not redact contractor names"))
    assert r["status"] == "NOT_AS_CITED"
    assert r["proof"]["url"].endswith("/section/12") and "appropriate limit" in r["proof"]["text"]


def test_known_act_missing_section_never_uses_an_act_level_hit_as_proof(world):
    world.exa = [hit("ukpga/2000/36/contents")]       # only an Act-level page, no fetchable section
    r = check.check(ref("section 64Z", claim="councils may not redact contractor names"))
    assert r["status"] == "NOT_FOUND" and r["proof"] is None
    assert "has no section 64Z" in r["note"]


def test_invented_act_is_not_found(world):
    world.exa = []
    r = check.check(ref("", act="Banking Consumer Protection Act 2019", claim="refund within 5 working days"))
    assert r["status"] == "NOT_FOUND" and r["proof"] is None


def test_act_named_without_section_exists(world):
    r = check.check(ref("", claim="this request is made under the Act"))
    assert r["status"] == "MATCH" and r["proof"]["url"].endswith("/ukpga/2000/36")


def test_implicit_claim_gets_possible_rule_with_fetched_text(world):
    world.exa = [hit("ukpga/2000/36/section/12")]
    r = check.check({**ref(kind="implicit", claim="the council cannot refuse because of cost"),
                     "hint": "Freedom of Information Act 2000"})
    assert r["status"] == "POSSIBLE_RULE" and r["proof"]["url"].endswith("/section/12")


def test_implicit_claim_with_wrong_deadline_is_misquoted(world):
    world.exa = [hit("ukpga/2000/36/section/10")]
    r = check.check(ref(kind="implicit", claim="the council must reply within 10 working days"))
    assert r["status"] == "MISQUOTED" and r["cited"] == "FOIA s.10"


def test_without_exa_unknown_laws_are_not_found_and_say_why(world):
    world.exa = None
    r = check.check(ref("section 64Z", claim="councils may not redact contractor names"))
    assert r["status"] == "NOT_FOUND" and "Exa not configured" in r["evidence"]


def test_exa_error_does_not_crash_the_row(world, monkeypatch):
    monkeypatch.setattr(exa_src, "search", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    r = check.check(ref(kind="implicit", claim="you must reply in two weeks"))
    assert r["status"] == "NOT_FOUND" and any(e.startswith("Exa error") for e in r["evidence"])


def test_only_the_five_statuses_exist():
    assert [s["code"] for s in check.STATUSES] == ["MATCH", "MISQUOTED", "NOT_AS_CITED", "POSSIBLE_RULE", "NOT_FOUND"]


@pytest.mark.xfail(strict=True, reason="docs/exa-audit.md action 1: check.py:86-90 still uses an Exa highlight as proof")
def test_unknown_act_with_only_an_act_level_hit_is_not_proven(world):
    world.exa = [hit("ukpga/2015/15/contents", title="Consumer Rights Act 2015")]
    r = check.check(ref("section 112A", act="Banking Fairness Act 2015", claim="banks must prevent all fraud"))
    assert r["status"] == "NOT_FOUND"

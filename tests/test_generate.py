import pytest
from fastapi.testclient import TestClient

from app import generate
from app.config import settings
from app.extract_rules import extract
from app.main import app


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    monkeypatch.setattr(type(settings), "ai_enabled", property(lambda self: False))


def test_every_area_has_a_builtin_letter_with_citations():
    for a in generate.AREAS:
        r = generate.generate(a["id"])
        assert r["source"] == "template" and r["country"] == "UK"
        assert len([c for c in extract(r["text"]) if c.kind == "legislation"]) >= 3


def test_gemini_failure_falls_back_to_builtin(monkeypatch):
    monkeypatch.setattr(type(settings), "ai_enabled", property(lambda self: True))
    monkeypatch.setattr(generate, "_gemini", lambda area: (_ for _ in ()).throw(RuntimeError("429")))
    assert generate.generate("foi")["source"] == "template"


def test_only_uk_and_known_areas():
    with pytest.raises(ValueError):
        generate.generate("foi", "FR")
    with pytest.raises(ValueError):
        generate.generate("tax")


def test_api():
    c = TestClient(app)
    opts = c.get("/api/generate/options").json()
    assert [x["code"] for x in opts["countries"]] == ["UK"]
    assert all(a["short"] and a["name"] for a in opts["areas"])
    assert c.post("/api/generate", json={"area": "sar"}).json()["area"] == "sar"
    assert c.post("/api/generate", json={"area": "sar", "country": "DE"}).status_code == 400

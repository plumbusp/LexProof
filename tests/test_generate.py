import re

import pytest
from fastapi.testclient import TestClient

from app import generate, laws
from app.config import settings
from app.extract_rules import extract
from app.main import app


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    monkeypatch.setattr(type(settings), "ai_enabled", property(lambda self: False))


def test_two_presets_per_area_each_with_real_and_fake_citations():
    for a in generate.AREAS:
        presets = generate.PRESETS[a["id"]]
        assert len(presets) == 2 and len({p["title"] for p in presets}) == 2
        for p in presets:
            r = generate.generate(a["id"], preset=p["id"])
            assert r["source"] == "preset" and r["title"] == p["title"]
            assert "{" not in r["text"] and "Northgate Borough" not in r["text"]
            assert len([c for c in extract(r["text"]) if c.kind == "legislation"]) >= 3


def test_every_act_a_preset_cites_on_purpose_is_in_the_dictionary():
    for name in ["Local Government Act 1974", "Consumer Credit Act 1974", "Consumer Rights Act 2015",
                 "Payment Services Regulations 2017", "Environmental Information Regulations 2004", "UK GDPR"]:
        assert laws.lookup(name), name


def test_without_ai_or_when_ai_fails_a_preset_is_used(monkeypatch):
    assert generate.generate("foi")["source"] == "fallback"
    monkeypatch.setattr(type(settings), "ai_enabled", property(lambda self: True))
    monkeypatch.setattr(generate, "_gemini", lambda area: (_ for _ in ()).throw(RuntimeError("429")))
    assert generate.generate("foi")["source"] == "fallback"
    monkeypatch.setattr(generate, "_gemini", lambda area: "A new letter " * 30)
    assert generate.generate("foi")["source"] == "gemini"


def test_only_uk_known_areas_and_known_presets():
    with pytest.raises(ValueError):
        generate.generate("foi", "FR")
    with pytest.raises(ValueError):
        generate.generate("tax")
    with pytest.raises(ValueError):
        generate.generate("foi", preset="eir-1")


def test_api():
    c = TestClient(app)
    opts = c.get("/api/generate/options").json()
    assert [x["code"] for x in opts["countries"]] == ["UK"]
    assert all(a["short"] and a["name"] and len(a["presets"]) == 2 for a in opts["areas"])
    assert c.post("/api/generate", json={"area": "sar", "preset": "sar-2"}).json()["title"] == "CCTV and a parking fine"
    assert c.post("/api/generate", json={"area": "sar", "country": "DE"}).status_code == 400
    assert c.get("/api/sample").status_code == 404


def test_page_assets_are_always_revalidated():
    c = TestClient(app)
    for path in ("/", "/static/app.js", "/static/app.css", "/static/home.js"):
        assert c.get(path).headers.get("cache-control") == "no-cache", path


def test_page_loads_scripts_and_styles_at_versioned_urls():
    html = TestClient(app).get("/").text
    for name in ("app.css", "app.js", "views.js", "home.js", "map.js"):
        assert re.search(rf'"/static/{name}\?v=\d+"', html), name

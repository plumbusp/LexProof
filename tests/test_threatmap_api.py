"""HTTP layer of the map: input checks and the shape the page relies on. Saved files only."""
import pytest
from conftest import art, exa_row, ext_row, item
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
U = "https://a.test/1"


@pytest.fixture
def seeded(store):
    store.exa(exa_row("Italy", "2026-09", []), exa_row("Italy", "2026-10", [art(U, "2026-10-21")]))
    store.ext(ext_row(U, item(date_start="2026-10-21")))
    return store


def test_defaults_to_everything_searched(seeded):
    d = client.get("/api/threatmap").json()
    assert (d["from"], d["to"], d["layer"]) == ("2026-09", "2026-10", "hostile")
    assert d["countries"]["Italy"]["level"] == 2 and len(d["countries"]) == 29
    assert {"months", "types", "n_events", "extracted", "articles", "saved_at"} <= d.keys()


def test_window_and_swapped_bounds(seeded):
    assert client.get("/api/threatmap?from=2026-09&to=2026-09").json()["n_events"] == 0
    swapped = client.get("/api/threatmap?from=2026-10&to=2026-09").json()
    assert (swapped["from"], swapped["to"]) == ("2026-09", "2026-10")


def test_types_absent_all_empty_none_listed_some(seeded):
    assert client.get("/api/threatmap").json()["n_events"] == 1
    assert client.get("/api/threatmap?types=").json()["n_events"] == 0
    assert client.get("/api/threatmap?types=T5").json()["n_events"] == 1
    assert client.get("/api/threatmap?types=T3").json()["n_events"] == 0


@pytest.mark.parametrize("qs", ["layer=nope", "from=2026-13", "to=26-10", "from=2026-1", "from=abc"])
def test_bad_input_is_a_400(seeded, qs):
    assert client.get("/api/threatmap?" + qs).status_code == 400


def test_no_saved_data_is_a_404_not_a_crash(store):
    assert client.get("/api/threatmap").status_code == 404


def test_every_event_served_has_a_source_and_a_quote(seeded):
    for c in client.get("/api/threatmap").json()["countries"].values():
        for e in c["events"]:
            assert e["url"].startswith("http") and e["quote"].strip()

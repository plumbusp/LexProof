"""Shared fixtures: a throwaway map data folder, so tests never touch data/threatmap/ and never call Exa or Gemini."""
import json
from types import SimpleNamespace

import pytest

from app import threatmap as tm
from app import threatstore as ts


def art(url, published="2026-10-10", title="A title", highlights=("An excerpt.",)):
    return {"url": url, "title": title, "published_date": published, "highlights": list(highlights)}


def exa_row(country, month, results, group="incident"):
    return {"id": f"{month}|{country}|{group}", "month": month, "country": country, "group": group, "query": "q",
            "fetched_at": "2026-11-14T10:00:00+00:00", "cost_usd": 0.007, "results": results}


def item(**kw):
    base = {"layer": "hostile", "type": "T5", "countries": ["Italy"], "date_start": "2026-10-19", "target": "schools",
            "volume": "", "voice": "official", "attribution": "none", "ai_use": "unknown", "kind": "incident",
            "weak_source": False, "quote": "a verbatim quote"}
    return {**base, **kw}


def ext_row(url, *items, pv=1):
    return {"url": url, "pv": pv, "items": list(items), "extracted_at": "2026-11-14T11:00:00+00:00"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "OUT_DIR", tmp_path)
    monkeypatch.setattr(tm, "RESULTS", tmp_path / "exa_results.jsonl")
    monkeypatch.setattr(ts, "EXTRACTIONS", tmp_path / "extractions.jsonl")
    monkeypatch.setattr(ts, "SNAPSHOT", tmp_path / "snapshot.json")   # never read the real committed snapshot

    def clear():
        for f in (ts._results_index, ts._extracted_count, ts._cached, ts._snapshot_view):
            f.cache_clear()

    def write(path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        clear()

    clear()
    yield SimpleNamespace(
        exa=lambda *rows: write(tm.RESULTS, rows),
        ext=lambda *rows: write(ts.EXTRACTIONS, rows),
        dir=tmp_path)
    clear()

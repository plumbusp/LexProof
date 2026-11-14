"""Translations are display-only: the original quote is kept and English text is never overwritten."""
import json

from app import threattranslate as tr


def _seed(tmp_path, monkeypatch, rows):
    f = tmp_path / "translations.jsonl"
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(tr, "FILE", f)
    tr._cached.cache_clear()


def test_attach_keeps_original_and_adds_english(tmp_path, monkeypatch):
    q = "Vairāk nekā 200 izglītības iestāžu saņēma draudu e-pastus."
    _seed(tmp_path, monkeypatch, [{"h": tr.key(q), "lang": "lv", "en": "More than 200 schools received threat emails."}])
    e = tr.attach({"quote": q, "title": ""})
    assert e["quote"] == q and e["quote_en"].startswith("More than 200") and e["lang"] == "lv"


def test_english_and_unknown_snippets_get_no_translation(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [{"h": tr.key("Already English."), "lang": "en", "en": "Already English."}])
    assert "quote_en" not in tr.attach({"quote": "Already English.", "title": ""})
    assert "quote_en" not in tr.attach({"quote": "Nie przetłumaczone.", "title": ""})

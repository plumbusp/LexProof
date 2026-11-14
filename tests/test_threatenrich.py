"""Exa context for map events: summaries are cleaned and capped; headlines attach by event id."""
import json

from app import threatenrich as en


def test_summary_prefix_and_bullets_are_removed():
    s = en.clean_summary("Summary: - What happened: Schools got threat emails.\n- Where: Latvia.")
    assert s.startswith("Schools got threat emails.") and "Summary" not in s and "What happened" not in s


def test_summary_is_two_or_three_lines_at_most():
    long = " ".join(f"Sentence number {i} says something about the incident in detail." for i in range(6))
    s = en.clean_summary(long)
    assert len(s) <= 260 and s.endswith(".")


def test_one_long_sentence_is_kept_whole():
    one = "A " + "very " * 80 + "long sentence."
    assert en.clean_summary(one) == one


def test_attach_adds_summary_context_and_headline(tmp_path, monkeypatch):
    f, h = tmp_path / "enrich.jsonl", tmp_path / "headlines.jsonl"
    f.write_text(json.dumps({"url": "https://a.test/1", "summary": "Summary: It happened.", "context": "around it"}) + "\n", encoding="utf-8")
    h.write_text(json.dumps({"id": "abc", "headline": "One line", "by": "claude"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(en, "FILE", f)
    monkeypatch.setattr(en, "HEADLINES", h)
    en._cached.cache_clear(); en._headlines.cache_clear()
    e = en.attach({"id": "abc", "url": "https://a.test/1"})
    assert (e["summary"], e["context"], e["headline"]) == ("It happened.", "around it", "One line")
    assert "summary" not in en.attach({"id": "zzz", "url": "https://b.test/2"})

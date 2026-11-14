from app import threatextract as tx
from app import threatmap as tm

ART = {"url": "https://example.test/a", "title": "Schools get hoax emails",
       "highlights": ["Una cinquantina di istituti in tutta Italia ha ricevuto la stessa email anonima."], "published_date": "2026-10-21"}


def item(**kw):
    base = {"i": 1, "type": "T5", "countries": ["Italy"], "voice": "official", "quote": "Una cinquantina di istituti in tutta Italia ha ricevuto la stessa email"}
    return tx.Item(**{**base, **kw})


def test_quote_must_be_in_the_saved_text():
    assert tx.verify(item(), ART)["countries"] == ["Italy"]
    assert tx.verify(item(quote="Fifty schools were attacked by a foreign state"), ART) is None  # invented quote
    assert tx.verify(item(quote=""), ART) is None


def test_quote_with_gaps_and_other_quote_marks():
    assert tx.quote_in_text("“Una cinquantina di istituti … ha ricevuto la stessa email anonima”", ART["highlights"][0])


def test_out_of_scope_countries_are_dropped():
    assert tx.verify(item(countries=["Serbia"]), ART) is None
    assert tx.verify(item(countries=["Serbia", "Italy"]), ART)["countries"] == ["Italy"]


def test_not_an_event_and_layer_type_mismatch_dropped():
    assert tx.verify(item(is_event=False), ART) is None
    assert tx.verify(item(layer="precursor", type="T5"), ART) is None


def test_precursor_is_never_hostile():
    out = tx.verify(item(layer="precursor", type="AI", voice="official", attribution="named"), ART)
    assert (out["voice"], out["attribution"]) == ("nobody", "none")
    assert tm.level({**out, "country": "Italy", "url": ART["url"]}) == 0


def test_precursor_levels_count_source_sites():
    evs = [{"layer": "precursor", "type": "AI", "country": "Italy", "url": f"https://s{i}.test/x", "quote": "q"} for i in range(3)]
    assert tm.country_levels(evs, "precursor") == {"Italy": 3}

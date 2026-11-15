# Plan for the agent that builds the "mass submissions to institutions" map

_14 Nov 2026. A written plan only, with no code, because of the hackathon rule. The agent (or teammate) writes all code during the event. Read first: [mass-submission-taxonomy.md](mass-submission-taxonomy.md), [exa-threat-map-queries.md](exa-threat-map-queries.md), [exa-october-2026-results.md](exa-october-2026-results.md)._

## 1. Goal in one sentence

A web page with a map of Europe where each country is coloured by the **strongest evidence** that a hostile actor is flooding public institutions with mass submissions, where every dot opens a real source and quote, and where "nothing found" is shown honestly.

This is a **pitch visual attached to the complaint checker**, not a second product. Time cap for the whole thing: about 3 hours. If it runs over, ship the seed-data version (step 4) and stop.

## 2. Hard rules (do not break these)

1. **No source, no dot.** Every event needs a URL and a short quote copied from the page.
2. **Code sets the evidence level, Gemini never does.** Gemini only extracts fields from text. A plain rule in code turns the fields into level 0 to 4.
3. **Level 0 is "nothing found", drawn hatched grey. Never green, never "safe".** Countries not yet searched are labelled "not searched".
4. **Scope of countries:** geographic Europe, 42 countries: EU-27, the UK, Switzerland, Norway, Iceland, Liechtenstein, the microstates, the Western Balkans and Moldova. Ukraine, Russia, Belarus, Turkey and Kosovo are not in the data; drawn as not covered, with no dots.
5. **Dates come from the Exa date fields (`startPublishedDate`, `endPublishedDate`), not from words in the query.** Writing "October 2026" in the query text does not filter anything (tested).
6. **Do not call a surge "foreign" without an official or multi-source attribution.** A rise in complaints alone is level 0 for the hostile layer.
7. No clustering, no wave layer, no similar-case finder (cut on Saturday, see project memory).

## 3. Inputs the agent already has

- The six event types T1 to T6, the three-test definition, the exclusions and the level ladder: taxonomy file.
- Query sentences by group (incidents, official warnings, precursors, counter-evidence): queries file.
- Test results to compare against: October file.
- The filing-ease table (UK, FI, SE, DE, NL...): section 4 of [threat-map.md](threat-map.md).

## 4. Build order (stop after any step and it still works)

**Step 1 (30 min): the data file.** One simple file of events, one record per event. No database. Fields: id, type (T1 to T6), country, date start, date end, target body, volume text, who says it is hostile (nobody / expert / official), attributed to (none / suspected / named state), AI use (yes / no / unknown), source URL, source domain, quote, language of source, found-on date.

**Step 2 (15 min): seed it by hand with the cases already verified**, so the map works with no live calls:

| Country | Type | Date | Body | Voice | Source |
|---|---|---|---|---|---|
| Latvia | T3 | Sep 2026 | ministries, State Police, municipalities, Food and Veterinary Service, administrative court | official (Security Police) | BB.LV, 7 Sep 2026 |
| Bulgaria | T5 | Jun to 9 Oct 2026 | courts, universities, kindergartens, ports | official (interior minister), hypothesis | BTA and BurgasMedia, 9 Oct 2026 |
| Hungary | T5 | 19 Oct 2026 | 70+ schools | weak source, unverified | ZVISTKA, 21 Oct 2026 |
| UK (Northern Ireland) | T5 | 19 Oct 2026 | ~160 schools | weak source, unverified | ZVISTKA, 21 Oct 2026 |
| France, Germany, Italy | T6 | 2024 onwards | newsrooms and fact-checkers | 2+ independent researchers and Viginum | CheckFirst report, 2024 |
| Sweden | T6 | Aug 2026 | newsrooms | researchers, SVT reporting | Euromaidan Press, 20 Aug 2026 |

Precursor layer seed (AI complaints by real people, **not** hostile): UK (Wiltshire +27%, Edinburgh, Ceredigion, Durham, Lincolnshire, Financial Ombudsman), Germany (NRW urgent court cases 1,400 to 3,600; Jobcenter objections 610k projected), Netherlands and Belgium (8 of 12 councils), Sweden (Consumer Disputes Board), Finland (Ähtäri). Sources are in the patterns and threat-map files.

**Step 3 (45 min): the map page.** Static page, no server needed.
- Europe map with the scoped countries. Fill colour = highest level in that country, one hue light to dark for levels 1 to 4, hatched grey for 0.
- Event dots inside countries with the type letter (T1 to T6). T6 dots use a different shape because the target is media, not government.
- Filter chips for T1 to T6 and for the layer toggle: Hostile events / AI complaint surge / Filing ease.
- Click a country or dot: side panel with country name, level, event list, each with date, target, who says it is hostile, quote and link.
- Legend that states "0 = nothing found, not safe" and "levels set by rules, not by AI".
- Footer line: "Reported or warned, not proven attack" so nobody over-reads it.

**Step 4 (45 min): the live refresh (only if time).** One button "check last 7 days".
- Run the incident and precursor queries from the queries file through Exa with the date fields set to the last 7 days, category news, 10 results each, highlights on (use the EU endpoint if the key supports it).
- Send each result's highlight text to Gemini with a fixed output shape (the fields in step 1) and an instruction to answer "not an event" when the three tests are not met.
- Code drops everything marked "not an event", drops duplicates (same country, type, and source domain within 7 days), assigns the level, appends to the data file.
- New items appear with a "new" badge and a note "unreviewed, check before pitching".

**Step 5 (20 min): checks.** See acceptance tests below.

## 5. Level rules for the code (plain words)

- **4:** voice is official or two independent researchers, attributed to a named state, and at least two different source domains.
- **3:** a reported incident, one origin, attribution not confirmed.
- **2:** an official says it is coordinated or suspects a hybrid operation, no attribution.
- **1:** forecast, or weak or aggregator-only source.
- **0:** no events found.
- The country takes the highest level among its events. T6 events do not raise a country's level; they show as a separate media marker.

## 6. Acceptance tests (do these before showing anyone)

1. Latvia is level 2, T3, and its panel shows the BB.LV quote and link.
2. Bulgaria is level 2, T5, and the panel says "hypothesis, no attribution".
3. Hungary and Northern Ireland are level 1 with a visible "unverified source" tag.
4. Ukraine, Russia, Belarus, Turkey and Kosovo are drawn grey as "Not covered in this demo", are not clickable, and no dot sits there.
5. Switzerland and Ireland show hatched grey with "not searched".
6. No dot exists without a URL and a quote.
7. Switching to the precursor layer lights up UK, Germany, NL, BE, SE, FI and does **not** use hostile-event colours.
8. Turning off every filter chip leaves the map empty, not broken.
9. A fake test event with attribution "none" and voice "nobody" cannot reach level 2.

## 7. Known weak spots to tell the judges honestly

- The two level 2 events are warnings or hypotheses, not proven attacks.
- Hungary and Northern Ireland rest on a weak aggregator.
- Nobody counts these events, so absence of a dot is not absence of events.
- The live refresh only reads the open web (and English or translated text), so local-language reports may be missed.

## 8. Fast fallback

If everything else breaks, step 3 with the seed data is the demo. It needs no API calls and still tells the honest story.

# Exa features in LexProof

_Updated 15 Nov 2026. Branch `foi-law-checker`._

Exa is how LexProof reads the web. Every dot on the Regional Abuse Map and every "nearest real rule" in the letter checker comes from an Exa call. Code, not AI, decides every status and every evidence level.

**Demo vs pitch.** The demo is fully local. The map reads saved files only (`data/threatmap/`, or `mapdata/snapshot.json` on a deploy) and makes no Exa or AI calls. In production the same scripts run once a day.

## 1. Features used, and where

| Exa feature | Endpoint and parameters | Where | What it gives us |
|---|---|---|---|
| **Search** | `/search`, `type="auto"`, `include_domains=["legislation.gov.uk"]`, `contents.highlights` | `app/check.py` (letter checker) | The official page for a law the letter cites or hints at. The section text is then fetched from the legislation.gov.uk API. |
| **News search with date filters** | `/search`, `category="news"`, `start_published_date` / `end_published_date`, `contents.highlights` | `app/threatmap.py --run` | 406 monthly searches (Apr–Oct 2026): 2,714 news articles for the map. Gemini reads the excerpts into events. |
| **Deep Search with structured output** | `/search`, `type="deep"`, `output_schema`, `additional_queries` (the country's own languages), `user_location` (the country), `system_prompt`, `category="news"`, dates since Jan 2025 | `app/threatdeep.py --sweep` | 58 searches (29 countries × 2 layers; the 13 countries added later, Norway to Moldova, need 26 more). Exa returns ready-made events: URL, date, type, target, who calls it hostile, who is blamed, and an exact quote, plus grounding with a confidence level per field. 269 proposed, 233 kept. |
| **Contents: full text** | `/contents`, `text` | `app/threatdeep.py` (check step), `app/threatenrich.py` | The whole article. Code checks that each event's quote is really on the page. No quote on the page means no dot. |
| **Contents: highlights with a query** | `/contents`, `highlights.query = the event's quote` | `app/threatenrich.py` | The paragraph around the quote on the real page (saved as `context`). |
| **Contents: summary with a query** | `/contents`, `summary.query = "In 2-3 short sentences in English: what happened, where, ..."` | `app/threatenrich.py` | A 2–3 line English summary per article, shown on each card as "Summary by Exa". It also translates: most articles are not in English. |
| **Deep-lite (corroboration)** | `/search`, `type="deep-lite"`, `exclude_domains=[the first outlet]`, ±21 days | `app/threatdeep.py --corroborate` | Built, not yet run. Finds a second outlet for the same incident. |
| **Agent: relevance to a country** | `/agent/runs`, `effort="low"`, `input.data` = the recent signal pool, `output_schema` with facts per signal | `app/relevance.py --run` | **Used.** One run for the UK, 122 signals, 44 s, $0.025. For each report abroad it returns two facts: does it hit a channel UK public bodies run, and did the same wave reach the UK (with a source URL). That URL is fetched with Exa Contents and must name the UK. Code then sets the level 1–5. |
| **Agent: investigate a country** | `/agent/runs`, `effort="low"`, the events `output_schema` | `app/threatdeep.py --investigate <country>` | Built and tested once (14 s, $0.012). Its events go through the same quote check. Not used in the demo. |

**Not used:**
- **Snapshot** (`/contents` with `snapshotAsOf`): Exa replies `SNAPSHOT_NOT_ON_PLAN` on this key. It also covers only the last 5 months. For laws, legislation.gov.uk already serves dated versions for free.
- **Monitors:** they need a public HTTPS webhook, so they're left for production.
- **Answer:** it gives no exact quote per dot, so it doesn't fit the "no source, no dot" rule.

## 2. What is saved locally

All of it lives under `data/threatmap/`, which is gitignored: it is paid data and holds full article text.

| File | What |
|---|---|
| `exa_results.jsonl` | One line per search: the request, cost and every result (URL, title, date, highlights). Covers news search and Deep Search. |
| `exa_raw/contents/<id>.json` | **Exa's full `/contents` response, unmodified**, with the request: full text, highlights, summary, statuses, `costDollars`. 136 articles. |
| `exa_raw/search/<id>.json` | Exa's full `/search` response for every future deep sweep: all results, structured output and grounding. The first sweep (14 Nov) ran before this was added, so only its parsed results are kept, in `exa_results.jsonl`. |
| `extractions.jsonl` | Verified event fields per article URL, from Gemini or from Exa Deep Search. |
| `enrich.jsonl` | Per article: the Exa summary, the context passage and the cost. |
| `translations.jsonl` | English versions of quotes and titles (Gemini or Claude). Display only. |
| `headlines.jsonl` | The one-line headline per event, written by AI from the title, Exa's summary and the quote. Display only. |

`python -m app.threatstore --export` writes `mapdata/snapshot.json`, the committed copy a deploy reads. It holds events, levels, quotes, summaries, headlines and translations, but not full article text.

## 3. Cost so far

| Run | Calls | Cost |
|---|---|---|
| Monthly news search (Apr–Oct 2026) | 406 | $2.44 |
| Deep Search sweep, first 29 countries × 2 layers | 58 searches + `/contents` checks | $1.46 |
| Contents (text + highlights + summary), last 3 months | 136 | $0.41 |
| Agent test run | 1 | $0.01 |
| Agent relevance run for the UK | 1 | $0.03 |

A daily refresh in production would be about 58 deep searches plus `/contents` on new articles: roughly $1–2 a day.

## 4. Relevance to one country (the UK view)

The map page is the **UK view**. Each report gets one level from 1 to 5 for the UK; another country would get its own profile and its own agent run.

| Level | Meaning |
|---|---|
| 5 · Act now | Hitting UK councils, schools or services. |
| 4 · Prepare | In the UK but another channel, or a specific wave abroad that has already reached the UK (checked source). |
| 3 · Watch | Same kind of pressure next door (Ireland), a coordinated campaign abroad, or a trend already seen in the UK. |
| 2 · Background | The same kind of pressure elsewhere in Europe. |
| 1 · Not relevant | Another channel or sector, for example media only. |

**Older than 90 days: one level lower**, the same for every level and country (5→4, 4→3, 3→2, 2→1; 1 stays 1).

Inputs: country, channel (from the agent; a keyword rule for reports older than 90 days), "reached the UK" (agent + checked source), the campaign signal (an official or researchers call it coordinated, or an actor is blamed: the old evidence ladder), and date. A source the agent cites for the AI-complaint trend never counts as proof that a specific wave reached the UK.

Files: `data/threatmap/relevance_GB.json` (facts per signal), `data/threatmap/exa_raw/agent/<run>.json` (the full agent request and response). Rules: `app/relevance.py`, tests: `tests/test_relevance.py`.

## 5. Daily run (production)

```
python -m app.threatdeep --sweep --since <yesterday's month>
python -m app.threatenrich --run --days 2 --budget 5
python -m app.threattranslate --run
python -m app.relevance --run --profile GB
python -m app.threatstore --export
```

## 6. Rules the Exa output never overrides

1. **No source, no dot.** Every event needs a URL and a quote found on the fetched page.
2. **Code sets the level.** Exa's fields (who calls it hostile, who is blamed) feed fixed rules in `app/threatmap.py`.
3. **Summaries, headlines and translations are display only.** The original quote stays the proof.
4. **Level 4 needs two outlets reporting the same incident:** same country, same type, dates within 7 days of each other.
5. **A person can remove a wrong event** (wrong country, the same article counted twice) in `mapdata/review.json`, with the reason written down. Review only removes events; it never adds or upgrades one.

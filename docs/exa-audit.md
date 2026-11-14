# Exa audit (LexProof)

Audited: 2026-11-14, branch `foi-law-checker`, exa-py 2.26.0, 58 tests passing.
No code was changed for this audit.

## 1. Every Exa call today

| # | Where | Endpoint | Parameters sent | Used for | Used as proof without fetching the page? |
|---|---|---|---|---|---|
| 1 | `app/check.py:71` via `app/sources/exa_src.py:51` | `/search` | `type="auto"`, `num_results=6`, `include_domains=["legislation.gov.uk"]`, `contents.highlights={query, maxCharacters: 600}`. No `category`, no dates, no `contents.text`, no `summary`, no `objective`, no `user_location`. | Nearest real rule for NOT AS CITED (`check.py:124`) and likely rule for POSSIBLE RULE (`check.py:135`). The hit URL is parsed and the section is fetched from the legislation.gov.uk API. | **Yes, in one path.** `check.py:86-90`: when only an Act-level page comes back, the Exa highlight becomes `proof`. That row can then be NOT AS CITED, POSSIBLE RULE or even MISQUOTED (`_compare` on the highlight) with no fetched text. Breaks the proof rule. |
| 2 | `app/threatmap.py:124` (`run_plan`, query params from `_kwargs` at `threatmap.py:49-53`) | `/search` | `type="auto"`, `category="news"`, `num_results=10`, `start_published_date`, `end_published_date`, `contents.highlights={query, maxCharacters: 600}` | Regional Abuse Map backfill (offline script). Results go to `data/threatmap/exa_results.jsonl`; an LLM extracts fields and code checks the quote against title/excerpt. | No verdicts. Map only. |
| 3 | `app/sources/exa_src.py:57-83` (`contents()`) | `/contents` | `text.maxCharacters` (30000), optional `highlights={query, maxCharacters: 800}`, optional `snapshot_as_of` | **Nothing calls it.** Dead code today. | n/a |

## 2. How the calls are made

- **Client:** exa-py SDK (`Exa(api_key=...)`, `exa_src.py:25`), not raw HTTP.
- **Timeout:** none set. The SDK default is 600 s, so a slow call can hang a letter check.
- **Retry:** none.
- **Cache:** SQLite key/value (`app/cache.py`), 24 h, keyed by the Python arguments (`exa_src.py:39`, `:60`), not by the request body.
- **Cost:** `cost_dollars.total` is logged only in the map backfill (`threatmap.py:127`), never per letter.
- **Errors:**
  - Checker: the exception becomes an evidence line (`Exa error: ...`) and the row ends **NOT FOUND**. An Exa outage looks the same as "this law does not exist".
  - `contents()`: returns `{ok: False, error}`.
  - Map backfill: the failed request is skipped and retried on the next run.

## 3. Rule check

| Rule | Today |
|---|---|
| Gemini never sets a status | OK. `check.py` sets every status; `ai.py` only extracts. |
| Every verdict ends with a fetched official text, or NOT FOUND | **Broken** at `check.py:86-90` (Exa highlight as proof). |
| Never label people | OK in the checker. |
| Privacy: send Exa only the claim or citation | **At risk.** `check.py:124` and `:135` send `ref["claim"]`, a whole clause copied from the letter, which can contain a name, address or email. |
| Five statuses only | OK. |

## 4. Proposed actions (in commit order)

1. **Fix the proof rule.** Delete the Act-level highlight fallback (`check.py:86-90`). If no section can be fetched, the row is NOT FOUND (or the Act-exists MATCH path for a bare Act name).
2. **Scrub before sending.** Strip names, emails, phone numbers, postcodes and street addresses from the claim, and cap it at ~200 characters before any Exa call.
3. **Say when the lookup failed.** Keep the status NOT FOUND (five statuses only) but set the note to "Lookup failed, try again" instead of "No law found".
4. **Shared `app/sources/exa_client.py`.** One place for: `timeout` per call (20 s, 25 s for deep), one retry, a disk cache keyed by the request body, `cost_dollars.total` summed per check and shown in a debug footer. Revive `contents()` on top of it.
5. **Upgrade every `/search`.**
   - `objective` (one sentence: what this step needs, what to rank first).
   - `user_location="GB"`.
   - Keep `highlights={"query": <claim>}` (already done).
   - `max_age_hours=168` on `/contents` only when the URL is on ico.org.uk or gov.uk. It is set per request, not per domain.
   - No `livecrawl` (deprecated; not used today).
6. **Case law and decision citations (new).**
   - Extract neutral citations (`[2012] UKUT 440 (AAC)`), ICO decision numbers (`FS50317322`, `IC-123456-A1B2`) and, via Gemini, case names (`kind: "case"`).
   - One `/search` per case over caselaw.nationalarchives.gov.uk, bailii.org, informationrights.decisions.tribunals.gov.uk, ico.org.uk and lgo.org.uk, then `/contents` on the top 1-2 URLs.
   - Code decides on the fetched text: citation present + claim supported = MATCH; citation present, claim not supported = MISQUOTED ("the case exists, but it does not say this"); same parties or topic but different citation = NOT AS CITED; nothing = NOT FOUND.
   - **Change to the spec:** the demo test should accept Find Case Law *or* BAILII for `[2012] UKUT 440 (AAC)`. Find Case Law may rank first, and BAILII often blocks automated fetches.
7. **Same wrong citation in many letters (local).**
   - New `citations` table in the existing SQLite: normalised citation, status, letter (job) id, received date.
   - Flag when 5+ letters in 7 days share a normalised citation with status NOT FOUND, NOT AS CITED or MISQUOTED.
   - **Change to the spec:** key MISQUOTED rows on the citation *plus the wrong value* (e.g. `foia s.10|30 days`). Otherwise honest, common mistakes about real sections would raise the banner.
   - Then one `/search` per repeated citation (cached 24 h), `/contents` on the hits, and "Matches published template" only if the page contains the exact wrong phrase (or fuzzy ratio ≥ 0.9). Never guess who wrote it.
8. **Deep search for vague claims and NOT AS CITED.**
   - `type="deep"` with `additional_queries`, `system_prompt` and `output_schema` (all supported by exa-py 2.26.0); read `output.content` and `output.grounding`.
   - Code maps `act_name` to our ID dictionary, fetches the section from the legislation.gov.uk API, and only then sets POSSIBLE RULE / NOT AS CITED.
   - 25 s timeout, falling back to `type="auto"`. UI shows "Searching official sources (deep)..." meanwhile.
   - Budget: deep (~20 s) + fetch with 4 rows in parallel is right at the 25 s target, so the fallback matters.
9. **Optional: Exa Monitor** for a found template (`exa.monitors` exists in the SDK). Needs a public HTTPS webhook; skip unless we have a deploy URL.

Every step ships with a test that uses a recorded (mocked) Exa response, so the demo and CI never need the network.

## 5. Pitch summary (after the actions above)

1. `/search` with `include_domains` + `objective` + `user_location=GB` finds the official page for a law, a court judgment or an ICO decision.
2. `/contents` (text + claim-focused `highlights`) fetches that page so **code** can prove the quote, case number or decision number is really there.
3. `type="deep"` with `output_schema` turns a vague claim ("you must reply in 10 days") into an exact Act and section, which we then fetch from legislation.gov.uk.
4. `/search` with `start_published_date` finds the public template behind the same wrong citation in many letters, and the news reports behind every dot on the Regional Abuse Map.
5. Every call is cached by request body with its `cost_dollars` logged, so a demo letter costs cents and is instant the second time.

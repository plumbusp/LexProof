# Complaint Citation Checker

Checks every legal claim in a customer complaint against the real law, **as in force on the incident date**,
and shows a caseworker exactly what is right, wrong or invented, with a link to double-check every row.

- **Exa is the engine**: source discovery, reading regulator pages that plain HTTP can't (FCA Handbook),
  rules as they stood on the incident date (Exa Snapshot), the nearest *real* rule for a fabricated one,
  where a claim circulates online, and similar Ombudsman decisions.
- **Deterministic code decides each status** using written rules (`app/taxonomy.py`).
- **AI is a second opinion only** (optional, any OpenAI-compatible model): it agrees/disagrees per row,
  rewrites the short claim, assesses the underlying complaint, and flags citations the rules missed.
  It never sets a status, and any quote it gives is dropped unless found verbatim in the fetched source.

## Run

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin/pip elsewhere
cp .env.example .env                              # add EXA_API_KEY; AI_* optional
.venv/Scripts/python -m uvicorn app.main:app --port 8765
# open http://localhost:8765 -> "Load sample" -> "Check complaint"
```

Tests (offline): `.venv/Scripts/python -m pytest -q`

Docker: `docker build -t complaint-checker . && docker run -p 8765:8765 --env-file .env complaint-checker`

## Workflow (what the caseworker sees)

1. **Parameters** (top): complaint received date, incident date, date reported, Ombudsman eligibility, and toggles for each Exa feature and the AI layer.
2. **Preview**: headline counts, problem tags (click to filter), underlying-complaint assessment, computed deadlines.
3. **Original text** with every cited law highlighted, coloured by status.
4. **Grid**: original text, short claim, status, verbatim quote from the source, source link, implication, AI second opinion.
5. **Details** for the selected row: full provision text, text today if it changed, related provisions read, nearest real rules, Exa Snapshot, where the claim appears online, AI opinion, and an evidence log of every fetch.

## API (the UI is a thin renderer)

| Endpoint | Purpose |
|---|---|
| `GET /api/schema` | statuses + rules, tag catalogue, grid columns, parameters, enabled engines |
| `POST /api/analyze` `{text, params}` | starts a job, returns `job_id` |
| `GET /api/jobs/{id}` | progress steps, then the full result document |
| `GET /api/sample` | demo complaint |

The result contains `highlights` (character offsets into the original text), `claims`, `tags`, `deadlines`,
`precedents`, `summary` and `view`, so the UI can be rebuilt without touching detection logic.

## Problems found in testing, and the fix in this design

| Problem in the A/B test | Fix |
|---|---|
| Status labels ambiguous (FOUND vs caveat vs regulator rule) | 6 statuses with written rules in `taxonomy.py` |
| Keyword check called GDPR Art 15 "misquoted" (deadline is in Art 12/12A) | `registry.RELATED` reads the provisions where the detail lives |
| "8% statutory interest" matched to the wrong statute (judgment debts) | Claims naming no authority are `NOT_FOUND` + "no source named"; Exa results shown as *related*, not a match |
| 8-week Ombudsman rule dropped (not legislation) | Regulator rules are first-class; FCA Handbook read via Exa (plain GET returns an empty JS shell) |
| 404 URLs presented as the answer | 404 kept as evidence; the link shown is the nearest real rule (Exa search inside the same Act first) |
| Inserted provisions 404 (UK GDPR `article/12A.`) | fetcher retries with the trailing dot |
| Law changed after the incident (UK GDPR Art 12, amended 2026) | point-in-time fetch at incident date + "changed since incident" tag + today's text |
| Exa MCP had no domain filter | Exa SDK used directly with `include_domains` (incl. path prefixes) |

## Configuration (.env)

| Variable | |
|---|---|
| `EXA_API_KEY` | required for the Exa features (app still runs without, deterministic only) |
| `AI_PROVIDER` | `openai` (default), `gemini` (OpenAI-compatible endpoint), or `custom` with `AI_BASE_URL` |
| `AI_API_KEY`, `AI_MODEL` | enable the AI second opinion; pick any model your key can use |

Exa Snapshot is a research preview on pay-as-you-go plans with a rolling 5-month window; when unavailable the row
says so and the live page is used.

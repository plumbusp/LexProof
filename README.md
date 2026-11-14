# Request Law Checker (UK)

Paste a request or complaint sent to a UK public body. Every legal reference is checked against
legislation.gov.uk, and the officer gets: each citation + real text as proof + reply deadline + a draft reply.

## Flow

```
text ──► Gemini: find references (explicit "FOIA s.10" / implicit "must reply in 2 weeks")
     └─► code: deadline = 20 working days (FOIA s.10), separately

explicit ─► dict name→ID ─► legislation.gov.uk section text ─► code compares ─► MATCH / MISQUOTED
            unknown name or 404 ─► Exa: nearest real rule ─► fetch its text ─► NOT AS CITED  (else NOT FOUND)
implicit ─► Exa: which law says this? ─► fetch its text ─► POSSIBLE RULE      (else NOT FOUND)
```

No Gemini key? A rule-based extractor finds explicit references so the demo still works.

## Run (Windows PowerShell)

```powershell
Copy-Item .env.example .env      # add EXA_API_KEY, AI_API_KEY (Gemini) and AI_MODEL
.venv\Scripts\python -m uvicorn app.main:app --port 8765 --reload
# open http://localhost:8765 -> Load sample -> Check
```

First time on a new machine: `python -m venv .venv` then `.venv\Scripts\pip install -r requirements.txt`.
Tests: `.venv\Scripts\python -m pytest -q`

## Regional Abuse Map (saved data, no live calls)

The map never calls Exa or Gemini. Picking another month range, layer or type just filters saved data.
The raw files in `data/threatmap/` stay on your PC (gitignored: paid data and full article excerpts).
A deploy only has `mapdata/snapshot.json` (committed: events with a short quote, title and link, plus the months searched).
The server uses the raw files when they exist, otherwise the snapshot.

```
Exa (once, costs credits)   python -m app.threatmap --run countries --month 2026-10   -> data/threatmap/exa_results.jsonl
Gemini (once, resumable)    python -m app.threatextract --run                         -> data/threatmap/extractions.jsonl
snapshot for deploys        python -m app.threatstore --export                        -> mapdata/snapshot.json (commit it)
map                         GET /api/threatmap?layer=hostile&from=2026-04&to=2026-10&types=T3,T5
```

After new Exa or Gemini runs, re-export and commit the snapshot, or the deployed map keeps showing the old data.

Code sets every evidence level (`app/threatmap.py`); Gemini only fills fields, and an event is kept only if its quote
is really in the saved article text. The event types are a working reading of the plan (T3, T5, T6); T1, T2, T4 are unused
until the taxonomy file is added.

## Files

| File | What it does |
|---|---|
| `app/ai.py` | Gemini reference extraction (OpenAI-compatible endpoint) |
| `app/laws.py` | name → legislation.gov.uk ID dictionary, URL/section parsing |
| `app/check.py` | the flowchart: dictionary → API → compare, Exa fallbacks, statuses |
| `app/deadlines.py` | 20 working days, excluding bank holidays anywhere in the UK (FOIA s.10(6)) |
| `app/pipeline.py` | runs everything, builds the officer view + draft reply |
| `app/sources/` | legislation.gov.uk fetcher, Exa wrapper, cached HTTP |
| `static/` | plain HTML/JS/CSS UI (no build step) |

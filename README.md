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

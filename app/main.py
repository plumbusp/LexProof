"""HTTP API. Analysis runs as a background job; the UI polls for progress."""
import re
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cache, generate, pipeline, threatdeep, threatmap as tm, threatstore
from .config import ROOT, settings

app = FastAPI(title="LexProof", version="0.2")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
MAX_CHARS = 60000


class AnalyzeRequest(BaseModel):
    text: str = Field(min_length=20, max_length=MAX_CHARS)
    params: dict = Field(default_factory=dict)


def _set(job_id, **kw):
    with _lock:
        _jobs[job_id].update(kw)


def _work(job_id, text, params):
    def progress(step, **kw):
        with _lock:
            _jobs[job_id]["progress"].append({"step": step, "at": datetime.now(timezone.utc).isoformat(), **kw})
    out = pipeline.run_safe(text, params, progress)
    if out["ok"]:
        _set(job_id, status="done", result=out["result"])
    else:
        _set(job_id, status="error", error=out["error"])
    with _lock:
        cache.save_job(job_id, _jobs[job_id])


@app.get("/api/health")
def health():
    return {"ok": True, "exa": settings.exa_enabled, "ai": settings.ai_enabled,
            "ai_provider": settings.ai_provider if settings.ai_enabled else ""}


@app.get("/api/schema")
def schema():
    return pipeline.view()


class GenerateRequest(BaseModel):
    area: str
    country: str = "UK"
    preset: str | None = None


@app.get("/api/generate/options")
def generate_options():
    return generate.options()


@app.post("/api/generate")
def generate_letter(req: GenerateRequest):
    try:
        return generate.generate(req.area, req.country, req.preset)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"id": job_id, "status": "running", "progress": [], "result": None, "error": None}
    threading.Thread(target=_work, args=(job_id, req.text, req.params), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    with _lock:
        j = _jobs.get(job_id)
        if j:
            return dict(j)
    j = cache.load_job(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/api/threatmap")
def threatmap(layer: str = "hostile", frm: str | None = Query(None, alias="from"), to: str | None = None,
              types: str | None = None):
    """Regional Abuse Map. Reads saved files only (no Exa, no Gemini); from/to are months 'YYYY-MM'
    (default: everything searched); types is a comma list like T3,T5, empty = none, absent = all."""
    months = threatstore.months_available()
    if layer not in ("hostile", "precursor", "relevance"):
        raise HTTPException(400, "layer must be hostile, precursor or relevance")
    for m in (frm, to):
        if m is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m):
            raise HTTPException(400, "from/to must look like 2026-10")
    if not months:
        raise HTTPException(404, "no saved map data (data/threatmap is empty)")
    a, b = frm or months[0], to or months[-1]
    if a > b:
        a, b = b, a
    return threatstore.query(a, b, layer, None if types is None else [t for t in types.split(",") if t])


class InvestigateRequest(BaseModel):
    country: str


# The map's two live buttons. Each is a background job the page polls (/api/jobs/{id}). If Exa is slow, fails,
# has no key or the budget refuses the call, the job ends as status "replay": the newest REAL saved run of the
# same kind, clearly labelled. Nothing is ever invented. A slow Exa call is not cut off: it keeps running and
# its result is saved, so the next click finds it.
DEADLINE = {"refresh": 60, "investigate": 150}
REUSE = {"refresh": 15 * 60, "investigate": 6 * 3600}


def _friendly(e: Exception) -> str:
    from .exacall import BudgetExceeded
    from .sources.exa_src import ExaUnavailable
    if isinstance(e, BudgetExceeded):
        return "The Exa budget for live checks is used up for today."
    if isinstance(e, ExaUnavailable):
        return "Live checks are off on this server (no Exa key)."
    if isinstance(e, TimeoutError):
        return "Exa did not answer in time."
    return "Exa did not answer properly."


def _replay(job_id, kind, country, reason):
    prev = threatdeep.last_live(kind, country) or (threatdeep.last_live(kind) if kind == "refresh" else None)
    if prev:
        _set(job_id, status="replay", result={**prev, "replay": True, "reason": reason, "cost_usd": 0.0})
    else:
        _set(job_id, status="error", error=reason + " No earlier live check is saved to show instead.")


def _live_job(job_id, kind, fn):
    def progress(step, **kw):
        with _lock:
            _jobs[job_id]["progress"].append({"step": step, "at": datetime.now(timezone.utc).isoformat(), **kw})
    box = {}

    def work():
        try:
            box["result"] = fn(progress)
        except Exception as e:  # handled below as a labelled replay
            box["error"] = e
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(DEADLINE[kind])
    country = _jobs[job_id].get("country")
    if t.is_alive():
        _replay(job_id, kind, country, _friendly(TimeoutError()))
    elif "error" in box:
        _replay(job_id, kind, country, _friendly(box["error"]))
    else:
        _set(job_id, status="done", result=box["result"])


def _start(kind, country, fn):
    with _lock:
        for j in _jobs.values():
            if j.get("kind") == kind and j["status"] == "running":
                return {"job_id": j["id"]}   # one live check of each kind at a time
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"id": job_id, "kind": kind, "country": country, "status": "running",
                         "progress": [], "result": None, "error": None}
    prev = threatdeep.last_live(kind, country, REUSE[kind])
    if prev and kind == "refresh":       # investigate reuses inside threatdeep.investigate (same 6 h rule)
        _set(job_id, status="done", result={**prev, "reused": True, "cost_usd": 0.0})
        return {"job_id": job_id}
    if not settings.exa_enabled:
        from .sources.exa_src import ExaUnavailable
        _replay(job_id, kind, country, _friendly(ExaUnavailable()))
        return {"job_id": job_id}
    threading.Thread(target=_live_job, args=(job_id, kind, fn), daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/threatmap/investigate")
def investigate(req: InvestigateRequest):
    """Live Exa Agent run on one country (effort low, known URLs excluded, at most $0.05). New events pass the
    same quote check. Reused for 6 hours per country; a labelled replay if Exa fails."""
    if req.country not in tm.SCOPE:
        raise HTTPException(400, "country not in scope")
    return _start("investigate", req.country, lambda progress: threatdeep.investigate(req.country, progress))


@app.post("/api/threatmap/refresh")
def refresh():
    """'Check the last 24 hours': one pan-European Exa deep search (at most $0.05). A check finished less than
    15 minutes ago is returned again at $0; a labelled replay if Exa fails."""
    return _start("refresh", None, lambda progress: threatdeep.refresh(progress))


@app.get("/api/threatmap/live")
def live():
    """Free: when Exa last checked, what is new since yesterday, and today's live-check spend against its cap."""
    from . import exacall
    runs = [r for r in threatdeep.live_runs() if r.get("status") == "done"]
    last = max(runs, key=lambda r: r.get("finished_at", ""), default=None)
    evs = threatstore.events()
    return {"exa": settings.exa_enabled, "last_checked": last.get("finished_at") if last else None,
            "last_kind": last.get("kind") if last else None, "last_cost_usd": last.get("cost_usd") if last else None,
            "last_country": last.get("country") if last else None,
            "new_since_yesterday": sum(1 for e in evs if e.get("new")),
            "spent_today_usd": exacall.spent(day=exacall.today(), trigger="click"),
            "cap_today_usd": exacall.CLICK_DAILY_CAP, "cap_click_usd": exacall.CLICK_CAP,
            "saved_at": threatstore.saved_at()}


@app.middleware("http")
async def revalidate_static(request, call_next):
    """The page, scripts and styles change often: make browsers check for a newer copy every time
    (a cheap 304 when unchanged), so a stale app.js never sits behind new HTML."""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def _versioned(match):
    """/static/app.js -> /static/app.js?v=<last change>, so a browser can never run an old cached copy."""
    path = match.group(2)
    f = ROOT / path.lstrip("/")
    return f'{match.group(1)}="{path}?v={int(f.stat().st_mtime)}"' if f.is_file() else match.group(0)


@app.get("/")
def index():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    html = re.sub(r'(src|href)="(/static/[\w./-]+\.(?:js|css))"', _versioned, html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

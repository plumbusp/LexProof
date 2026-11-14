"""HTTP API. Analysis runs as a background job; the UI polls for progress."""
import re
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cache, generate, pipeline, threatstore
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
    def progress(step):
        with _lock:
            _jobs[job_id]["progress"].append({"step": step, "at": datetime.now(timezone.utc).isoformat()})
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


@app.get("/api/sample")
def sample():
    return {"text": (ROOT / "samples" / "foi_request.txt").read_text(encoding="utf-8")}


class GenerateRequest(BaseModel):
    area: str
    country: str = "UK"


@app.get("/api/generate/options")
def generate_options():
    return generate.options()


@app.post("/api/generate")
def generate_letter(req: GenerateRequest):
    try:
        return generate.generate(req.area, req.country)
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
    if layer not in ("hostile", "precursor"):
        raise HTTPException(400, "layer must be hostile or precursor")
    for m in (frm, to):
        if m is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m):
            raise HTTPException(400, "from/to must look like 2026-10")
    if not months:
        raise HTTPException(404, "no saved map data (data/threatmap is empty)")
    a, b = frm or months[0], to or months[-1]
    if a > b:
        a, b = b, a
    return threatstore.query(a, b, layer, None if types is None else [t for t in types.split(",") if t])


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


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")

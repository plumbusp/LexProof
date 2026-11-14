"""HTTP API. Analysis runs as a background job; the UI polls for progress."""
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cache, pipeline, taxonomy
from .config import ROOT, settings

app = FastAPI(title="Complaint citation checker", version="0.2")
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
    return taxonomy.view_schema(settings.ai_enabled, settings.exa_enabled)


@app.get("/api/sample")
def sample():
    return {"text": (ROOT / "samples" / "margaret_ellis.txt").read_text(encoding="utf-8")}


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


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")

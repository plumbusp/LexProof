"""One gateway for every Exa call the map makes: cache by request body, budget caps, cost ledger, raw save.

    resp = exacall.call("contents", "/contents", body, est=0.002, run=exacall.Run(cap=0.50))

1. Cache. The key is sha256(endpoint + canonical JSON body). The full request and response are saved under
   data/threatmap/exa_raw/<kind>/<key>.json; a later call with the same body reads that file at $0
   (ttl=None: forever; ttl=N seconds: only if younger; ttl=0: never read the cache).
2. Caps, checked BEFORE the call with the caller's estimate (Exa's real cost is only known afterwards):
     run cap        passed by the caller (a CLI run or one click)
     click caps     trigger="click": at most CLICK_CAP per click and CLICK_DAILY_CAP for all clicks today
     session cap    the whole ledger since SESSION_START must stay under SESSION_CAP
   Crossing one raises BudgetExceeded and nothing is sent.
3. Ledger. One JSON line per paid call in data/threatmap/ledger.jsonl:
   {ts, kind, endpoint, h, trigger, est_usd, cost_usd (Exa's costDollars.total), request_id, country}.

The legacy raw folders (exa_raw/contents/<sha1(url)>.json, exa_raw/search, exa_raw/agent) stay readable;
this module writes next to them under the same kind names.
"""
import hashlib
import json
import os
import threading
import time
from datetime import date, datetime, timezone

from . import threatmap as tm

SESSION_START = os.getenv("EXA_SESSION_START", "2026-11-15")
SESSION_CAP = float(os.getenv("EXA_SESSION_CAP", "6.00"))
CLICK_CAP = float(os.getenv("EXA_CLICK_CAP", "0.05"))
CLICK_DAILY_CAP = float(os.getenv("EXA_CLICK_DAILY_CAP", "1.00"))

# Fixed prices from docs/admin/pricing, used as the estimate before a call.
PRICE = {"deep": 0.012, "deep_extra": 0.001, "contents_type": 0.001,
         "agent": {"minimal": 0.012, "low": 0.025, "medium": 0.10, "high": 0.50, "xhigh": 1.00}}


class BudgetExceeded(RuntimeError):
    pass


class Run:
    """A spending envelope for one CLI run or one click."""

    def __init__(self, cap: float, trigger: str = "cli"):
        self.cap, self.trigger, self.spent, self.calls = cap, trigger, 0.0, 0


_lock = threading.Lock()
_pending = 0.0        # estimates of calls in flight, so parallel calls cannot jointly overshoot a cap


def raw_dir():
    return tm.OUT_DIR / "exa_raw"


def ledger_path():
    return tm.OUT_DIR / "ledger.jsonl"


def body_hash(endpoint: str, body: dict) -> str:
    canon = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((endpoint + "\n" + canon).encode("utf-8")).hexdigest()[:24]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ledger() -> list[dict]:
    p = ledger_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _cost(r: dict) -> float:
    c = r.get("cost_usd")
    return float(c if c is not None else r.get("est_usd") or 0.0)


def spent(since: str | None = None, trigger: str | None = None, day: str | None = None) -> float:
    """Sum of the ledger: since an ISO date, or on one UTC day, optionally for one trigger."""
    tot = 0.0
    for r in ledger():
        ts = r.get("ts", "")
        if since and ts[:10] < since:
            continue
        if day and ts[:10] != day:
            continue
        if trigger and r.get("trigger") != trigger:
            continue
        tot += _cost(r)
    return round(tot, 6)


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def check(est: float, run: Run | None) -> None:
    """Raises BudgetExceeded if a call costing about `est` would cross any cap. Call with _lock held."""
    total = spent(since=SESSION_START) + _pending
    if total + est > SESSION_CAP:
        raise BudgetExceeded(f"session Exa budget reached (${total:.3f} of ${SESSION_CAP:.2f})")
    if run is not None:
        if run.spent + est > run.cap + 1e-9:
            raise BudgetExceeded(f"run budget reached (${run.spent:.3f} of ${run.cap:.2f})")
        if run.trigger == "click":
            if run.spent + est > CLICK_CAP + 1e-9:
                raise BudgetExceeded(f"per-click budget reached (${run.spent:.3f} of ${CLICK_CAP:.2f})")
            day = spent(day=today(), trigger="click") + _pending
            if day + est > CLICK_DAILY_CAP:
                raise BudgetExceeded(f"daily budget for live checks reached (${day:.3f} of ${CLICK_DAILY_CAP:.2f})")


def _cache_file(kind: str, h: str):
    return raw_dir() / kind / f"{h}.json"


def cached(kind: str, endpoint: str, body: dict, ttl: float | None = None) -> dict | None:
    f = _cache_file(kind, body_hash(endpoint, body))
    if not f.exists() or ttl == 0:
        return None
    if ttl is not None and time.time() - f.stat().st_mtime > ttl:
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))["response"]
    except (ValueError, KeyError):
        return None


def _client():
    from .sources.exa_src import _exa
    return _exa()


def _append_ledger(row: dict) -> None:
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def record(kind: str, endpoint: str, h: str, est: float, resp: dict, run: Run | None, country: str | None = None,
           request_id: str | None = None) -> float:
    cost = (resp.get("costDollars") or {}).get("total") if isinstance(resp, dict) else None
    row = {"ts": _now(), "kind": kind, "endpoint": endpoint, "h": h, "trigger": run.trigger if run else "cli",
           "est_usd": est, "cost_usd": cost, "request_id": request_id or (resp.get("requestId") or resp.get("id") if isinstance(resp, dict) else None),
           "country": country}
    _append_ledger(row)
    c = cost if cost is not None else est
    if run is not None:
        run.spent += c
        run.calls += 1
    return c


def save(kind: str, h: str, endpoint: str, body: dict, resp) -> None:
    d = raw_dir() / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{h}.json").write_text(json.dumps({"endpoint": endpoint, "request": body, "response": resp,
                                             "fetched_at": _now()}, ensure_ascii=False, indent=1, default=str),
                                 encoding="utf-8")


def call(kind: str, endpoint: str, body: dict, est: float, run: Run | None = None, ttl: float | None = None,
         country: str | None = None, client=None) -> dict:
    """POST one Exa request through the cache, the caps and the ledger. Returns Exa's JSON (camelCase)."""
    global _pending
    hit = cached(kind, endpoint, body, ttl)
    if hit is not None:
        return hit
    h = body_hash(endpoint, body)
    with _lock:
        check(est, run)
        _pending += est
    try:
        resp = (client or _client()).request(endpoint, body)
    finally:
        with _lock:
            _pending -= est
    save(kind, h, endpoint, body, resp)
    with _lock:
        record(kind, endpoint, h, est, resp, run, country)
    return resp


def agent(body: dict, run: Run | None = None, country: str | None = None, on_progress=None, client=None,
          max_wait: float = 600, poll: float = 4) -> dict:
    """One Exa Agent run (POST /agent/runs, then polls GET /agent/runs/{id}). The estimate is the fixed effort price.
    The full run is saved and its cost logged when it ends (even if it failed)."""
    global _pending
    est = PRICE["agent"].get(body.get("effort", "low"), 0.10)
    cl = client or _client()
    h = body_hash("/agent/runs", body)
    with _lock:
        check(est, run)
        _pending += est
    say = on_progress or (lambda *_a, **_k: None)
    try:
        r = cl.request("/agent/runs", body)
        say("agent_started", run_id=r.get("id"))
        t0 = time.time()
        while r.get("status") in ("queued", "running") and time.time() - t0 < max_wait:
            time.sleep(poll)
            r = cl.request(f"/agent/runs/{r['id']}", method="GET")
            say("agent_" + str(r.get("status")), seconds=int(time.time() - t0))
    finally:
        with _lock:
            _pending -= est
    save("agent", h, "/agent/runs", body, r)
    with _lock:
        record("agent", "/agent/runs", h, est, r, run, country, request_id=r.get("id"))
    return r


def summary(run: Run | None = None) -> str:
    s = f"ledger since {SESSION_START}: ${spent(since=SESSION_START):.4f} of ${SESSION_CAP:.2f}"
    if run is not None:
        s = f"this run ${run.spent:.4f} ({run.calls} paid calls) · " + s
    return s


if __name__ == "__main__":
    rows = ledger()
    by = {}
    for r in rows:
        by[r["kind"]] = by.get(r["kind"], 0.0) + _cost(r)
    print(summary(), {k: round(v, 4) for k, v in by.items()}, f"{len(rows)} paid calls",
          f"clicks today ${spent(day=today(), trigger='click'):.4f}")

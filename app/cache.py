"""Tiny SQLite key/value cache (HTTP fetches, Exa calls) and job store."""
import hashlib
import json
import sqlite3
import threading
import time

from .config import settings

_lock = threading.Lock()
_conn = sqlite3.connect(settings.data_dir / "cache.sqlite3", check_same_thread=False)
_conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, t REAL)")
_conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, v TEXT, t REAL)")
_conn.commit()


def key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def get(k: str, max_age_s: float):
    with _lock:
        row = _conn.execute("SELECT v, t FROM kv WHERE k=?", (k,)).fetchone()
    if row and time.time() - row[1] < max_age_s:
        return json.loads(row[0])
    return None


def put(k: str, value) -> None:
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (k, json.dumps(value), time.time()))
        _conn.commit()


def save_job(job_id: str, value) -> None:
    with _lock:
        _conn.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?)", (job_id, json.dumps(value), time.time()))
        _conn.commit()


def load_job(job_id: str):
    with _lock:
        row = _conn.execute("SELECT v FROM jobs WHERE id=?", (job_id,)).fetchone()
    return json.loads(row[0]) if row else None

"""Polite, cached HTTP GET (stdlib only)."""
import threading
import time
import urllib.error
import urllib.request

from .. import cache
from ..config import settings

_lock = threading.Lock()
_last = {"t": 0.0}


def get(url: str, max_age_s: float = 3600 * 24, throttle: bool = True) -> dict:
    """Returns {"status": int, "body": str, "url": str}. Never raises for HTTP errors."""
    k = cache.key("GET", url)
    hit = cache.get(k, max_age_s)
    if hit is not None:
        return {**hit, "cached": True}
    if throttle:
        with _lock:
            wait = settings.legislation_min_interval_s - (time.time() - _last["t"])
            if wait > 0:
                time.sleep(wait)
            _last["t"] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": settings.user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            res = {"status": r.status, "body": r.read().decode("utf-8", "replace"), "url": r.geturl()}
    except urllib.error.HTTPError as e:
        res = {"status": e.code, "body": "", "url": url}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"status": 0, "body": "", "url": url, "error": str(e), "cached": False}
    if res["status"] in (200, 400, 404):
        cache.put(k, res)
    return {**res, "cached": False}

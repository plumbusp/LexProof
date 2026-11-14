"""Exa: the primary discovery/reading engine.

Features used (each surfaces in the UI):
- search with include/exclude domains (source discovery, nearest real rule, provenance, precedent)
- contents for JS-rendered regulator pages (FCA Handbook returns an empty shell to plain GET)
- snapshot_as_of: the page as Exa stored it at/before the incident date (5-month window, paid tier)
- highlights to pull the relevant passage out of long pages
"""
from .. import cache
from ..config import settings

_client = None


class ExaUnavailable(RuntimeError):
    pass


def _exa():
    global _client
    if not settings.exa_enabled:
        raise ExaUnavailable("EXA_API_KEY not set")
    if _client is None:
        from exa_py import Exa
        _client = Exa(api_key=settings.exa_api_key)
    return _client


def _res(r) -> dict:
    return {"url": r.url, "title": getattr(r, "title", None) or "",
            "published_date": (getattr(r, "published_date", None) or "")[:10],
            "snapshot_at": (getattr(r, "snapshot_at", None) or "")[:19],
            "highlights": list(getattr(r, "highlights", None) or []),
            "text": getattr(r, "text", None) or "", "score": getattr(r, "score", None)}


def search(query: str, include_domains=None, exclude_domains=None, num_results=5,
           highlights_query: str | None = None, text_chars: int = 0, max_age_s=3600 * 24) -> list[dict]:
    k = cache.key("exa.search", query, include_domains, exclude_domains, num_results, highlights_query, text_chars)
    hit = cache.get(k, max_age_s)
    if hit is not None:
        return hit
    contents = {"highlights": {"query": highlights_query or query, "maxCharacters": 600}}
    if text_chars:
        contents["text"] = {"maxCharacters": text_chars}
    kwargs = {"num_results": num_results, "contents": contents, "type": "auto"}
    if include_domains:
        kwargs["include_domains"] = include_domains
    if exclude_domains:
        kwargs["exclude_domains"] = exclude_domains
    resp = _exa().search(query, **kwargs)
    out = [_res(r) for r in resp.results]
    cache.put(k, out)
    return out


def contents(url: str, snapshot_as_of: str | None = None, text_chars: int = 30000,
             highlights_query: str | None = None, max_age_s=3600 * 24) -> dict:
    """Returns {"ok": bool, "url", "title", "text", "highlights", "snapshot_at", "error"}."""
    k = cache.key("exa.contents", url, snapshot_as_of, text_chars, highlights_query)
    hit = cache.get(k, max_age_s)
    if hit is not None:
        return hit
    kwargs = {"text": {"maxCharacters": text_chars}}
    if highlights_query:
        kwargs["highlights"] = {"query": highlights_query, "maxCharacters": 800}
    if snapshot_as_of:
        kwargs["snapshot_as_of"] = snapshot_as_of
    try:
        resp = _exa().get_contents([url], **kwargs)
    except ExaUnavailable:
        raise
    except Exception as e:  # API errors (e.g. snapshot not on plan) are data, not crashes
        return {"ok": False, "url": url, "error": str(e)[:300]}
    if resp.results:
        out = {"ok": True, **_res(resp.results[0])}
    else:
        err = ""
        for s in resp.statuses or []:
            err = str(getattr(s, "error", "") or getattr(s, "status", ""))
        out = {"ok": False, "url": url, "error": err or "no content"}
    cache.put(k, out)
    return out

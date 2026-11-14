"""legislation.gov.uk: point-in-time provision text, existence checks, title search.

Behaviour verified against the live site (Nov 2026):
- /{type}/{year}/{num}/{kind}/{id}/{YYYY-MM-DD}/data.xml gives the version in force on that date
- missing provision -> 404; missing Act -> 400 (HTML body, so check status first)
- some inserted EU-retained articles carry a trailing dot (UK GDPR 'article/12A.')
"""
import re
import urllib.parse
import xml.etree.ElementTree as ET

from . import http

BASE = "https://www.legislation.gov.uk"
NS = {"leg": "http://www.legislation.gov.uk/namespaces/legislation",
      "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
      "dc": "http://purl.org/dc/elements/1.1/", "dct": "http://purl.org/dc/terms/",
      "atom": "http://www.w3.org/2005/Atom"}
L = "{%s}" % NS["leg"]
SKIP = {L + t for t in ("CommentaryRef", "FootnoteRef", "Commentaries", "Footnotes")}
LEVEL = {L + "P%d" % i: i for i in range(1, 7)}
HAS_VERSION = "http://purl.org/dc/terms/hasVersion"


def _flat(el):
    parts = [el.text or ""]
    for c in el:
        if c.tag not in SKIP:
            parts.append(_flat(c))
        parts.append(c.tail or "")
    return "".join(parts)


def _inline(el):
    return re.sub(r"\s+", " ", _flat(el)).strip()


def _walk(el, lines, pending=None):
    for c in el:
        if c.tag in SKIP or c.tag == L + "Title":
            continue
        if c.tag == L + "Pnumber":
            lvl = LEVEL.get(el.tag, 1)
            n = _inline(c)
            if lvl > 1:
                n = c.get("PuncBefore", "(") + n + c.get("PuncAfter", ")")
            pending = ("  " * (lvl - 1), n)
        elif c.tag == L + "Text":
            ind, n = pending or ("", "")
            body = _inline(c)
            lines.append(f"{ind}{n} {body}".rstrip() if n else f"{ind}{body}")
            pending = None
        else:
            pending = _walk(c, lines, pending)
    return pending


def parse_provision_xml(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    md = root.find("ukm:Metadata", NS)
    grp = root.find(".//leg:P1group", NS)
    blocks = root.findall(".//leg:P1", NS) or ([grp] if grp is not None else [])
    lines = []
    for b in blocks:
        _walk(b, lines)
    holder = grp if grp is not None else (blocks[0] if blocks else root)
    versions = [a.get("title") for a in md.findall("atom:link", NS) if a.get("rel") == HAS_VERSION] if md is not None else []
    return {
        "act_title": md.findtext("dc:title", namespaces=NS) if md is not None else "",
        "heading": _inline(grp.find("leg:Title", NS)) if grp is not None and grp.find("leg:Title", NS) is not None else "",
        "version_start": holder.get("RestrictStartDate", ""),
        "version_end": holder.get("RestrictEndDate", ""),
        "versions": [v for v in versions if v],
        "text": "\n".join(lines),
    }


def provision_url(leg_type, year, number, kind, pid, as_of=None):
    u = f"{BASE}/{leg_type}/{year}/{number}/{kind}/{pid}"
    return f"{u}/{as_of}" if as_of else u


def fetch_provision(leg_type, year, number, kind, pid, as_of=None) -> dict:
    """status: ok | not_in_force_on_date | provision_not_found | act_not_found | error"""
    attempts = []
    for pid_try in (pid, pid + "."):
        for date in ([as_of, None] if as_of else [None]):
            url = provision_url(leg_type, year, number, kind, pid_try, date)
            r = http.get(url + "/data.xml")
            attempts.append({"url": url, "http_status": r["status"]})
            if r["status"] == 200:
                data = parse_provision_xml(r["body"])
                status = "ok" if (date or not as_of) else "not_in_force_on_date"
                changed = bool(as_of) and any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and v > as_of
                                              for v in data["versions"])
                out = {"status": status, "url": provision_url(leg_type, year, number, kind, pid_try),
                       "as_of_url": url, "as_of": date or "", "changed_since_event": changed,
                       "attempts": attempts, **data}
                if changed and date:
                    cur = http.get(provision_url(leg_type, year, number, kind, pid_try) + "/data.xml")
                    if cur["status"] == 200:
                        out["current_text"] = parse_provision_xml(cur["body"])["text"]
                return out
            if r["status"] == 400:
                return {"status": "act_not_found", "attempts": attempts}
            if r["status"] == 0:
                return {"status": "error", "attempts": attempts}
    return {"status": "provision_not_found", "attempts": attempts}


def act_exists(leg_type, year, number) -> dict:
    url = f"{BASE}/{leg_type}/{year}/{number}"
    r = http.get(url)
    title = ""
    m = re.search(r"<title>(.*?)</title>", r["body"] or "", re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
    return {"exists": r["status"] == 200, "http_status": r["status"], "url": url, "page_title": title}


def title_search(title: str) -> list[dict]:
    """Exact-ish title lookup via the Atom search feed."""
    q = re.sub(r"\s+\d{4}\s*$", "", title).strip()
    r = http.get(f"{BASE}/all/data.feed?title={urllib.parse.quote(q)}")
    if r["status"] != 200:
        return []
    try:
        root = ET.fromstring(r["body"])
    except ET.ParseError:
        return []
    out = []
    for e in root.findall("atom:entry", NS):
        ident = e.findtext("atom:id", default="", namespaces=NS)
        m = re.search(r"/id/([a-z]+)/(.+)/(\d+)$", ident)
        if m:
            out.append({"title": e.findtext("atom:title", default="", namespaces=NS),
                        "type": m.group(1), "year": m.group(2), "number": m.group(3),
                        "url": f"{BASE}/{m.group(1)}/{m.group(2)}/{m.group(3)}"})
    return out

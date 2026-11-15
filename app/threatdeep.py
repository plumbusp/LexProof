"""Regional Abuse Map, Exa-native pipeline: Exa finds, reads and structures; code verifies and levels.

Four Exa features, each feeding the same saved files the map already reads
(data/threatmap/exa_results.jsonl + extractions.jsonl), so the store and the page need no new format:

1. sweep       /search type="deep" per country and layer, in the country's OWN languages
               (additional_queries), localised (user_location), news-only, date-filtered,
               with an output_schema so Exa returns structured events plus field-level grounding.
2. verify      /contents on every URL an event cites, through app/pages.py: ONE call per URL ever
               (text + an English summary schema). The event's quote must really be in the
               ORIGINAL text (no source, no quote, no dot - plan rule 1).
3. corroborate /search type="deep-lite" seeded with each hostile event's headline, excluding its
               own site, +-21 days: other outlets reporting the same thing. A second site is what
               lets an event reach level 4 (threatmap.level pools domains per country and type).
4. investigate Exa Agent API (/agent/runs): an autonomous multi-step research run on one country,
               triggered from the map's side panel. Same schema, same verification.

5. live       'Check the last 24 hours' (refresh): one pan-European deep search, 24 h window, new URLs only.

Every call goes through app/exacall.py (request cache, budget caps, ledger.jsonl).
Exa only READS and fills fields. Code still decides validity, scope and the evidence level (plan rule 2).

  python -m app.threatdeep --sweep [--country Latvia] [--since 2025-01] [--cap 1]   back-fill (~$0.025 a call + pages)
  python -m app.threatdeep --incremental [--cap 1]       only since the last successful run per country/layer
  python -m app.threatdeep --refresh                     the last 24 hours (~$0.012-0.022)
  python -m app.threatdeep --investigate Latvia          Exa Agent, effort low (~$0.025 + pages)
  python -m app.threatdeep --corroborate                 spends Exa credits
  python -m app.threatdeep --dry-run                     prints the calls, spends nothing
"""
import argparse
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from . import exacall, pages
from . import threatmap as tm
from . import threatstore as ts
from .threatextract import quote_in_text

PV = 1  # same prompt version as threatextract, so Gemini never re-reads an article Exa already structured

ISO2 = {"Austria": "AT", "Belgium": "BE", "Bulgaria": "BG", "Croatia": "HR", "Cyprus": "CY", "Czechia": "CZ",
        "Denmark": "DK", "Estonia": "EE", "Finland": "FI", "France": "FR", "Germany": "DE", "Greece": "GR",
        "Hungary": "HU", "Ireland": "IE", "Italy": "IT", "Latvia": "LV", "Lithuania": "LT", "Luxembourg": "LU",
        "Malta": "MT", "Netherlands": "NL", "Poland": "PL", "Portugal": "PT", "Romania": "RO", "Slovakia": "SK",
        "Slovenia": "SI", "Spain": "ES", "Sweden": "SE", "United Kingdom": "GB", "Switzerland": "CH",
        "Norway": "NO", "Iceland": "IS", "Liechtenstein": "LI", "Andorra": "AD", "Monaco": "MC",
        "San Marino": "SM", "Vatican": "VA", "Serbia": "RS", "Bosnia and Herzegovina": "BA", "Montenegro": "ME",
        "North Macedonia": "MK", "Albania": "AL", "Moldova": "MD"}

LANGS = {"Austria": ["de"], "Belgium": ["nl", "fr"], "Bulgaria": ["bg"], "Croatia": ["hr"], "Cyprus": ["el"],
         "Czechia": ["cs"], "Denmark": ["da"], "Estonia": ["et", "ru"], "Finland": ["fi"], "France": ["fr"],
         "Germany": ["de"], "Greece": ["el"], "Hungary": ["hu"], "Ireland": ["en"], "Italy": ["it"],
         "Latvia": ["lv", "ru"], "Lithuania": ["lt"], "Luxembourg": ["fr", "de"], "Malta": ["en"],
         "Netherlands": ["nl"], "Poland": ["pl"], "Portugal": ["pt"], "Romania": ["ro"], "Slovakia": ["sk"],
         "Slovenia": ["sl"], "Spain": ["es"], "Sweden": ["sv"], "United Kingdom": ["en"],
         "Switzerland": ["de", "fr", "it"],
         "Norway": ["no"], "Iceland": ["is"], "Liechtenstein": ["de"], "Andorra": ["ca", "es", "fr"],
         "Monaco": ["fr"], "San Marino": ["it"], "Vatican": ["it"], "Serbia": ["sr"],
         "Bosnia and Herzegovina": ["bs", "hr", "sr"], "Montenegro": ["sr"], "North Macedonia": ["mk"],
         "Albania": ["sq"], "Moldova": ["ro", "ru"]}

# Native-language search phrases per layer: [incident: complaints/requests flood, incident: threat emails, precursor].
PHRASES = {
    "en": ["council or agency flooded with coordinated mass complaints or requests",
           "hoax bomb threat emails sent to hundreds of schools or courts",
           "surge in complaints written with ChatGPT or AI overwhelms council or ombudsman"],
    "de": ["Behörden mit massenhaften koordinierten Beschwerden oder Anfragen überflutet",
           "Bombendrohungen per E-Mail an Schulen und Gerichte",
           "Flut von mit KI oder ChatGPT geschriebenen Beschwerden und Widersprüchen bei Behörden"],
    "fr": ["administrations submergées par des plaintes ou demandes coordonnées en masse",
           "alertes à la bombe par e-mail envoyées à des écoles et tribunaux",
           "afflux de réclamations rédigées avec l'IA ou ChatGPT dans les administrations"],
    "nl": ["overheidsinstanties overspoeld met massale gecoördineerde klachten of verzoeken",
           "bommeldingen per e-mail aan scholen en rechtbanken",
           "toename van klachten en bezwaren geschreven met AI of ChatGPT bij gemeenten"],
    "it": ["enti pubblici sommersi da segnalazioni o richieste coordinate di massa",
           "allarmi bomba via email a scuole e tribunali",
           "aumento di reclami scritti con l'intelligenza artificiale o ChatGPT"],
    "es": ["instituciones públicas inundadas de quejas o solicitudes masivas coordinadas",
           "amenazas de bomba por correo electrónico a colegios y juzgados",
           "aumento de reclamaciones escritas con inteligencia artificial o ChatGPT"],
    "pt": ["instituições públicas inundadas com queixas ou pedidos em massa coordenados",
           "ameaças de bomba por e-mail a escolas e tribunais",
           "aumento de reclamações escritas com inteligência artificial ou ChatGPT"],
    "pl": ["urzędy zalane masowymi skoordynowanymi skargami lub wnioskami",
           "fałszywe alarmy bombowe e-mailem do szkół i sądów",
           "fala skarg pisanych przez sztuczną inteligencję lub ChatGPT w urzędach"],
    "cs": ["úřady zahlceny hromadnými koordinovanými stížnostmi nebo žádostmi",
           "výhrůžky bombou e-mailem školám a soudům",
           "nárůst stížností psaných umělou inteligencí nebo ChatGPT na úřadech"],
    "sk": ["úrady zahltené hromadnými koordinovanými sťažnosťami alebo žiadosťami",
           "bombové vyhrážky e-mailom školám a súdom",
           "nárast sťažností písaných umelou inteligenciou alebo ChatGPT"],
    "hu": ["hivatalokat elárasztották tömeges, összehangolt panaszokkal vagy kérelmekkel",
           "bombafenyegetés e-mailben iskoláknak és bíróságoknak",
           "mesterséges intelligenciával vagy ChatGPT-vel írt panaszok megugrása"],
    "ro": ["instituții publice inundate cu plângeri sau cereri coordonate în masă",
           "amenințări cu bombă prin e-mail la școli și instanțe",
           "creștere a plângerilor scrise cu inteligența artificială sau ChatGPT"],
    "bg": ["институции залети с масови координирани жалби или искания",
           "сигнали за бомби по имейл до училища и съдилища",
           "ръст на жалби, написани с изкуствен интелект или ChatGPT"],
    "hr": ["institucije zatrpane masovnim koordiniranim pritužbama ili zahtjevima",
           "dojave o bombi e-poštom školama i sudovima",
           "porast pritužbi napisanih umjetnom inteligencijom ili ChatGPT-om"],
    "sl": ["ustanove zasute z množičnimi usklajenimi pritožbami ali zahtevami",
           "grožnje z bombo po e-pošti šolam in sodiščem",
           "porast pritožb, napisanih z umetno inteligenco ali ChatGPT"],
    "el": ["δημόσιες υπηρεσίες πλημμύρισαν με μαζικές συντονισμένες καταγγελίες ή αιτήματα",
           "απειλές για βόμβα με email σε σχολεία και δικαστήρια",
           "αύξηση καταγγελιών γραμμένων με τεχνητή νοημοσύνη ή ChatGPT"],
    "da": ["myndigheder oversvømmet med koordinerede masseklager eller henvendelser",
           "bombetrusler på e-mail til skoler og domstole",
           "stigning i klager skrevet med kunstig intelligens eller ChatGPT"],
    "sv": ["myndigheter översvämmas av samordnade massanmälningar eller förfrågningar",
           "bombhot via mejl till skolor och domstolar",
           "ökning av klagomål skrivna med AI eller ChatGPT hos kommuner"],
    "fi": ["viranomaiset hukutettu koordinoiduilla joukkovalituksilla tai pyynnöillä",
           "pommiuhkaukset sähköpostilla kouluille ja tuomioistuimille",
           "tekoälyllä tai ChatGPT:llä kirjoitettujen valitusten määrä kasvaa"],
    "et": ["ametiasutused uputatud massiliste koordineeritud kaebuste või taotlustega",
           "pommiähvardused e-kirjaga koolidele ja kohtutele",
           "tehisintellekti või ChatGPT-ga kirjutatud kaebuste kasv"],
    "lv": ["iestādes pārpludinātas ar masveida koordinētām sūdzībām vai iesniegumiem",
           "spridzināšanas draudu e-pasti skolām un tiesām",
           "ar mākslīgo intelektu vai ChatGPT rakstītu sūdzību pieaugums"],
    "lt": ["institucijos užverstos masiniais koordinuotais skundais ar prašymais",
           "grasinimai susprogdinti el. paštu mokykloms ir teismams",
           "dirbtiniu intelektu ar ChatGPT parašytų skundų padaugėjo"],
    "ru": ["госучреждения завалили массовыми скоординированными жалобами и обращениями",
           "письма с угрозами о минировании школ и судов",
           "жалобы, написанные с помощью искусственного интеллекта, перегружают учреждения"],
    "no": ["offentlige etater oversvømmet av koordinerte masseklager eller henvendelser",
           "bombetrusler på e-post til skoler og domstoler",
           "økning i klager skrevet med kunstig intelligens eller ChatGPT"],
    "is": ["opinberar stofnanir yfirfullar af samræmdum fjöldakvörtunum eða beiðnum",
           "sprengjuhótanir í tölvupósti til skóla og dómstóla",
           "aukning kvartana sem skrifaðar eru með gervigreind eða ChatGPT"],
    "sr": ["institucije zatrpane masovnim koordinisanim pritužbama ili zahtevima",
           "dojave o bombi imejlom školama i sudovima",
           "porast pritužbi napisanih veštačkom inteligencijom ili ChatGPT-om"],
    "bs": ["institucije zatrpane masovnim koordiniranim žalbama ili zahtjevima",
           "dojave o bombi putem e-maila školama i sudovima",
           "porast žalbi napisanih umjetnom inteligencijom ili ChatGPT-om"],
    "mk": ["институции преплавени со масовни координирани жалби или барања",
           "дојави за бомба по е-пошта до училишта и судови",
           "пораст на жалби напишани со вештачка интелигенција или ChatGPT"],
    "sq": ["institucionet të përmbytura me ankesa ose kërkesa masive të koordinuara",
           "kërcënime për bombë me email në shkolla dhe gjykata",
           "rritje e ankesave të shkruara me inteligjencë artificiale ose ChatGPT"],
    "ca": ["administracions públiques inundades de queixes o sol·licituds massives coordinades",
           "amenaces de bomba per correu electrònic a escoles i jutjats",
           "augment de queixes escrites amb intel·ligència artificial o ChatGPT"],
}

# What Exa fills. 9 properties in all (the deep output_schema limit is 10, nested ones included).
SCHEMA = {"type": "object", "required": ["events"], "properties": {"events": {"type": "array", "items": {
    "type": "object", "required": ["url", "quote", "type"], "properties": {
        "url": {"type": "string", "description": "URL of the article that reports this event; must be one of the search results"},
        "date": {"type": "string", "description": "YYYY-MM-DD when it happened, else the article's publication date"},
        "type": {"type": "string", "enum": ["T3", "T5", "T6", "AI", "none"],
                 "description": "T3 mass complaints/requests/petitions to public bodies; T5 mass threat or hoax emails to schools, courts, hospitals; "
                                "T6 flooding of newsrooms, fact-checkers or media regulators; AI rise in complaints written with AI by ordinary people; none otherwise"},
        "target": {"type": "string", "description": "bodies targeted and the size as written, a few words in English"},
        "voice": {"type": "string", "enum": ["nobody", "expert", "researchers", "official"],
                  "description": "who says it is hostile or coordinated: official = named government body, minister, police or security service"},
        "attribution": {"type": "string", "enum": ["none", "suspected", "named"],
                        "description": "named = a named state or group is blamed; suspected = suspicion or hypothesis"},
        "kind": {"type": "string", "enum": ["incident", "warning", "forecast"]},
        "quote": {"type": "string", "description": "ONE sentence copied EXACTLY from the article, original language, under 220 characters"}}}}}}

SYSTEM = ("You map reports of public institutions or media in {c} being flooded with mass submissions. "
          "List one event per article. Only events in {c}. Use only what the article says; never guess. "
          "Quotes are copied verbatim in the original language, never translated. "
          "Skip opinion pieces, scams aimed at individuals, single complaints and other countries. "
          "Precursor (type AI) is ordinary people using AI to complain: voice nobody, attribution none.")

QUERY = {"hostile": "News reports that public institutions, schools, courts or media in {c} were flooded with coordinated mass complaints, requests or threat emails",
         "precursor": "News reports that councils, courts, ombudsmen or agencies in {c} receive a surge of complaints written with AI"}


def _month(d: date) -> str:
    return d.strftime("%Y-%m")


def _months_between(start: date, end: date) -> list[str]:
    return [_month(a) for a, _ in tm.months(start, end)]


def additional_queries(country: str, layer: str) -> list[str]:
    idx = [0, 1] if layer == "hostile" else [2]
    return [PHRASES[lang][i] for lang in LANGS[country] for i in idx][:10]


def sweep_request(country: str, layer: str, since: date, today: date | None = None, num_results: int = 25,
                  incremental: bool = False) -> dict:
    """A back-fill (id deep|<since-month>|...) or an incremental window (id inc|<since-day>|...)."""
    today = today or date.today()
    q = QUERY[layer].format(c=country)
    rid = f"inc|{since.isoformat()}|{country}|{layer}" if incremental else f"deep|{_month(since)}|{country}|{layer}"
    return {"id": rid, "via": "exa-deep", "country": country, "group": layer,
            "query": q, "months": _months_between(since, today), "month": _month(today),
            "kwargs": {"type": "deep", "category": "news", "num_results": num_results, "user_location": ISO2[country],
                       "start_published_date": f"{since.isoformat()}T00:00:00.000Z",
                       "end_published_date": f"{today.isoformat()}T23:59:59.999Z",
                       "additional_queries": additional_queries(country, layer),
                       "system_prompt": SYSTEM.format(c=country), "output_schema": SCHEMA,
                       "contents": {"highlights": {"query": q, "maxCharacters": 600}}}}


def sweep_plan(countries=None, layers=("hostile", "precursor"), since: date = date(2025, 1, 1)) -> list[dict]:
    return [sweep_request(c, lay, since) for c in sorted(countries or tm.SCOPE) for lay in layers]


def last_swept() -> dict[tuple[str, str], date]:
    """(country, layer) -> day of the last successful deep sweep (back-fill or incremental) stored for it."""
    out: dict[tuple[str, str], date] = {}
    for r in ts.read_jsonl(tm.RESULTS):
        if str(r.get("id", "")).startswith(("deep|", "inc|")) and r.get("country") and r.get("fetched_at"):
            d = date.fromisoformat(r["fetched_at"][:10])
            k = (r["country"], r.get("group"))
            out[k] = max(out.get(k, d), d)
    return out


def incremental_plan(countries=None, layers=("hostile", "precursor"), today: date | None = None) -> list[dict]:
    """Only the window since the last successful run (minus one day of overlap), 10 results.
    A country/layer never swept gets the full back-fill since Jan 2025 instead. Swept today: nothing to do."""
    today = today or date.today()
    last = last_swept()
    out = []
    for c in sorted(countries or tm.SCOPE):
        for lay in layers:
            d = last.get((c, lay))
            if d is None:
                out.append(sweep_request(c, lay, date(2025, 1, 1), today))
            elif d < today:
                out.append(sweep_request(c, lay, d - timedelta(days=1), today, num_results=10, incremental=True))
    return out


# ---------- reading Exa's answer

def _get(o, k, default=None):
    return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)


def _norm_url(u: str) -> str:
    return pages.norm(u)


def parse_output(output) -> tuple[list[dict], dict[str, str]]:
    """Exa's structured events, and per-event grounding confidence ('events[3]' -> 'high')."""
    content = _get(output, "content") if output is not None else None
    if content is None and output is not None:
        content = _get(output, "structured")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {}
    evs = [e for e in (_get(content, "events") or []) if isinstance(e, dict)] if content else []
    conf = {}
    for g in (_get(output, "grounding") if output is not None else None) or []:
        f = _get(g, "field") or ""
        if f.startswith("events[") and _get(g, "confidence"):
            conf.setdefault(f.split("]")[0] + "]", _get(g, "confidence"))
    return evs, conf


def to_item(ev: dict, country: str, layer: str, via: str, confidence: str | None) -> dict | None:
    """Exa's fields -> the item shape threatextract stores. Code, not Exa, keeps layer and type consistent."""
    t = ev.get("type")
    if t not in ("T3", "T5", "T6", "AI") or (layer == "precursor") != (t == "AI"):
        return None
    pick = lambda k, ok, d: ev.get(k) if ev.get(k) in ok else d
    item = {"layer": layer, "type": t, "countries": [country], "date_start": str(ev.get("date") or "")[:10],
            "target": str(ev.get("target") or "")[:160], "volume": "",
            "voice": pick("voice", ("nobody", "expert", "researchers", "official"), "nobody"),
            "attribution": pick("attribution", ("none", "suspected", "named"), "none"),
            "ai_use": "yes" if t == "AI" else "unknown",
            "kind": pick("kind", ("incident", "warning", "forecast"), "incident"),
            "weak_source": False, "quote": str(ev.get("quote") or "").strip(), "via": via}
    if confidence:
        item["confidence"] = confidence
    if layer == "precursor":
        item["voice"], item["attribution"] = "nobody", "none"   # a surge of real complaints is never hostile (plan rule 6)
    return item


def fetch_pages(client, urls: list[str], run: exacall.Run | None = None, quotes: dict | None = None,
                with_summary: bool = False) -> dict[str, dict]:
    """Kept for callers: page text via pages.get (one /contents per URL ever). url key = pages.norm."""
    return pages.get(urls, quotes, run=run, with_summary=with_summary, client=client)


def verify_events(raw: list[dict], conf: dict, results: list[dict], country: str | None, layer: str | None, via: str,
                  run: exacall.Run | None = None, client=None, max_new: int | None = None, skip_urls: set | None = None
                  ) -> tuple[list[dict], dict[str, list[dict]], list[dict]]:
    """Keeps an Exa event only if its URL is one of the results and its quote is on the fetched page.
    country/layer None: taken from the event (country field) and its type (AI -> precursor).
    Each URL's page comes from pages.get: text for the check plus Exa's English fields, ONE call per URL ever.
    Returns (articles for exa_results, url -> verified items, dropped [{url, reason}])."""
    by_url = {pages.norm(r["url"]): r for r in results}
    skip = {pages.norm(u) for u in (skip_urls or ())}
    cands, dropped = [], []
    for n, ev in enumerate(raw):
        u = pages.norm(ev.get("url"))
        c = country or ev.get("country")
        lay = layer or ("precursor" if ev.get("type") == "AI" else "hostile")
        if c not in tm.SCOPE:
            dropped.append({"url": ev.get("url"), "reason": "outside the 42 countries"})
            continue
        item = to_item(ev, c, lay, via, conf.get(f"events[{n}]"))
        if not item:
            dropped.append({"url": ev.get("url"), "reason": "not one of the four report types"})
        elif u in skip:
            dropped.append({"url": ev.get("url"), "reason": "already on the map"})
        elif u not in by_url:
            dropped.append({"url": ev.get("url"), "reason": "URL not among Exa's results"})
        elif not item["quote"]:
            dropped.append({"url": ev.get("url"), "reason": "no quote"})
        else:
            cands.append((u, item))
    urls = list(dict.fromkeys(by_url[u]["url"] for u, _ in cands))
    if max_new is not None and len(urls) > max_new:
        keep = set(pages.norm(x) for x in urls[:max_new])
        for u, _ in cands:
            if u not in keep:
                dropped.append({"url": by_url[u]["url"], "reason": "over the per-check page limit"})
        cands = [(u, it) for u, it in cands if u in keep]
        urls = urls[:max_new]
    quotes = {}
    for u, it in cands:
        quotes.setdefault(by_url[u]["url"], it["quote"])
    got = pages.get(urls, quotes, run=run, client=client) if urls else {}
    verified: dict[str, list[dict]] = {}
    for u, item in cands:
        r, page = by_url[u], got.get(u)
        if page is None:
            dropped.append({"url": r["url"], "reason": "page could not be opened"})
            continue
        hay = "\n".join([r.get("title", ""), *r.get("highlights", []), page.get("title", ""), page.get("text", "")])
        if quote_in_text(item["quote"], hay):
            verified.setdefault(r["url"], []).append(item)
        else:
            dropped.append({"url": r["url"], "reason": "quote not on the page"})
    arts = []
    for r in results:  # the verified quote becomes the stored excerpt, so a re-check against the saved file passes
        items = verified.get(r["url"])
        arts.append({**r, "highlights": ([items[0]["quote"]] if items else []) + list(r.get("highlights", []))})
    return arts, verified, dropped


def event_id(url: str, country: str, typ: str) -> str:
    """The same id threatstore gives the event."""
    return hashlib.sha1(f"{url}|{country}|{typ}".encode()).hexdigest()[:10]


def store_run(req: dict, arts: list[dict], verified: dict, cost: float | None, extra: dict | None = None) -> int:
    """Appends one exa_results row and one extractions row per verified URL. Skips URLs already extracted.
    New items carry found_at (when Exa found them), so the map can mark what is new."""
    done = {r["url"] for r in ts.read_jsonl(ts.EXTRACTIONS)}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {k: req[k] for k in ("id", "month", "months", "country", "group", "query", "via") if k in req}
    row.update({"params": {k: v for k, v in req.get("kwargs", {}).items() if k != "output_schema"},
                "fetched_at": now, "cost_usd": cost, "results": arts, **(extra or {})})
    tm.OUT_DIR.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        with tm.RESULTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        n = 0
        with ts.EXTRACTIONS.open("a", encoding="utf-8") as f:
            for url, items in verified.items():
                if url in done:
                    continue
                items = [{**it, "found_at": now} for it in items]
                f.write(json.dumps({"url": url, "pv": PV, "items": items, "extracted_at": now, "found_at": now,
                                    "via": req["via"]}, ensure_ascii=False) + "\n")
                n += len(items)
    ts.clear_caches()
    return n


_write_lock = threading.Lock()


def _res(r) -> dict:
    return {"url": _get(r, "url"), "title": _get(r, "title") or "",
            "published_date": (_get(r, "publishedDate") or _get(r, "published_date") or "")[:10],
            "highlights": list(_get(r, "highlights") or [])}


RAW_DIR = tm.OUT_DIR / "exa_raw"


def _plain(o):
    """SDK response objects -> plain JSON (dataclasses / pydantic / objects with __dict__)."""
    if isinstance(o, (str, int, float, bool)) or o is None:
        return o
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if hasattr(o, "model_dump"):
        return _plain(o.model_dump())
    if hasattr(o, "__dict__"):
        return {k: _plain(v) for k, v in vars(o).items() if not k.startswith("_")}
    return str(o)


def _camel(k: str) -> str:
    a, *b = k.split("_")
    return a + "".join(x.title() for x in b)


def search_body(req: dict) -> dict:
    """The /search JSON body for a request (camelCase, as the SDK would send it)."""
    return {"query": req["query"], **{_camel(k): v for k, v in req["kwargs"].items()}}


def search_est(req: dict) -> float:
    n = req["kwargs"].get("num_results", 10)
    return exacall.PRICE["deep"] + max(0, n - 10) * exacall.PRICE["deep_extra"]


def run_search(client, req: dict, run: exacall.Run | None = None, ttl: float | None = None) -> tuple[int, float, int]:
    """One deep (or deep-lite) search -> verified events stored. Returns (events kept, cost, events Exa proposed)."""
    before = run.spent if run else 0.0
    resp = exacall.call("search", "/search", search_body(req), search_est(req), run=run, ttl=ttl,
                        country=req.get("country"), client=client)
    results = [_res(r) for r in resp.get("results") or []]
    raw, conf = parse_output(resp.get("output"))
    arts, verified, _ = verify_events(raw, conf, results, req["country"], req["group"], req["via"], run=run, client=client)
    cost = (resp.get("costDollars") or {}).get("total") or 0.0
    kept = store_run(req, arts, verified, cost, {"proposed": len(raw)})
    return kept, (run.spent - before) if run else cost, len(raw)


def _done_ids() -> set[str]:
    return {r["id"] for r in ts.read_jsonl(tm.RESULTS)}


def run_many(reqs: list[dict], workers: int = 4, cap: float = 2.0) -> exacall.Run:
    """Runs searches not stored yet, inside a hard budget (searches + page checks). Prints the ledger after each."""
    from .sources.exa_src import _exa
    client, done = _exa(), _done_ids()
    todo = [r for r in reqs if r["id"] not in done]
    run = exacall.Run(cap)
    print(f"{len(reqs) - len(todo)} already stored, {len(todo)} Exa calls to make (run cap ${cap:.2f})", flush=True)
    kept = proposed = 0
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(run_search, client, r, run): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                k, c, p = f.result()
            except exacall.BudgetExceeded as e:
                print(f"  STOP {r['id']}: {e}", flush=True)
                continue
            except Exception as e:  # not stored, so the next run retries it
                print(f"  ERROR {r['id']}: {str(e)[:200]}", flush=True)
                continue
            kept, proposed = kept + k, proposed + p
            print(f"  {r['country']:<22} {r['group']:<10} Exa proposed {p:>2}, verified {k:>2}  · {exacall.summary(run)}", flush=True)
    print(f"done: Exa proposed {proposed} events, {kept} passed the quote check · {exacall.summary(run)}")
    return run


# ---------- corroboration: other sites reporting the same event

def corroborate_plan(max_level: int = 3) -> list[dict]:
    """One deep-lite search per hostile event at level 2-3 that has only one source site."""
    evs = tm.with_levels([e for e in ts.events() if e["layer"] == "hostile" and e["type"] != "T6"])
    reqs = []
    for e in evs:
        if not (2 <= e["level"] <= max_level) or len(e["domains"]) > 1:
            continue
        d = date.fromisoformat(e["date_start"])
        q = f"{e['title'] or e['target']} ({e['country']})"
        reqs.append({"id": f"corr|{e['id']}", "via": "exa-corroborate", "country": e["country"], "group": "hostile",
                     "query": q, "months": [], "month": _month(d),
                     "kwargs": {"type": "deep-lite", "category": "news", "num_results": 10,
                                "user_location": ISO2[e["country"]], "exclude_domains": [e["domain"]],
                                "start_published_date": f"{(d - timedelta(days=21)).isoformat()}T00:00:00.000Z",
                                "end_published_date": f"{(d + timedelta(days=21)).isoformat()}T23:59:59.999Z",
                                "system_prompt": SYSTEM.format(c=e["country"]) + f" Only reports of this same event: {e['target']}.",
                                "output_schema": SCHEMA,
                                "contents": {"highlights": {"query": q, "maxCharacters": 600}}}})
    return reqs


# ---------- live runs (the map's two buttons): saved, reusable, replayable

def live_file():
    return tm.OUT_DIR / "live_runs.jsonl"


def save_live(kind: str, result: dict) -> None:
    with _write_lock:
        with live_file().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, **result}, ensure_ascii=False) + "\n")


def live_runs() -> list[dict]:
    """Saved live runs; on a deploy without data/, the copy in the snapshot (so a replay still has real runs)."""
    if live_file().exists():
        return ts.read_jsonl(live_file())
    if ts.SNAPSHOT.exists():
        return ts._snapshot_view(ts._stamp(ts.SNAPSHOT)).get("live_runs", [])
    return []


def last_live(kind: str, country: str | None = None, max_age_s: float | None = None) -> dict | None:
    """The newest real (not replayed, not reused) run of this kind, optionally for one country and not too old."""
    best = None
    for r in live_runs():
        if r.get("kind") != kind or r.get("status") != "done" or (country and r.get("country") != country):
            continue
        if best is None or r.get("finished_at", "") > best.get("finished_at", ""):
            best = r
    if best and max_age_s is not None:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(best["finished_at"])
        if age.total_seconds() > max_age_s:
            return None
    return best


def _events_out(verified: dict) -> list[dict]:
    return [{"id": event_id(u, it["countries"][0], it["type"]), "country": it["countries"][0], "type": it["type"],
             "layer": it["layer"], "url": u, "quote": it["quote"]} for u, items in verified.items() for it in items]


SCHEMA_24H = json.loads(json.dumps(SCHEMA))
_props = SCHEMA_24H["properties"]["events"]["items"]["properties"]
_props.pop("kind")
_props["country"] = {"type": "string", "enum": sorted(ISO2), "description": "the country where it happened"}
SCHEMA_24H["properties"]["events"]["items"]["required"] = ["url", "quote", "type", "country"]
LANGS_24H = ["en", "de", "fr", "pl", "it", "es", "nl", "ro", "cs", "sv"]
SYSTEM_24H = ("You map reports of public institutions or media in Europe being flooded with mass submissions. "
              "List one event per article and give the country where it happened. Use only what the article says; never guess. "
              "Quotes are copied verbatim in the original language, never translated. "
              "Skip opinion pieces, scams aimed at individuals and single complaints.")


def last24h_request(now: datetime | None = None) -> dict:
    """One pan-European deep search over the last 24 h (start rounded down to the hour: two clicks in one hour
    send the same body, so the second is a $0 cache hit)."""
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=24)
    q = "News that public institutions, schools, courts or media in Europe were flooded with coordinated mass complaints, requests or threat emails"
    return {"id": f"24h|{start.isoformat(timespec='minutes')}", "via": "exa-24h", "group": "hostile", "query": q,
            "months": [], "month": _month(now.date()),
            "kwargs": {"type": "deep", "category": "news", "num_results": 10,
                       "start_published_date": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                       "end_published_date": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                       "additional_queries": [PHRASES[lg][n % 2] for n, lg in enumerate(LANGS_24H)],
                       "system_prompt": SYSTEM_24H, "output_schema": SCHEMA_24H,
                       "contents": {"highlights": {"query": q, "maxCharacters": 600}}}}


def known_urls(country: str | None = None) -> list[str]:
    """URLs already on the map (verified), newest first."""
    rows = ts.read_jsonl(ts.EXTRACTIONS)
    out = []
    for r in reversed(rows):
        if country and not any(country in it.get("countries", []) for it in r.get("items", [])):
            continue
        out.append(r["url"])
    return list(dict.fromkeys(out))


def refresh(on_progress=None, run: exacall.Run | None = None, client=None, max_new: int = 5) -> dict:
    """'Check the last 24 hours': one deep search, new URLs only, each quote checked on its page."""
    say = on_progress or (lambda *_a, **_k: None)
    run = run or exacall.Run(exacall.CLICK_CAP, "click")
    req = last24h_request()
    t0 = time.time()
    say("asking", languages=len(LANGS_24H) + 1)
    resp = exacall.call("search", "/search", search_body(req), search_est(req), run=run, ttl=3600, client=client)
    results = [_res(r) for r in resp.get("results") or []]
    raw, conf = parse_output(resp.get("output"))
    say("proposed", n=len(raw))
    known = set(known_urls())
    n_new = len({pages.norm(e.get("url")) for e in raw} - {pages.norm(u) for u in known})
    say("checking", n=min(n_new, max_new))
    arts, verified, dropped = verify_events(raw, conf, results, None, None, req["via"], run=run, client=client,
                                            max_new=max_new, skip_urls=known)
    kept = store_run(req, arts, verified, (resp.get("costDollars") or {}).get("total"), {"proposed": len(raw)})
    out = {"status": "done", "proposed": len(raw), "passed": kept, "dropped": dropped, "events": _events_out(verified),
           "cost_usd": round(run.spent, 4), "seconds": int(time.time() - t0), "window_start": req["kwargs"]["start_published_date"],
           "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    say("checked", passed=kept, dropped=len(dropped))
    save_live("refresh", out)
    return out


# ---------- Exa Agent: an autonomous investigation of one country

INVESTIGATE_DAYS = 60


def investigate_request(country: str, today: date | None = None) -> dict:
    today = today or date.today()
    since = today - timedelta(days=INVESTIGATE_DAYS)
    return {"id": f"agent|{today.isoformat()}|{country}", "via": "exa-agent", "country": country,
            "group": "hostile", "months": _months_between(since, today), "month": _month(today),
            "query": (f"Find news reports published since {since.isoformat()} that public institutions, schools, courts or "
                      f"media in {country} were flooded with coordinated mass complaints, requests, petitions or "
                      f"threat emails. Search in English and in {', '.join(LANGS[country])}. For each, give the article URL "
                      f"and one sentence copied exactly from it. Skip the URLs in the exclusion list: they are already known."),
            "kwargs": {}}


def investigate_body(country: str, effort: str = "low") -> dict:
    req = investigate_request(country)
    excl = [{"url": u} for u in known_urls(country)[:100]]
    body = {"query": req["query"], "systemPrompt": SYSTEM.format(c=country), "outputSchema": SCHEMA, "effort": effort,
            "metadata": {"app": "lexproof", "country": country}}
    if excl:
        body["input"] = {"exclusion": excl}
    return body


def investigate(country: str, on_progress=None, effort: str = "low", run: exacall.Run | None = None, client=None,
                max_new: int = 10, reuse_s: float = 6 * 3600) -> dict:
    """'Ask Exa about <country> now': one Agent run (effort low), known URLs excluded, quotes checked on the page.
    A run for the same country in the last 6 hours is reused at $0."""
    if country not in tm.SCOPE:
        raise ValueError("country not in scope")
    say = on_progress or (lambda *_a, **_k: None)
    prev = last_live("investigate", country, reuse_s)
    if prev:
        say("reused", finished_at=prev["finished_at"])
        return {**prev, "reused": True, "cost_usd": 0.0}
    run = run or exacall.Run(exacall.CLICK_CAP, "click")
    req = investigate_request(country)
    t0 = time.time()
    say("asking", country=country)
    r = exacall.agent(investigate_body(country, effort), run=run, country=country, on_progress=say, client=client,
                      max_wait=300)
    if r.get("status") != "completed":
        raise RuntimeError(f"Exa Agent run ended as {r.get('status')}")
    raw, conf = parse_output(r.get("output"))
    say("proposed", n=len(raw))
    known = set(known_urls(country))
    lay = lambda e: "precursor" if e.get("type") == "AI" else "hostile"
    usable = [e for e in raw if e.get("quote") and to_item(e, country, lay(e), "exa-agent", None)]   # no page call for a dud
    urls = [u for u in dict.fromkeys(e.get("url") for e in usable) if str(u or "").startswith("http")]
    new = [u for u in urls if pages.norm(u) not in {pages.norm(k) for k in known}][:max_new]
    say("checking", n=len(new))
    quotes = {}
    for e in raw:
        if e.get("url") in new and e.get("quote"):
            quotes.setdefault(e["url"], e["quote"])
    got = pages.get(new, quotes, run=run, client=client)
    results = [{"url": u, "title": got[pages.norm(u)].get("title", ""),
                "published_date": got[pages.norm(u)].get("published_date", ""), "highlights": []}
               for u in new if pages.norm(u) in got]
    arts, verified, dropped = verify_events(raw, conf, results, country, None, "exa-agent", run=run, client=client,
                                            skip_urls=known)
    kept = store_run(req, arts, verified, (r.get("costDollars") or {}).get("total"),
                     {"proposed": len(raw), "agent_run_id": r.get("id"), "stop_reason": r.get("stopReason")})
    out = {"status": "done", "country": country, "proposed": len(raw), "passed": kept, "dropped": dropped,
           "events": _events_out(verified), "cost_usd": round(run.spent, 4), "run_id": r.get("id"),
           "seconds": int(time.time() - t0), "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    say("checked", passed=kept, dropped=len(dropped))
    save_live("investigate", out)
    return out


def dry_run() -> None:
    plan = incremental_plan()
    print(f"DRY RUN. {len(plan)} incremental deep searches would run now, e.g.:")
    if plan:
        r = plan[0]
        print(r["id"], json.dumps({k: v for k, v in r["kwargs"].items() if k != "output_schema"}, ensure_ascii=False, indent=1))
    print(f"\n{len(corroborate_plan())} corroboration searches would run on current events.")
    print(exacall.summary())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true", help="back-fill since --since")
    ap.add_argument("--incremental", action="store_true", help="only since the last successful run per country/layer")
    ap.add_argument("--corroborate", action="store_true")
    ap.add_argument("--investigate", metavar="COUNTRY")
    ap.add_argument("--refresh", action="store_true", help="the last 24 hours, all of Europe")
    ap.add_argument("--country", action="append")
    ap.add_argument("--layer", choices=["hostile", "precursor"])
    ap.add_argument("--since", default="2025-01")
    ap.add_argument("--cap", type=float, default=1.0, help="hard Exa budget for this run, $")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    layers = [a.layer] if a.layer else ("hostile", "precursor")
    if a.sweep:
        run_many(sweep_plan(a.country, layers, date.fromisoformat(a.since + "-01")), cap=a.cap)
    elif a.incremental:
        run_many(incremental_plan(a.country, layers), cap=a.cap)
    elif a.corroborate:
        run_many(corroborate_plan(), cap=a.cap)
    elif a.investigate:
        print(json.dumps(investigate(a.investigate, lambda s, **k: print(" ", s, k, flush=True),
                                     run=exacall.Run(min(a.cap, exacall.CLICK_CAP))), ensure_ascii=False, indent=1))
        print(exacall.summary())
    elif a.refresh:
        print(json.dumps(refresh(lambda s, **k: print(" ", s, k, flush=True),
                                 run=exacall.Run(min(a.cap, exacall.CLICK_CAP))), ensure_ascii=False, indent=1))
        print(exacall.summary())
    elif a.dry_run:
        dry_run()
    else:
        ap.print_help()

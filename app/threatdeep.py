"""Regional Abuse Map, Exa-native pipeline: Exa finds, reads and structures; code verifies and levels.

Four Exa features, each feeding the same saved files the map already reads
(data/threatmap/exa_results.jsonl + extractions.jsonl), so the store and the page need no new format:

1. sweep       /search type="deep" per country and layer, in the country's OWN languages
               (additional_queries), localised (user_location), news-only, date-filtered,
               with an output_schema so Exa returns structured events plus field-level grounding.
2. verify      /contents on every URL an event cites: the full page text is fetched and the
               event's quote must really be in it (no source, no quote, no dot - plan rule 1).
3. corroborate /search type="deep-lite" seeded with each hostile event's headline, excluding its
               own site, +-21 days: other outlets reporting the same thing. A second site is what
               lets an event reach level 4 (threatmap.level pools domains per country and type).
4. investigate Exa Agent API (/agent/runs): an autonomous multi-step research run on one country,
               triggered from the map's side panel. Same schema, same verification.

Exa (like Gemini in threatextract) only READS and fills fields. Code still decides validity,
scope and the evidence level (plan rule 2).

  python -m app.threatdeep --sweep [--country Latvia] [--since 2025-01]   spends Exa credits (~$0.02 a call)
  python -m app.threatdeep --corroborate                                  spends Exa credits
  python -m app.threatdeep --investigate Latvia                           spends Exa credits (agent run)
  python -m app.threatdeep --dry-run                                      prints the calls, spends nothing
"""
import argparse
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

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


def sweep_request(country: str, layer: str, since: date, today: date | None = None) -> dict:
    today = today or date.today()
    q = QUERY[layer].format(c=country)
    return {"id": f"deep|{_month(since)}|{country}|{layer}", "via": "exa-deep", "country": country, "group": layer,
            "query": q, "months": _months_between(since, today), "month": _month(today),
            "kwargs": {"type": "deep", "category": "news", "num_results": 25, "user_location": ISO2[country],
                       "start_published_date": f"{since.isoformat()}T00:00:00.000Z",
                       "end_published_date": f"{today.isoformat()}T23:59:59.999Z",
                       "additional_queries": additional_queries(country, layer),
                       "system_prompt": SYSTEM.format(c=country), "output_schema": SCHEMA,
                       "contents": {"highlights": {"query": q, "maxCharacters": 600}}}}


def sweep_plan(countries=None, layers=("hostile", "precursor"), since: date = date(2025, 1, 1)) -> list[dict]:
    return [sweep_request(c, lay, since) for c in sorted(countries or tm.SCOPE) for lay in layers]


# ---------- reading Exa's answer

def _get(o, k, default=None):
    return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)


def _norm_url(u: str) -> str:
    return (u or "").split("#")[0].rstrip("/").lower()


def parse_output(output) -> tuple[list[dict], dict[str, str]]:
    """Exa's structured events, and per-event grounding confidence ('events[3]' -> 'high')."""
    content = _get(output, "content") if output is not None else None
    if content is None:
        content = _get(output, "structured")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {}
    evs = [e for e in (_get(content, "events") or []) if isinstance(e, dict)] if content else []
    conf = {}
    for g in _get(output, "grounding") or []:
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


def fetch_pages(client, urls: list[str]) -> dict[str, dict]:
    """/contents for up to 100 URLs at once: full text for the quote check. url -> {title, text, published_date}."""
    out = {}
    for i in range(0, len(urls), 100):
        try:
            resp = client.get_contents(urls[i:i + 100], text={"maxCharacters": 60000})
        except Exception as e:  # a failed fetch only means those events cannot be verified now
            print(f"  contents failed: {str(e)[:160]}", flush=True)
            continue
        for r in resp.results or []:
            out[_norm_url(r.url)] = {"title": r.title or "", "text": r.text or "",
                                     "published_date": (getattr(r, "published_date", None) or "")[:10]}
    return out


def verify_events(client, raw: list[dict], conf: dict, results: list[dict], country: str, layer: str, via: str
                  ) -> tuple[list[dict], dict[str, list[dict]]]:
    """Keeps an Exa event only if its URL is one of the search results and its quote is on the fetched page.
    Returns (articles for exa_results, url -> verified items)."""
    by_url = {_norm_url(r["url"]): r for r in results}
    cands = []
    for n, ev in enumerate(raw):
        u = _norm_url(ev.get("url"))
        item = to_item(ev, country, layer, via, conf.get(f"events[{n}]"))
        if item and u in by_url and item["quote"]:
            cands.append((u, item))
    pages = fetch_pages(client, sorted({by_url[u]["url"] for u, _ in cands})) if cands else {}
    verified: dict[str, list[dict]] = {}
    for u, item in cands:
        r, page = by_url[u], pages.get(u, {})
        hay = "\n".join([r.get("title", ""), *r.get("highlights", []), page.get("title", ""), page.get("text", "")])
        if quote_in_text(item["quote"], hay):
            verified.setdefault(r["url"], []).append(item)
    arts = []
    for r in results:  # the verified quote becomes the stored excerpt, so a re-check against the saved file passes
        items = verified.get(r["url"])
        arts.append({**r, "highlights": ([items[0]["quote"]] if items else []) + list(r.get("highlights", []))})
    return arts, verified


def store_run(req: dict, arts: list[dict], verified: dict, cost: float | None, extra: dict | None = None) -> int:
    """Appends one exa_results row and one extractions row per verified URL. Skips URLs already extracted."""
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
                f.write(json.dumps({"url": url, "pv": PV, "items": items, "extracted_at": now, "via": req["via"]},
                                   ensure_ascii=False) + "\n")
                n += len(items)
    for fn in (ts._results_index, ts._extracted_count, ts._cached):
        fn.cache_clear()
    return n


_write_lock = threading.Lock()


def _res(r) -> dict:
    return {"url": r.url, "title": getattr(r, "title", None) or "",
            "published_date": (getattr(r, "published_date", None) or "")[:10],
            "highlights": list(getattr(r, "highlights", None) or [])}


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


def save_raw(kind: str, rid: str, req: dict, resp) -> None:
    """Keeps Exa's full answer (every result, the structured output and its grounding) on disk."""
    d = RAW_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(rid.encode()).hexdigest()[:16] + ".json"
    (d / name).write_text(json.dumps({"id": rid, "request": {"query": req.get("query"), **req.get("kwargs", {})},
                                      "response": _plain(resp),
                                      "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                     ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def run_search(client, req: dict) -> tuple[int, float, int]:
    """One deep (or deep-lite) search -> verified events stored. Returns (events kept, cost, events Exa proposed)."""
    resp = client.search(req["query"], **req["kwargs"], timeout=120)
    save_raw("search", req["id"], req, resp)
    results = [_res(r) for r in resp.results]
    raw, conf = parse_output(getattr(resp, "output", None))
    arts, verified = verify_events(client, raw, conf, results, req["country"], req["group"], req["via"])
    cost = getattr(getattr(resp, "cost_dollars", None), "total", None) or 0.0
    kept = store_run(req, arts, verified, cost, {"proposed": len(raw)})
    return kept, cost, len(raw)


def _done_ids() -> set[str]:
    return {r["id"] for r in ts.read_jsonl(tm.RESULTS)}


def run_many(reqs: list[dict], workers: int = 4) -> None:
    from .sources.exa_src import _exa
    client, done = _exa(), _done_ids()
    todo = [r for r in reqs if r["id"] not in done]
    print(f"{len(reqs) - len(todo)} already stored, {len(todo)} Exa calls to make", flush=True)
    kept = proposed = 0
    cost = 0.0
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(run_search, client, r): r for r in todo}
        for f in as_completed(futs):
            r = futs[f]
            try:
                k, c, p = f.result()
            except Exception as e:  # not stored, so the next run retries it
                print(f"  ERROR {r['id']}: {str(e)[:200]}", flush=True)
                continue
            kept, proposed, cost = kept + k, proposed + p, cost + c
            print(f"  {r['country']:<15} {r['group']:<10} Exa proposed {p:>2}, verified {k:>2}  (${c:.3f})", flush=True)
    print(f"done: Exa proposed {proposed} events, {kept} passed the quote check, ${cost:.3f}")


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


# ---------- Exa Agent: an autonomous investigation of one country

def investigate_request(country: str, since: date = date(2025, 1, 1)) -> dict:
    return {"id": f"agent|{date.today().isoformat()}|{country}", "via": "exa-agent", "country": country,
            "group": "hostile", "months": _months_between(since, date.today()), "month": _month(date.today()),
            "query": (f"Find every news report since {since.isoformat()} that public institutions, schools, courts or "
                      f"media in {country} were flooded with coordinated mass complaints, requests, petitions or "
                      f"threat emails. Search in English and in {', '.join(LANGS[country])}. For each, give the article URL "
                      f"and one sentence copied exactly from it."),
            "kwargs": {}}


def investigate(country: str, on_progress=None, effort: str = "low") -> dict:
    """Runs an Exa Agent research run and stores whatever passes the quote check."""
    if country not in tm.SCOPE:
        raise ValueError("country not in scope")
    from .sources.exa_src import _exa
    client, req = _exa(), investigate_request(country)
    say = on_progress or (lambda *_: None)
    run = client.agent.runs.create(query=req["query"], system_prompt=SYSTEM.format(c=country),
                                   output_schema=SCHEMA, effort=effort,
                                   metadata={"app": "lexproof", "country": country})
    say("agent_started", run_id=run.id)
    t0 = time.time()
    while run.status in ("queued", "running") and time.time() - t0 < 600:
        time.sleep(4)
        run = client.agent.runs.get(run.id)
        say("agent_" + run.status, seconds=int(time.time() - t0))
    if run.status != "completed":
        raise RuntimeError(f"agent run {run.status}: {getattr(run.error, 'message', '') or run.stop_reason}")
    raw, conf = parse_output(run.output)
    urls = sorted({e.get("url") for e in raw if str(e.get("url", "")).startswith("http")})
    say("verifying", proposed=len(raw))
    pages = fetch_pages(client, urls)
    results = [{"url": u, "title": pages.get(_norm_url(u), {}).get("title", ""),
                "published_date": pages.get(_norm_url(u), {}).get("published_date", ""), "highlights": []}
               for u in urls if _norm_url(u) in pages]
    arts, verified = verify_events(client, raw, conf, results, country, "hostile", "exa-agent")
    cost = getattr(run.cost_dollars, "total", None) if run.cost_dollars else None
    kept = store_run(req, arts, verified, cost, {"proposed": len(raw), "agent_run_id": run.id,
                                                 "stop_reason": run.stop_reason})
    return {"country": country, "proposed": len(raw), "verified": kept, "cost_usd": cost, "run_id": run.id,
            "seconds": int(time.time() - t0)}


def dry_run() -> None:
    plan = sweep_plan()
    print(f"DRY RUN. {len(plan)} deep searches would run (~$0.017 each = ~${0.017 * len(plan):.2f}), e.g.:")
    r = plan[0]
    print(json.dumps({k: v for k, v in r["kwargs"].items() if k != "output_schema"}, ensure_ascii=False, indent=1))
    print(f"\n{len(corroborate_plan())} corroboration searches would run on current events.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--corroborate", action="store_true")
    ap.add_argument("--investigate", metavar="COUNTRY")
    ap.add_argument("--country", action="append")
    ap.add_argument("--layer", choices=["hostile", "precursor"])
    ap.add_argument("--since", default="2025-01")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.sweep:
        run_many(sweep_plan(a.country, [a.layer] if a.layer else ("hostile", "precursor"),
                            date.fromisoformat(a.since + "-01")))
    elif a.corroborate:
        run_many(corroborate_plan())
    elif a.investigate:
        print(investigate(a.investigate, lambda s, **k: print(" ", s, k, flush=True)))
    elif a.dry_run:
        dry_run()
    else:
        ap.print_help()

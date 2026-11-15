// Regional Abuse Map, UK view. Reads /api/threatmap?layer=relevance, which serves saved data (no Exa call).
// Changing the layer, the month range or the type chips just asks the server for another slice.
// Two buttons ask Exa live (budget-capped, server side): "Check the last 24 hours" and "Ask Exa about <country> now".
// New reports go into the same saved data; if Exa fails the server replays its newest real saved check, labelled.
(() => {
  // Geographic Europe in the data (42 countries) by ISO 3166 numeric id (as in world-atlas).
  // Same list as app/threatmap.py SCOPE (plan rule 4).
  const SCOPE = {
    "040": "Austria", "056": "Belgium", "100": "Bulgaria", "191": "Croatia", "196": "Cyprus",
    "203": "Czechia", "208": "Denmark", "233": "Estonia", "246": "Finland", "250": "France",
    "276": "Germany", "300": "Greece", "348": "Hungary", "372": "Ireland", "380": "Italy",
    "428": "Latvia", "440": "Lithuania", "442": "Luxembourg", "470": "Malta", "528": "Netherlands",
    "616": "Poland", "620": "Portugal", "642": "Romania", "703": "Slovakia", "705": "Slovenia",
    "724": "Spain", "752": "Sweden", "826": "United Kingdom", "756": "Switzerland",
    "578": "Norway", "352": "Iceland", "438": "Liechtenstein", "020": "Andorra", "492": "Monaco",
    "674": "San Marino", "336": "Vatican", "688": "Serbia", "070": "Bosnia and Herzegovina",
    "499": "Montenegro", "807": "North Macedonia", "008": "Albania", "498": "Moldova",
  };
  const SCOPE_ID = Object.fromEntries(Object.entries(SCOPE).map(([id, name]) => [name, id]));
  // Pieces world-atlas draws apart, shown and clicked as their country (world-atlas id or name -> country).
  const ALIAS = {
    "248": "Finland", "234": "Denmark", "833": "United Kingdom", "832": "United Kingdom", "831": "United Kingdom",
    "N. Cyprus": "Cyprus",
  };
  // Geographic Europe outside the data: drawn grey, never clickable, no dots (id or name -> label).
  const NOT_COVERED = { "804": "Ukraine", "643": "Russia", "112": "Belarus", "792": "Turkey", "Kosovo": "Kosovo" };
  const MICRO = 40; // px²: a country smaller than this on screen also gets a clickable dot
  const LIBS = [
    "https://cdn.jsdelivr.net/npm/d3-array@3.2.4/dist/d3-array.min.js",
    "https://cdn.jsdelivr.net/npm/d3-geo@3.1.1/dist/d3-geo.min.js",
    "https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js",
  ];
  const SHAPES = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json";
  const W = 800, H = 760, NS = "http://www.w3.org/2000/svg";
  // Shown by name only. The codes (T3, T5, T6) stay in the data; they come from the plan's taxonomy, where T1, T2 and T4 are unused.
  // Named after the header's two words: threat floods and complaint floods.
  const TYPE_NAMES = { T3: "Coordinated complaints", T5: "Threat emails", T6: "Newsroom floods", AI: "AI-written complaints" };
  const CHIP_GROUPS = [["Threat floods", ["T5", "T3", "T6"]], ["Complaint floods", ["AI"]]];
  const UK = "United Kingdom";

  // Relevance to the UK, 1-5. Set by fixed rules in app/relevance.py from facts; never by AI.
  const REL = {
    5: { name: "Act now", why: "Hitting UK councils, schools or services." },
    4: { name: "Prepare", why: "In the UK but another channel, or a wave abroad that has already reached the UK." },
    3: { name: "Watch", why: "Same kind of pressure next door, a coordinated campaign abroad, or a trend already seen in the UK." },
    2: { name: "Background", why: "The same kind of pressure elsewhere in Europe." },
    1: { name: "Not relevant", why: "Another channel or sector, for example media only." },
  };
  const FLOOD = { T3: "threat", T5: "threat", T6: "media", AI: "complaint" };
  const COUNTRY_NONE = "No reports found in this window. Not proof of safety.";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const monthName = (m) => new Date(m + "-01T00:00:00").toLocaleDateString("en-GB", { month: "short", year: "numeric" });
  const dayName = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

  let boot = "idle";                       // idle | loading | ready
  let layer = "relevance", frm = null, to = null, types = null; // types: null = all, [] = none
  let data = null, selected = null, seq = 0, showAll = false, centroids = {};
  let firstLoad = true;
  let live = null;                         // /api/threatmap/live: last check, spend today, cap
  const jobs = { refresh: null, investigate: null }; // {country, lines: [[icon, html, cls]], status}
  const newIds = new Set();                // reports a live check just added (also flagged e.new by the server)
  const original = new Set(); // ids of events whose quote is shown in its original language
  const langName = (c) => { try { return new Intl.DisplayNames(["en"], { type: "language" }).of(c); } catch { return c; } };

  const loadScript = (src) => new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = () => fail(new Error("could not load " + src));
    document.head.appendChild(s);
  });

  async function init() {
    if (boot !== "idle") return;
    boot = "loading";
    try {
      if (!window.topojson) for (const src of LIBS) await loadScript(src);
      const r = await fetch(SHAPES);
      if (!r.ok) throw new Error("map shapes " + r.status);
      draw(await r.json());
      boot = "ready";
      await load();
    } catch (e) {
      boot = "idle"; // retry the next time the view opens
      $("#map-status").textContent = "The map could not load. It needs an internet connection for the country shapes.";
      console.error("[map]", e);
    }
  }

  // ---------- data
  async function load() {
    const mine = ++seq;
    const q = new URLSearchParams({ layer });
    if (frm) q.set("from", frm);
    if (to) q.set("to", to);
    if (types) q.set("types", types.join(","));
    try {
      const r = await fetch("/api/threatmap?" + q);
      if (!r.ok) throw new Error(r.status === 404 ? "No saved map data found." : `Server said ${r.status}.`);
      const d = await r.json();
      if (mine !== seq) return; // a newer request is already on its way
      if (firstLoad && d.months.length > 3) {   // open on the last 3 months: the fully enriched window
        firstLoad = false;
        frm = d.months[d.months.length - 3]; to = d.months[d.months.length - 1];
        return load();
      }
      firstLoad = false;
      data = d; frm = d.from; to = d.to;
      render();
    } catch (e) {
      if (mine === seq) $("#map-status").textContent = "Could not load map data. " + e.message;
    }
  }

  // ---------- drawing the base map (once)
  function draw(topo) {
    const feats = topojson.feature(topo, topo.objects.countries).features;
    // Frame mainland Europe so overseas territories (e.g. French Guiana) do not shrink the map.
    const frame = { type: "MultiPoint", coordinates: [[-11, 35], [-11, 66], [34, 71], [34, 34], [12, 34], [12, 71]] };
    // clipAngle keeps the far side of the globe from wrapping round the rim; clipExtent cuts shapes at the frame.
    const projection = d3.geoAzimuthalEqualArea().rotate([-10, -52]).clipAngle(90)
      .fitExtent([[12, 12], [W - 12, H - 12]], frame).clipExtent([[0, 0], [W, H]]);
    const path = d3.geoPath(projection);
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Map of Europe");
    svg.innerHTML = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect class="hatch-bg" width="6" height="6"></rect><line class="hatch-line" x1="0" y1="0" x2="0" y2="6"></line></pattern></defs>`;
    const back = document.createElementNS(NS, "g"), shapes = document.createElementNS(NS, "g"), micro = document.createElementNS(NS, "g");
    back.setAttribute("class", "land-layer");
    const titled = (el, text) => { const t = document.createElementNS(NS, "title"); t.textContent = text; el.appendChild(t); return el; };
    for (const f of feats) {
      const d = path(f);
      if (!d) continue; // outside the frame
      const nm = f.properties && f.properties.name, own = SCOPE[f.id], name = own || ALIAS[f.id] || ALIAS[nm];
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      if (!name) { // never class "country": render() looks every .country up in data.countries
        const out = NOT_COVERED[f.id] || NOT_COVERED[nm];
        p.setAttribute("class", out ? "land-out" : "land-bg");
        back.appendChild(titled(p, out ? `${out}: Not covered in this demo` : "Outside the map's scope"));
        continue;
      }
      p.setAttribute("class", "country ns");
      p.dataset.id = SCOPE_ID[name]; // pieces (Åland, Faroe, N. Cyprus...) select their country
      if (own) { p.setAttribute("tabindex", "0"); p.setAttribute("role", "button"); }
      p.appendChild(document.createElementNS(NS, "title"));
      shapes.appendChild(p);
      if (!own) continue; // dots stay on the country's own main piece
      // marker position: centre of the biggest piece, so France is not pulled towards Guiana
      const polys = f.geometry.type === "MultiPolygon" ? f.geometry.coordinates.map((c) => ({ type: "Polygon", coordinates: c })) : [f.geometry];
      const main = polys.reduce((a, b) => (d3.geoArea(b) > d3.geoArea(a) ? b : a));
      const at = (centroids[own] = path.centroid(main));
      if (path.area(f) < MICRO && at.every(Number.isFinite)) { // microstates: a dot you can click, on top of neighbours
        p.removeAttribute("tabindex"); p.removeAttribute("role");
        const c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", at[0]); c.setAttribute("cy", at[1]); c.setAttribute("r", 4);
        c.setAttribute("class", "country ns");
        c.setAttribute("tabindex", "0"); c.setAttribute("role", "button");
        c.dataset.id = f.id;
        c.appendChild(document.createElementNS(NS, "title"));
        micro.appendChild(c);
      }
    }
    const markers = document.createElementNS(NS, "g");
    markers.setAttribute("id", "markers");
    markers.setAttribute("pointer-events", "none");
    svg.append(back, shapes, micro, markers);
    svg.addEventListener("click", (e) => { const p = e.target.closest(".country"); if (p) select(SCOPE[p.dataset.id]); });
    svg.addEventListener("keydown", (e) => {
      const p = e.target.closest(".country");
      if (p && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(SCOPE[p.dataset.id]); }
    });
    $("#map-canvas").replaceChildren(svg);
  }

  // ---------- render from data
  function countryText(name, c) {
    if (!c.searched) return "not searched in this window";
    return c.level ? `Highest relevance to the UK: level ${c.level} · ${REL[c.level].name}. ${REL[c.level].why}` : COUNTRY_NONE;
  }

  function render() {
    renderControls();
    renderLegend();
    const prefix = "r";
    document.querySelectorAll(".country").forEach((p) => {
      const name = SCOPE[p.dataset.id], c = data.countries[name];
      p.setAttribute("class", `country ${c.searched ? prefix + c.level : "ns"}${name === selected ? " selected" : ""}`);
      const text = `${name}: ${countryText(name, c)}`;
      p.setAttribute("aria-label", text);
      p.firstChild.textContent = text;
    });
    const g = $("#markers");
    g.replaceChildren();
    for (const [name, c] of Object.entries(data.countries)) {
      const n = c.events.filter((e) => e.type !== "T6").length, media = c.events.length - n, at = centroids[name];
      if (!at || !c.events.length || !(c.level >= 3 || name === selected)) continue; // numbers only where they matter (level 3+) or on the selected country
      if (n) g.append(marker("circle", at[0], at[1], n));
      if (media) g.append(marker("diamond", at[0] + (n ? 16 : 0), at[1] + (n ? 6 : 0), media));
      if (c.events.some(isNew)) g.append(newMark(at[0], at[1]));
    }
    const span = data.from === data.to ? monthName(data.from) : `${monthName(data.from)} to ${monthName(data.to)}`;
    const sites = data.sites ? ` from ${data.sites} news site${data.sites === 1 ? "" : "s"}` : "";
    $("#map-status").textContent = `${data.n_events} report${data.n_events === 1 ? "" : "s"}${sites} · ${span} · every quote checked on the source page.`;
    $("#map-foot").textContent = `Exa searches the web for these reports${data.saved_at ? `; last run ${dayName(data.saved_at)}` : ""}. AI gives facts. Fixed rules set the level. Reported or warned, not a proven attack.`;
    renderTiers();
    renderSide();
  }

  const isNew = (e) => e.new || newIds.has(e.id);

  // A ring that pulses a few times and a small "NEW" label next to the dot.
  function newMark(x, y) {
    const g = document.createElementNS(NS, "g");
    const ring = document.createElementNS(NS, "circle");
    ring.setAttribute("cx", x); ring.setAttribute("cy", y); ring.setAttribute("r", 12); ring.setAttribute("class", "new-ring");
    const b = document.createElementNS(NS, "g");
    b.setAttribute("class", "new-badge");
    const r = document.createElementNS(NS, "rect");
    r.setAttribute("x", x + 8); r.setAttribute("y", y - 21); r.setAttribute("width", 24); r.setAttribute("height", 11);
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", x + 20); t.setAttribute("y", y - 13); t.setAttribute("text-anchor", "middle"); t.textContent = "NEW";
    b.append(r, t);
    g.append(ring, b);
    return g;
  }

  function marker(kind, x, y, n) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "marker " + kind);
    const shape = document.createElementNS(NS, kind === "circle" ? "circle" : "rect");
    if (kind === "circle") { shape.setAttribute("cx", x); shape.setAttribute("cy", y); shape.setAttribute("r", 8); }
    else { shape.setAttribute("x", x - 8); shape.setAttribute("y", y - 8); shape.setAttribute("width", 16); shape.setAttribute("height", 16); shape.setAttribute("transform", `rotate(45 ${x} ${y})`); }
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", x); t.setAttribute("y", y + 3.5); t.setAttribute("text-anchor", "middle"); t.textContent = n;
    g.append(shape, t);
    return g;
  }

  function renderControls() {
    const opts = (sel) => data.months.map((m) => `<option value="${m}"${m === sel ? " selected" : ""}>${monthName(m)}</option>`).join("");
    $("#map-from").innerHTML = opts(data.from);
    $("#map-to").innerHTML = opts(data.to);
    document.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.layer === layer)));
    const on = types === null ? data.types : types;
    const chip = (t) => `<button type="button" class="chip-btn" data-type="${esc(t)}" aria-pressed="${on.includes(t)}">${esc(TYPE_NAMES[t] || t)}</button>`;
    const grouped = CHIP_GROUPS.flatMap(([, ts]) => ts);
    $("#map-chips").innerHTML = CHIP_GROUPS.map(([label, ts]) => {
      const here = ts.filter((t) => data.types.includes(t));
      return here.length ? `<span class="chip-group"><span class="chip-label">${esc(label)}</span>${here.map(chip).join("")}</span>` : "";
    }).join("") + data.types.filter((t) => !grouped.includes(t)).map(chip).join("");
    $("#map-chips").hidden = !data.types.length;
  }

  function renderLegend() {
    const unsearched = Object.values(data.countries).some((c) => !c.searched);
    $("#legend").innerHTML = [5, 4, 3, 2, 1].map((n) => `<span class="legend-item" title="${esc(REL[n].why)}"><span class="sw r${n}"></span>${n} · ${esc(REL[n].name)}</span>`).join("")
     
      + `<span class="legend-item" title="${esc(COUNTRY_NONE)}"><span class="sw sw-hatch"></span>None found (not proof of safety)</span>`
      + (unsearched ? `<span class="legend-item"><span class="sw sw-ns"></span>Not searched</span>` : "")
      + `<span class="legend-item"><span class="sw sw-out"></span>Not covered</span>`
      + `<span class="legend-item"><span class="sw-dot">2</span>Reports (shown at level 3+)</span>`
      + `<span class="legend-item"><span class="sw-dia"></span>Reports about newsrooms</span>`;
  }

  // ---------- side panel
  function select(name) { selected = name; showAll = false; render(); }

  // Raw titles often end in the site's name ("... | krone.at", "... - BBC News"): drop that tail.
  function cleanTitle(t, dom) {
    let s = String(t || "").replace(/\s+\|\s+[^|]{1,40}$/, "");
    const m = s.match(/\s+[-–—]\s+([^-–—]{1,30})$/);
    const base = String(dom || "").replace(/^www\./, "").split(".")[0].toLowerCase();
    if (m && (m[1].includes(".") || (base.length > 2 && m[1].toLowerCase().replace(/\s+/g, "").includes(base)))) s = s.slice(0, m.index);
    return s.trim() || String(t || "");
  }

  // Card: date + relevance + kind / headline / 2-3 line Exa summary / why (max 4) / agent check / quote / source.
  function eventCard(e) {
    const r = e.rel || 1, kind = FLOOD[e.type] || "threat";
    const headline = e.headline || cleanTitle(e.title_en || e.title, e.domain) || e.target || "Untitled report";
    const fx = e.rel_facts || {};
    const agent = fx.note && e.country !== "United Kingdom"
      ? `<div class="ev-agent fine"><b>Exa Agent:</b> ${esc(fx.note)}${fx.evidence_url ? ` <a href="${esc(fx.evidence_url)}" target="_blank" rel="noopener noreferrer">UK source<span class="icon">open_in_new</span></a>` : ""}</div>` : "";
    return `<article class="ev">
      <div class="ev-top"><span class="ev-date">${esc(dayName(e.date_start))}</span><span class="rel-chip r${r}" title="${esc(REL[r].why)}">Level ${r} · ${esc(REL[r].name)}</span><span class="tag kind-${kind}">${esc(TYPE_NAMES[e.type] || e.type)}</span>${layer === "relevance" && !selected ? `<span class="tag">${esc(e.country)}</span>` : ""}${isNew(e) ? `<span class="tag new-tag" title="Found by a live Exa check in the last 24 hours">New</span>` : ""}</div>
      <h3 class="ev-head"${e.headline ? ` title="${e.headline_by === "exa" ? "Headline by Exa from the article" : "Headline written by AI from the article"}"` : ""}>${esc(headline)}</h3>
      ${e.summary ? `<p class="ev-sum">${esc(e.summary)} <span class="fine">Summary by Exa</span></p>` : ""}
      <div class="ev-tags">${(e.rel_why || []).map((l) => `<span class="tag">${esc(l)}</span>`).join("")}</div>
      ${agent}
      ${quoteBlock(e)}      ${/^https?:\/\//i.test(e.url) ? `<a class="ev-src" href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.domain)}<span class="icon">open_in_new</span></a>` : `<span class="ev-src">${esc(e.domain)}</span>`}
    </article>`;
  }

  // English by default when a translation exists; the original (the text the quote check ran on) is one click away.
  const TR_BY = { exa: "Exa", claude: "Claude", gemini: "Gemini" };
  function quoteBlock(e) {
    if (!e.quote_en) return `<blockquote>“${esc(e.quote)}”</blockquote>`;
    const orig = original.has(e.id), lang = langName(e.lang || "");
    return `<blockquote${orig ? ` lang="${esc(e.lang || "")}"` : ""}>“${esc(orig ? e.quote : e.quote_en)}”</blockquote>
      <div class="ev-tr fine">${orig ? `Original, ${esc(lang)}` : `Translated from ${esc(lang)} by ${TR_BY[e.tr_by] || "Gemini"}`}
        <button type="button" class="linkish" data-orig="${esc(e.id)}" aria-pressed="${orig}">${orig ? "Show English" : "Show original"}</button></div>`;
  }

  function renderTiers() {
    const box = $("#map-tiers");
    if (!box) return;
    box.innerHTML = `<h3>What the levels mean for the UK</h3><dl>${[5, 4, 3, 2, 1].map((n) =>
      `<div><dt><span class="sw r${n}"></span>${n} · ${esc(REL[n].name)}</dt><dd>${esc(REL[n].why)}</dd></div>`).join("")}</dl>
      <p class="fine"><b>Older than 90 days: one level lower</b> (5 becomes 4, 4 becomes 3, and so on; 1 stays 1). Fixed rules set the level from facts. For each report abroad an Exa Agent checks two facts: does it hit a channel UK public bodies run, and did the same wave reach the UK? A "reached the UK" claim counts only if its source page, fetched with Exa, names the UK.</p>`;
  }

  function renderSide() {
    const side = $("#map-side");
    if (!selected) {
      // Two groups: what is hitting the UK now, and waves abroad that an Exa Agent found already reached the UK.
      const all = Object.values(data.countries).flatMap((c) => c.events);
      const newest = (a, b) => (a.date_start < b.date_start ? 1 : -1);
      const here = all.filter((e) => e.country === UK && e.rel === 5).sort(newest);
      const abroad = all.filter((e) => e.country !== UK && e.rel === 4).sort(newest);
      const cap = showAll ? Infinity : 6;
      const group = (title, sub, evs, none) => `<div class="ev-group"><h3>${esc(title)}</h3><p class="fine">${esc(sub)}</p></div>`
        + (evs.length ? evs.slice(0, cap).map(eventCard).join("") : `<div class="empty">${esc(none)}</div>`);
      const more = !showAll && (here.length > cap || abroad.length > cap);
      side.innerHTML = `<div class="section-head"><div><h2>What matters for the UK now</h2><div class="section-sub">Click a country on the map to see all its reports.</div></div></div>
        <div class="ev-list">${group("In the UK now", "Level 5 · Act now: hitting UK councils, schools or services.", here, "Nothing at level 5 in this window.")}
        ${group("Abroad, already reached the UK", "Level 4 · Prepare: the same wave was also reported in the UK (source checked).", abroad, "Nothing at level 4 abroad in this window.")}</div>
        ${more ? `<button class="btn more" type="button" id="more">Show all ${here.length + abroad.length}</button>` : ""}`;
      return;
    }
    const c = data.countries[selected], evs = c.events, shown = showAll ? evs : evs.slice(0, 8);
    side.innerHTML = `<div class="section-head"><div><button type="button" class="linkish back" id="map-back">← Back to what matters for the UK</button><h2>${esc(selected)}</h2><div class="section-sub">${esc(countryText(selected, c))}</div>${askButton(selected)}</div></div>
      ${jobs.investigate && jobs.investigate.country === selected ? stepsHtml(jobs.investigate) : ""}
      <div class="ev-list">${evs.length ? shown.map(eventCard).join("") : `<div class="empty">${c.searched ? "No reports with a source and a quote in this window. Nothing found is not the same as safe." : "This country was not searched in this window."}</div>`}</div>
      ${evs.length > shown.length ? `<button class="btn more" type="button" id="more">Show all ${evs.length}</button>` : ""}`;
  }

  // ---------- controls
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("#more")) { showAll = true; renderSide(); return; }
    if (t.closest("#map-back")) { selected = null; render(); return; }
    if (t.closest("#live-24h")) { runLive("refresh"); return; }
    const ask = t.closest("[data-ask]");
    if (ask) { runLive("investigate", ask.dataset.ask); return; }
    const tr = t.closest("[data-orig]");
    if (tr) { const id = tr.dataset.orig; original.has(id) ? original.delete(id) : original.add(id); renderSide(); return; }
    const seg = t.closest(".seg-btn");
    if (seg && seg.dataset.layer !== layer) { layer = seg.dataset.layer; types = null; load(); return; }
    const chip = t.closest(".chip-btn");
    if (chip) {
      const all = data.types, cur = types === null ? all.slice() : types;
      types = cur.includes(chip.dataset.type) ? cur.filter((x) => x !== chip.dataset.type) : [...cur, chip.dataset.type];
      if (types.length === all.length) types = null;
      load(); return;
    }
    const pre = t.closest("[data-preset]");
    if (pre && data) {
      const n = +pre.dataset.preset, ms = data.months;
      to = ms[ms.length - 1]; frm = n ? ms[Math.max(0, ms.length - n)] : ms[0];
      load();
    }
  });
  $("#map-from").onchange = (e) => { frm = e.target.value; if (to && frm > to) to = frm; load(); };
  $("#map-to").onchange = (e) => { to = e.target.value; if (frm && to < frm) frm = to; load(); };

  // ---------- live Exa checks (the two buttons)
  const clock = (iso) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const usd = (x) => "$" + Number(x || 0).toFixed(3);
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

  function askButton(country) {
    if (live && !live.exa) return "";       // a deploy without an Exa key: no live research button
    const busy = jobs.investigate && jobs.investigate.status === "running";
    return `<button type="button" class="btn ask-exa" data-ask="${esc(country)}"${busy ? " disabled" : ""} title="One Exa Agent run on ${esc(country)}: reports from the last 60 days that are not on the map yet. At most $0.05.">
      <span class="icon">travel_explore</span>Ask Exa about ${esc(country)} now</button>`;
  }

  // lines hold trusted HTML built below (every outside value is escaped where it is put in)
  function stepsHtml(job, id) {
    return `<ol class="live-steps"${id ? ` id="${id}"` : ""} aria-live="polite">${job.lines.map(([icon, html, cls]) =>
      `<li class="${cls || ""}">${icon === "spin" ? `<span class="spin"></span>` : `<span class="icon">${icon}</span>`}<span>${html}</span></li>`).join("")}</ol>`;
  }

  function paintJob(kind) {
    const job = jobs[kind];
    if (kind === "refresh") {
      const box = $("#live-steps");
      if (job) box.outerHTML = stepsHtml(job, "live-steps"); else box.hidden = true;
      const btn = $("#live-24h");
      if (btn) btn.disabled = !!job && job.status === "running";
    } else if (data) {
      renderSide();
    }
  }

  function paintLiveLine() {
    const el = $("#live-line"), btn = $("#live-24h");
    if (!live) { el.textContent = ""; return; }
    if (btn && !live.exa) btn.lastChild.textContent = "Replay last live check";
    const parts = [live.last_checked ? `Last checked by Exa: <b>${esc(clock(live.last_checked))}</b>` : "Not checked live yet"];
    if (live.last_checked && live.last_cost_usd != null) parts.push(`this check cost ${esc(usd(live.last_cost_usd))}`);
    parts.push(`today ${esc(usd(live.spent_today_usd))} of ${esc("$" + Number(live.cap_today_usd).toFixed(2))}`);
    if (live.new_since_yesterday) parts.push(`${esc(plural(live.new_since_yesterday, "new report", "new reports"))} since yesterday`);
    el.innerHTML = parts.join(" · ");
  }

  async function loadLive() {
    try {
      const r = await fetch("/api/threatmap/live");
      if (r.ok) { live = await r.json(); paintLiveLine(); }
    } catch { /* the line just stays empty */ }
  }

  // Server progress steps -> plain lines. Only the last line can still be in progress.
  function lines(kind, country, j) {
    const out = [], who = kind === "refresh" ? "the last 24 hours" : esc(country);
    for (const p of j.progress || []) {
      if (p.step === "asking") out.push(["spin", kind === "refresh" ? `Asking Exa about the last 24 hours, in ${Number(p.languages) || 11} languages…` : `Asking Exa about ${esc(country)}…`]);
      else if (p.step.startsWith("agent_") && p.step !== "agent_started" && p.seconds != null) {
        const msg = `Exa Agent is researching ${esc(country)} (${Number(p.seconds)} s)…`;
        if (out.length && out[out.length - 1][1].startsWith("Exa Agent is researching")) out[out.length - 1][1] = msg; else out.push(["spin", msg]);
      } else if (p.step === "proposed") out.push(["inbox", `Exa proposed ${plural(Number(p.n), "report", "reports")}.`]);
      else if (p.step === "checking") out.push(["spin", `Checking each quote on its page… <span class="fine">(${plural(Number(p.n), "new page", "new pages")})</span>`]);
      else if (p.step === "checked") out.push(["check_circle", `${Number(p.passed)} passed, ${Number(p.dropped)} dropped.`]);
      else if (p.step === "reused") out.push(["history", `Exa already checked ${who} at ${esc(clock(p.finished_at))}. Showing that check, at no new cost.`]);
    }
    return out.map((l, i) => (l[0] === "spin" && (i < out.length - 1 || j.status !== "running") ? ["check", l[1]] : l));
  }

  function finish(kind, country, j) {
    const out = lines(kind, country, j), res = j.result || {};
    const where = kind === "refresh" ? "in the last 24 hours" : `about ${esc(country)} in the last 60 days`;
    if (j.status === "error") {
      out.push(["error", esc(j.error || "Exa did not answer properly."), "live-replay"]);
      return out;
    }
    if (j.status === "replay") {
      out.push(["history", `Replay of a real Exa check from ${esc(clock(res.finished_at))}. ${esc(res.reason || "Exa did not answer in time.")}`, "live-replay"]);
    } else if (res.reused && kind === "refresh") {
      const mins = Math.max(0, Math.round((Date.now() - new Date(res.finished_at)) / 60000));
      out.push(["history", `Exa checked the last 24 hours ${mins ? plural(mins, "minute", "minutes") + " ago" : "just now"}. Showing that check, at no new cost.`]);
    }
    const n = Number(res.passed) || 0;
    const dropped = (res.dropped || []).filter((d) => d.reason !== "already on the map");
    const known = (res.dropped || []).length - dropped.length;
    if ((j.status === "replay" || res.reused) && (res.proposed || 0) > 0)
      out.push(["inbox", `Exa proposed ${plural(Number(res.proposed), "report", "reports")}; ${n} passed the quote check.`]);
    if (dropped.length || known) {
      const why = {};
      for (const d of dropped) why[d.reason] = (why[d.reason] || 0) + 1;
      if (known) why["already on the map"] = known;
      out.push(["block", `<span class="fine">Not added: ${Object.entries(why).map(([r, k]) => `${k} × ${esc(r)}`).join(" · ")}</span>`]);
    }
    out.push(n
      ? ["fiber_new", `${plural(n, "new report", "new reports")} ${where}${j.status === "replay" ? "" : ", marked <b>New</b> on the map"}.`, "live-note"]
      : ["info", `No new reports ${where}. Nothing found is not the same as safe.`, "live-note"]);
    const cost = j.status === "replay" || res.reused ? 0 : res.cost_usd;
    out.push(["payments", `<span class="fine">This check cost ${esc(usd(cost))}.</span>`]);
    return out;
  }

  async function runLive(kind, country) {
    if (jobs[kind] && jobs[kind].status === "running") return;
    const job = (jobs[kind] = { country, status: "running",
      lines: [["spin", kind === "refresh" ? "Asking Exa about the last 24 hours…" : `Asking Exa about ${esc(country)}…`]] });
    paintJob(kind);
    let j;
    try {
      const r = await fetch(kind === "refresh" ? "/api/threatmap/refresh" : "/api/threatmap/investigate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: kind === "refresh" ? "{}" : JSON.stringify({ country }),
      });
      if (!r.ok) throw new Error(String(r.status));
      const { job_id } = await r.json();
      const t0 = Date.now();
      for (;;) {
        const jr = await fetch("/api/jobs/" + job_id);
        if (!jr.ok) throw new Error(String(jr.status));
        j = await jr.json();
        if (j.status !== "running") break;
        const l = lines(kind, country, j);
        if (l.length) job.lines = l;
        paintJob(kind);
        if (Date.now() - t0 > 200000) throw new Error("timeout");
        await new Promise((ok) => setTimeout(ok, 1000));
      }
    } catch {
      j = { status: "error", error: "The live check could not reach the server. The map is unchanged.", progress: [] };
    }
    job.status = j.status;
    job.lines = finish(kind, country, j);
    if (j.status === "done" && j.result) for (const e of j.result.events || []) newIds.add(e.id);
    paintJob(kind);
    await loadLive();
    if (j.status === "done" && j.result && (j.result.events || []).length) await load(); else if (data && kind === "investigate") renderSide();
  }

  document.addEventListener("viewchange", (e) => { if (e.detail === "map") { init(); loadLive(); } });
})();

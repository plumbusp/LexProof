// Regional Abuse Map, UK view. Reads /api/threatmap?layer=relevance, which serves saved data only (no live fetching).
// Changing the layer, the month range or the type chips just asks the server for another slice.
(() => {
  // EU-27 + UK + Switzerland by ISO 3166 numeric id (as in world-atlas).
  // Ukraine, Kosovo, Serbia and Moldova are deliberately not drawn (plan rule 4).
  const SCOPE = {
    "040": "Austria", "056": "Belgium", "100": "Bulgaria", "191": "Croatia", "196": "Cyprus",
    "203": "Czechia", "208": "Denmark", "233": "Estonia", "246": "Finland", "250": "France",
    "276": "Germany", "300": "Greece", "348": "Hungary", "372": "Ireland", "380": "Italy",
    "428": "Latvia", "440": "Lithuania", "442": "Luxembourg", "470": "Malta", "528": "Netherlands",
    "616": "Poland", "620": "Portugal", "642": "Romania", "703": "Slovakia", "705": "Slovenia",
    "724": "Spain", "752": "Sweden", "826": "United Kingdom", "756": "Switzerland",
  };
  const LIBS = [
    "https://cdn.jsdelivr.net/npm/d3-array@3.2.4/dist/d3-array.min.js",
    "https://cdn.jsdelivr.net/npm/d3-geo@3.1.1/dist/d3-geo.min.js",
    "https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js",
  ];
  const SHAPES = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json";
  const W = 800, H = 760, NS = "http://www.w3.org/2000/svg";
  const TYPE_NAMES = { T3: "T3 · Mass complaints", T5: "T5 · Threat emails", T6: "T6 · Media targets", AI: "AI complaints" };

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
    const feats = topojson.feature(topo, topo.objects.countries).features.filter((f) => SCOPE[f.id]);
    // Frame mainland Europe so overseas territories (e.g. French Guiana) do not shrink the map.
    const frame = { type: "MultiPoint", coordinates: [[-11, 35], [-11, 66], [34, 71], [34, 34], [12, 34], [12, 71]] };
    const projection = d3.geoAzimuthalEqualArea().rotate([-10, -52]).fitExtent([[12, 12], [W - 12, H - 12]], frame);
    const path = d3.geoPath(projection);
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", "Map of the EU, the UK and Switzerland");
    svg.innerHTML = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect class="hatch-bg" width="6" height="6"></rect><line class="hatch-line" x1="0" y1="0" x2="0" y2="6"></line></pattern></defs>`;
    const shapes = document.createElementNS(NS, "g");
    for (const f of feats) {
      const d = path(f);
      if (!d) continue;
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      p.setAttribute("class", "country ns");
      p.setAttribute("tabindex", "0");
      p.setAttribute("role", "button");
      p.dataset.id = f.id;
      p.appendChild(document.createElementNS(NS, "title"));
      shapes.appendChild(p);
      // marker position: centre of the biggest piece, so France is not pulled towards Guiana
      const polys = f.geometry.type === "MultiPolygon" ? f.geometry.coordinates.map((c) => ({ type: "Polygon", coordinates: c })) : [f.geometry];
      const main = polys.reduce((a, b) => (d3.geoArea(b) > d3.geoArea(a) ? b : a));
      centroids[SCOPE[f.id]] = path.centroid(main);
    }
    const markers = document.createElementNS(NS, "g");
    markers.setAttribute("id", "markers");
    markers.setAttribute("pointer-events", "none");
    svg.append(shapes, markers);
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
      if (!at || !c.events.length) continue;
      if (n) g.append(marker("circle", at[0], at[1], n));
      if (media) g.append(marker("diamond", at[0] + (n ? 16 : 0), at[1] + (n ? 6 : 0), media));
    }
    const behind = data.extracted < data.articles ? ` Reading articles: ${data.extracted} of ${data.articles} done, so this is incomplete.` : "";
    $("#map-status").textContent = `${data.n_events} report${data.n_events === 1 ? "" : "s"} · ${monthName(data.from)} to ${monthName(data.to)} · ${data.searched_months.length} month${data.searched_months.length === 1 ? "" : "s"} searched.` + behind;
    $("#map-foot").textContent = `Saved news, fetched up to ${data.saved_at ? dayName(data.saved_at) : "an unknown date"}. Reported or warned, not proven attack. Levels are set by fixed rules, not by AI.`;
    renderTiers();
    renderSide();
  }

  function marker(kind, x, y, n) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "marker " + kind);
    const shape = document.createElementNS(NS, kind === "circle" ? "circle" : "rect");
    if (kind === "circle") { shape.setAttribute("cx", x); shape.setAttribute("cy", y); shape.setAttribute("r", 9); }
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
    $("#map-chips").innerHTML = data.types.map((t) => `<button type="button" class="chip-btn" data-type="${esc(t)}" aria-pressed="${on.includes(t)}">${esc(TYPE_NAMES[t] || t)}</button>`).join("");
    $("#map-chips").hidden = !data.types.length;
  }

  function renderLegend() {
    $("#legend").innerHTML = `<span class="legend-item" title="${esc(COUNTRY_NONE)}"><span class="sw sw-hatch"></span>No reports</span>`
      + [1, 2, 3, 4, 5].map((n) => `<span class="legend-item" title="${esc(REL[n].why)}"><span class="sw r${n}"></span>${n} · ${esc(REL[n].name)}</span>`).join("")
      + `<span class="legend-item"><span class="sw sw-ns"></span>not searched</span>`
      + `<span class="legend-item"><span class="sw-dot">n</span>reports</span>`;
  }

  // ---------- side panel
  function select(name) { selected = name; showAll = false; render(); }

  // Card: date + relevance + kind / headline / 2-3 line Exa summary / why (max 4) / agent check / quote / source.
  function eventCard(e) {
    const r = e.rel || 1, kind = FLOOD[e.type] || "threat";
    const headline = e.headline || e.title_en || e.title || e.target || "Untitled report";
    const fx = e.rel_facts || {};
    const agent = fx.note && e.country !== "United Kingdom"
      ? `<div class="ev-agent fine"><b>Exa Agent:</b> ${esc(fx.note)}${fx.evidence_url ? ` <a href="${esc(fx.evidence_url)}" target="_blank" rel="noopener noreferrer">UK source<span class="icon">open_in_new</span></a>` : ""}</div>` : "";
    return `<article class="ev">
      <div class="ev-top"><span class="ev-date">${esc(dayName(e.date_start))}</span><span class="rel-chip r${r}" title="${esc(REL[r].why)}">Level ${r} · ${esc(REL[r].name)}</span><span class="tag kind-${kind}">${esc(TYPE_NAMES[e.type] || e.type)}</span>${layer === "relevance" && !selected ? `<span class="tag">${esc(e.country)}</span>` : ""}</div>
      <h3 class="ev-head"${e.headline ? ` title="Headline written by AI from the article"` : ""}>${esc(headline)}</h3>
      ${e.summary ? `<p class="ev-sum">${esc(e.summary)} <span class="fine">Summary by Exa</span></p>` : ""}
      <div class="ev-tags">${(e.rel_why || []).map((l) => `<span class="tag">${esc(l)}</span>`).join("")}</div>
      ${agent}
      ${quoteBlock(e)}      ${/^https?:\/\//i.test(e.url) ? `<a class="ev-src" href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">${esc(e.domain)}<span class="icon">open_in_new</span></a>` : `<span class="ev-src">${esc(e.domain)}</span>`}
    </article>`;
  }

  // English by default when Gemini translated it; the original (the text the quote check ran on) is one click away.
  function quoteBlock(e) {
    if (!e.quote_en) return `<blockquote>“${esc(e.quote)}”</blockquote>`;
    const orig = original.has(e.id), lang = langName(e.lang || "");
    return `<blockquote${orig ? ` lang="${esc(e.lang || "")}"` : ""}>“${esc(orig ? e.quote : e.quote_en)}”</blockquote>
      <div class="ev-tr fine">${orig ? `Original, ${esc(lang)}` : `Translated from ${esc(lang)} by ${e.tr_by === "claude" ? "Claude" : "Gemini"}`}
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
      const top = data.top || [];
      side.innerHTML = `<div class="section-head"><div><h2>What matters for the UK now</h2><div class="section-sub">Reports at level 4 and 5 in this window. Select a country on the map to see all its reports.</div></div></div>
        <div class="ev-list">${top.length ? top.map(eventCard).join("") : `<div class="empty">Nothing at level 4 or 5 in this window.</div>`}</div>`;
      return;
    }
    const c = data.countries[selected], evs = c.events, shown = showAll ? evs : evs.slice(0, 8);
    side.innerHTML = `<div class="section-head"><div><button type="button" class="linkish back" id="map-back">← What matters for the UK</button><h2>${esc(selected)}</h2><div class="section-sub">${esc(countryText(selected, c))}</div></div></div>
      <div class="ev-list">${evs.length ? shown.map(eventCard).join("") : `<div class="empty">${c.searched ? "No reports with a source and a quote in this window. Nothing found is not the same as safe." : "This country was not searched in this window."}</div>`}</div>
      ${evs.length > shown.length ? `<button class="btn more" type="button" id="more">Show all ${evs.length}</button>` : ""}`;
  }

  // ---------- controls
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (t.closest("#more")) { showAll = true; renderSide(); return; }
    if (t.closest("#map-back")) { selected = null; render(); return; }
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

  document.addEventListener("viewchange", (e) => { if (e.detail === "map") init(); });
})();

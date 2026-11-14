// Regional Abuse Map: base map only (plan step 3, threat-map-agent-plan.md).
// Draws the 29 in-scope countries. No events are loaded yet, so every country
// is honestly "not searched". Events need a source URL and a quote (plan rule 1).
(() => {
  // EU-27 + UK + Switzerland, keyed by ISO 3166 numeric id (as in world-atlas).
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
  const W = 800, H = 760;
  const $ = (s) => document.querySelector(s);
  let state = "idle"; // idle | loading | ready

  const loadScript = (src) => new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = () => fail(new Error("could not load " + src));
    document.head.appendChild(s);
  });

  async function init() {
    if (state !== "idle") return;
    state = "loading";
    $("#map-status").textContent = "Loading map…";
    try {
      for (const src of LIBS) await loadScript(src);
      const r = await fetch(SHAPES);
      if (!r.ok) throw new Error("map shapes " + r.status);
      draw(await r.json());
      state = "ready";
      $("#map-status").textContent = "No events loaded yet. Every country is shown as not searched.";
    } catch (e) {
      state = "idle"; // allow a retry next time the view opens
      document.querySelectorAll('script[src*="cdn.jsdelivr.net"]').forEach((s) => s.remove());
      $("#map-status").textContent = "The map could not load. It needs an internet connection for the country shapes.";
      console.error("[map]", e);
    }
  }

  function draw(topo) {
    const feats = topojson.feature(topo, topo.objects.countries).features.filter((f) => SCOPE[f.id]);
    // Frame mainland Europe, so overseas territories (e.g. French Guiana) don't shrink the map.
    const frame = { type: "MultiPoint", coordinates: [[-11, 35], [-11, 66], [34, 71], [34, 34], [12, 34], [12, 71]] };
    const projection = d3.geoAzimuthalEqualArea().rotate([-10, -52]).fitExtent([[12, 12], [W - 12, H - 12]], frame);
    const path = d3.geoPath(projection);
    const ns = "http://www.w3.org/2000/svg";

    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Map of the EU, the UK and Switzerland");
    svg.innerHTML = `<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect class="hatch-bg" width="6" height="6"></rect><line class="hatch-line" x1="0" y1="0" x2="0" y2="6"></line></pattern></defs>`;

    for (const f of feats) {
      const d = path(f);
      if (!d) continue;
      const p = document.createElementNS(ns, "path");
      p.setAttribute("d", d);
      p.setAttribute("class", "country");
      p.setAttribute("fill", "url(#hatch)");
      p.setAttribute("tabindex", "0");
      p.setAttribute("role", "button");
      p.setAttribute("aria-label", `${SCOPE[f.id]}: not searched`);
      p.dataset.id = f.id;
      const t = document.createElementNS(ns, "title");
      t.textContent = `${SCOPE[f.id]}: not searched`;
      p.appendChild(t);
      svg.appendChild(p);
    }

    svg.addEventListener("click", (e) => { const p = e.target.closest(".country"); if (p) select(p.dataset.id); });
    svg.addEventListener("keydown", (e) => {
      const p = e.target.closest(".country");
      if (p && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(p.dataset.id); }
    });
    $("#map-canvas").replaceChildren(svg);
  }

  function select(id) {
    document.querySelectorAll(".country").forEach((p) => p.classList.toggle("selected", p.dataset.id === id));
    const side = $("#map-side");
    side.innerHTML = `<div class="section-head"><div><h2></h2><div class="section-sub">Not searched</div></div></div>
      <div class="empty">No events loaded for this country. A country only gets a level once an event with a source link and a quote is added.</div>`;
    side.querySelector("h2").textContent = SCOPE[id];
  }

  document.addEventListener("viewchange", (e) => { if (e.detail === "map") init(); });
})();

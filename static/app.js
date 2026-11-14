// Thin renderer: every label, status, tag, column and parameter comes from the backend.
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const get = (o, path) => path.split(".").reduce((v, k) => (v == null ? v : v[k]), o);
  let schema = null, result = null, selected = null, filter = null;

  const statusOf = (code) => schema.statuses.find((s) => s.code === code) || { label: code, severity: "muted" };
  const tagOf = (code) => schema.tags.find((t) => t.code === code) || { label: code, severity: "muted" };
  const badge = (code) => { const s = statusOf(code); return `<span class="badge sev-${s.severity}" title="${esc(s.rule || "")}">${esc(s.label)}</span>`; };
  const link = (url, label) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label || url)}</a>` : esc(label || "");

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  }

  // ---------- parameters (backend-defined)
  function renderParams() {
    const today = new Date().toISOString().slice(0, 10);
    $("#params").innerHTML = schema.parameters.map((p) => {
      if (p.type === "bool") {
        const disabled = (p.key.startsWith("exa_") && !schema.capabilities.exa) || (p.key === "ai_validate" && !schema.capabilities.ai);
        return `<label class="bool${disabled ? " off" : ""}" title="${esc(disabled ? "Not configured: add the API key in .env" : p.help || "")}"><input type="checkbox" name="${p.key}" ${p.default && !disabled ? "checked" : ""} ${disabled ? "disabled" : ""}> ${esc(p.label)}</label>`;
      }
      const v = p.default === "today" ? today : p.default || "";
      return `<label title="${esc(p.help || "")}">${esc(p.label)}<input type="${p.type}" name="${p.key}" value="${esc(v)}"></label>`;
    }).join("");
    $("#legend").innerHTML = schema.statuses.map((s) => `<span class="sev-${s.severity}" title="${esc(s.rule)}">${esc(s.label)}</span>`).join("");
    $("#engines").textContent = `Exa ${schema.capabilities.exa ? "on" : "off"} · AI second opinion ${schema.capabilities.ai ? "on" : "off"}`;
  }
  function readParams() {
    const out = {};
    for (const p of schema.parameters) {
      const el = $(`#params [name="${p.key}"]`);
      out[p.key] = p.type === "bool" ? el.checked : el.value;
    }
    return out;
  }

  // ---------- run
  async function run() {
    const text = $("#input").value.trim();
    if (text.length < 20) { alertBox("Paste a complaint first."); return; }
    $("#btn-run").disabled = true;
    showProgress([{ step: "Starting…" }], false);
    try {
      const { job_id } = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, params: readParams() }) });
      history.replaceState(null, "", `?job=${job_id}`);
      await poll(job_id);
    } catch (e) { alertBox(e.message); }
    $("#btn-run").disabled = false;
  }
  async function poll(id) {
    for (;;) {
      const j = await api(`/api/jobs/${id}`);
      showProgress(j.progress, j.status !== "running");
      if (j.status === "done") { $("#progress").hidden = true; render(j.result); return; }
      if (j.status === "error") { alertBox(j.error); return; }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  function showProgress(steps, done) {
    const el = $("#progress"); el.hidden = false;
    el.innerHTML = `<ol>${steps.map((s, i) => `<li class="${done || i < steps.length - 1 ? "done" : ""}">${esc(s.step)}</li>`).join("")}</ol>`;
  }
  function alertBox(msg) { const el = $("#progress"); el.hidden = false; el.innerHTML = `<span class="ev-bad">${esc(msg)}</span>`; }

  // ---------- render
  function render(res) {
    result = res; schema = res.view || schema; selected = null; filter = null;
    $("#preview").hidden = false; $("#grid-wrap").hidden = false;
    $("#headline").textContent = res.summary.headline;
    $("#tags").innerHTML = res.tags.map((t) => `<span class="chip sev-${t.severity}" data-tag="${t.code}" title="${esc(t.reason)}">${esc(t.label)}${t.claim_ids.length ? ` · ${t.claim_ids.length}` : ""}</span>`).join("");
    const core = res.summary.core;
    $("#core").innerHTML = `<div class="card-title">Underlying complaint</div>` + (core
      ? `<span class="badge sev-${core.strength === "strong" ? "ok" : core.strength === "weak" ? "alert" : "info"}">${esc(core.strength)}</span> <span class="qsrc">(AI assessment)</span><p>${esc(core.reason)}</p>`
      : `<p class="muted">Bad citations do not make a complaint weak. Assess the facts separately${schema.capabilities.ai ? "" : " (AI assessment is off)"}.</p>`);
    $("#deadlines").innerHTML = `<div class="card-title">Deadlines (from ${esc(res.params.received_date)})</div><ul>` + res.deadlines.map((d) =>
      `<li><b>${esc(d.due)}</b> - ${esc(d.label)}${d.extended_due ? ` <span class="muted">(max ${esc(d.extended_due)})</span>` : ""}<br><span class="qsrc">${link(d.source_url, d.basis)}</span></li>`).join("") + "</ul>";
    $("#warnings").innerHTML = (res.meta.warnings || []).map((w) => `<div>⚠ ${esc(w)}</div>`).join("") + `<div class="muted">Law checked as in force on ${esc(res.params.as_of)} · ${res.meta.seconds}s</div>`;
    renderDoc(); renderGrid(); renderPrecedents();
    $("#detail").innerHTML = `<div class="card-title">Details</div><p class="muted">Click a highlighted passage or a table row.</p>`;
    $("#input").hidden = true; $("#docview").hidden = false; $("#btn-edit").hidden = false;
  }

  function renderDoc() {
    const text = result.document.text;
    const claims = result.highlights.filter((h) => h.kind === "claim");
    const cits = result.highlights.filter((h) => h.kind === "citation");
    const cuts = new Set([0, text.length]);
    for (const h of result.highlights) { cuts.add(h.start); cuts.add(h.end); }
    const pts = [...cuts].sort((a, b) => a - b);
    const nOf = Object.fromEntries(result.claims.map((c) => [c.id, c.n]));
    let html = "";
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1], seg = esc(text.slice(a, b));
      const cl = claims.find((h) => h.start <= a && b <= h.end);
      const ci = cits.find((h) => h.start <= a && b <= h.end);
      if (!cl && !ci) { html += seg; continue; }
      const h = ci || cl;
      const inner = ci ? `<span class="cit sev-${ci.severity}">${seg}</span>${b === ci.end ? `<span class="badge-n">${nOf[ci.claim_id]}</span>` : ""}` : seg;
      html += `<span class="claim sev-${(cl || h).severity}" data-claim="${h.claim_id}">${inner}</span>`;
    }
    $("#docview").innerHTML = html;
  }

  function cell(c, key) {
    switch (key) {
      case "status": return badge(c.status);
      case "short_claim":
        return `${esc(c.short_claim)}${c.short_claim_source === "ai" ? ' <span class="qsrc">(AI)</span>' : ""}<div class="tagline">${c.tags.map((t) => `<span class="sev-${tagOf(t).severity}">${esc(tagOf(t).label)}</span>`).join("")}</div>`;
      case "law.quote": return c.law.quote ? `“${esc(c.law.quote)}”<div class="qsrc">verbatim from source · ${esc(c.law.quote_source)}</div>` : `<span class="muted">${esc(c.reason)}</span>`;
      case "source_link": {
        const s = c.source_link || {};
        return `${link(s.url, s.label)}${s.as_of ? `<div class="qsrc">as in force ${esc(s.as_of)}</div>` : ""}${s.evidence_url ? `<div class="qsrc">checked: ${link(s.evidence_url, "404 evidence")}</div>` : ""}`;
      }
      case "ai.verdict": return c.ai ? esc(c.ai.verdict) : `<span class="muted">-</span>`;
      default: return esc(get(c, key));
    }
  }
  function renderGrid() {
    const cols = schema.grid_columns;
    $("#grid").innerHTML = `<thead><tr>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}</tr></thead><tbody>` +
      result.claims.map((c) => `<tr data-claim="${c.id}">${cols.map((col) => `<td>${cell(c, col.key)}</td>`).join("")}</tr>`).join("") + "</tbody>";
    applyFilter();
  }
  function applyFilter() {
    const ids = filter ? new Set(result.tags.find((t) => t.code === filter)?.claim_ids || []) : null;
    document.querySelectorAll("#grid tbody tr").forEach((tr) => tr.classList.toggle("hidden", !!ids && ids.size > 0 && !ids.has(tr.dataset.claim)));
    document.querySelectorAll("#tags .chip").forEach((ch) => ch.classList.toggle("active", ch.dataset.tag === filter));
    $("#clear-filter").hidden = !filter;
  }

  function select(id) {
    selected = id;
    document.querySelectorAll("[data-claim]").forEach((el) => el.classList.toggle("selected", el.dataset.claim === id));
    const c = result.claims.find((x) => x.id === id);
    if (!c) return;
    const l = c.law, ex = c.exa || {};
    const sec = (title, body) => (body ? `<h4>${title}</h4>${body}` : "");
    $("#detail").innerHTML = `
      <div class="card-title">#${c.n} ${badge(c.status)}</div>
      <h3>${esc(c.citation_as_written)}</h3>
      <blockquote>${esc(c.original_text)}</blockquote>
      ${sec("Why", `<p>${esc(c.reason)}</p>`)}
      ${sec("What it means", c.implication ? `<p>${esc(c.implication)}</p>` : "")}
      ${sec("Source", l.url ? `${link(l.as_of_url || l.url, `${l.title}${l.heading ? " - " + l.heading : ""}`)}${l.as_of ? ` <span class="qsrc">as in force ${esc(l.as_of)}</span>` : ""}${l.changed_since_event ? ` <span class="badge sev-info">changed since incident</span>` : ""}` : "")}
      ${sec("Quoted text", l.quote ? `<blockquote>${esc(l.quote)}</blockquote><div class="qsrc">checked verbatim against the fetched source (${esc(l.quote_source)})</div>` : "")}
      ${sec("Full provision", l.text ? `<div class="lawtext">${esc(l.text)}</div>` : "")}
      ${sec("Text today (if changed)", l.current_text ? `<div class="lawtext">${esc(l.current_text)}</div>` : "")}
      ${sec("Related provisions read", l.related?.length ? `<ul>${l.related.map((r) => `<li>${link(r.url, r.label)}${r.changed_since_event ? ' <span class="badge sev-info">changed since incident</span>' : ""}</li>`).join("")}</ul>` : "")}
      ${sec("Nearest real rules (Exa)", c.nearest_real?.length ? `<ul>${c.nearest_real.map((n) => `<li>${link(n.url, n.title)}<div class="qsrc">${esc(n.why)}</div></li>`).join("")}</ul>` : "")}
      ${sec("Rule as at incident date (Exa Snapshot)", ex.snapshot ? (ex.snapshot.error ? `<p class="muted">Snapshot unavailable: ${esc(ex.snapshot.error)}</p>` : `<p>Stored copy from ${esc(ex.snapshot.snapshot_at || "n/a")} (requested as at ${esc(ex.snapshot.as_of)}). Relevant rules ${ex.snapshot.changed ? "<b>changed</b>" : "unchanged"} since.</p>`) : "")}
      ${sec("Where this claim appears online (Exa)", ex.provenance?.length ? `<ul>${ex.provenance.map((p) => `<li>${link(p.url, p.title || p.url)} <span class="qsrc">${esc(p.published_date || "date unknown")}</span><div class="qsrc">${esc(p.highlight)}</div></li>`).join("")}</ul>` : "")}
      ${sec("Searched for a source (Exa)", ex.discovery?.length ? `<ul>${ex.discovery.map((p) => `<li>${link(p.url, p.title)}<div class="qsrc">${esc(p.highlight)}</div></li>`).join("")}</ul>` : "")}
      ${sec("AI second opinion", c.ai ? `<p>${c.ai.agree ? "Agrees" : "Disagrees"} - suggests ${badge(c.ai.suggested_status)}</p><p>${esc(c.ai.reason)}</p>${c.ai.implication ? `<p><i>${esc(c.ai.implication)}</i></p>` : ""}${c.ai.law_quote_rejected ? '<p class="ev-bad">AI quote rejected: not found verbatim in the source.</p>' : ""}` : "")}
      ${sec("Evidence log", `<ul>${c.evidence.map((e) => `<li class="${e.ok ? "" : "ev-bad"}">${esc(e.step)}${e.detail ? ": " + esc(e.detail) : ""} ${e.url ? link(e.url, "↗") : ""}</li>`).join("")}</ul>`)}`;
    document.querySelector(`#grid tr[data-claim="${id}"]`)?.scrollIntoView({ block: "nearest" });
  }

  function renderPrecedents() {
    const p = result.precedents || [];
    $("#precedents").hidden = !p.length;
    $("#precedents").innerHTML = `<div class="card-title">Similar Ombudsman decisions (Exa)</div><ul>` + p.map((d) =>
      `<li>${link(d.url, d.title || d.url)} <span class="qsrc">${esc(d.published_date || "")}</span><div class="qsrc">${esc(d.highlight)}</div></li>`).join("") + "</ul>";
  }

  // ---------- events
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) { filter = filter === chip.dataset.tag ? null : chip.dataset.tag; applyFilter(); return; }
    if (e.target.closest("a")) return;
    const el = e.target.closest("[data-claim]");
    if (el && result) select(el.dataset.claim);
  });
  $("#clear-filter").onclick = () => { filter = null; applyFilter(); };
  $("#btn-run").onclick = run;
  $("#btn-sample").onclick = async () => { $("#input").value = (await api("/api/sample")).text; $("#input").hidden = false; $("#docview").hidden = true; };
  $("#btn-edit").onclick = () => { $("#input").hidden = false; $("#docview").hidden = true; };

  (async () => {
    schema = await api("/api/schema");
    renderParams();
    const job = new URLSearchParams(location.search).get("job");
    if (job) { try { const j = await api(`/api/jobs/${job}`); if (j.status === "done") { $("#input").value = j.result.document.text; render(j.result); } else if (j.status === "running") poll(job); } catch (_) {} }
  })();
})();

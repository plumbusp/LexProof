// Thin renderer: statuses, columns and parameters come from /api/schema and the result.
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let schema = null, result = null, filter = null;

  const statusOf = (code) => schema.statuses.find((s) => s.code === code) || { label: code, severity: "muted" };
  const badge = (code) => { const s = statusOf(code); return `<span class="badge sev-${s.severity}" title="${esc(s.rule || "")}">${esc(s.label)}</span>`; };
  const link = (url, label) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(label || url)}</a>` : esc(label || "");
  const srcLabel = (p) => p ? `${p.title || ""}${p.heading ? " - " + p.heading : ""}` : "";

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  }

  function renderParams() {
    const today = new Date().toISOString().slice(0, 10);
    $("#params").innerHTML = schema.parameters.map((p) =>
      `<label>${esc(p.label)}<input type="${p.type}" name="${p.key}" value="${esc(p.default === "today" ? today : p.default || "")}"></label>`).join("");
    $("#legend").innerHTML = schema.statuses.map((s) => `<span class="sev-${s.severity}" title="${esc(s.rule)}">${esc(s.label)}</span>`).join("");
    $("#engines").textContent = `Gemini ${schema.capabilities.ai ? "on" : "off (rules fallback)"} · Exa ${schema.capabilities.exa ? "on" : "off"}`;
  }
  const readParams = () => Object.fromEntries(schema.parameters.map((p) => [p.key, $(`#params [name="${p.key}"]`).value]));

  async function run() {
    const text = $("#input").value.trim();
    if (text.length < 20) return showMsg("Paste a request first.");
    $("#btn-run").disabled = true;
    showProgress([{ step: "Starting…" }]);
    try {
      const { job_id } = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, params: readParams() }) });
      history.replaceState(null, "", `?job=${job_id}`);
      await poll(job_id);
    } catch (e) { showMsg(e.message); }
    $("#btn-run").disabled = false;
  }
  async function poll(id) {
    for (;;) {
      const j = await api(`/api/jobs/${id}`);
      showProgress(j.progress);
      if (j.status === "done") { $("#progress").hidden = true; return render(j.result); }
      if (j.status === "error") return showMsg(j.error);
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  function showProgress(steps) { const el = $("#progress"); el.hidden = false; el.innerHTML = `<ol>${steps.map((s) => `<li>${esc(s.step)}</li>`).join("")}</ol>`; }
  function showMsg(msg) { const el = $("#progress"); el.hidden = false; el.innerHTML = `<span class="ev-bad">${esc(msg)}</span>`; }

  function render(res) {
    result = res; schema = res.view || schema; filter = null;
    $("#preview").hidden = false; $("#grid-wrap").hidden = false;
    $("#headline").textContent = res.summary.headline;
    $("#tags").innerHTML = res.tags.map((t) => `<span class="chip sev-${t.severity}" data-tag="${t.code}" title="${esc(t.reason)}">${esc(t.label)} · ${t.claim_ids.length}</span>`).join("");
    $("#deadlines").innerHTML = `<div class="card-title">Reply deadline</div><ul>` + res.deadlines.map((d) =>
      `<li><b>${esc(d.due)}</b> - ${esc(d.label)}<br><span class="qsrc">${link(d.url, d.basis)}</span></li>`).join("") + "</ul>";
    $("#reply").innerHTML = `<div class="card-title">Draft reply <button class="link" id="copy-reply">copy</button></div><textarea id="reply-text" class="reply">${esc(res.draft_reply)}</textarea>`;
    $("#copy-reply").onclick = () => navigator.clipboard.writeText($("#reply-text").value);
    $("#warnings").innerHTML = (res.meta.warnings || []).map((w) => `<div>⚠ ${esc(w)}</div>`).join("") + `<div class="muted">References found by ${esc(res.meta.extractor)} · ${res.meta.seconds}s</div>`;
    renderDoc(); renderGrid();
    $("#detail").innerHTML = `<div class="card-title">Details</div><p class="muted">Click a highlighted passage or a table row.</p>`;
    $("#input").hidden = true; $("#docview").hidden = false; $("#btn-edit").hidden = false;
  }

  function renderDoc() {
    const text = result.document.text;
    const hs = [...result.highlights].sort((a, b) => a.start - b.start);
    const nOf = Object.fromEntries(result.claims.map((c) => [c.id, c.n]));
    let html = "", pos = 0;
    for (const h of hs) {
      if (h.start < pos) continue; // skip overlaps
      html += esc(text.slice(pos, h.start));
      html += `<span class="claim cit sev-${h.severity}" data-claim="${h.claim_id}">${esc(text.slice(h.start, h.end))}</span><span class="badge-n">${nOf[h.claim_id]}</span>`;
      pos = h.end;
    }
    $("#docview").innerHTML = html + esc(text.slice(pos));
  }

  function cell(c, key) {
    switch (key) {
      case "status": return badge(c.status);
      case "kind": return `<span class="muted">${esc(c.kind)}</span>`;
      case "quote": return `${esc(c.quote)}${c.claim && c.claim !== c.quote ? `<div class="qsrc">${esc(c.claim)}</div>` : ""}`;
      case "proof": return c.proof ? `${c.proof.quote ? `“${esc(c.proof.quote)}”` : ""}<div class="qsrc">${link(c.proof.url, srcLabel(c.proof))}</div>` : `<span class="muted">-</span>`;
      default: return esc(c[key]);
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
    document.querySelectorAll("#grid tbody tr").forEach((tr) => tr.classList.toggle("hidden", !!ids && !ids.has(tr.dataset.claim)));
    document.querySelectorAll("#tags .chip").forEach((ch) => ch.classList.toggle("active", ch.dataset.tag === filter));
    $("#clear-filter").hidden = !filter;
  }

  function select(id) {
    document.querySelectorAll("[data-claim]").forEach((el) => el.classList.toggle("selected", el.dataset.claim === id));
    const c = result.claims.find((x) => x.id === id);
    if (!c) return;
    const p = c.proof;
    $("#detail").innerHTML = `
      <div class="card-title">#${c.n} ${badge(c.status)} <span class="muted">${esc(c.kind)}</span></div>
      <blockquote>${esc(c.quote)}</blockquote>
      ${c.claim ? `<h4>Claim</h4><p>${esc(c.claim)}</p>` : ""}
      ${c.note ? `<h4>Note</h4><p>${esc(c.note)}</p>` : ""}
      ${p ? `<h4>Proof</h4>${link(p.url, srcLabel(p))}${p.quote ? `<blockquote>${esc(p.quote)}</blockquote>` : ""}${p.text ? `<h4>Full text</h4><div class="lawtext">${esc(p.text)}</div>` : ""}` : ""}
      <h4>How we checked</h4><ul>${(c.evidence || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`;
    document.querySelector(`#grid tr[data-claim="${id}"]`)?.scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) { filter = filter === chip.dataset.tag ? null : chip.dataset.tag; return applyFilter(); }
    if (e.target.closest("a, button, textarea")) return;
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

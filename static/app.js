// Officer view. Renders the backend result; statuses/labels come from the API.
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) {} } };
  let schema = null, result = null, openId = null;

  const label = (code) => (schema.statuses.find((s) => s.code === code) || { label: code }).label.toUpperCase();
  const rule = (code) => (schema.statuses.find((s) => s.code === code) || {}).rule || "";
  const badge = (code) => `<span class="badge st-${code}" title="${esc(rule(code))}">${esc(label(code))}</span>`;
  const fmtDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" }).replace(",", "");

  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json();
  }

  // ---------- theme + panel
  function setTheme(t) { document.documentElement.dataset.theme = t; $("#theme-icon").textContent = t === "dark" ? "light_mode" : "dark_mode"; store.set("theme", t); }
  function setPanel(open) {
    $("#workspace").classList.toggle("panel-closed", !open);
    $("#btn-panel").setAttribute("aria-pressed", String(open));
    $("#btn-panel .icon").textContent = open ? "right_panel_close" : "right_panel_open";
    store.set("panel", open ? "open" : "closed");
  }
  const panelOpen = () => !$("#workspace").classList.contains("panel-closed");

  // ---------- run
  async function run() {
    const text = $("#input").value.trim();
    if (text.length < 20) return progress("Paste a request first.", true);
    $("#btn-run").disabled = true;
    progress("Starting…");
    try {
      const { job_id } = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, params: { received_date: $("#received").value } }) });
      history.replaceState(null, "", `?job=${job_id}${location.hash}`);
      for (;;) {
        const j = await api(`/api/jobs/${job_id}`);
        if (j.status === "done") { $("#progress").hidden = true; render(j.result); break; }
        if (j.status === "error") { progress(j.error, true); break; }
        progress((j.progress.at(-1) || {}).step || "Working…");
        await new Promise((r) => setTimeout(r, 900));
      }
    } catch (e) { progress(e.message, true); }
    $("#btn-run").disabled = false;
  }
  function progress(msg, err) { const el = $("#progress"); el.hidden = false; el.innerHTML = err ? `<span class="err">${esc(msg)}</span>` : esc(msg); }

  // ---------- render
  function render(res) {
    result = res; schema = res.view || schema; openId = null;
    renderKpis(); renderLetter(); renderAsks(); renderCites(); renderDraft();
    $("#input-wrap").hidden = true; $("#letter").hidden = false; $("#btn-edit").hidden = false;
    const name = res.request.requester;
    $("#request-sub").innerHTML = `${name ? `from <b>${esc(name)}</b>, ` : ""}received <span class="mono">${esc(fmtDate(res.params.received_date))}</span>`;
    const w = res.meta.warnings || [];
    $("#warn").hidden = !w.length; $("#warn").textContent = w.join(" · ");
  }

  function renderKpis() {
    const d = result.deadline, s = result.summary, rt = result.request.type;
    const left = d.working_days_left;
    const leftTxt = left >= 0 ? `${left} working day${left === 1 ? "" : "s"} left` : `overdue by ${-left} working days`;
    const basis = rt === "SAR" ? "One month from receipt – UK GDPR Art 12A" : "20 working days from receipt – FOIA s.10(1)";
    $("#kpis").hidden = false;
    $("#kpis").innerHTML = `
      <div class="card kpi"><span class="kpi-label">Reply deadline</span><div class="kpi-value small">${esc(fmtDate(d.due))} – ${esc(leftTxt)}</div><span class="kpi-sub">${esc(basis)}</span></div>
      <div class="card kpi"><span class="kpi-label">Request type</span><div class="kpi-value word">${esc(rt)}</div><span class="kpi-sub">${esc(result.request.requester ? "From " + result.request.requester : "Requester not named")}</span></div>
      <div class="card kpi"><span class="kpi-label">Citations</span><div class="kpi-value">${s.total}</div><span class="kpi-sub sans">Legal references found</span></div>
      <div class="card kpi"><span class="kpi-label">Checked against official text</span><div class="kpi-value">${s.verified} of ${s.total}</div><span class="kpi-sub sans">Verified accurately</span></div>`;
  }

  function renderLetter() {
    const text = result.document.text;
    const hs = [...result.highlights].sort((a, b) => a.start - b.start);
    const nOf = Object.fromEntries(result.claims.map((c) => [c.id, c.n]));
    let html = "", pos = 0;
    for (const h of hs) {
      if (h.start < pos) continue;
      html += esc(text.slice(pos, h.start));
      html += `<span class="hl st-${h.status}" data-claim="${h.claim_id}">${esc(text.slice(h.start, h.end))}</span><span class="num st-${h.status}" data-claim="${h.claim_id}">${nOf[h.claim_id]}</span>`;
      pos = h.end;
    }
    $("#letter").innerHTML = html + esc(text.slice(pos));
  }

  function renderAsks() {
    const asks = result.request.asks || [];
    $("#asks").hidden = !asks.length;
    $("#asks").innerHTML = `<h2>What is being asked</h2><ul>${asks.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>`;
  }

  function mark(text, term, cls) {
    const t = esc(text);
    if (!term) return t;
    const i = text.toLowerCase().indexOf(term.toLowerCase());
    if (i < 0) return t;
    return esc(text.slice(0, i)) + `<span class="${cls}">${esc(text.slice(i, i + term.length))}</span>` + esc(text.slice(i + term.length));
  }

  function chipFor(c) {
    if (c.status === "NOT_AS_CITED") return `<span class="chip">Nearest real rule</span>`;
    if (c.status === "POSSIBLE_RULE") return `<span class="chip">Check it applies</span>`;
    return "";
  }

  function drawer(c) {
    const p = c.proof || {};
    const official = p.quote || p.text?.split("\n")[0] || "";
    const srcName = c.source || "source";
    const right = p.url
      ? `<div class="box"><div class="box-head"><span class="box-label">Official text – ${esc(srcName)}</span><a href="${esc(p.url)}" target="_blank" rel="noopener">View on ${esc(srcName)}<span class="icon" style="font-size:12px">open_in_new</span></a></div>
         <div class="box-text">${p.title ? `<div class="fine">${esc(p.title)}${p.heading ? " – " + esc(p.heading) : ""}</div>` : ""}“${mark(official, c.diff?.law, "mark-good")}”</div></div>`
      : `<div class="box"><div class="box-head"><span class="box-label">Official text</span></div><div class="box-text">No official text found.</div></div>`;
    return `<tr class="drawer"><td colspan="5"><div class="drawer-inner">
      <div class="compare">
        <div class="box"><div class="box-head"><span class="box-label">What the letter says</span></div><div class="box-text">“${mark(c.quote, c.diff?.claim, "mark-bad")}”</div></div>
        ${right}
      </div>
      ${c.note ? `<div class="note">${esc(c.note)}</div>` : ""}
      ${p.text ? `<details class="how full"><summary>Full provision text</summary><pre>${esc(p.text)}</pre></details>` : ""}
      <details class="how"><summary>How this was checked</summary><ul>${(c.evidence || []).map((e) => `<li>${esc(e)}</li>`).join("")}</ul></details>
    </div></td></tr>`;
  }

  function renderCites() {
    $("#extractor").textContent = `found by ${result.meta.extractor} · ${result.meta.seconds}s`;
    if (!result.claims.length) { $("#cites").innerHTML = `<div class="empty">No legal references found.</div>`; return; }
    $("#cites").innerHTML = `<div style="overflow-x:auto"><table class="cites"><thead><tr><th class="n">#</th><th>What the letter says</th><th>Cited law</th><th>Status</th><th>Source</th></tr></thead><tbody>` +
      result.claims.map((c) => `<tr class="row${openId === c.id ? " open" : ""}" data-row="${c.id}">
        <td class="n">${c.n}</td>
        <td>“${esc(c.quote)}”</td>
        <td class="law${c.cited ? "" : " none"}">${esc(c.cited || "—")}</td>
        <td>${badge(c.status)}</td>
        <td class="src">${esc(c.source || "No official text")}${chipFor(c) ? "<br>" + chipFor(c) : ""}</td></tr>${openId === c.id ? drawer(c) : ""}`).join("") +
      `</tbody></table></div>`;
  }

  function renderDraft() {
    $("#draft").innerHTML = result.draft_reply.split(/\n{2,}/).map((p) => `<p>${esc(p)}</p>`).join("");
  }

  function focusClaim(id, fromText) {
    openId = openId === id && !fromText ? null : id;
    if (fromText && !panelOpen()) setPanel(true);
    renderCites();
    document.querySelectorAll(".hl").forEach((el) => el.classList.toggle("active", el.dataset.claim === openId));
    if (fromText) document.querySelector(`tr[data-row="${id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    else document.querySelector(`.hl[data-claim="${id}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ---------- events
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-claim]");
    if (t && result) return focusClaim(t.dataset.claim, true);
    if (e.target.closest("a, details, .drawer")) return;
    const r = e.target.closest("tr[data-row]");
    if (r && result) focusClaim(r.dataset.row, false);
  });
  $("#btn-run").onclick = run;
  $("#btn-panel").onclick = () => setPanel(!panelOpen());
  $("#btn-theme").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  $("#btn-sample").onclick = async () => { $("#input").value = (await api("/api/sample")).text; };
  $("#btn-edit").onclick = () => { $("#input-wrap").hidden = false; $("#letter").hidden = true; $("#btn-edit").hidden = true; };
  $("#btn-copy").onclick = async (e) => {
    try { await navigator.clipboard.writeText($("#draft").innerText); e.target.textContent = "Copied"; } catch (_) { e.target.textContent = "Select and copy"; }
    setTimeout(() => (e.target.textContent = "Copy"), 2000);
  };

  (async () => {
    setTheme(document.documentElement.dataset.theme || "light");
    setPanel(store.get("panel") !== "closed");
    $("#received").value = new Date().toISOString().slice(0, 10);
    schema = await api("/api/schema");
    $("#org").textContent = schema.org_name;
    const job = new URLSearchParams(location.search).get("job");
    if (job) {
      try {
        const j = await api(`/api/jobs/${job}`);
        if (j.status === "done") { $("#input").value = j.result.document.text; $("#received").value = j.result.params.received_date; render(j.result); }
      } catch (_) {}
    }
  })();
})();

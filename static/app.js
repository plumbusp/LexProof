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
    loading([]);
    try {
      const { job_id } = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, params: { received_date: $("#received").value } }) });
      history.replaceState(null, "", `?job=${job_id}${location.hash}`);
      for (;;) {
        const j = await api(`/api/jobs/${job_id}`);
        if (j.status === "done") { $("#progress").hidden = true; render(j.result); break; }
        if (j.status === "error") { console.warn("[check]", j.error); progress(FAILED, true); break; }
        loading(j.progress || []);
        await new Promise((r) => setTimeout(r, 900));
      }
    } catch (e) { console.warn("[check]", e); progress(FAILED, true); }
    $("#btn-run").disabled = false;
  }
  // The officer never sees raw server or provider errors; the details stay in the job and the console.
  const FAILED = "The check could not finish. Press Check to try again.";
  // Loading hint: which stage of the check is running and what it is doing right now.
  const STAGES = [
    { key: "read", title: "Reading the letter", sub: "Finding every law it cites or implies" },
    { key: "official", title: "Official database", sub: "Fetching the real text from legislation.gov.uk" },
    { key: "exa", title: "Exa search", sub: "Tracing ambiguous or unnamed law references" },
    { key: "compare", title: "Comparing", sub: "Claims vs. the real text, plus the deadline" },
  ];
  function loading(steps) {
    const seen = new Set(steps.map((s) => s.stage).filter(Boolean));
    const final = seen.has("compare"), checking = seen.has("official") && !final;
    const state = (k) => {
      if (k === "read") return seen.has("official") || final ? "done" : "active";
      if (k === "official") return final ? "done" : checking ? "active" : "pending";
      if (k === "exa") return final ? (seen.has("exa") ? "done" : "skipped") : seen.has("exa") ? "active" : "pending";
      return final ? "active" : "pending";
    };
    const icon = { done: "check_circle", pending: "radio_button_unchecked", skipped: "remove" };
    const last = steps.at(-1) || {}, counted = [...steps].reverse().find((s) => s.total);
    const count = checking && counted ? `<span class="load-count">${counted.done} / ${counted.total} checked</span>` : "";
    const el = $("#progress"); el.hidden = false;
    el.innerHTML = `<ol class="load-steps">${STAGES.map((s) => { const st = state(s.key); return `<li class="ls-${st}">
        ${st === "active" ? `<span class="spin"></span>` : `<span class="icon">${icon[st]}</span>`}
        <div><div class="ls-title">${esc(s.title)}</div><div class="ls-sub">${esc(st === "skipped" ? "Not needed: every law was named" : s.sub)}</div></div>
      </li>`; }).join("")}</ol>
      <div class="load-now" aria-live="polite"><span>${esc(last.step || "Starting…")}</span>${count}</div>`;
  }
  function progress(msg, err) { const el = $("#progress"); el.hidden = false; el.innerHTML = err ? `<span class="err">${esc(msg)}</span>` : esc(msg); }

  // A new letter means the old results no longer apply: back to the empty state.
  function clearResults() {
    result = null; openId = null;
    $("#kpis").hidden = true; $("#kpis").innerHTML = "";
    $("#warn").hidden = true; $("#asks").hidden = true; $("#progress").hidden = true;
    $("#extractor").textContent = "";
    $("#cites").innerHTML = `<div class="empty">Press Check to see each law the letter cites, with its status and the official text.</div>`;
    $("#draft").innerHTML = `<p class="fine">A draft reply to the sender appears here after a check. You can edit it, then copy it.</p>`;
    $("#request-sub").textContent = "Paste a request or complaint, then press Check.";
    $("#input-wrap").hidden = false; $("#letter").hidden = true; $("#letter").innerHTML = ""; $("#btn-edit").hidden = true;
    history.replaceState(null, "", location.pathname + location.hash);
  }

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
      <div class="card kpi"><span class="kpi-label">Correct as cited</span><div class="kpi-value">${s.verified} of ${s.total}</div><span class="kpi-sub sans">${esc(closer(s.total - s.verified))}</span></div>`;
  }

  const closer = (n) => (n ? `${n} need${n === 1 ? "s" : ""} a closer look: click a row below` : "None flagged. Read the official text to confirm.");
  const FOUND_BY = { rules: "Found by pattern rules, no AI", Gemini: "Found by Gemini (AI reading)" };

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
    $("#extractor").textContent = `${FOUND_BY[result.meta.extractor] || "Found by " + result.meta.extractor} · ${result.meta.seconds} s`;
    if (!result.claims.length) { $("#cites").innerHTML = `<div class="empty">No legal references found in this letter.</div>`; return; }
    $("#cites").innerHTML = `<p class="cites-hint fine">Click a row to see the letter's words next to the official text. Hover a status for what it means.</p><div style="overflow-x:auto"><table class="cites"><thead><tr><th class="n">#</th><th>What the letter says</th><th>Cited law</th><th>Status</th><th>Source</th></tr></thead><tbody>` +
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
  $("#btn-edit").onclick = () => { $("#input-wrap").hidden = false; $("#letter").hidden = true; $("#btn-edit").hidden = true; };

  // ---------- generate a test complaint (UK only in this demo)
  // Pick an area, then either let AI write a new letter (wait) or load a hand-written preset (instant).
  let genAreasList = [], genArea = null;
  function genStatus(html, err) { const el = $("#gen-status"); el.hidden = !html; el.innerHTML = html; el.classList.toggle("err", !!err); }
  // Always open. Two modes with the same choices: "Use samples" (ready-made, no wait, the default)
  // or "Generate complaint" (AI writes a new letter, wait).
  function genMode(mode) {
    $("#gen-body").dataset.mode = mode;
    document.querySelectorAll(".gen-mode").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
  }
  document.querySelectorAll(".gen-mode").forEach((b) => (b.onclick = () => genMode(b.dataset.mode)));
  const areaName = (a) => `${a.short} (${a.name})`;
  function genSelect(id) {
    genArea = genAreasList.find((a) => a.id === id) || genAreasList[0];
    document.querySelectorAll(".gen-area").forEach((x) => x.setAttribute("aria-checked", String(x.dataset.area === genArea.id)));
    $("#gen-presets").innerHTML = genArea.presets.map((p) =>
      `<button type="button" class="btn gen-btn gen-preset" data-preset="${esc(p.id)}"><span class="icon">description</span><span class="gen-btn-label">${esc(p.title)}</span><span class="gen-wait">no wait</span></button>`).join("");
  }
  async function genAreas() {
    genAreasList = (await api("/api/generate/options")).areas;
    $("#gen-areas").innerHTML = genAreasList.map((a) => `
      <button type="button" class="gen-area" role="radio" aria-checked="false" data-area="${esc(a.id)}">
        <span class="gen-area-head"><span class="gen-short">${esc(a.short)}</span><span class="gen-name">${esc(a.name)}</span></span>
        <span class="gen-law">${esc(a.law)}</span>
        <span class="gen-about">${esc(a.about)}</span>
      </button>`).join("");
    genSelect(genAreasList[0].id);
  }
  $("#gen-areas").addEventListener("click", (e) => {
    const b = e.target.closest(".gen-area");
    if (b && !b.disabled) genSelect(b.dataset.area);
  });
  async function genLetter(preset) {
    const name = areaName(genArea), busy = document.querySelectorAll(".gen-area, .gen-preset, #btn-gen-ai");
    busy.forEach((x) => (x.disabled = true));
    if (!preset) genStatus(`<span class="spin"></span>Writing a new ${esc(name)} letter…`);
    try {
      const r = await api("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ area: genArea.id, country: "UK", preset }) });
      clearResults();
      $("#input").value = r.text;
      const what = r.source === "preset" ? `Sample ${esc(name)} letter "${esc(r.title)}" loaded`
        : r.source === "fallback" ? `AI was unavailable, so a sample ${esc(name)} letter was loaded`
        : `New ${esc(name)} letter ready`;
      genStatus(`<span class="icon">check_circle</span>${what}. Press <b>Check</b> to see every law it cites checked.`);
      $("#btn-run").classList.add("pulse");
      setTimeout(() => $("#btn-run").classList.remove("pulse"), 4000);
      $("#input").scrollIntoView({ behavior: "smooth", block: "center" });
    } catch (err) {
      console.warn("[generate]", err);
      genStatus("Could not load a letter. Try again, or paste your own.", true);
    }
    busy.forEach((x) => (x.disabled = false));
  }
  $("#btn-gen-ai").onclick = () => genLetter(null);
  $("#gen-presets").addEventListener("click", (e) => {
    const b = e.target.closest(".gen-preset");
    if (b && !b.disabled) genLetter(b.dataset.preset);
  });
  genAreas().catch(() => genStatus("Could not load the letter types.", true));
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

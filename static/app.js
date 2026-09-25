/* Pawan Flipkart Label Cropper Automatically - front-end */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = { cfg: null, job: null, poll: null, modal: null, drag: null, manualRect: null };

  // ------------------------------------------------------------------ utils
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function toast(msg, ms = 3200) {
    const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), ms);
  }
  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.json();
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const statusClass = (s) => ({ detected: "ok", review: "review", failed: "fail", manual: "manual" }[s] || "busy");
  const statusText = (s) => ({ detected: "Detected", review: "Review", failed: "Not found", manual: "Manual crop" }[s] || s);
  const methodText = (m) => ({ "vector-border": "PDF border", "text-anchor": "Keyword region", "raster-border": "Image border + OCR", "raster-anchor": "Image + OCR", manual: "Manual", none: "—" }[m] || m);

  // ------------------------------------------------------------------ init
  async function init() {
    state.cfg = await api("/api/config");
    $("appName").textContent = state.cfg.app_name;
    const chips = [`4×6 in · ${state.cfg.page_size_pt[0]}×${state.cfg.page_size_pt[1]} pt`,
      `OCR fallback: ${state.cfg.ocr_available ? "on" : "not installed"}`,
      `Barcode check: ${state.cfg.barcode_decoder}`];
    $("envChips").innerHTML = chips.map((c) => `<span class="chip">${esc(c)}</span>`).join("");
    fillSettings(state.cfg.defaults);
    $("setFilename").value = state.cfg.default_filename;
    bindUpload();
    bindActions();
    bindModal();
  }

  function fillSettings(s) {
    $("setThreshold").value = s.confidence_threshold;
    $("setMargin").value = s.page_margin_pt;
    $("setPadding").value = s.label_padding_pt;
    $("setRotate").checked = s.auto_rotate;
    $("setStrip").checked = s.strip_hidden_content;
    $("setIncludeLow").checked = s.include_low_confidence;
  }
  function readSettings() {
    return {
      confidence_threshold: parseFloat($("setThreshold").value) || 0,
      page_margin_pt: parseFloat($("setMargin").value) || 0,
      label_padding_pt: parseFloat($("setPadding").value) || 0,
      auto_rotate: $("setRotate").checked,
      strip_hidden_content: $("setStrip").checked,
      include_low_confidence: $("setIncludeLow").checked,
    };
  }

  // ------------------------------------------------------------------ upload
  function bindUpload() {
    const dz = $("dropzone"), input = $("fileInput");
    dz.addEventListener("click", (e) => { if (e.target.id !== "fileInput") input.click(); });
    dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") input.click(); });
    input.addEventListener("change", () => { if (input.files.length) upload([...input.files]); input.value = ""; });
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => {
      const files = [...e.dataTransfer.files].filter((f) => /\.(pdf|png|jpe?g)$/i.test(f.name));
      if (!files.length) return toast("Please drop PDF files");
      upload(files);
    });
  }

  async function upload(files) {
    if (state.job) { try { await fetch(`/api/jobs/${state.job.job_id}`, { method: "DELETE" }); } catch (_) {} }
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("settings", JSON.stringify(readSettings()));
    $("summaryCard").classList.remove("hidden");
    showProgress(`Uploading ${files.length} file(s)…`, 5);
    try {
      state.job = await api("/api/jobs", { method: "POST", body: fd });
      render();
      startPolling();
    } catch (e) { toast("Upload failed: " + e.message); hideProgress(); }
  }

  function startPolling() {
    clearInterval(state.poll);
    state.poll = setInterval(async () => {
      try {
        state.job = await api(`/api/jobs/${state.job.job_id}`);
        render();
        if (!["analyzing", "processing"].includes(state.job.state)) {
          clearInterval(state.poll);
          if (state.job.state === "done") toast(`Done — ${state.job.outputs.pages} label page(s) ready`);
          if (state.job.state === "error") toast(state.job.error, 6000);
        }
      } catch (e) { clearInterval(state.poll); toast(e.message, 6000); }
    }, 500);
  }

  // ------------------------------------------------------------------ render
  function showProgress(label, pct) {
    $("progressWrap").classList.remove("hidden");
    $("progressLabel").textContent = label;
    $("progressBar").style.width = `${Math.max(3, Math.min(100, pct))}%`;
  }
  const hideProgress = () => $("progressWrap").classList.add("hidden");

  function render() {
    const j = state.job; if (!j) return;
    const busy = ["analyzing", "processing"].includes(j.state);
    if (busy) {
      const p = j.progress || {}; const pct = p.total ? (100 * p.done / p.total) : 5;
      showProgress(`${p.phase || "Working"}… ${p.done || 0}/${p.total || "?"}`, pct);
    } else hideProgress();

    // files table
    $("filesBody").innerHTML = j.files.map((f) => {
      let st;
      if (f.error) st = `<span class="pill fail">${esc(f.error)}</span>`;
      else if (j.state === "analyzing") st = `<span class="pill busy">Analysing…</span>`;
      else if (f.needs_review) st = `<span class="pill review">${f.needs_review} need review</span>`;
      else st = `<span class="pill ok">Ready</span>`;
      return `<tr><td>${esc(f.name)}</td><td>${f.pages}</td><td>${j.state === "analyzing" ? "…" : f.labels_detected}</td><td>${j.state === "analyzing" ? "…" : f.needs_review}</td><td>${st}</td></tr>`;
    }).join("");
    const t = j.totals;
    $("filesFoot").innerHTML = j.files.length > 1 ? `<tr><td>Total (${t.files} files)</td><td>${t.pages}</td><td>${t.labels_detected}</td><td>${t.needs_review}</td><td></td></tr>` : "";

    // warnings
    const warn = j.state === "analyzing" ? [] : j.labels.filter((l) => (l.status === "review" || l.status === "failed") && l.approved === null);
    $("warnings").innerHTML = warn.map((l) => {
      const where = `${j.files.length > 1 ? esc(l.file_name) + " · " : ""}Page ${l.page + 1}`;
      const msg = l.status === "failed" ? `No shipping label detected on ${where}.` : `Label detection confidence is low for ${where} (${l.confidence.toFixed(0)}%).`;
      return `<div class="warn-row ${l.status === "failed" ? "fail" : ""}"><span>⚠ ${msg}</span><button class="btn small warn" data-review="${l.file_idx}:${l.page}">Review Page</button></div>`;
    }).join("");

    // process / download
    $("processBtn").disabled = busy || j.totals.included === 0;
    $("processBtn").textContent = j.state === "processing" ? "Processing…" : "Process Labels";
    const done = j.state === "done" && j.outputs && j.outputs.has_pdf;
    $("downloadRow").classList.toggle("hidden", !done);
    if (done) {
      $("processedText").textContent = `Processed: ${j.outputs.pages}/${j.totals.labels_detected}`;
      $("dlPdf").href = `/api/jobs/${j.job_id}/download/pdf`;
      $("dlZip").href = `/api/jobs/${j.job_id}/download/zip`;
    } else if (!busy) {
      $("processedText").textContent = j.labels.length ? `${j.totals.included} label(s) will be placed on 4×6 pages` : "";
    } else $("processedText").textContent = "";

    renderLabels();
  }

  function renderLabels() {
    const j = state.job;
    if (!j || j.state === "analyzing" || !j.labels.length) { $("labelsCard").classList.add("hidden"); return; }
    $("labelsCard").classList.remove("hidden");
    $("labelsCount").textContent = `(${j.totals.labels_detected} detected · ${j.totals.included} in output)`;
    const s = j.settings;
    $("labelsGrid").innerHTML = j.labels.map((l) => {
      const cls = [l.status === "failed" ? "failed" : "", l.status === "review" && l.approved === null ? "review" : "", !l.included ? "excluded" : ""].join(" ");
      const bust = `${(l.crop || []).join(",")}|${s.page_margin_pt}|${s.auto_rotate}`;
      const thumb = l.crop ? `<img loading="lazy" alt="Label preview" src="/api/jobs/${j.job_id}/labels/${l.uid}/preview.png?v=${encodeURIComponent(bust)}">` : `<div class="nolabel">No shipping label detected on this page</div>`;
      const bc = l.output && l.output.barcode_check;
      let bcTxt = "";
      if (bc && bc.checked) bcTxt = bc.level === "ok" ? `<span class="pill ok" title="Decoded on the 4×6 page at 203 dpi (thermal printer resolution)">Barcode ✓</span>`
        : bc.level === "marginal" ? `<span class="pill review" title="Readable at 300 dpi but not at 203 dpi - the source barcode is a low-resolution image">Barcode ⚠</span>`
        : `<span class="pill fail" title="Barcode could not be read on the output page">Barcode ✗</span>`;
      const b = l.breakdown || {}; const parts = b.parts || {}; const mx = b.max || {};
      const rows = Object.keys(mx).map((k) => `<tr><td>${esc(k)}</td><td style="text-align:right">${parts[k] ?? 0}/${mx[k]}</td></tr>`).join("") +
        Object.entries(b.penalties || {}).map(([k, v]) => `<tr><td>− ${esc(k)}</td><td style="text-align:right">−${v}</td></tr>`).join("");
      const reasons = (l.reasons || []).filter((r) => l.status !== "detected" || /inside|cuts|outside/.test(r));
      const incBtn = l.status === "failed" ? "" : (l.included
        ? `<button class="btn small" data-include="${l.uid}" data-val="false">Exclude</button>`
        : `<button class="btn small" data-include="${l.uid}" data-val="true">${l.status === "review" ? "Approve" : "Include"}</button>`);
      const dl = l.output && l.output.output_page ? `<a class="btn small" href="/api/jobs/${j.job_id}/download/label/${l.uid}">PDF</a>` : "";
      return `<div class="lcard ${cls}">
        <div class="thumb">${thumb}</div>
        <div class="lbody">
          <div class="lrow"><strong>#${l.seq} · Page ${l.page + 1}${l.index_on_page ? " (" + (l.index_on_page + 1) + ")" : ""}</strong><span class="pill ${statusClass(l.status)}">${statusText(l.status)}</span></div>
          <div class="lrow"><span>Shipping Label Detection</span><span class="conf">${l.status === "failed" ? "—" : l.confidence.toFixed(0) + "%"}</span></div>
          <div class="meta">${esc(l.order_id || l.file_name)} · ${methodText(l.method)} ${bcTxt}</div>
          ${reasons.length ? `<ul class="reasons">${reasons.slice(0, 3).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
          ${l.status !== "failed" ? `<details class="bd"><summary>Score breakdown</summary><table>${rows}</table></details>` : ""}
          <div class="lbtns"><button class="btn small" data-review="${l.file_idx}:${l.page}">Review Page</button>${incBtn}${dl}</div>
        </div></div>`;
    }).join("");
  }

  // ------------------------------------------------------------------ actions
  function bindActions() {
    $("processBtn").addEventListener("click", processNow);
    $("reprocessBtn").addEventListener("click", processNow);
    $("clearBtn").addEventListener("click", async () => {
      if (state.job) { try { await fetch(`/api/jobs/${state.job.job_id}`, { method: "DELETE" }); } catch (_) {} }
      state.job = null; clearInterval(state.poll);
      ["summaryCard", "labelsCard"].forEach((id) => $(id).classList.add("hidden"));
      toast("Cleared — temporary files deleted");
    });
    $("printBtn").addEventListener("click", () => {
      const f = $("printFrame");
      f.onload = () => { try { f.contentWindow.focus(); f.contentWindow.print(); } catch (_) { window.open(f.src, "_blank"); } };
      f.src = `/api/jobs/${state.job.job_id}/download/pdf?inline=1&t=${Date.now()}`;
    });
    // setting changes that affect status/crops -> sync to server
    ["setThreshold", "setPadding", "setMargin", "setRotate", "setIncludeLow", "setStrip"].forEach((id) =>
      $(id).addEventListener("change", async () => {
        if (!state.job || ["analyzing", "processing"].includes(state.job.state)) return;
        try { state.job = await post(`/api/jobs/${state.job.job_id}/settings`, readSettings()); render(); } catch (e) { toast(e.message); }
      }));
    document.addEventListener("click", async (e) => {
      const rv = e.target.closest("[data-review]");
      if (rv) { const [f, p] = rv.dataset.review.split(":").map(Number); openModal(f, p); return; }
      const inc = e.target.closest("[data-include]");
      if (inc) {
        try {
          state.job = await post(`/api/jobs/${state.job.job_id}/labels/${inc.dataset.include}/include`, { include: inc.dataset.val === "true" });
          render();
          if (state.modal) await loadModal();
          toast(inc.dataset.val === "true" ? "Label will be included" : "Label excluded");
        }
        catch (err) { toast(err.message); }
      }
    });
  }

  async function processNow() {
    if (!state.job) return;
    try {
      state.job = await post(`/api/jobs/${state.job.job_id}/process`, { settings: readSettings(), filename: $("setFilename").value.trim() });
      render(); startPolling();
    } catch (e) { toast(e.message); }
  }

  // ------------------------------------------------------------------ review modal
  function bindModal() {
    $("modalClose").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
    document.querySelectorAll("[data-redetect]").forEach((b) => b.addEventListener("click", async () => {
      const m = state.modal; if (!m) return;
      b.disabled = true; const old = b.textContent; b.textContent = "Working…";
      try {
        state.job = await post(`/api/jobs/${state.job.job_id}/pages/${m.f}/${m.p}/redetect`, { mode: b.dataset.redetect || null });
        render(); await loadModal();
        toast("Page re-detected");
      } catch (e) { toast(e.message); }
      b.disabled = false; b.textContent = old;
    }));
    const view = $("pageView"), box = $("drawBox");
    const rel = (e) => { const r = $("pageImg").getBoundingClientRect(); return { x: Math.max(0, Math.min(r.width, e.clientX - r.left)), y: Math.max(0, Math.min(r.height, e.clientY - r.top)) }; };
    view.addEventListener("mousedown", (e) => { e.preventDefault(); state.drag = rel(e); box.classList.remove("hidden"); drawBox(state.drag, state.drag); });
    window.addEventListener("mousemove", (e) => { if (state.drag) drawBox(state.drag, rel(e)); });
    window.addEventListener("mouseup", (e) => {
      if (!state.drag) return;
      const a = state.drag, b = rel(e); state.drag = null;
      const img = $("pageImg").getBoundingClientRect(); const info = state.modal.info;
      const sx = info.width / img.width, sy = info.height / img.height;
      const r = [Math.min(a.x, b.x) * sx + info.x0, Math.min(a.y, b.y) * sy + info.y0, Math.max(a.x, b.x) * sx + info.x0, Math.max(a.y, b.y) * sy + info.y0];
      if (r[2] - r[0] < 20 || r[3] - r[1] < 20) { box.classList.add("hidden"); state.manualRect = null; $("saveManual").disabled = true; return; }
      state.manualRect = r; $("saveManual").disabled = false;
    });
    $("saveManual").addEventListener("click", async () => {
      const m = state.modal; if (!m || !state.manualRect) return;
      const replace = $("manualTarget").value || null;
      try {
        state.job = await post(`/api/jobs/${state.job.job_id}/pages/${m.f}/${m.p}/manual`, { rect: state.manualRect, replace_uid: replace });
        render(); await loadModal(); toast("Manual crop saved");
      } catch (e) { toast(e.message); }
    });
  }
  function drawBox(a, b) {
    const box = $("drawBox"), img = $("pageImg");
    const off = { x: img.offsetLeft, y: img.offsetTop };
    box.style.left = `${Math.min(a.x, b.x) + off.x}px`; box.style.top = `${Math.min(a.y, b.y) + off.y}px`;
    box.style.width = `${Math.abs(b.x - a.x)}px`; box.style.height = `${Math.abs(b.y - a.y)}px`;
  }
  async function openModal(f, p) {
    state.modal = { f, p }; $("modal").classList.remove("hidden");
    await loadModal();
  }
  async function loadModal() {
    const m = state.modal, j = state.job;
    state.manualRect = null; $("saveManual").disabled = true; $("drawBox").classList.add("hidden");
    m.info = await api(`/api/jobs/${j.job_id}/pages/${m.f}/${m.p}/info`);
    $("modalTitle").textContent = `${m.info.file} — Page ${m.p + 1}`;
    $("pageImg").src = `/api/jobs/${j.job_id}/pages/${m.f}/${m.p}/image.png?dpi=110&t=${Date.now()}`;
    const labs = m.info.labels;
    $("pageLabels").innerHTML = `<h4>Detected on this page</h4>` + (labs.length ? labs.map((l, i) => `
      <div class="plabel"><div class="lrow"><strong>#${i + 1}</strong><span class="pill ${statusClass(l.status)}">${statusText(l.status)}${l.status !== "failed" ? " · " + l.confidence.toFixed(0) + "%" : ""}</span></div>
      <div class="meta">${methodText(l.method)}${l.order_id ? " · " + esc(l.order_id) : ""}</div>
      ${(l.reasons || []).length ? `<ul class="reasons">${l.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      ${l.status !== "failed" ? `<div class="lbtns"><button class="btn small" data-include="${l.uid}" data-val="true">Approve</button><button class="btn small" data-include="${l.uid}" data-val="false">Exclude</button></div>` : ""}
      </div>`).join("") : `<p class="muted">Nothing detected.</p>`);
    $("manualTarget").innerHTML = `<option value="">Add as new label</option>` + labs.filter((l) => l.status !== "failed").map((l, i) => `<option value="${l.uid}">Replace label #${i + 1}</option>`).join("");
  }
  function closeModal() { $("modal").classList.add("hidden"); state.modal = null; state.drag = null; }

  init().catch((e) => toast("Could not start: " + e.message, 8000));
})();

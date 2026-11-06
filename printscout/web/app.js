/* PrintScout dashboard - vanilla JS single-page app. */
"use strict";

const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];
// settings = saved settings, status = which products tab is open, current = the product in the drawer
const state = { settings: null, status: "new", current: null };

// helpers
// escape anything from the db/eBay before it goes into innerHTML
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// formatters, all show a dash for missing values
const money = v => (v == null ? "–" : `${v < 0 ? "-" : ""}£${Math.abs(v).toFixed(2)}`);
const num = v => (v == null ? "–" : Number(v).toLocaleString());
const pct = v => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
// score bands match the verdicts from the evaluator (70+ strong, 40 and under avoid)
const scoreClass = s => (s >= 70 ? "good" : s < 40 ? "bad" : "");

// fetch wrapper for the backend. pass {json: ...} to send a body (defaults to POST).
// throws with FastAPI's "detail" message so guard() can show it
async function api(path, opts = {}) {
  if (opts.json !== undefined) {
    opts = { method: "POST", ...opts, body: JSON.stringify(opts.json), headers: { "Content-Type": "application/json" } };
  }
  const res = await fetch(path, opts);
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : res.statusText);
  return data;
}

// little message at the bottom of the screen
function toast(msg, error = false) {
  const el = document.createElement("div");
  el.className = error ? "error" : "";
  el.textContent = msg;
  $("#toast").append(el);
  setTimeout(() => el.remove(), error ? 6000 : 3000);
}

// wrap async handlers so an error shows as a red toast instead of vanishing in the console
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

// pages
// the url hash decides the page, so back/forward and bookmarks work
const PAGES = { overview: loadOverview, find: loadFind, products: loadProducts, detector: loadDetector, settings: loadSettings };
function show() {
  const page = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : "overview";
  $$(".page").forEach(p => p.classList.toggle("active", p.id === `page-${page}`));
  $$("header nav a").forEach(a => a.classList.toggle("active", a.hash === `#${page}`));
  guard(PAGES[page])();
}
window.addEventListener("hashchange", show);

// jobs
// long tasks (scans, training, renders...) run as background jobs on the server.
// this polls one every second until it finishes. log/bar are optional elements to fill in
function watchJob(job, { log, bar, onTick, onDone } = {}) {
  const timer = setInterval(async () => {
    let j;
    try { j = await api(`/api/jobs/${job.id}`); } catch { return; }  // just try again next tick
    if (bar) bar.style.width = `${Math.round(j.progress * 100)}%`;
    if (log) {
      log.textContent = j.logs.join("\n");
      log.scrollTop = log.scrollHeight;
    }
    onTick?.(j);
    if (j.status !== "running") {
      clearInterval(timer);
      onDone?.(j);
    }
  }, 1000);
}
// a job of this kind that's still running, so a page can pick it back up after a reload
async function runningJob(kind) {
  const jobs = await api("/api/jobs");
  return jobs.find(j => j.kind === kind && j.status === "running");
}

// overview
async function loadOverview() {
  const s = await api("/api/stats");
  const tile = (n, label) => `<div><b>${num(n)}</b>${label}</div>`;
  $("#stats").innerHTML = tile(s.listings, "products found") + tile(s.new, "new to review")
    + tile(s.shortlisted, "shortlisted") + tile(s.sellers, "sellers scanned") + tile(s.scans, "scans run");
  $("#topList").innerHTML = s.top.length
    ? s.top.map(productRow).join("")
    : `<tr><td class="muted">Nothing yet - <a href="#find">run a scan</a>.</td></tr>`;
}

// find products
async function loadFind() {
  state.settings = await api("/api/settings");
  if (!$("#scanMax").value) $("#scanMax").value = state.settings.max_sellers;
  const job = await runningJob("scan");
  if (job) attachScan(job);
}
// Start swaps to Stop while the scan runs, and the log streams in
function attachScan(job) {
  $("#scanBtn").classList.add("hidden");
  $("#scanStop").classList.remove("hidden");
  $("#scanStop").onclick = guard(() => api(`/api/jobs/${job.id}/stop`, { method: "POST" }));
  watchJob(job, {
    log: $("#scanLog"), bar: $("#scanBar"),
    onDone: j => {
      $("#scanBtn").classList.remove("hidden");
      $("#scanStop").classList.add("hidden");
      toast(j.status === "done" ? "Scan finished" : `Scan ${j.status}`, j.status === "failed");
    },
  });
}
$("#scanBtn").onclick = guard(async () => {
  const seeds = $("#seeds").value.split("\n").map(s => s.trim()).filter(Boolean);
  const job = await api("/api/scan", { json: { seeds, max_sellers: Number($("#scanMax").value) || null } });
  attachScan(job);
});

// products
// one table row. used on the products page and the overview's top 5.
// the title links to the eBay listing, the buttons change its status, clicking anywhere else opens the panel
const productRow = p => `
  <tr data-id="${esc(p.item_id)}">
    <td class="score ${scoreClass(p.score)}">${Math.round(p.score)}</td>
    <td>${p.image_url ? `<img src="${esc(p.image_url)}" alt="" loading="lazy">` : ""}</td>
    <td><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a>${p.ip_risk === "high" ? ` <span class="bad">IP risk</span>` : ""}
      ${p.group_size > 1 ? `<div class="muted">${p.group_size} similar listings, ${num(p.group_sold)} sold</div>` : ""}</td>
    <td>${num(p.sold)}</td><td>${money(p.price)}</td>
    <td class="${p.profit < 0 ? "bad" : ""}">${money(p.profit)}</td><td>${esc(p.seller)}</td>
    <td class="actions">
      <button data-open>Details</button>
      ${p.status === "new" ? `<button data-status="shortlisted">Shortlist</button><button data-status="dismissed">Dismiss</button>`
        : `<button data-status="new">${p.status === "shortlisted" ? "Remove" : "Restore"}</button>`}
    </td>
  </tr>`;

async function loadProducts() {
  const q = encodeURIComponent($("#search").value.trim());
  const rows = await api(`/api/products?status=${state.status}&q=${q}`);
  $("#productRows").innerHTML = rows.length
    ? rows.map(productRow).join("")
    : `<tr><td colspan="8" class="muted">No products here.</td></tr>`;
}
$$("#tabs button").forEach(b => b.onclick = () => {
  state.status = b.dataset.status;
  $$("#tabs button").forEach(x => x.classList.toggle("active", x === b));
  // market checks are for new finds
  $("#batchComp").classList.toggle("hidden", state.status !== "new");
  guard(loadProducts)();
});
// wait until typing stops before searching
let searchTimer;
$("#search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(guard(loadProducts), 300); };

// clicks inside a product row: a status button, the eBay link, or anywhere else opens the panel
document.addEventListener("click", guard(async e => {
  const row = e.target.closest("tr[data-id]");
  if (!row || e.target.closest("a")) return;  // let the eBay link open normally
  const btn = e.target.closest("button[data-status]");
  if (!btn) return openProduct(row.dataset.id);
  await api(`/api/products/${row.dataset.id}/status`, { method: "PUT", json: { status: btn.dataset.status } });
  toast({ shortlisted: "Shortlisted", dismissed: "Dismissed", new: "Moved back to new finds" }[btn.dataset.status]);
  // reload whichever page the row was on
  if ($("#page-overview").classList.contains("active")) loadOverview(); else loadProducts();
}));

// the batch button starts a job, then reloads the table when it's done
const batch = (path, label) => guard(async () => {
  const job = await api(path, { method: "POST" });
  toast(`${label} started`);
  watchJob(job, { onDone: j => { toast(j.message || `${label} ${j.status}`, j.status === "failed"); guard(loadProducts)(); } });
});
$("#batchComp").onclick = batch("/api/competition/batch", "Market check");

// product drawer
async function openProduct(id) {
  if (!state.settings) state.settings = await api("/api/settings");
  state.current = await api(`/api/products/${id}`);
  renderDrawer();
  $("#drawer").classList.remove("hidden");
}
$("#dClose").onclick = () => $("#drawer").classList.add("hidden");
document.addEventListener("keydown", e => { if (e.key === "Escape") $("#drawer").classList.add("hidden"); });

// redrawn from scratch every time something in it changes
function renderDrawer() {
  const { product: p, evaluation: ev, similar } = state.current;
  const c = ev.costs;
  const s = state.settings;
  $("#dBody").innerHTML = `
    ${p.image_url ? `<img class="main" src="${esc(p.image_url)}" alt="">` : ""}
    <h2><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></h2>
    <p>${esc(p.seller)} · ${num(p.sold)} sold at ${money(p.price)}</p>
    <h1 class="${scoreClass(ev.score)}">${Math.round(ev.score)} / 100 - ${esc(ev.verdict)}</h1>
    <div class="row">
      <button id="dShort">${p.status === "shortlisted" ? "Remove from shortlist" : "Shortlist"}</button>
      <button id="dDismiss">Dismiss</button>
      <a href="${esc(p.amazon_search_url)}" target="_blank" rel="noopener">Search Amazon</a>
    </div>
    ${ev.flags.map(f => `<div class="flag">⚠ ${esc(f)}</div>`).join("")}

    <h2>Why this score</h2>
    <table>${ev.factors.map(f => `
      <tr class="factor"><td>${esc(f.label)} <span class="muted">${Math.round(f.weight * 100)}%</span></td>
        <td><div class="bar"><div style="width:${f.score}%"></div></div></td>
        <td><b>${Math.round(f.score)}</b></td></tr>
      <tr><td colspan="3" class="muted">${esc(f.detail)}</td></tr>`).join("")}
    </table>
    <p><button id="dComp">${p.market_sold == null ? "Check market" : "Re-check market"}</button></p>

    <h2>Your print estimate</h2>
    <label>Filament (g) <input type="number" id="dGrams" min="0" value="${p.grams ?? ""}" placeholder="${s.default_grams}"></label>
    <label>Print time (h) <input type="number" id="dHours" min="0" step="0.1" value="${p.print_hours ?? ""}" placeholder="${s.default_print_hours}"></label>
    <label>Your Amazon price <input type="number" id="dPrice" min="0" step="0.01" value="${p.sell_price ?? ""}" placeholder="eBay ${money(p.price)}"></label>
    <button id="dSave" class="primary">Save &amp; re-score</button>

    ${c ? `
    <h2>Costs per sale</h2>
    <table>
      <tr><td>Sale price</td><td>${money(c.price)}</td></tr>
      <tr><td>Amazon fees</td><td>-${money(c.fees)}</td></tr>
      <tr><td>Filament (${c.grams} g)</td><td>-${money(c.material)}</td></tr>
      <tr><td>Electricity (${c.print_hours} h)</td><td>-${money(c.electricity)}</td></tr>
      <tr><td>Machine wear</td><td>-${money(c.machine)}</td></tr>
      <tr><td>Packaging + postage</td><td>-${money(c.packaging + c.postage)}</td></tr>
      <tr><td><b>Profit</b></td><td class="${c.profit < 0 ? "bad" : "good"}"><b>${money(c.profit)}</b> (${pct(c.margin)})</td></tr>
      <tr><td>Profit per printer-hour</td><td>${money(c.profit_per_hour)}</td></tr>
    </table>` : ""}

    ${similar.length ? `
    <h2>Similar listings (${similar.length})</h2>
    <table>${similar.map(productRowSmall).join("")}</table>` : ""}`;

  const id = p.item_id;
  // the innerHTML above wipes the buttons, so the handlers get attached again each time
  const reload = data => { state.current = data; renderDrawer(); guard(loadProducts)(); };
  const setStatus = status => guard(async () => {
    await api(`/api/products/${id}/status`, { method: "PUT", json: { status } });
    p.status = status;
    renderDrawer();
    guard(loadProducts)();
  });
  $("#dShort").onclick = setStatus(p.status === "shortlisted" ? "new" : "shortlisted");
  $("#dDismiss").onclick = setStatus("dismissed");
  // blank boxes are sent as null so the defaults from settings get used
  $("#dSave").onclick = guard(async () => {
    const val = sel => ($(sel).value === "" ? null : Number($(sel).value));
    reload(await api(`/api/products/${id}/costs`, { method: "PUT", json: { grams: val("#dGrams"), print_hours: val("#dHours"), sell_price: val("#dPrice") } }));
  });
  // waits for the reply rather than running as a job, it's only a few requests
  $("#dComp").onclick = guard(async () => {
    $("#dComp").disabled = true;
    $("#dComp").textContent = "Searching eBay";
    try { reload(await api(`/api/products/${id}/competition`, { method: "POST" })); }
    finally { $("#dComp").disabled = false; }
  });
}
const productRowSmall = m => `<tr data-id="${esc(m.item_id)}"><td><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a></td><td>${num(m.sold)} sold</td><td>${money(m.price)}</td><td>${esc(m.seller)}</td></tr>`;

// detector
async function loadDetector() {
  const d = await api("/api/detector");
  $("#torchWarn").classList.toggle("hidden", d.torch);
  $("#datasetInfo").innerHTML = `In the training set: <b>${num(d.dataset["3d_printed"])}</b> 3D printed,
    <b>${num(d.dataset.not_3d_printed)}</b> not 3D printed.<br>
    <span class="muted">Available from scans: ${num(d.sources.positives)} printed, ${num(d.sources.negatives)} not printed.</span>`;
  // metrics only exist once a model has been trained.
  // confusion matrix: rows are the real label, columns what the model said
  const m = d.metrics;
  $("#metrics").innerHTML = m ? `
    <h2>Last trained model</h2>
    <p class="muted">${esc(m.arch)}, best epoch ${m.best_epoch}, ${m.val_size} validation images</p>
    <div class="stats">
      <div><b>${pct(m.accuracy)}</b>Accuracy</div><div><b>${pct(m.precision)}</b>Precision</div>
      <div><b>${pct(m.recall)}</b>Recall</div><div><b>${m.f1.toFixed(3)}</b>F1</div>
    </div>
    <h2>Confusion matrix</h2>
    <table class="confusion" style="width:auto">
      <tr><th></th><th>Said printed</th><th>Said not</th></tr>
      <tr><th>Printed</th><td class="good">${m.confusion.tp}</td><td class="bad">${m.confusion.fn}</td></tr>
      <tr><th>Not printed</th><td class="bad">${m.confusion.fp}</td><td class="good">${m.confusion.tn}</td></tr>
    </table>` : "";
  const job = await runningJob("train");
  if (job) attachTrain(job);
  const build = await runningJob("autobuild");
  if (build) attachAutoBuild(build);
}
// downloading the photos takes a while, so show a progress bar and the latest log line
function attachAutoBuild(job) {
  $("#autoBuild").disabled = true;
  watchJob(job, {
    bar: $("#autoBar"),
    onTick: j => ($("#autoMsg").textContent = `${Math.round(j.progress * 100)}% - ${j.message}`),
    onDone: j => {
      $("#autoBuild").disabled = false;
      $("#autoMsg").textContent = j.message;
      toast(j.status === "done" ? "Training set updated" : `Auto-build ${j.status}`, j.status === "failed");
      guard(loadDetector)();
    },
  });
}
$("#autoBuild").onclick = guard(async () => {
  attachAutoBuild(await api("/api/detector/auto-build", { json: { max_per_class: Number($("#autoMax").value) } }));
});
function attachTrain(job) {
  $("#trainBtn").disabled = true;
  watchJob(job, {
    log: $("#trainLog"), bar: $("#trainBar"),
    onDone: j => {
      $("#trainBtn").disabled = false;
      toast(j.status === "done" ? "Training complete" : `Training ${j.status}`, j.status === "failed");
      guard(loadDetector)();
    },
  });
}
$("#trainBtn").onclick = guard(async () => {
  attachTrain(await api("/api/detector/train", { json: { epochs: Number($("#trainEpochs").value), arch: $("#trainArch").value } }));
});
$("#testFile").onchange = guard(async e => {
  const fd = new FormData();
  fd.append("file", e.target.files[0]);
  const r = await api("/api/detector/predict", { method: "POST", body: fd });
  $("#testResult").innerHTML = `<p><b class="${r.is_3d_printed ? "good" : ""}">${r.is_3d_printed ? "3D printed" : "Not 3D printed"}</b>
    - ${pct(r.probability)} sure it's printed (threshold ${pct(r.threshold)})</p>`;
});

// settings
// [key, label] for each setting. the type of input comes from the saved value
const SETTINGS = [
  ["Scanner", [["required_phrase", "Phrase that means 3D printed"], ["excluded_phrases", "Skip listings mentioning (comma separated)"],
    ["min_seller_feedback", "Minimum seller feedback"], ["max_sellers", "Sellers per scan"], ["workers", "Worker threads"],
    ["request_delay", "Delay between requests (s)"], ["check_descriptions", "Check listing descriptions"],
    ["use_image_detector", "Use the image detector"], ["detector_threshold", "Detector threshold (0-1)"]]],
  ["Costs", [["fee_percent", "Amazon fee %"], ["fee_minimum", "Minimum fee (£)"], ["fee_fixed", "Fixed fee per item (£)"],
    ["filament_cost_per_kg", "Filament £/kg"], ["electricity_cost_per_hour", "Electricity £/hour"],
    ["machine_cost_per_hour", "Machine wear £/hour"], ["packaging_cost", "Packaging £"], ["postage_cost", "Postage £"],
    ["default_grams", "Default filament (g)"], ["default_print_hours", "Default print time (h)"]]],
  ["Score weights", [["weight_demand", "Demand"], ["weight_market", "Market (90 days)"],
    ["weight_competition", "Competition"], ["weight_margin", "Profit margin"], ["weight_efficiency", "Profit per print hour"],
    ["ip_risk_penalty", "IP risk penalty (points)"]]],
];
async function loadSettings() {
  const s = (state.settings = await api("/api/settings"));
  $("#settingsForm").innerHTML = SETTINGS.map(([group, fields]) => `<h2>${group}</h2>` + fields.map(([k, label]) => {
    const v = s[k];
    const input = typeof v === "boolean" ? `<input type="checkbox" name="${k}" ${v ? "checked" : ""}>`
      : typeof v === "number" ? `<input type="number" step="any" name="${k}" value="${v}">`
      : `<input name="${k}" size="40" value="${esc(Array.isArray(v) ? v.join(", ") : v)}">`;
    return `<label>${label} ${input}</label>`;
  }).join("")).join("");
}
// reverse of the above, each field back to the right type
$("#saveSettings").onclick = guard(async () => {
  const body = {};
  for (const el of $("#settingsForm").elements) {
    body[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
  }
  state.settings = await api("/api/settings", { method: "PUT", json: body });
  toast("Settings saved");
});

show();

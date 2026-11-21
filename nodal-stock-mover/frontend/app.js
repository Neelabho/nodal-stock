"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
const lakh = (n) => "₹" + (n / 1e5).toLocaleString("en-IN", { maximumFractionDigits: 1 }) + " L";
const num = (n) => Number(n).toLocaleString("en-IN");
const cov = (x) => (x === null || x === undefined || x >= 365 ? "1y+" : x < 100 ? x.toFixed(0) + "d" : x.toFixed(0) + "d");

const S = { tab: "overview", ov: null, acts: [], f: { q: "", type: "", reason: "", urgency: "", store: "", cat: "", conf: "", appr: false, dec: "", sort: "default" }, page: 0, dec: {}, quality: null, safety: null };
const PAGE = 50;
const MQ = window.matchMedia("(max-width:700px)");
const isMobile = () => MQ.matches;
const ico = (n) => `<i data-lucide="${n}"></i>`;
let _iconQueued = false;
new MutationObserver(() => { if (_iconQueued || !window.lucide) return; _iconQueued = true; requestAnimationFrame(() => { _iconQueued = false; window.lucide.createIcons(); }); }).observe(document.body, { childList: true, subtree: true });
const TITLES = { overview: "Today", register: "All moves", ask: "Ask", data: "Add data", ai: "AI review", runs: "Daily runs", history: "History", map: "Map", safety: "Rules & gaps", quality: "Data quality", model: "Forecast", method: "Method" };
const MTITLES = TITLES;

S.decFull = {};
function clientId() { try { let c = localStorage.getItem("nodal-client-id"); if (!c) { c = Math.random().toString(36).slice(2, 10); localStorage.setItem("nodal-client-id", c); } return c; } catch { return "anon"; } }
async function loadDec() {
  const d = await api("/api/decisions");
  S.dec = {}; S.decFull = {};
  Object.entries(d.current).forEach(([id, v]) => { S.decFull[id] = v.decision; S.dec[id] = v.decision === "rejected" ? "rejected" : "approved"; });
}
async function refreshStatus() { try { S.status = await api("/api/status"); } catch { /* keep last */ } }
function saveDec() {}
async function api(p, opt) {
  const r = await fetch(p, opt);
  if (r.headers.get("x-nodal-cache")) S.stale = true; else if (r.ok && (!opt || !opt.method || opt.method === "GET")) S.stale = false;
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch { /* */ } throw new Error(m); }
  return r.json();
}

async function boot(refresh) {
  $("#bar-title").textContent = TITLES[S.tab];
  $("#main").innerHTML = '<div class="loading">Loading today\'s plan…</div>';
  try {
    if (refresh) await api("/api/run", { method: "POST" });
    S.ov = await api("/api/overview");
    S.acts = await api("/api/actions");
    S.quality = null; S.safety = null;
    S.cap = await api("/api/capabilities").catch(() => ({}));
    S.ship = await api("/api/shipments");
    await loadDec(); await refreshStatus(); gateNav();
    $("#asof").textContent = `Stock as of ${S.ov.manifest.as_of} · plan dispatches ${S.ov.manifest.dispatch_date}`;
    netBanner(); render();
  } catch (e) {
    $("#main").innerHTML = `<div class="note"><b>Could not load the plan.</b><br>${esc(e.message)}<br><span class="muted">If you are offline, reconnect and try again. Operators: run <code>python -m nodal.cli daily</code> to publish a plan.</span></div>`;
  }
}

function setTab(t, filter) {
  S.tab = t;
  $("#bar-title").textContent = TITLES[t] || t;
  if (filter) { S.f = { ...S.f, q: "", type: "", reason: "", urgency: "", store: "", cat: "", conf: "", appr: false, dec: "", ...filter }; S.page = 0; }
  document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  render();
  window.scrollTo(0, 0);
}

function render() {
  const m = $("#main");
  if (!S.ov) return;
  if (S.tab === "overview") { m.innerHTML = vToday(); bindToday(m); }
  else if (S.tab === "ask") { m.innerHTML = vAsk(); bindAsk(); }
  else if (S.tab === "data") { vData(); return; }
  else if (S.tab === "register") { m.innerHTML = vRegister(); bindRegister(); }
  else if (S.tab === "map") { m.innerHTML = vMapShell(); bindMap(); }
  else if (S.tab === "model") { vModel(); return; }
  else if (S.tab === "ai") { vAI(); return; }
  else if (S.tab === "runs") { vRuns(); return; }
  else if (S.tab === "history") { vHistory(); return; }
  else if (S.tab === "quality") vQuality();
  else if (S.tab === "safety") vSafety();
  else { vMethod(); return; }
  m.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => setTab(el.dataset.go, el.dataset.f ? JSON.parse(el.dataset.f) : null)));
}

function filtered() {
  const f = S.f, q = f.q.trim().toLowerCase();
  let r = S.acts.filter((a) =>
    (!f.type || a.type === f.type) && (!f.reason || a.reason === f.reason) && (!f.urgency || a.urgency === f.urgency) &&
    (!f.store || a.source === f.store || a.destination === f.store) && (!f.cat || a.category === f.cat) && (!f.conf || a.confidence === f.conf) &&
    (!f.appr || a.requires_approval) && (!f.dec || (f.dec === "pending" ? !S.dec[a.id] : S.dec[a.id] === f.dec)) &&
    (!q || (a.sku_id + " " + a.style_name + " " + a.source + " " + a.destination + " " + a.id + " " + a.explanation).toLowerCase().includes(q)));
  if (f.sort === "value") r = [...r].sort((a, b) => b.value_mrp - a.value_mrp);
  else if (f.sort === "qty") r = [...r].sort((a, b) => b.qty - a.qty);
  else if (f.sort === "cover") r = [...r].sort((a, b) => (a.cover_before_days ?? 1e9) - (b.cover_before_days ?? 1e9));
  return r;
}
const uniq = (k) => [...new Set(S.acts.map((a) => a[k]))].sort();
function opts(list, sel, label) { return `<option value="">${label}</option>` + list.map(([v, t]) => `<option value="${esc(v)}" ${sel === v ? "selected" : ""}>${esc(t)}</option>`).join(""); }

function vRegister() {
  const f = S.f;
  const stores = S.ov.stores.map((s) => [s.store_id, `${s.store_id} · ${s.city} (${s.tier})`]);
  const reasons = [...new Set(S.acts.map((a) => a.reason))].map((r) => [r, S.acts.find((a) => a.reason === r).reason_label]);
  const rows = filtered();
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  if (S.page >= pages) S.page = pages - 1;
  const slice = rows.slice(S.page * PAGE, S.page * PAGE + PAGE);
  const approved = S.acts.filter((a) => S.dec[a.id] === "approved").length, rejected = S.acts.filter((a) => S.dec[a.id] === "rejected").length;
  return `
  <h1>All moves</h1><p class="lead">The individual moves behind each shipment. Open one for its evidence.</p>
  <div class="filters ${S.fopen ? "open" : ""}">
    <input type="search" id="f-q" placeholder="Search SKU, style, store…" value="${esc(f.q)}" aria-label="Search">
    <select id="f-type" aria-label="Type">${opts([["WH_TO_STORE", "Warehouse → Store"], ["STORE_TO_STORE", "Store → Store"], ["PULL_BACK", "Pull back"]], f.type, "All types")}</select>
    <select class="adv" id="f-reason" aria-label="Reason">${opts(reasons, f.reason, "All reasons")}</select>
    <select class="adv" id="f-urgency" aria-label="Urgency">${opts(["Critical", "High", "Normal", "Housekeeping"].map((x) => [x, x]), f.urgency, "All urgency")}</select>
    <select class="adv" id="f-store" aria-label="Store">${opts(stores, f.store, "All stores")}</select>
    <select class="adv" id="f-cat" aria-label="Category">${opts(uniq("category").map((x) => [x, x]), f.cat, "All categories")}</select>
    <select class="adv" id="f-conf" aria-label="Confidence">${opts([["high", "High confidence"], ["medium", "Medium"], ["low", "Low"]], f.conf, "Any confidence")}</select>
    <select class="adv" id="f-dec" aria-label="Decision">${opts([["pending", "Pending"], ["approved", "Approved"], ["rejected", "Rejected"]], f.dec, "Any decision")}</select>
    <select class="adv" id="f-sort" aria-label="Sort">${[["default", "Sort: priority"], ["value", "Sort: value"], ["qty", "Sort: quantity"], ["cover", "Sort: lowest cover"]].map(([v, t]) => `<option value="${v}" ${f.sort === v ? "selected" : ""}>${t}</option>`).join("")}</select>
    <label class="adv"><input type="checkbox" id="f-appr" ${f.appr ? "checked" : ""}> needs approval</label>
    <button class="btn adv" id="f-reset">Reset</button>
    <button class="btn mobile-only" id="f-toggle">${ico("sliders-horizontal")}${S.fopen ? "Hide filters" : "More filters"}</button>
    <button class="btn primary" id="f-export">Export view (${num(rows.length)})</button>
  </div>
  <div class="muted" style="font-size:.86rem">${num(rows.length)} of ${num(S.acts.length)} moves · ${approved} approved · ${rejected} rejected</div>
  ${true ? (slice.map((a, i) => cardHtml(a, false, i)).join("") || `<div class="empty">No moves match these filters.</div>`) : `<div class="tw"><table><thead><tr><th>Urgency</th><th>Move</th><th>Item</th><th>Route</th><th>Qty</th><th>Arrives</th><th>Cover</th><th>Value</th><th>Why (one line)</th><th>Decision</th></tr></thead><tbody>
  ${slice.map(rowHtml).join("") || `<tr><td colspan="10" class="empty">No moves match these filters.</td></tr>`}
  </tbody></table></div>`}
  <div class="pager"><button class="btn" id="pg-prev" ${S.page === 0 ? "disabled" : ""}>← Prev</button> Page ${S.page + 1} / ${pages} <button class="btn" id="pg-next" ${S.page >= pages - 1 ? "disabled" : ""}>Next →</button></div>`;
}

function rowHtml(a) {
  const d = S.dec[a.id];
  const fl = a.flags.map((x) => `<span class="pill flag">${esc(x.replace(/_/g, " "))}</span>`).join(" ");
  return `<tr tabindex="0" data-id="${a.id}">
    <td><span class="pill ${a.urgency}">${a.urgency}</span></td>
    <td><span class="pill type">${esc(a.type_label)}</span><span class="sub">${esc(a.reason_label)}</span></td>
    <td><span class="sku">${esc(a.sku_id)}</span><span class="sub">${esc(a.style_name)}</span></td>
    <td class="route">${esc(a.source)}<span class="arrow">→</span>${esc(a.destination)}<span class="sub">${a.dispatch_date} (+${a.lead_days}d)</span></td>
    <td class="num"><b>${a.qty}</b></td>
    <td class="num">${a.arrival_date.slice(5)}</td>
    <td class="num">${a.type === "PULL_BACK" ? "—" : cov(a.cover_before_days) + " → " + cov(a.cover_after_days)}</td>
    <td class="num">${inr(a.value_mrp)}${a.requires_approval ? '<span class="sub"><span class="pill flag">approval</span></span>' : ""}</td>
    <td class="why">${esc(a.explanation)}<div>${aiChip(a)} ${fl}</div></td>
    <td class="dec"><button data-d="approved" class="${d === "approved" ? "on-a" : ""}">${ico("check")}${a.requires_approval ? "Sign off" : "Approve"}</button><button data-d="rejected" class="${d === "rejected" ? "on-r" : ""}">${ico("x")}Reject</button></td></tr>`;
}

function bindRegister() {
  const set = (id, key, ev = "change") => $(id).addEventListener(ev, (e) => { S.f[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value; S.page = 0; render(); if (key === "q") { const i = $("#f-q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); } });
  set("#f-q", "q", "input"); set("#f-type", "type"); set("#f-reason", "reason"); set("#f-urgency", "urgency"); set("#f-store", "store"); set("#f-cat", "cat"); set("#f-conf", "conf"); set("#f-dec", "dec"); set("#f-sort", "sort"); set("#f-appr", "appr");
  $("#f-reset").onclick = () => { S.f = { q: "", type: "", reason: "", urgency: "", store: "", cat: "", conf: "", appr: false, dec: "", sort: "default" }; S.page = 0; render(); };
  $("#f-export").onclick = exportView;
  $("#f-toggle").onclick = () => { S.fopen = !S.fopen; render(); };
  $("#pg-prev").onclick = () => { S.page--; render(); window.scrollTo(0, 0); };
  $("#pg-next").onclick = () => { S.page++; render(); window.scrollTo(0, 0); };
  bindRows($("#main"));
}
function bindRows(root) {
  root.querySelectorAll("tbody tr[data-id]").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-d]");
      if (b) { setDecision(tr.dataset.id, b.dataset.d); return; }
      openDrawer(tr.dataset.id);
    });
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openDrawer(tr.dataset.id); });
  });
}

function exportView() {
  const cols = ["id", "decision", "urgency", "type_label", "reason_label", "sku_id", "style_name", "category", "size", "source", "destination", "qty", "dispatch_date", "arrival_date", "lead_days", "value_mrp", "requires_approval", "confidence", "explanation"];
  const q = (v) => '"' + String(v ?? "").replace(/"/g, '""') + '"';
  const lines = [cols.join(",")].concat(filtered().map((a) => cols.map((c) => q(c === "decision" ? S.dec[a.id] || "pending" : a[c])).join(",")));
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  const el = document.createElement("a"); el.href = url; el.download = "nodal_review_list.csv"; el.click(); URL.revokeObjectURL(url);
}

/* ------------------------------------------------------------ drawer */
const kv = (rows) => `<dl class="kv">${rows.filter((r) => r[1] !== undefined && r[1] !== null).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
function posBlock(title, p) {
  if (!p) return "";
  return `<h3>${title}</h3>` + kv([
    ["Sales in window", `${p.units_84d} units over ${p.window_days} days`], ["In-stock days", `${p.in_stock_days_84d} of ${p.window_days}`],
    ["Observed velocity", `${p.velocity_observed}/day`], ["Velocity used", `${p.velocity_used}/day${p.shrunk_to_peer ? " (shrunk toward peers: " + esc(p.peer_source) + ", " + p.peer_velocity + "/day)" : ""}`],
    ["Festival index", "×" + p.season_index], ["Effective velocity", `${p.velocity_effective}/day`], ["Zero-sales class", esc(p.zero_class)], ["Confidence", `<span class="pill ${p.confidence === "low" ? "low" : "ok"}">${esc(p.confidence)}</span>`],
    ["On hand / in transit", `${p.on_hand} / ${p.in_transit}`], ["Oldest unit age", p.oldest_unit_age_days === null ? undefined : p.oldest_unit_age_days + " days"],
  ]);
}
async function openDrawer(id) {
  const d = $("#drawer");
  d.innerHTML = '<div class="loading">Loading…</div>'; d.classList.add("open"); $("#scrim").classList.add("open"); d.setAttribute("aria-hidden", "false");
  let a;
  try { a = await api("/api/actions/" + id); } catch (e) { d.innerHTML = `<div class="note">${esc(e.message)}</div>`; return; }
  const e = a.evidence, rc = e.recipient, dn = e.donor;
  d.innerHTML = `<button class="btn x" id="dx">${ico("x")}Close</button>
  <span class="pill ${a.urgency}">${a.urgency}</span> <span class="pill type">${esc(a.type_label)} · ${esc(a.reason_label)}</span>
  <h1 style="margin-top:.5rem">${a.qty} × ${esc(a.sku_id)}</h1><div class="muted">${esc(a.style_name)} · ${esc(a.category)} · size ${esc(a.size)}</div>
  <div class="say">${esc(a.explanation)}</div>
  ${decisionBar(a)}
  ${kv([["Route", `${esc(a.source)} → ${esc(a.destination)}`], ["Dispatch → arrival", `${a.dispatch_date} → ${a.arrival_date} (${a.lead_days} days)`], ["Value at MRP", inr(a.value_mrp) + ` (${inr(a.unit_mrp)} each)`],
      ["Status", a.requires_approval ? "Needs human approval (> " + inr(S.ov.manifest.policy.approval_value_mrp) + ")" : "Proposed"], ["Supply basis", a.supply_basis === "on_hand" ? "stock on hand" : "planned inbound (status Planned)"], ["Confidence", esc(a.confidence)]])}
  ${rc && a.type !== "PULL_BACK" ? `<h3>Destination: cover</h3>` + kv([["Cover at arrival (before)", cov(rc.cover_before_days)], ["Cover after move", cov(rc.cover_after_days)], ["Projected stock at arrival", rc.projected_stock_at_arrival], ["Units needed to reach target", rc.need_units], ["Capacity room before", rc.capacity_room_before !== undefined ? `${rc.capacity_room_before} of ${rc.capacity}` : undefined]]) + posBlock("Destination: demand evidence", rc) : ""}
  ${e.supply ? `<h3>Warehouse supply</h3>` + kv([["Available before → after", `${e.supply.warehouse_available_before} → ${e.supply.warehouse_available_after ?? "—"}`], ["Competing stores", e.supply.competing_stores], ["Supply short?", e.supply.shortfall === undefined ? undefined : e.supply.shortfall ? "Yes — allocated to equalise cover" : "No"], ["Note", esc(e.supply.note || "")]]) : ""}
  ${dn ? `<h3>${a.type === "PULL_BACK" ? "Stock being pulled back" : "Source store"}: ${esc(a.source)}</h3>` + kv([["On hand before → after", `${dn.on_hand_before} → ${dn.on_hand_after}`], ["SKU cover after", dn.sku_cover_after_days !== undefined ? cov(dn.sku_cover_after_days) : undefined], ["Style cover after", dn.style_cover_after_days !== undefined ? cov(dn.style_cover_after_days) : undefined], ["Category display after / minimum", dn.category_display_after !== undefined ? `${dn.category_display_after} / ${dn.category_display_min}` : undefined], ["Protected style", dn.protected_style ? "yes" : "no"], ["Ageing candidate", dn.is_ageing ? "yes" : "no"]]) + posBlock("Source: demand evidence", dn) : ""}
  ${e.display ? `<h3>Display minimum</h3>` + kv([["Category", esc(e.display.category)], ["Units before plan / minimum", `${e.display.units_before_plan} / ${e.display.minimum}`], ["After plan", e.display.units_after_plan]]) : ""}
  ${e.capacity ? `<h3>Capacity</h3>` + kv([["Store capacity", e.capacity.capacity], ["Excess at start", e.capacity.excess_at_start]]) : ""}
  <h3>Rules checked for this move</h3><ul class="checks">${(e.checks || []).map((c) => `<li>${esc(c)}</li>`).join("")}<li>passed the independent post-plan validator</li></ul>
  ${e.assumption ? `<div class="note">Assumption: ${esc(e.assumption)}</div>` : ""}
  ${a.flags.length ? `<div>${a.flags.map((x) => `<span class="pill flag">${esc(x.replace(/_/g, " "))}</span>`).join(" ")}</div>` : ""}
  ${aiSection(a.review)}${altCompare(a, a.review)}
  <details style="margin-top:14px"><summary>Raw evidence (JSON)</summary><pre>${esc(JSON.stringify(e, null, 1))}</pre></details>`;
  $("#dx").onclick = closeDrawer;
  bindDecisionBar(d, a);
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#scrim").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }

/* ------------------------------------------------------------ data quality */
async function vQuality() {
  $("#main").innerHTML = '<div class="loading">Loading audit…</div>';
  S.quality = S.quality || (await api("/api/audit"));
  const a = S.quality;
  $("#main").innerHTML = `<h1>Data quality</h1><p class="lead">Computed from the supplied CSVs at run time. Source files are never modified; problems are handled in the processing layer and listed here. Source hash <code>${esc(a.source_hash)}</code>.</p>
  <h2>Findings</h2><div class="tw"><table><thead><tr><th>Severity</th><th>Table</th><th>Check</th><th>Count</th><th>Detail</th><th>How NODAL handles it</th></tr></thead><tbody>
  ${a.findings.map((f) => `<tr style="cursor:default"><td><span class="pill ${f.severity}">${f.severity}</span></td><td>${esc(f.table)}</td><td>${esc(f.check)}</td><td class="num">${num(f.count)}</td><td>${esc(f.detail)}</td><td>${esc(f.handling)}</td></tr>`).join("")}</tbody></table></div>
  <h2>Table profiles</h2><div class="tw"><table><thead><tr><th>Table</th><th>Rows</th><th>Columns</th><th>Blank cells</th><th>Duplicate rows</th></tr></thead><tbody>
  ${a.tables.map((t) => `<tr style="cursor:default"><td>${esc(t.table)}</td><td class="num">${num(t.rows)}</td><td class="num">${t.columns}</td><td>${esc(Object.entries(t.blank_cells).map(([k, v]) => k + ": " + num(v)).join(", ") || "—")}</td><td class="num">${t.duplicate_rows}</td></tr>`).join("")}</tbody></table></div>`;
}

/* ------------------------------------------------------------ rules & gaps */
async function vSafety() {
  $("#main").innerHTML = '<div class="loading">Loading…</div>';
  S.safety = S.safety || (await api("/api/safety"));
  const s = S.safety;
  const blk = Object.entries(s.blocked_samples).map(([rule, list]) => `<h3>${esc(rule.replace(/_/g, " "))} <span class="muted">(${s.blocked_counts[rule]} candidate sources refused)</span></h3><ul class="checks" style="list-style:disc;padding-left:20px">${list.map((x) => `<li style="list-style:disc">${esc(x.detail)}</li>`).join("")}</ul>`).join("");
  const un = s.unresolved.filter((u) => u.kind === "unmet_demand").slice(0, 100);
  const other = s.unresolved.filter((u) => u.kind !== "unmet_demand");
  $("#main").innerHTML = `<h1>Rules &amp; gaps</h1><p class="lead">The validator re-derives the post-plan stock state from the source tables and checks every hard rule from the brief. Violating moves are excluded, never left approvable.</p>
  <div class="card">${s.validation.rules.map((r) => `<div class="rule"><span class="pill ${r.passed ? "pass" : "error"}">${r.passed ? "PASS" : r.violations + " VIOLATIONS"}</span><span>${esc(r.description)}</span></div>`).join("")}</div>
  <h2>Moves the engine refused to make</h2><p class="lead">Examples of eligible-looking stock the rules protected (counts are positions considered, not units).</p><div class="card">${blk || "<span class='muted'>None.</span>"}</div>
  ${other.length ? `<h2>Unresolved problems</h2><div class="note">${other.map((u) => esc(u.detail)).join("<br>")}</div>` : ""}
  <h2>Demand the network could not cover</h2><p class="lead">After warehouse supply and every eligible donor are used, these positions still need stock. Showing the ${un.length} most severe of ${num(s.unresolved.filter((u) => u.kind === "unmet_demand").length)}.</p>
  <div class="tw"><table><thead><tr><th>Position</th><th>Units short</th><th>Cover at arrival</th><th>Why</th></tr></thead><tbody>${un.map((u) => `<tr style="cursor:default"><td>${esc(u.store)} / ${esc(u.sku)}</td><td class="num">${u.units}</td><td class="num">${cov(u.cover_days)}</td><td>${esc(u.detail.split("; ").slice(1).join("; "))}</td></tr>`).join("")}</tbody></table></div>`;
}

/* ------------------------------------------------------------ method */
async function vMethod() {
  const { manifest: m } = S.ov;
  let bt = null;
  try { bt = await api("/api/backtest"); } catch { /* back-test not generated */ }
  const names = { ml_poisson_gbm: "ML: Poisson gradient-boosted", current_trailing_rate: "Trailing 12-week rate", peer_smoothed: "Peer-smoothed rate" };
  const btHtml = bt ? `<h2>Forecast back-test (rolling origin)</h2><p class="lead">For each origin the model is trained only on data that ended before it, then predicts units sold in the next ${bt.horizon_weeks} weeks per store × SKU. Lower WAPE and deviance are better; bias 1.00 is unbiased. Weekly store × SKU counts are tiny, so a WAPE near 0.5 is the noise floor, the comparison between methods is what matters.</p>
    <div class="tw"><table><thead><tr><th>Forecast window</th><th>Actual units</th><th>Method</th><th>WAPE</th><th>MAE</th><th>Bias</th><th>Poisson deviance</th></tr></thead><tbody>${bt.origins.map((o) => Object.entries(o.models).map(([k, v], i) => `<tr style="cursor:default"><td>${i === 0 ? esc(o.target_window[0] + " → " + o.target_window[1]) : ""}</td><td class="num">${i === 0 ? num(o.actual_units) : ""}</td><td>${esc(names[k] || k)}</td><td class="num">${v.active_pairs.wape.toFixed(3)}</td><td class="num">${v.active_pairs.mae.toFixed(3)}</td><td class="num">${v.active_pairs.bias.toFixed(2)}</td><td class="num">${v.active_pairs.poisson_dev.toFixed(3)}</td></tr>`).join("")).join("")}</tbody></table></div>` : `<div class="note">Forecast back-test not generated yet (python -m nodal.cli --backtest).</div>`;
  const pol = Object.entries(m.policy).map(([k, v]) => `<tr style="cursor:default"><td><code>${esc(k)}</code></td><td>${esc(Array.isArray(v) ? v.join(" – ") : v)}</td><td>${esc(m.policy_sources[k] || "")}</td></tr>`).join("");
  const seas = Object.entries(m.seasonality).map(([st, x]) => `<tr style="cursor:default"><td>${esc(st)}</td><td class="num">×${x.index.toFixed(2)}</td><td>${esc(x.note)}</td></tr>`).join("");
  $("#main").innerHTML = `<h1>Method</h1>
  <p class="lead">Deterministic, auditable, reproducible. Same data + same policy → same plan. Engine ${esc(m.engine_version)}, ran in ${m.runtime_seconds}s.</p>
  <h2>How a move is decided</h2><div class="card"><ol style="margin:0;padding-left:20px">
   <li><b>Demand forecast</b> per store × SKU: ${m.velocity.ml_forecast ? "a Poisson gradient-boosted model predicts units in the next 28 days from sales lags, peer aggregates, price, tier, festival and wedding calendar and last year's window (back-tested below)." : "trailing in-stock rate."} Where a position was out of stock part of the window, the forecast is floored by the in-stock trailing rate × festival index (${num(m.velocity.floor_applied)} positions), so stock-outs never hide demand.</li>
   <li><b>Festival index</b> from last year's same-offset window, per state (below); the ML forecast learns the same effect from calendar features.</li>
   <li><b>Projection</b>: stock at arrival = on hand + in-transit (counted once) − velocity × lead time. Cover = stock ÷ velocity.</li>
   <li><b>Allocation order</b>: ageing stock → stores that sell it · minimum-display fills · warehouse → under-stocked stores (water-filling: each next unit goes to the lowest-cover store, so scarce stock equalises cover) · surplus store → store · pull-backs · scheduled dispatches against planned inbound.</li>
   <li><b>Donors</b> keep ≥ ${m.policy.donor_min_cover_days} days of cover (SKU and style level), their category minimum display, and never give protected S++ styles or in-transit units.</li>
   <li><b>Independent validator</b> re-checks the finished plan; anything that breaks a rule is excluded.</li></ol></div>
  ${btHtml}
  <h2>Festival seasonality (derived from data)</h2><div class="tw"><table><thead><tr><th>State</th><th>Index</th><th>Evidence</th></tr></thead><tbody>${seas}</tbody></table></div>
  <h2>Lead times (parsed from lead_times.csv)</h2><div class="card">Warehouse → store: ${Object.entries(m.lead_times.warehouse_to_store_by_zone).map(([z, d]) => `${z} ${d}d`).join(", ")} · Store → store: same city ${m.lead_times.store_to_store.same_city}d, same zone ${m.lead_times.store_to_store.same_zone}d, other zone ${m.lead_times.store_to_store.diff_zone}d</div>
  <h2>Policy &amp; assumptions</h2><p class="lead">SOURCE values are read from the data; ASSUMED values are engineering assumptions, not Manyavar policy. All live in <code>backend/nodal/policy.py</code>.</p>
  <div class="tw"><table><thead><tr><th>Parameter</th><th>Value</th><th>Origin</th></tr></thead><tbody>${pol}</tbody></table></div>
  <h2>Known limitations</h2><div class="note">Returns/cancellations are not identifiable in bill_lines (gross sales used). Pull-back destination: the brief does not define it; the builder confirmed it means a store that sells the SKU first, otherwise the warehouse. Warehouse inbound is all "Planned" — scheduled dispatches depend on it arriving. Festival index is measured from one prior cycle. Positions with no sales and no stock are not range-extended.</div>
  <h2>Run manifest</h2><p><a class="btn" href="/api/manifest" target="_blank" rel="noopener">Open run_manifest.json</a> <span class="muted">source hash ${esc(m.source_hash)} · run at ${esc(m.run_at)}</span></p>`;
}

document.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
document.querySelector("[data-more]").addEventListener("click", openMore);
MQ.addEventListener("change", () => { if (S.ov) render(); });
$("#main").addEventListener("click", onMainClick);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); onKey(e); });

/* ------------------------------------------------------------ hotspot map */
const MAP = { prods: null, map: null, layer: null, sel: { category: "", style: "", size: "", metric: "demand" }, data: null };
function vMapShell() {
  return `<h1>Map</h1><p class="lead">Pick a product to see where it is selling, where it is going next, and the top item at each stop.</p>
  <div class="filters"><select id="m-cat" aria-label="Category"></select><select id="m-style" aria-label="Style"></select><select id="m-size" aria-label="Size"></select>
  <select id="m-metric" aria-label="Metric"><option value="demand">Colour: cover after plan</option><option value="gap">Colour: unmet demand (gap)</option><option value="surplus">Colour: surplus stock</option></select></div>
  <div class="legend" id="m-legend"></div>
  <div class="maplayout"><div id="map" role="application" aria-label="Map of stores"></div><div class="card slist" id="m-list"><span class="muted">Loading…</span></div></div>
  <p class="muted" style="font-size:.78rem;text-align:center">Circle size = demand in the next 28 days. Numbered pins = where the stock goes next, ranked by demand. Lines: blue warehouse to store, purple store to store, grey pull-back. Positions are approximate city centres. Map tiles © OpenStreetMap contributors.</p>`;
}
async function bindMap() {
  if (!MAP.prods) MAP.prods = await api("/api/map/products");
  const s = MAP.sel, P = MAP.prods;
  if (!s.category) s.category = P.categories[0];
  $("#m-cat").innerHTML = P.categories.map((c) => `<option ${c === s.category ? "selected" : ""}>${esc(c)}</option>`).join("");
  const styles = P.styles.filter((x) => x.category === s.category);
  $("#m-style").innerHTML = `<option value="">All ${esc(s.category)} styles</option>` + styles.map((x) => `<option value="${esc(x.style)}" ${x.style === s.style ? "selected" : ""}>${esc(x.style)} · ${esc(x.name)}</option>`).join("");
  const st = styles.find((x) => x.style === s.style);
  $("#m-size").innerHTML = `<option value="">All sizes</option>` + (st ? st.sizes.map((z) => `<option ${z === s.size ? "selected" : ""}>${esc(z)}</option>`).join("") : "");
  $("#m-size").disabled = !st;
  $("#m-metric").value = s.metric;
  $("#m-cat").onchange = (e) => { s.category = e.target.value; s.style = ""; s.size = ""; bindMap(); };
  $("#m-style").onchange = (e) => { s.style = e.target.value; s.size = ""; bindMap(); };
  $("#m-size").onchange = (e) => { s.size = e.target.value; drawMap(); };
  $("#m-metric").onchange = (e) => { s.metric = e.target.value; drawMap(); };
  if (typeof L === "undefined") { $("#map").innerHTML = '<div class="empty">The map library could not load (needs internet). The ranked list on the right still works.</div>'; }
  else if (!MAP.map || MAP.map._container !== $("#map")) {
    MAP.map = L.map("map", { scrollWheelZoom: true }).setView([21.5, 82.5], 5);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 12, attribution: "© OpenStreetMap contributors" }).addTo(MAP.map);
    MAP.layer = L.layerGroup().addTo(MAP.map);
  }
  drawMap();
}
function metricColour(x, metric) {
  if (metric === "gap") return x.gap_units <= 0 ? "#62d3b6" : x.gap_units < 5 ? "#f0b35a" : "#ff7b72";
  if (metric === "surplus") return x.surplus_units <= 0 ? "#8d9b99" : x.surplus_units < 5 ? "#a9cdfb" : "#86b9ff";
  const c = x.cover_after_days; return c === null ? "#8d9b99" : c < 14 ? "#ff7b72" : c < 30 ? "#f0b35a" : c <= 90 ? "#62d3b6" : "#86b9ff";
}
async function drawMap() {
  const s = MAP.sel;
  const q = s.style ? `style=${encodeURIComponent(s.style)}${s.size ? "&size=" + encodeURIComponent(s.size) : ""}` : `category=${encodeURIComponent(s.category)}`;
  let d; try { d = await api("/api/map?" + q); } catch (e) { $("#m-list").innerHTML = `<div class="note">${esc(e.message)}</div>`; return; }
  MAP.data = d;
  const legends = { demand: [["#ff7b72", "cover < 14 d"], ["#f0b35a", "14–30 d"], ["#62d3b6", "30–90 d"], ["#86b9ff", "> 90 d (overstock)"], ["#8d9b99", "no demand"]], gap: [["#ff7b72", "gap ≥ 5 units"], ["#f0b35a", "gap < 5"], ["#62d3b6", "covered"]], surplus: [["#86b9ff", "surplus ≥ 5 units"], ["#a9cdfb", "< 5"], ["#8d9b99", "none"]] };
  $("#m-legend").innerHTML = legends[s.metric].map(([c, t]) => `<span><i style="background:${c}"></i>${t}</span>`).join("") + `<span class="muted">· cover after plan = stock incl. planned moves ÷ forecast daily demand</span>`;
  const stops = d.next_stops || [];
  $("#m-list").innerHTML = `<div class="eyebrow" style="margin-bottom:.6rem">Next stops, ranked by demand</div>` +
    (stops.map((x) => `<div class="row" data-s="${x.store}"><span class="rk">${x.rank}</span><div class="rm"><b>${esc(x.city)} · ${esc(x.store)}</b><span>${num(x.incoming_units)} units arriving ${esc(x.arrival.slice(5))} · demand ${x.demand_28d.toFixed(0)} in 28 days</span><span>Top item: ${x.top_sku ? esc(x.top_sku.sku) + " (" + x.top_sku.demand_28d.toFixed(0) + " in 28 days)" : "—"}</span></div></div>`).join("") || '<div class="empty">No moves are planned for this product.</div>') +
    `<div class="row" style="cursor:default"><span class="rk">W</span><div class="rm"><b>WH-KOL warehouse</b><span>${d.warehouse.on_hand} on hand · ${d.warehouse.plan_out} sent</span></div></div>`;
  if (typeof L === "undefined" || !MAP.layer) return;
  MAP.layer.clearLayers();
  const pos = {}; d.stores.forEach((x) => (pos[x.store] = [x.lat, x.lon])); pos["WH-KOL"] = [d.warehouse.lat, d.warehouse.lon];
  const agg = {};
  d.moves.forEach((m) => { const k = m.type + m.from + m.to; (agg[k] = agg[k] || { ...m, qty: 0 }).qty += m.qty; });
  Object.values(agg).forEach((m) => { if (pos[m.from] && pos[m.to]) L.polyline([pos[m.from], pos[m.to]], { color: m.type === "WH_TO_STORE" ? "#86b9ff" : m.type === "STORE_TO_STORE" ? "#7a3fa8" : "#8d9b99", weight: Math.min(1 + Math.sqrt(m.qty) * 1.2, 9), opacity: .65, dashArray: m.type === "PULL_BACK" ? "6 6" : null }).bindTooltip(`${m.from} → ${m.to}: ${m.qty} units`).addTo(MAP.layer); });
  const maxD = Math.max(...d.stores.map((x) => x.demand_28d), 1);
  d.stores.forEach((x) => {
    const r = 7 + 22 * Math.sqrt(x.demand_28d / maxD);
    L.circleMarker([x.lat, x.lon], { radius: r, color: "#222", weight: 1, fillColor: metricColour(x, s.metric), fillOpacity: .8 }).bindPopup(`<b>${x.store}</b> · ${esc(x.city)} (${x.tier})<br>Demand next 28d: <b>${x.demand_28d.toFixed(1)}</b> units<br>Stock now: ${x.stock} (+${x.plan_in} in, −${x.plan_out} out) → ${x.stock_after_plan}<br>Cover now / after plan: ${x.cover_days === null ? "∞" : x.cover_days.toFixed(0)}d / ${x.cover_after_days === null ? "∞" : x.cover_after_days.toFixed(0)}d<br>Gap after plan: ${x.gap_units} units · Surplus: ${x.surplus_units}`).addTo(MAP.layer);
  });
  stops.forEach((x) => { const p = pos[x.store]; if (p) L.marker(p, { icon: L.divIcon({ className: "", html: `<div class="rankpin">${x.rank}</div>`, iconSize: [24, 24] }), keyboard: false }).bindPopup(`<b>#${x.rank} ${esc(x.city)} (${esc(x.store)})</b><br>${num(x.incoming_units)} units arriving ${esc(x.arrival)}<br>Top item: ${x.top_sku ? esc(x.top_sku.sku) + " · " + esc(x.top_sku.style) + " (" + x.top_sku.demand_28d.toFixed(0) + " in 28 days, " + x.top_sku.stock + " in stock)" : "—"}${x.top_incoming ? "<br>Largest delivery: " + esc(x.top_incoming.sku) + " × " + x.top_incoming.qty : ""}`).addTo(MAP.layer); });
  L.circleMarker(pos["WH-KOL"], { radius: 9, color: "#000", weight: 2, fillColor: "#fff", fillOpacity: 1 }).bindPopup(`<b>WH-KOL</b> warehouse<br>${d.warehouse.on_hand} on hand for this selection<br>${d.warehouse.plan_out} dispatched in plan`).addTo(MAP.layer);
  MAP.map.invalidateSize();
  const b = L.latLngBounds(Object.values(pos)); MAP.map.fitBounds(b.pad(0.15), { maxZoom: 7 });
  document.querySelectorAll("#m-list .row[data-s]").forEach((el) => el.addEventListener("click", () => { const p = pos[el.dataset.s]; if (p) MAP.map.setView(p, 9); }));
}

/* ------------------------------------------------------------ forecast model trust */
async function vModel() {
  $("#main").innerHTML = '<div class="loading">Loading…</div>';
  let r; try { r = await api("/api/backtest"); } catch { $("#main").innerHTML = '<div class="note">Model report not generated yet: run <code>python -m nodal.cli --backtest</code>.</div>'; return; }
  const f3 = (x) => Number(x).toFixed(3), pc = (x) => (x * 100).toFixed(0) + "%";
  const names = { ml_poisson_gbm: "NODAL model (ML blend)", ml_only_uncalibrated: "ML only (uncalibrated)", current_trailing_rate: "Trailing rate (old method)", peer_smoothed: "Peer-smoothed rate" };
  const sc = r.scorecard.map((c) => `<div class="check"><span class="pill ${c.passed ? "pass" : "error"}">${c.passed ? "PASS" : "FAIL"}</span><span>${esc(c.check)}</span></div>`).join("");
  const ori = r.origins.map((o) => Object.entries(o.models).map(([k, v], i) => `<tr style="cursor:default"><td>${i === 0 ? esc(o.target_window[0] + " → " + o.target_window[1]) : ""}</td><td class="num">${i === 0 ? num(o.actual_units) : ""}</td><td>${esc(names[k] || k)}</td><td class="num">${f3(v.active_pairs.wape)}</td><td class="num">${v.store_category_wape === undefined ? "" : f3(v.store_category_wape)}</td><td class="num">${v.active_pairs.bias.toFixed(2)}</td><td class="num">${f3(v.active_pairs.poisson_dev)}</td></tr>`).join("")).join("");
  const cold = r.cold_start.origins.map((o) => ["unseen_stores", "unseen_styles"].map((k) => `<tr style="cursor:default"><td>${esc(o.origin_week_end)}</td><td>${k === "unseen_stores" ? "Unseen stores (" + r.cold_start.stores.join(", ") + ")" : "Unseen styles (" + r.cold_start.styles.length + " held out)"}</td><td class="num">${f3(o[k].ml.active_pairs.wape)}</td><td class="num">${f3(o[k].peer_smoothed.active_pairs.wape)}</td><td class="num">${f3(o[k].current_trailing_rate.active_pairs.wape)}</td></tr>`).join("")).join("");
  const imp = (r.importance || []).map(([k, v]) => `<div class="bar-row"><span class="n">${esc(k)}</span><span class="t"><i style="width:${(v / r.importance[0][1]) * 100}%"></i></span><span class="c">${v.toFixed(3)}</span></div>`).join("");
  const rel = r.reliability ? r.reliability.bins.map((b) => `<div class="bar-row"><span class="n">predicted ${pc(b.predicted)}</span><span class="t"><i style="width:${b.observed * 100}%"></i></span><span class="c">observed ${pc(b.observed)} (n=${b.n})</span></div>`).join("") : "";
  const st = r.festival_stress;
  $("#main").innerHTML = `<h1>Forecast model: how far to trust it</h1>
  <p class="lead">Poisson gradient-boosted demand model on a ${num(r.n_pairs)}-pair × ${r.n_weeks}-week panel, ${r.features} features, blended with a peer-smoothed rate, bias-recalibrated, with negative-binomial uncertainty. No store, SKU or style IDs are inputs, so it applies to unseen stores and styles. Hyper-parameters, blend weight (${r.config.blend_w}), bias scale (${r.config.scale.toFixed(2)}) and dispersion were fitted on validation origins that precede every test origin. Generated ${esc(r.generated_at)}.</p>
  <div class="note"><b>Read this first.</b> Weekly counts for one store × SKU are tiny (mostly 0–2), so even a perfect model has high error at that level (WAPE near 0.5 is the noise floor). "90% accurate" is therefore not a meaningful target there. Trust is measured where it can be: calibration, bias, accuracy at the store × category level, and out-of-sample behaviour on unseen periods, stores and styles. Failed checks below are shown as measured.</div>
  <h2>Trust scorecard (thresholds fixed in advance)</h2><div class="card">${sc}</div>
  <h2>Held-out future periods (rolling origin)</h2><div class="tw"><table><thead><tr><th>Forecast window</th><th>Actual units</th><th>Method</th><th>WAPE (pair)</th><th>WAPE (store×category)</th><th>Bias</th><th>Poisson deviance</th></tr></thead><tbody>${ori}</tbody></table></div>
  <h2>Cold start: stores and styles the model never trained on</h2><div class="tw"><table><thead><tr><th>Origin</th><th>Held out</th><th>NODAL model WAPE</th><th>Peer-smoothed</th><th>Trailing rate</th></tr></thead><tbody>${cold}</tbody></table></div>
  ${st ? `<h2>Festival stress test</h2><div class="card">${esc(st.note)}. Origin ${esc(st.origin_week_end)}: NODAL bias ${st.ml.active_pairs.bias.toFixed(2)}, trailing-rate bias ${st.current_trailing_rate.active_pairs.bias.toFixed(2)} (1.00 = unbiased). The model has seen only one Diwali with training signal, so festival-window accuracy is the least validated part of the forecast.</div>` : ""}
  <h2>Probability calibration</h2><p class="lead">When the model says "${"x"}% chance this pair sells at least one unit in 4 weeks", how often does it? ECE ${r.reliability ? r.reliability.ece.toFixed(3) : "n/a"}, Brier ${r.reliability ? r.reliability.brier.toFixed(3) : "n/a"}.</p><div class="card">${rel}</div>
  <h2>What drives the forecast (permutation importance)</h2><div class="card">${imp}</div>`;
}

/* ------------------------------------------------------------ mobile: cards, review, more sheet */
S.skip = new Set();
const VERB = { WH_TO_STORE: "Send", STORE_TO_STORE: "Move", PULL_BACK: "Pull back" };
function decide(id, d) { return setDecision(id, d); }
function cardHtml(a, review, i = 0) {
  const d = S.dec[a.id];
  const cover = a.type === "PULL_BACK" ? "—" : cov(a.cover_before_days) + " → " + cov(a.cover_after_days);
  const approve = a.requires_approval ? "Sign off" : "Approve";
  return `<article class="mcard" style="--i:${Math.min(i, 12)}" data-id="${a.id}" ${review ? "" : 'tabindex="0" role="button"'}>
    <div class="mc-top"><span class="pill ${a.urgency}">${a.urgency}</span><span class="muted">${esc(a.type_label)} · ${esc(a.reason_label)}</span></div>
    <div style="margin-top:6px">${aiChip(a)} <span class="pill ok">Rules passed</span></div>
    <h3 class="mc-title">${VERB[a.type]} ${a.qty} × ${esc(a.sku_id)}</h3>
    <div class="muted">${esc(a.style_name)} · size ${esc(a.size)}</div>
    <div class="mc-route">${esc(a.source)} → ${esc(a.destination)} <span>arrives ${esc(a.arrival_date.slice(5))}</span></div>
    <div class="mc-facts"><div><b>${cover}</b><span>Cover</span></div><div><b>${inr(a.value_mrp)}</b><span>Value</span></div><div><b>${esc(a.confidence)}</b><span>Confidence</span></div></div>
    <p class="mc-why">${esc(a.explanation)}</p>
    ${a.has_alt ? `<button class="alt-banner" data-act="details">${ico("sparkles")}<span>AI found a validated alternative. Compare</span>${ico("chevron-right")}</button>` : ""}
    <div class="mc-actions">
      <button data-act="rejected" class="${d === "rejected" ? "on-r" : ""}">${ico("x")}Reject</button>
      ${review ? `<button data-act="skip">Skip</button>` : ""}
      <button data-act="approved" class="${d === "approved" ? "on-a" : "go"}">${ico("check")}${approve}</button>
    </div>
    ${review ? `<button class="mc-link" data-act="details">See why and the rules checked</button>` : ""}</article>`;
}
function onMainClick(e) {
  const btn = e.target.closest("button[data-act]");
  const card = e.target.closest(".mcard");
  if (btn && card) {
    const id = card.dataset.id, act = btn.dataset.act;
    if (act === "details") return openDrawer(id);
    if (act === "skip") { S.skip.add(id); return render(); }
    return setDecision(id, act);
  }
  if (card && !btn) openDrawer(card.dataset.id);
}

/* ------------------------------------------------------------ decisions (server-side, audited) */
function toast(msg, undo) {
  let t = $("#toast"); if (!t) { t = document.createElement("div"); t.id = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
  t.innerHTML = `<span>${esc(msg)}</span>${undo ? '<button type="button">Undo</button>' : ""}`;
  if (undo) t.querySelector("button").onclick = () => { t.classList.remove("on"); undo(); };
  t.classList.add("on"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("on"), undo ? 7000 : 4200);
}
async function setDecision(id, decision, opts = {}) {
  if (!navigator.onLine) return toast("You are offline. Approvals need a connection.");
  const cur = S.decFull[id];
  const body = { move_id: id, decision: cur === decision ? "cleared" : decision, reason: opts.reason || null, confirm_high_value: !!opts.confirm };
  const post = () => fetch("/api/decisions", { method: "POST", headers: { "Content-Type": "application/json", "X-Client-Id": clientId() }, body: JSON.stringify(body) });
  try {
    let r = await post();
    if (r.status === 409) {
      const j = await r.json();
      if (!confirm(((j.detail && j.detail.message) || "This is a high-value move.") + "\n\nApproving records approval of the plan only. No stock is dispatched.")) return;
      body.confirm_high_value = true; r = await post();
    }
    if (!r.ok) { let m = r.statusText; try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || m; } catch { /* */ } throw new Error(m); }
    const c = (await r.json()).current;
    if (c) { S.decFull[id] = c.decision; S.dec[id] = c.decision === "rejected" ? "rejected" : "approved"; } else { delete S.decFull[id]; delete S.dec[id]; }
    S.acts.forEach((a) => { if (a.id === id) a.decision = S.decFull[id] || null; });
    refreshStatus(); closeDrawer(); render();
    toast(c ? (c.decision === "rejected" ? "Rejected and recorded." : "Approved and recorded. No stock is dispatched.") : "Decision cleared.");
  } catch (e) { toast("Could not save: " + e.message); }
}
function decisionBar(a) {
  const cur = S.decFull[a.id], alt = a.review && a.review.alternative;
  const approve = a.requires_approval ? "Sign off" : "Approve";
  return `<div class="dbar"><div class="mc-actions">
    <button data-dec="rejected" class="${cur === "rejected" ? "on-r" : ""}">${ico("x")}Reject</button>
    <button data-dec="approved" class="${cur === "approved" ? "on-a" : "go"}">${ico("check")}${approve}${alt ? " original" : ""}</button></div>
    ${alt ? `<div class="mc-actions" style="margin-top:8px"><button data-dec="approved_alternative" class="${cur === "approved_alternative" ? "on-a" : "go"}">${ico("sparkles")}Approve AI alternative</button></div>` : ""}
    <label class="muted" style="display:block;font-size:12px;margin-top:8px">Reason if rejecting or changing (optional)
      <select id="dec-reason" style="width:100%;min-height:44px;margin-top:4px;font:inherit;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:3px;padding:0 8px"><option value="">No reason</option><option>Quantity too high</option><option>Wrong destination</option><option>Stock needed elsewhere</option><option>Data looks wrong</option><option>Other</option></select></label>
    <p class="muted" style="font-size:12px;margin:8px 0 0">Approving records approval of this plan only. No stock is dispatched.${a.requires_approval ? " Above the sign-off limit, you will be asked to confirm." : ""}</p></div>`;
}
function bindDecisionBar(d, a) {
  d.querySelectorAll("[data-dec]").forEach((b) => b.addEventListener("click", () => setDecision(a.id, b.dataset.dec, { reason: ($("#dec-reason") || {}).value || null })));
}

/* ------------------------------------------------------------ AI chips, sections, comparison */
const AI_LABEL = { CONFIRM: "AI agrees", FLAG: "AI flagged", ALTERNATIVE: "AI alternative", INSUFFICIENT_EVIDENCE: "AI: not enough evidence" };
const AI_TONE = { CONFIRM: "ok", FLAG: "warn", ALTERNATIVE: "info", INSUFFICIENT_EVIDENCE: "type" };
function aiChip(a) {
  if (a.ai_state === "completed") return `<span class="pill ${AI_TONE[a.ai_outcome] || "type"}">${AI_LABEL[a.ai_outcome] || a.ai_outcome}</span>`;
  if (a.ai_state === "failed") return `<span class="pill error">AI failed</span>`;
  if (a.ai_state === "pending") return `<span class="pill type">AI pending</span>`;
  return "";
}
const KIND = { alternative_donor: "A different store gives the stock", donor_instead_of_warehouse: "A store with spare stock instead of the warehouse", warehouse_instead_of_store: "Send from the warehouse instead of a store", alternative_destination: "A different destination store" };
function aiSection(rv) {
  if (!rv) return `<h3>AI review (advisory)</h3><div class="note">Not reviewed by Qwen3. Only the highest-priority moves are reviewed, and reviews are skipped if the AI server is unavailable. The deterministic checks above still apply.</div>`;
  if (rv.state === "failed") return `<h3>AI review (advisory)</h3><div class="note">The AI review failed and was discarded: ${esc(rv.error || "unknown error")}</div>`;
  if (rv.state !== "completed") return `<h3>AI review (advisory)</h3><div class="note">${rv.state === "skipped" ? "Outside the AI review budget for this run." : "AI review pending."}</div>`;
  const r = rv.review;
  return `<h3>AI review (advisory)</h3><p style="margin:0 0 6px"><span class="pill ${AI_TONE[rv.outcome]}">${AI_LABEL[rv.outcome]}</span> <span class="muted" style="font-size:12px">model ${esc(rv.model || "")} · stated confidence ${esc(r.confidence)} (not a calibrated probability)</span></p>
    <div class="say">${esc(r.plain_explanation)}</div>
    ${r.deterministic_note ? `<div class="note">${esc(r.deterministic_note)}</div>` : ""}
    <p class="muted" style="font-size:13px;margin:6px 0">${esc(r.reasoning)}</p>
    ${r.risks.length ? `<b style="font-size:13px">Risks</b><ul class="checks" style="list-style:disc;padding-left:18px">${r.risks.map((x) => `<li style="list-style:disc">${esc(x)}</li>`).join("")}</ul>` : ""}
    ${r.missing_information.length ? `<b style="font-size:13px">Missing information</b><ul class="checks" style="list-style:disc;padding-left:18px">${r.missing_information.map((x) => `<li style="list-style:disc">${esc(x)}</li>`).join("")}</ul>` : ""}
    <div style="margin:6px 0">${(r.evidence_refs || []).map((x) => `<span class="pill type" style="text-transform:none">${esc(x)}</span>`).join(" ")}</div>
    <p class="muted" style="font-size:12px;margin:4px 0">The AI's view is kept separate from the deterministic validator, which every move has already passed.</p>`;
}
function altCompare(a, rv) {
  const c = rv && rv.alternative; if (!c) return "";
  const m = c.metrics, row = (k, o, n) => `<div class="cmp-row"><div class="cmp-k">${k}</div><div class="cmp-v"><small>Original NODAL</small>${o}</div><div class="cmp-v alt"><small>Qwen3 alternative</small><b>${n}</b></div></div>`;
  return `<h3>Original vs AI alternative</h3><p class="muted" style="margin:0 0 6px;font-size:13px">${esc(KIND[c.kind] || c.kind)}. Numbers below are calculated by NODAL, not by the AI.</p>
  <div class="cmp">${row("From", esc(a.source), esc(c.source))}${row("To", esc(a.destination), esc(c.destination))}${row("Units", a.qty, c.qty)}${row("Lead time", a.lead_days + " days", c.lead_days + " days")}
  ${row("Expected revenue (30 days)", "baseline", (m.delta_expected_revenue >= 0 ? "+" : "−") + inr(Math.abs(m.delta_expected_revenue)))}
  ${row("Destination cover after", cov(a.cover_after_days), cov(m.dest_cover_after_days))}${row("Source cover after", "—", cov(m.source_cover_after_days))}${row("Hard rules", "Passed", "Passed, re-checked")}</div>
  <p class="muted" style="font-size:12px;margin:6px 0">${esc(m.note || "")}</p>`;
}

/* ------------------------------------------------------------ status panel, banners */
function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000; if (!isFinite(s)) return "";
  return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago";
}
function netBanner() {
  let b = $("#net"); if (!b) { b = document.createElement("div"); b.id = "net"; b.setAttribute("role", "status"); document.body.appendChild(b); }
  const off = !navigator.onLine, st = S.stale;
  b.textContent = off ? "You are offline. Showing saved data. Approvals need a connection." : st ? "Showing saved data from your last visit." : "";
  b.classList.toggle("on", off || st);
}
window.addEventListener("online", () => { netBanner(); });
window.addEventListener("offline", netBanner);

/* ------------------------------------------------------------ AI Intelligence / Daily runs / History */
async function vAI() {
  $("#main").innerHTML = '<div class="loading">Loading…</div>';
  const d = await api("/api/ai"), s = S.status || {};
  const total = (S.acts || []).length, o = d.outcomes;
  const cards = d.reviews.map((x) => `<article class="mcard" tabindex="0" role="button" data-id="${x.id}"><div class="mc-top"><span class="pill ${x.state === "failed" ? "error" : AI_TONE[x.outcome] || "type"}">${x.state === "failed" ? "AI failed" : AI_LABEL[x.outcome] || x.state}</span><span class="muted">${esc(x.move.type_label)} · ${esc(x.move.urgency)}</span></div>
    <h3 class="mc-title">${x.move.qty} × ${esc(x.move.sku_id)}</h3><div class="mc-route">${esc(x.move.source)} → ${esc(x.move.destination)} <span>${inr(x.move.value_mrp)}</span></div>
    <p class="mc-why" style="margin:8px 0 0">${esc(x.review ? x.review.plain_explanation : x.error || "")}</p>${x.alternative ? `<div class="alt-banner" style="margin-top:8px">${ico("sparkles")}<span>Validated alternative available</span></div>` : ""}</article>`).join("");
  $("#main").innerHTML = `<h1>AI Intelligence</h1><p class="lead">Qwen3 8B runs locally through Ollama and reviews the highest-priority moves. It is advisory: it only sees facts NODAL computed, every number it writes is checked against those facts, and any alternative is re-validated by the independent rule validator before it can be approved.</p>
    <div class="grid kpis"><div class="card kpi"><div class="v">${num(s.ai ? s.ai.reviews_completed : 0)}</div><div class="l">Reviews completed</div><div class="s">of ${num(total)} moves (prioritised)</div></div>
    <div class="card kpi"><div class="v">${num(o.FLAG || 0)}</div><div class="l">Flagged</div></div><div class="card kpi"><div class="v">${num(s.ai ? s.ai.alternatives : 0)}</div><div class="l">Alternatives</div><div class="s">${num(d.candidates_evaluated)} candidates evaluated · ${num(d.better_by_calculation)} better by calculation</div></div>
    <div class="card kpi"><div class="v">${num((d.states.failed || 0) + (d.states.pending || 0))}</div><div class="l">Failed / pending</div><div class="s">${num(d.states.skipped || 0)} outside budget</div></div></div>
    <div class="note">${esc(d.note)} Model-stated confidence is not a calibrated probability.</div>
    <h2>Reviewed moves</h2>${cards || '<div class="empty">No AI reviews in this run. Start Ollama and re-run the daily pipeline.</div>'}`;
  document.querySelectorAll("#main .mcard").forEach((c) => c.addEventListener("click", () => openDrawer(c.dataset.id)));
}
async function vRuns() {
  $("#main").innerHTML = '<div class="loading">Loading…</div>';
  const runs = await api("/api/runs"), s = S.status || {};
  const stg = (st) => Object.entries(st || {}).map(([k, v]) => `<span class="pill ${v.status === "completed" ? "ok" : v.status === "failed" ? "error" : "type"}" title="${esc(k)}">${esc(k.replace(/_/g, " "))}${v.seconds ? " " + v.seconds + "s" : ""}</span>`).join(" ");
  $("#main").innerHTML = `<h1>Daily runs</h1><p class="lead">Each run ingests the datasets, validates them, snapshots them, forecasts, allocates, checks every rule, reviews with Qwen3 and publishes. Identical data is never published twice.</p>
    <div class="grid cols2"><div class="card"><div class="eyebrow">Automation</div><p style="margin:6px 0 0">Scheduled daily at <b>${esc((s.automation || {}).schedule || "")}</b>. Ingestion: watched folder and operator upload. SFTP, cloud storage and inventory-system APIs are future connectors (not built).</p></div>
    <div class="card"><div class="eyebrow">Latest</div><p style="margin:6px 0 0">${esc((s.automation || {}).last_run_status || "n/a")} · AI ${esc((s.automation || {}).ai_status || "n/a")} · data as of ${esc(s.as_of || "")}</p></div></div>
    <h2>History</h2>${runs.map((r) => `<div class="card" style="margin-bottom:10px"><div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><b>${esc(r.run_id)}</b><span class="pill ${r.status === "completed" ? "ok" : r.status === "failed" ? "error" : "warn"}">${esc(r.status)}</span></div>
      <div class="muted" style="font-size:13px">${esc(r.created_at)} · ${esc(r.kind)} · as of ${esc(r.as_of || "—")} · input ${esc((r.input_hash || "").slice(0, 10))}</div>
      ${r.metrics ? `<div style="margin:6px 0;font-size:13px">${num(r.metrics.moves || 0)} moves · ${num(r.metrics.units || 0)} units${r.metrics.needs_approval !== undefined ? " · " + r.metrics.needs_approval + " need sign-off" : ""} · AI ${esc(r.ai_status)}</div>` : ""}
      <div style="display:flex;gap:4px;flex-wrap:wrap">${stg(r.stages)}</div>${r.error ? `<div class="note">${esc(r.error)}</div>` : ""}</div>`).join("") || '<div class="empty">No runs recorded.</div>'}`;
}
async function vHistory() {
  $("#main").innerHTML = '<div class="loading">Loading…</div>';
  const h = await api("/api/history");
  $("#main").innerHTML = `<h1>History</h1><p class="lead">Every approval and rejection is stored on the server and never edited. ${esc(h.note)}</p>
    <h2>Decisions</h2>${h.decisions.map((x) => `<div class="card" style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><b>${esc(x.move_id)} · ${x.move.qty ?? ""} × ${esc(x.move.sku_id || "")}</b><span class="pill ${x.decision === "rejected" ? "error" : x.decision === "cleared" ? "type" : "ok"}">${esc(x.decision.replace("_", " "))}</span></div>
      <div class="muted" style="font-size:13px">${esc(x.move.source || "")} → ${esc(x.move.destination || "")} · ${esc(x.actor)} · ${esc(x.ts)}${x.had_ai_alternative ? " · AI alternative was available" : ""}${x.confirmed ? " · high-value confirmed" : ""}</div>${x.reason ? `<div style="font-size:13px">Reason: ${esc(x.reason)}</div>` : ""}</div>`).join("") || '<div class="empty">No decisions yet. Approve or reject a move to start the audit trail.</div>'}
    <h2>Audit events</h2><div class="card">${h.audit.map((e) => `<div class="rule"><span class="muted" style="width:170px;flex:none">${esc(e.ts)}</span><span>${esc(e.event)}${e.actor ? " · " + esc(e.actor) : ""}</span></div>`).join("") || "<span class='muted'>None.</span>"}</div>
    <p class="muted" style="font-size:13px">Outcomes recorded so far (actual moves, later sales): ${num(h.outcomes_recorded)}.</p>`;
}

function openMore() {
  const d = $("#drawer");
  const row = (icon, label, m) => `<button class="sheet-row" data-m="${m}">${ico(icon)}<span>${label}</span></button>`;
  d.innerHTML = `<button class="btn x" id="dx">${ico("x")}<span>Close</span></button><h2 style="margin:0 0 .6rem">More</h2>
    ${S.cap && S.cap.can_upload ? row("upload-cloud", "Add data", "data") : ""}${row("sparkles", "AI review", "ai")}${row("calendar-clock", "Daily runs", "runs")}${row("history", "History", "history")}
    ${row("shield-check", "Rules and gaps", "safety")}${row("database", "Data quality", "quality")}${row("line-chart", "Forecast", "model")}${row("info", "How it works", "method")}
    <a class="sheet-row" href="/api/export.csv">${ico("download")}<span>Export all moves</span></a>`;
  d.classList.add("open"); $("#scrim").classList.add("open"); d.setAttribute("aria-hidden", "false");
  $("#dx").onclick = closeDrawer;
  d.querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () => { closeDrawer(); setTab(b.dataset.m); }));
}

/* ------------------------------------------------------------ PWA */
if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* PWA features are optional */ }));


/* ============================================================ v3: shipments, quick decisions, Ask, Add data, intro */
const money = (n) => (n >= 1e7 ? "₹" + (n / 1e7).toFixed(2) + " Cr" : n >= 1e5 ? "₹" + (n / 1e5).toFixed(1) + " L" : "₹" + num(n));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
S.skipShip = new Set(); S.chat = [];
function gateNav() { document.querySelectorAll("[data-needs]").forEach((b) => { b.style.display = S.cap && S.cap[b.dataset.needs] ? "" : "none"; }); }
const pendingShips = () => (S.ship || []).filter((s) => s.status === "pending" || s.status === "mixed");

function shipCard(s) {
  const noun = s.kind === "pullback" ? "Pull-back" : "Shipment";
  return `<article class="ship glass" data-id="${esc(s.id)}">
    <span class="pill ${s.urgency}">${s.urgency === "Critical" ? "Needs attention" : s.urgency === "High" ? "Important" : "Routine"}</span>
    <div class="town">${esc(s.kind === "pullback" ? s.store + " pull-back" : s.city || s.store)}</div>
    <div class="where">${s.kind === "pullback" ? "Take stock back from this store" : esc(s.store) + " · tier " + esc(s.tier || "")}</div>
    <div class="big"><div><b data-count="${s.units}">${num(s.units)}</b><span>units</span></div><div><b>${money(s.value_mrp)}</b><span>value</span></div><div><b>${s.n_moves}</b><span>items</span></div></div>
    <p class="why">${esc(s.summary)}</p>
    <div>${s.needs_signoff ? `<span class="pill warn look">${s.needs_signoff} big item${s.needs_signoff > 1 ? "s" : ""} included</span> ` : ""}${s.ai_flagged ? `<span class="pill warn look">${s.ai_flagged} flagged by AI</span>` : ""}</div>
    <div class="acts"><button class="round no" data-ship="rejected" aria-label="Reject ${noun}">${ico("x")}</button><button class="round" data-ship="skip" aria-label="Decide later">${ico("skip-forward")}</button><button class="round go" data-ship="approved" aria-label="Approve ${noun}">${ico("check")}</button></div>
    <div><button class="link" data-ship="open">See what is inside</button></div>
    <div class="hint">${isMobile() ? "Swipe right to approve · left to reject" : "Press → to approve · ← to reject · S to skip"}</div></article>`;
}
function qrow(s, i) {
  return `<div class="qrow" style="--i:${i}" data-open="${esc(s.id)}" tabindex="0" role="button"><div class="qm"><b>${esc(s.kind === "pullback" ? s.store + " pull-back" : s.city || s.store)} · ${num(s.units)} units</b><span>${money(s.value_mrp)} · ${s.n_moves} items${s.needs_signoff ? " · " + s.needs_signoff + " big" : ""}</span></div>
    <div class="mini"><button data-q="rejected" data-id="${esc(s.id)}" aria-label="Reject">${ico("x")}</button><button data-q="approved" data-id="${esc(s.id)}" class="ok" aria-label="Approve">${ico("check")}</button></div></div>`;
}
function vToday() {
  const all = S.ship || [], pend = pendingShips();
  const cur = pend.find((s) => !S.skipShip.has(s.id)) || null;
  const rest = pend.filter((s) => s !== cur && !S.skipShip.has(s.id)).slice(0, 6);
  const skipped = pend.filter((s) => S.skipShip.has(s.id));
  const routine = pend.filter((s) => s.routine && !S.skipShip.has(s.id));
  const hl = ((S.ov && S.ov.summary && S.ov.summary.highlights) || []);
  const stale = S.status && Date.now() - new Date(S.status.created_at).getTime() > 36 * 3600 * 1000;
  const reviewed = all.length - pend.length;
  const stage = cur ? shipCard(cur) : `<div class="card" style="max-width:520px;width:100%"><h3 style="margin:0">${pend.length ? "You skipped the rest for now" : "All caught up"}</h3><p class="muted" style="margin:.5rem 0 0">${pend.length ? "Skipped shipments are waiting below." : "Every shipment has a decision. You can export the list from the top right."}</p>${skipped.length && !cur ? `<p><button class="btn" data-unskip>Review skipped</button></p>` : ""}</div>`;
  return `${stale ? `<div class="note">This plan is from ${esc(ago(S.status.created_at))}. A newer daily run has not been published yet.</div>` : ""}
    <h1>Today</h1><p class="lead" style="margin:.4rem auto .3rem">${all.length ? `${pend.length} of ${all.length} shipments to review` : "No plan has been published yet"}</p>
    <div class="progress"><i style="width:${all.length ? (reviewed / all.length) * 100 : 0}%"></i></div>
    <div class="stage">${stage}</div>
    ${routine.length > 1 ? `<div style="text-align:center;margin:.4rem 0 1.2rem"><button class="btn" data-routine>${ico("check-check")}<span>Approve ${routine.length} routine shipments</span></button></div>` : ""}
    ${rest.length ? `<div class="eyebrow" style="margin:1.4rem 0 .7rem">Up next</div><div class="queue">${rest.map(qrow).join("")}</div>` : ""}
    ${skipped.length && cur ? `<div class="eyebrow" style="margin:1.4rem 0 .7rem">Skipped for now</div><div class="queue">${skipped.map(qrow).join("")}</div>` : ""}
    ${hl.length ? `<details class="card" style="margin:1.6rem auto 0;max-width:640px"><summary>What NODAL noticed in the data</summary><div class="grid" style="margin-top:.6rem">${hl.map((h) => `<div class="hl" data-go="${h.tab}" ${h.filter ? `data-f='${esc(JSON.stringify(h.filter))}'` : ""}><div><b>${esc(h.title)}</b><p>${esc(h.text)}</p></div></div>`).join("")}</div></details>` : ""}`;
}
function countUps(root) {
  root.querySelectorAll("[data-count]").forEach((el) => {
    const to = Number(el.dataset.count); if (!isFinite(to) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const t0 = performance.now(), D = 800;
    const step = (t) => { const p = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - p, 3); el.textContent = num(Math.round(to * e)); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
}
function bindToday(m) {
  countUps(m);
  m.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => setTab(el.dataset.go, el.dataset.f ? JSON.parse(el.dataset.f) : null)));
  const card = m.querySelector(".ship");
  if (card) {
    const id = card.dataset.id;
    card.querySelectorAll("[data-ship]").forEach((b) => b.addEventListener("click", () => {
      const a = b.dataset.ship;
      if (a === "skip") { S.skipShip.add(id); render(); } else if (a === "open") openShipment(id); else decideShip(id, a, { card });
    }));
    bindSwipe(card, id);
  }
  m.querySelectorAll(".qrow").forEach((r) => {
    r.addEventListener("click", (e) => { const q = e.target.closest("[data-q]"); if (q) { e.stopPropagation(); decideShip(q.dataset.id, q.dataset.q); } else openShipment(r.dataset.open); });
    r.addEventListener("keydown", (e) => { if (e.key === "Enter") openShipment(r.dataset.open); });
  });
  const rt = m.querySelector("[data-routine]"); if (rt) rt.addEventListener("click", openRoutine);
  const us = m.querySelector("[data-unskip]"); if (us) us.addEventListener("click", () => { S.skipShip.clear(); render(); });
}
function bindSwipe(el, id) {
  let x0 = null, dx = 0;
  el.addEventListener("pointerdown", (e) => { if (e.target.closest("button")) return; x0 = e.clientX; dx = 0; try { el.setPointerCapture(e.pointerId); } catch { /* */ } el.style.transition = "none"; });
  el.addEventListener("pointermove", (e) => { if (x0 === null) return; dx = e.clientX - x0; el.style.transform = `translateX(${dx}px) rotate(${dx / 24}deg)`; el.style.opacity = String(1 - Math.min(Math.abs(dx) / 460, 0.5)); });
  const end = () => { if (x0 === null) return; el.style.transition = ""; const d = dx; x0 = null; if (d > 110) decideShip(id, "approved", { card: el }); else if (d < -110) decideShip(id, "rejected", { card: el }); else { el.style.transform = ""; el.style.opacity = ""; } };
  el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end);
}
function onKey(e) {
  if (S.tab !== "overview" || $("#drawer").classList.contains("open") || /INPUT|SELECT|TEXTAREA/.test((e.target.tagName || ""))) return;
  const cur = pendingShips().find((s) => !S.skipShip.has(s.id)); if (!cur) return;
  const card = $(".ship");
  if (e.key === "ArrowRight" || e.key === "a") decideShip(cur.id, "approved", { card });
  else if (e.key === "ArrowLeft" || e.key === "r") decideShip(cur.id, "rejected", { card });
  else if (e.key === "s") { S.skipShip.add(cur.id); render(); }
}
async function decideShip(id, decision, opts = {}) {
  if (!navigator.onLine) return toast("You are offline. Approvals need a connection.");
  const card = opts.card, reset = () => { if (card) { card.classList.remove("fly-left", "fly-right"); card.style.transform = ""; card.style.opacity = ""; } };
  const post = (confirmed) => fetch("/api/decisions/batch", { method: "POST", headers: { "Content-Type": "application/json", "X-Client-Id": clientId() }, body: JSON.stringify({ shipment_id: id, decision, confirm_high_value: confirmed }) });
  try {
    let r = await post(false);
    if (r.status === 409) {
      const j = await r.json();
      if (!confirm(((j.detail && j.detail.message) || "This includes big items.") + "\n\nApproving records approval of the plan only. No stock is dispatched.")) { reset(); return; }
      r = await post(true);
    }
    if (!r.ok) { let m = r.statusText; try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : (j.detail && j.detail.message) || m; } catch { /* */ } throw new Error(m); }
    if (card && decision !== "cleared") { card.classList.add(decision === "approved" ? "fly-right" : "fly-left"); await sleep(320); }
    const sh = (S.ship || []).find((s) => s.id === id);
    S.ship = await api("/api/shipments"); await loadDec(); refreshStatus(); S.acts = await api("/api/actions");
    render(); closeDrawer();
    const label = sh ? (sh.kind === "pullback" ? sh.store + " pull-back" : sh.city || sh.store) : "Shipment";
    if (decision === "cleared") toast("Decision undone.");
    else toast(`${decision === "approved" ? "Approved" : "Rejected"}: ${label}. ${decision === "approved" ? "No stock is dispatched." : ""}`.trim(), () => decideShip(id, "cleared"));
  } catch (e) { reset(); toast("Could not save: " + e.message); }
}
async function openRoutine() {
  const r = pendingShips().filter((s) => s.routine && !S.skipShip.has(s.id));
  const units = r.reduce((a, s) => a + s.units, 0), value = r.reduce((a, s) => a + s.value_mrp, 0), big = r.reduce((a, s) => a + s.needs_signoff, 0);
  const d = $("#drawer");
  d.innerHTML = `<button class="btn x" id="dx">${ico("x")}<span>Close</span></button><h2 style="margin:0 0 .4rem">Approve routine shipments</h2>
    <p class="lead" style="margin:.2rem auto 1rem">These ${r.length} shipments have no AI flags, no low-confidence items and nothing critical.</p>
    <div class="big" style="display:flex;justify-content:center;gap:2rem;margin:1rem 0"><div><b style="font-size:2rem;font-weight:200">${num(units)}</b><br><span class="muted">units</span></div><div><b style="font-size:2rem;font-weight:200">${money(value)}</b><br><span class="muted">value</span></div></div>
    ${big ? `<div class="note">${big} items are above the ₹1,00,000 sign-off limit. Approving confirms them too.</div>` : ""}
    <div class="mc-actions" style="justify-content:center"><button id="rt-go" class="go">${ico("check")}Approve all ${r.length}</button></div>
    <p class="muted" style="font-size:.78rem">Approving records approval of the plan only. No stock is dispatched.</p>`;
  d.classList.add("open"); $("#scrim").classList.add("open"); d.setAttribute("aria-hidden", "false");
  $("#dx").onclick = closeDrawer;
  $("#rt-go").onclick = async () => {
    try {
      const res = await fetch("/api/decisions/routine", { method: "POST", headers: { "Content-Type": "application/json", "X-Client-Id": clientId() }, body: JSON.stringify({ confirm_high_value: true }) });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      S.ship = await api("/api/shipments"); await loadDec(); refreshStatus(); closeDrawer(); render(); toast(`Approved ${r.length} routine shipments. No stock is dispatched.`);
    } catch (e) { toast("Could not save: " + e.message); }
  };
}
async function openShipment(id) {
  const d = $("#drawer");
  d.innerHTML = '<div class="loading">Loading…</div>'; d.classList.add("open"); $("#scrim").classList.add("open"); d.setAttribute("aria-hidden", "false");
  let s; try { s = await api("/api/shipments/" + encodeURIComponent(id)); } catch (e) { d.innerHTML = `<div class="note">${esc(e.message)}</div>`; return; }
  const lines = s.moves.map((m, i) => `<div class="qrow" style="--i:${Math.min(i, 8)}" data-move="${esc(m.id)}" tabindex="0" role="button"><div class="qm"><b>${m.qty} × ${esc(m.sku_id)}</b><span>${esc(m.style_name)} · size ${esc(m.size)} · from ${esc(m.source)} · arrives ${esc(m.arrival_date.slice(5))}</span></div><div>${aiChip(m)}${m.requires_approval ? ' <span class="pill warn">big</span>' : ""}</div></div>`);
  d.innerHTML = `<button class="btn x" id="dx">${ico("x")}<span>Close</span></button>
    <div class="eyebrow">${s.kind === "pullback" ? "Pull-back" : "Shipment"} · ${esc(s.store)}</div><h2 style="margin:.2rem 0 .6rem">${esc(s.city || s.store)}</h2>
    <div class="say">${esc(s.summary)}<br><small class="faint">${s.summary_source === "local_ai" ? "Written by the local AI model" : "Standard wording"}</small></div>
    <div style="display:flex;justify-content:center;gap:2rem;margin:1rem 0"><div><b style="font-size:1.8rem;font-weight:200">${num(s.units)}</b><br><span class="muted" style="font-size:.7rem;letter-spacing:.16em;text-transform:uppercase">units</span></div><div><b style="font-size:1.8rem;font-weight:200">${money(s.value_mrp)}</b><br><span class="muted" style="font-size:.7rem;letter-spacing:.16em;text-transform:uppercase">value</span></div><div><b style="font-size:1.8rem;font-weight:200">${s.moves.length}</b><br><span class="muted" style="font-size:.7rem;letter-spacing:.16em;text-transform:uppercase">items</span></div></div>
    <div style="display:flex;gap:6px;justify-content:center;flex-wrap:wrap">${s.reasons.map((r) => `<span class="pill type" style="text-transform:none;letter-spacing:.04em">${r.items} ${esc(r.reason)}</span>`).join("")}</div>
    <div class="dbar"><div class="mc-actions"><button data-sd="rejected" class="${s.status === "rejected" ? "on-r" : ""}">${ico("x")}Reject all</button><button data-sd="approved" class="${s.status === "approved" ? "on-a" : "go"}">${ico("check")}Approve all</button></div>
      <p class="muted" style="font-size:.76rem;margin:.6rem 0 0">Approving records approval of the plan only. No stock is dispatched.${s.needs_signoff ? " Items above the sign-off limit are confirmed together." : ""}</p></div>
    <h3>What is inside</h3><div class="queue" id="ship-lines">${lines.slice(0, 10).join("")}</div>
    ${lines.length > 10 ? `<p><button class="link" id="ship-more">Show all ${lines.length} items</button></p>` : ""}`;
  $("#dx").onclick = closeDrawer;
  d.querySelectorAll("[data-sd]").forEach((b) => b.addEventListener("click", () => decideShip(id, b.dataset.sd)));
  const bind = () => d.querySelectorAll("[data-move]").forEach((r) => { r.onclick = () => openDrawer(r.dataset.move); });
  bind();
  const more = $("#ship-more"); if (more) more.onclick = () => { $("#ship-lines").innerHTML = lines.join(""); more.remove(); bind(); };
}

/* ---------- Ask ---------- */
function vAsk() {
  return `<h1>Ask</h1><p class="lead">Ask about retail stock rules, or about a store in today's plan. General questions go to Sarvam AI with no company data. Questions about stores and moves are answered here from NODAL's own facts.</p>
    <div class="chips" id="chips"></div><div class="chat" id="chat"></div>
    <form class="ask-in" id="ask-form"><input id="ask-q" type="text" maxlength="300" placeholder="Ask a question…" aria-label="Your question" autocomplete="off"><button class="round go" style="width:50px;height:50px;flex:none" aria-label="Send">${ico("send")}</button></form>`;
}
const SRC = { sarvam: "Answered by Sarvam AI", cache: "From saved answers", plan: "From today's plan", local_ai: "From today's plan, written by the local AI" };
function drawChat() {
  const c = $("#chat"); if (!c) return;
  c.innerHTML = S.chat.map((b) => b.role === "me" ? `<div class="bub me">${esc(b.text)}</div>` : b.typing ? `<div class="bub ai"><span class="typing"><i></i><i></i><i></i></span></div>` : `<div class="bub ai">${esc(b.text)}${b.source ? `<small>${esc(SRC[b.source] || b.source)}</small>` : ""}</div>`).join("");
  c.lastElementChild && c.lastElementChild.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
async function askQ(q) {
  q = (q || "").trim(); if (q.length < 3) return;
  S.chat.push({ role: "me", text: q }, { role: "ai", typing: true }); drawChat();
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json", "X-Client-Id": clientId() }, body: JSON.stringify({ question: q }) });
    const j = await r.json();
    S.chat.pop();
    S.chat.push({ role: "ai", text: r.ok ? j.answer : (typeof j.detail === "string" ? j.detail : "Sorry, that did not work."), source: r.ok ? j.source : null });
  } catch { S.chat.pop(); S.chat.push({ role: "ai", text: "You seem to be offline. Questions need a connection." }); }
  drawChat();
}
async function bindAsk() {
  drawChat();
  let sug = []; try { sug = await api("/api/ask/suggestions"); } catch { /* */ }
  const ch = $("#chips"); if (ch) { ch.innerHTML = sug.map((s) => `<button class="chip" type="button">${esc(s)}</button>`).join(""); ch.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => askQ(b.textContent))); }
  $("#ask-form").addEventListener("submit", (e) => { e.preventDefault(); const i = $("#ask-q"); const q = i.value; i.value = ""; askQ(q); });
}

/* ---------- Add data (local only) ---------- */
const STAGE_LABEL = { ingest_validate: "Checking the files", snapshot: "Saving a snapshot", forecast_allocate_validate: "Forecasting and planning", alternative_candidates: "Looking for alternatives", ai_review: "AI review", simple_summaries: "Writing plain summaries", publish: "Publishing the plan" };
async function vData() {
  const m = $("#main"); m.innerHTML = '<div class="loading">Loading…</div>';
  let inb; try { inb = await api("/api/inbox"); } catch (e) { m.innerHTML = `<h1>Add data</h1><div class="note">${esc(e.message)}</div>`; return; }
  const draw = (inb) => {
    m.innerHTML = `<h1>Add data</h1><p class="lead">Drop new CSV files here. Any file you add replaces the matching supplied file; the rest stay as supplied. Then press Calculate.</p>
      <div class="drop" id="drop" tabindex="0" role="button">${ico("upload-cloud")}<p><b>Drop CSV files here</b></p><p>or tap to choose. Name each file after its table, for example <code>stock_snapshot.csv</code>.</p><input id="pick" type="file" accept=".csv,text/csv" multiple hidden></div>
      <div class="files">${inb.files.map((f) => `<div class="frow ${f.state === "uploaded" ? "up" : ""}"><span>${esc(f.name)}</span>${f.state === "uploaded" ? `<span>new</span><button data-rm="${esc(f.name)}" aria-label="Remove ${esc(f.name)}">${ico("x")}</button>` : `<span class="faint">supplied</span>`}</div>`).join("")}</div>
      <div style="text-align:center;margin:1.4rem 0"><button class="btn primary" id="calc" ${inb.job.running ? "disabled" : ""}><span>${inb.job.running ? "Calculating…" : "Calculate"}</span></button><p class="muted" style="font-size:.8rem;margin:.6rem 0 0">Uses: ${esc(inb.dataset)}</p></div>
      <div id="prog"></div>`;
    const drop = $("#drop"), pick = $("#pick");
    drop.addEventListener("click", () => pick.click());
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => upload([...e.dataTransfer.files]));
    pick.addEventListener("change", () => upload([...pick.files]));
    m.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", async () => { await fetch("/api/inbox/" + b.dataset.rm, { method: "DELETE" }); draw(await api("/api/inbox")); }));
    $("#calc").addEventListener("click", startCalc);
    if (inb.job.running) poll();
  };
  const upload = async (files) => {
    const msgs = [];
    for (const f of files) {
      const name = f.name.replace(/\.csv$/i, "");
      const r = await fetch("/api/inbox/" + encodeURIComponent(name), { method: "PUT", body: await f.arrayBuffer() });
      if (!r.ok) { let t = "failed"; try { const j = await r.json(); t = j.detail.problems ? j.detail.problems.join("; ") : j.detail; } catch { /* */ } msgs.push(`${name}: ${t}`); }
    }
    draw(await api("/api/inbox"));
    toast(msgs.length ? msgs.join(" · ") : `${files.length} file${files.length > 1 ? "s" : ""} added. Press Calculate.`);
  };
  const startCalc = async () => {
    const r = await fetch("/api/pipeline/run", { method: "POST" });
    if (!r.ok) { toast((await r.json()).detail || "Could not start."); return; }
    $("#calc").disabled = true; $("#calc span").textContent = "Calculating…"; poll();
  };
  const poll = async () => {
    const box = $("#prog"); if (!box) return;
    const s = await api("/api/pipeline/status");
    const st = (s.run && s.run.stages) || {};
    box.innerHTML = `<div class="stagelist">${Object.keys(STAGE_LABEL).map((k) => { const v = st[k]; const cls = !v ? "" : v.status === "completed" ? "done" : v.status === "failed" ? "run" : "run"; return `<div class="stg ${cls}">${ico(!v ? "circle" : v.status === "completed" ? "check-circle-2" : "loader-2")}<span>${STAGE_LABEL[k]}</span></div>`; }).join("")}</div>`;
    if (s.running) { setTimeout(poll, 1500); return; }
    if (s.result) {
      const ok = ["completed", "partial"].includes(s.result.status);
      box.innerHTML += `<div class="note">${ok ? "Done. The new plan is ready." : s.result.status === "skipped_duplicate" ? "These files are identical to a plan that already exists, so nothing changed." : "The calculation stopped: " + esc(s.result.error || s.result.status)}</div>`;
      if (ok) { await boot(false); toast("New plan ready."); setTab("overview"); }
    }
  };
  draw(inb);
}

/* ---------- intro: the logo draws itself, then dissolves into the app ---------- */
(function splash() {
  const el = $("#splash"); if (!el) return;
  let seen = false; try { seen = !!sessionStorage.getItem("nodal-splash"); } catch { /* */ }
  if (seen || matchMedia("(prefers-reduced-motion: reduce)").matches) { el.remove(); return; }
  const done = () => { el.classList.add("out"); setTimeout(() => el.remove(), 1000); try { sessionStorage.setItem("nodal-splash", "1"); } catch { /* */ } };
  setTimeout(done, 3300); el.addEventListener("click", done); document.addEventListener("keydown", done, { once: true });
})();

boot(false);

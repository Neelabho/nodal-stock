"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
const lakh = (n) => "₹" + (n / 1e5).toLocaleString("en-IN", { maximumFractionDigits: 1 }) + " L";
const num = (n) => Number(n).toLocaleString("en-IN");
const cov = (x) => (x === null || x === undefined ? "∞" : x < 100 ? x.toFixed(0) + "d" : "99d+");

const S = { tab: "overview", ov: null, acts: [], f: { q: "", type: "", reason: "", urgency: "", store: "", cat: "", conf: "", appr: false, dec: "", sort: "default" }, page: 0, dec: {}, quality: null, safety: null };
const PAGE = 50;
const ico = (n) => `<i data-lucide="${n}"></i>`;
let _iconQueued = false;
new MutationObserver(() => { if (_iconQueued || !window.lucide) return; _iconQueued = true; requestAnimationFrame(() => { _iconQueued = false; window.lucide.createIcons(); }); }).observe(document.body, { childList: true, subtree: true });
const TITLES = { overview: "Overview", register: "Action register", map: "Hotspot map", safety: "Rules & gaps", quality: "Data quality", model: "Forecast model", method: "Method" };

function decKey() { return "nodal-decisions-" + (S.ov ? S.ov.manifest.source_hash : "x"); }
function loadDec() { try { S.dec = JSON.parse(localStorage.getItem(decKey()) || "{}"); } catch { S.dec = {}; } }
function saveDec() { try { localStorage.setItem(decKey(), JSON.stringify(S.dec)); } catch { /* storage unavailable: decisions last for this session only */ } }

async function api(p, opt) {
  const r = await fetch(p, opt);
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch { /* */ } throw new Error(m); }
  return r.json();
}

async function boot(refresh) {
  $("#main").innerHTML = '<div class="loading">Running the engine on the Manyavar data…</div>';
  try {
    if (refresh) await api("/api/run", { method: "POST" });
    S.ov = await api("/api/overview");
    S.acts = await api("/api/actions");
    S.quality = null; S.safety = null;
    loadDec();
    $("#asof").textContent = `Stock as of ${S.ov.manifest.as_of} · plan dispatches ${S.ov.manifest.dispatch_date}`;
    render();
  } catch (e) {
    $("#main").innerHTML = `<div class="note"><b>Could not run the engine.</b><br>${esc(e.message)}<br><span class="muted">Check that the organiser CSVs are at manyavar-vch3-data/csv/ or set NODAL_DATA_DIR.</span></div>`;
  }
}

function setTab(t, filter) {
  S.tab = t;
  $("#bar-title").textContent = TITLES[t] || t;
  if (filter) { S.f = { ...S.f, q: "", type: "", reason: "", urgency: "", store: "", cat: "", conf: "", appr: false, dec: "", ...filter }; S.page = 0; }
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  render();
  window.scrollTo(0, 0);
}

function render() {
  const m = $("#main");
  if (!S.ov) return;
  if (S.tab === "overview") { m.innerHTML = vOverview(); bindRows(m); }
  else if (S.tab === "register") { m.innerHTML = vRegister(); bindRegister(); }
  else if (S.tab === "map") { m.innerHTML = vMapShell(); bindMap(); }
  else if (S.tab === "model") { vModel(); return; }
  else if (S.tab === "quality") vQuality();
  else if (S.tab === "safety") vSafety();
  else { vMethod(); return; }
  m.querySelectorAll("[data-go]").forEach((el) => el.addEventListener("click", () => setTab(el.dataset.go, el.dataset.f ? JSON.parse(el.dataset.f) : null)));
}

/* ------------------------------------------------------------ overview */
function vOverview() {
  const { summary: s, manifest: mf, validation: v } = S.ov;
  const passN = v.rules.filter((r) => r.passed).length;
  const maxU = Math.max(...Object.values(s.by_reason).map((x) => x.units), 1);
  const reasonNames = { under_stocked: "Under-stocked → warehouse", scheduled_inbound: "Scheduled inbound dispatch", display_minimum: "Minimum display fill", ageing_relocation: "Ageing → where it sells", surplus_rebalance: "Surplus store → store", over_capacity: "Over-capacity relief", ageing_pullback: "Ageing pull-back" };
  const bars = Object.entries(s.by_reason).filter(([, x]) => x.actions).map(([k, x]) => `<div class="bar-row"><span class="n">${reasonNames[k] || k}</span><span class="t"><i style="width:${(x.units / maxU) * 100}%"></i></span><span class="c">${num(x.actions)} moves · ${num(x.units)} units</span></div>`).join("");
  const hl = (s.highlights || []).map((h) => `<div class="card hl ${h.tone}" tabindex="0" role="button" data-go="${h.tab}" ${h.filter ? `data-f='${esc(JSON.stringify(h.filter))}'` : ""}>${ico(h.tone === "bad" ? "alert-triangle" : h.tone === "warn" ? "alert-triangle" : "info")}<div><b>${esc(h.title)}</b><p>${esc(h.text)}</p></div></div>`).join("");
  return `
  <h1>${num(s.total.actions)} moves proposed for ${esc(mf.dispatch_date)}</h1>
  <p class="lead">Built from ${num(mf.velocity.positions)} store × SKU positions across 20 stores and the Kolkata warehouse. Every quantity comes from sales, stock and rule data; nothing is dispatched until a merchandiser approves it.</p>
  <div class="grid kpis">
    <div class="card kpi"><div class="v">${num(s.total.units)}</div><div class="l">units to move</div><div class="s">${lakh(s.total.value_mrp)} at MRP</div></div>
    <div class="card kpi"><div class="v">${num(s.by_type.WH_TO_STORE.actions)}</div><div class="l">warehouse → store</div><div class="s">${num(s.by_type.WH_TO_STORE.units)} units · ${num(s.scheduled)} scheduled on planned inbound</div></div>
    <div class="card kpi"><div class="v">${num(s.by_type.STORE_TO_STORE.actions)}</div><div class="l">store → store</div><div class="s">${num(s.by_type.STORE_TO_STORE.units)} units</div></div>
    <div class="card kpi"><div class="v">${num(s.by_type.PULL_BACK.actions)}</div><div class="l">pull-backs</div><div class="s">${num(s.by_type.PULL_BACK.units)} units (assumed destination: WH-KOL)</div></div>
    <div class="card kpi"><div class="v">${num(s.needs_approval)}</div><div class="l">need human approval</div><div class="s">single move &gt; ${inr(mf.policy.approval_value_mrp)} at MRP</div></div>
    <div class="card kpi" data-go="safety" style="cursor:pointer"><div class="v">${passN}/${v.rules.length}</div><div class="l">hard rules pass</div><div class="s">independent validator, ${s.excluded_by_validator} moves excluded</div></div>
  </div>
  <h2>What NODAL found</h2><div class="grid cols2">${hl}</div>
  <h2>Where the moves come from</h2><div class="card">${bars}</div>
  <h2>Urgency</h2><div class="card">${["Critical", "High", "Normal", "Housekeeping"].map((u) => `<span class="pill ${u}" style="margin-right:8px">${u}: ${num(s.urgency[u] || 0)}</span>`).join("")}
    <p class="muted" style="margin:.7rem 0 0;font-size:.86rem">Critical = display-minimum breach, over-capacity relief, or a store that will be out of stock on arrival. Sorted by dispatch date, then lowest cover — no hidden weights.</p></div>
  ${queueHtml()}`;
}

function queueHtml() {
  const total = S.acts.length, done = S.acts.filter((a) => S.dec[a.id]).length;
  const pend = S.acts.filter((a) => !S.dec[a.id]).slice(0, 8);
  return `<h2>Today's approval queue</h2>
  <p class="lead">The ${pend.length} highest-priority moves still waiting for a decision. ${num(done)} of ${num(total)} reviewed. Open any row for the evidence and rule checks.</p>
  <div class="progress"><i style="width:${(done / Math.max(total, 1)) * 100}%"></i></div>
  <div class="tw"><table><thead><tr><th>Urgency</th><th>Move</th><th>Item</th><th>Route</th><th>Qty</th><th>Arrives</th><th>Cover</th><th>Value</th><th>Why (one line)</th><th>Decision</th></tr></thead><tbody>
  ${pend.map(rowHtml).join("") || `<tr><td colspan="10" class="empty">Nothing pending. All moves reviewed.</td></tr>`}</tbody></table></div>
  <p style="margin-top:14px"><button class="btn primary" data-go="register">Open the full register ${ico("arrow-right")}</button></p>`;
}

/* ------------------------------------------------------------ register */
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
  <h1>Action register</h1>
  <p class="lead">Click any row for the evidence and rule checks behind it. Approve / reject is saved in this browser and included in the CSV export of the current view.</p>
  <div class="filters">
    <input type="search" id="f-q" placeholder="Search SKU, style, store…" value="${esc(f.q)}" aria-label="Search">
    <select id="f-type" aria-label="Type">${opts([["WH_TO_STORE", "Warehouse → Store"], ["STORE_TO_STORE", "Store → Store"], ["PULL_BACK", "Pull back"]], f.type, "All types")}</select>
    <select id="f-reason" aria-label="Reason">${opts(reasons, f.reason, "All reasons")}</select>
    <select id="f-urgency" aria-label="Urgency">${opts(["Critical", "High", "Normal", "Housekeeping"].map((x) => [x, x]), f.urgency, "All urgency")}</select>
    <select id="f-store" aria-label="Store">${opts(stores, f.store, "All stores")}</select>
    <select id="f-cat" aria-label="Category">${opts(uniq("category").map((x) => [x, x]), f.cat, "All categories")}</select>
    <select id="f-conf" aria-label="Confidence">${opts([["high", "High confidence"], ["medium", "Medium"], ["low", "Low"]], f.conf, "Any confidence")}</select>
    <select id="f-dec" aria-label="Decision">${opts([["pending", "Pending"], ["approved", "Approved"], ["rejected", "Rejected"]], f.dec, "Any decision")}</select>
    <select id="f-sort" aria-label="Sort">${[["default", "Sort: priority"], ["value", "Sort: value"], ["qty", "Sort: quantity"], ["cover", "Sort: lowest cover"]].map(([v, t]) => `<option value="${v}" ${f.sort === v ? "selected" : ""}>${t}</option>`).join("")}</select>
    <label><input type="checkbox" id="f-appr" ${f.appr ? "checked" : ""}> needs approval</label>
    <button class="btn" id="f-reset">Reset</button>
    <button class="btn primary" id="f-export">Export view (${num(rows.length)})</button>
  </div>
  <div class="muted" style="font-size:.86rem">${num(rows.length)} of ${num(S.acts.length)} moves · ${approved} approved · ${rejected} rejected</div>
  <div class="tw"><table><thead><tr><th>Urgency</th><th>Move</th><th>Item</th><th>Route</th><th>Qty</th><th>Arrives</th><th>Cover</th><th>Value</th><th>Why (one line)</th><th>Decision</th></tr></thead><tbody>
  ${slice.map(rowHtml).join("") || `<tr><td colspan="10" class="empty">No moves match these filters.</td></tr>`}
  </tbody></table></div>
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
    <td class="why">${esc(a.explanation)}<div>${fl}</div></td>
    <td class="dec"><button data-d="approved" class="${d === "approved" ? "on-a" : ""}">${ico("check")}${a.requires_approval ? "Sign off" : "Approve"}</button><button data-d="rejected" class="${d === "rejected" ? "on-r" : ""}">${ico("x")}Reject</button></td></tr>`;
}

function bindRegister() {
  const set = (id, key, ev = "change") => $(id).addEventListener(ev, (e) => { S.f[key] = e.target.type === "checkbox" ? e.target.checked : e.target.value; S.page = 0; render(); if (key === "q") { const i = $("#f-q"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); } });
  set("#f-q", "q", "input"); set("#f-type", "type"); set("#f-reason", "reason"); set("#f-urgency", "urgency"); set("#f-store", "store"); set("#f-cat", "cat"); set("#f-conf", "conf"); set("#f-dec", "dec"); set("#f-sort", "sort"); set("#f-appr", "appr");
  $("#f-reset").onclick = () => { S.f = { q: "", type: "", reason: "", urgency: "", store: "", cat: "", conf: "", appr: false, dec: "", sort: "default" }; S.page = 0; render(); };
  $("#f-export").onclick = exportView;
  $("#pg-prev").onclick = () => { S.page--; render(); window.scrollTo(0, 0); };
  $("#pg-next").onclick = () => { S.page++; render(); window.scrollTo(0, 0); };
  bindRows($("#main"));
}
function bindRows(root) {
  root.querySelectorAll("tbody tr[data-id]").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-d]");
      if (b) { const id = tr.dataset.id; S.dec[id] = S.dec[id] === b.dataset.d ? undefined : b.dataset.d; if (!S.dec[id]) delete S.dec[id]; saveDec(); render(); return; }
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
  <details style="margin-top:14px"><summary>Raw evidence (JSON)</summary><pre>${esc(JSON.stringify(e, null, 1))}</pre></details>`;
  $("#dx").onclick = closeDrawer;
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
  <h2>Known limitations</h2><div class="note">Returns/cancellations are not identifiable in bill_lines (gross sales used). "Pull back" destination is undefined in the brief (assumed WH-KOL; stores that sell the SKU are tried first). Warehouse inbound is all "Planned" — scheduled dispatches depend on it arriving. Festival index is measured from one prior cycle. Positions with no sales and no stock are not range-extended.</div>
  <h2>Run manifest</h2><p><a class="btn" href="/api/manifest" target="_blank" rel="noopener">Open run_manifest.json</a> <span class="muted">source hash ${esc(m.source_hash)} · run at ${esc(m.run_at)}</span></p>`;
}

document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
$("#btn-run").addEventListener("click", async (e) => { e.target.disabled = true; e.target.textContent = "Running…"; await boot(true); e.target.disabled = false; e.target.textContent = "Re-run engine"; });
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
boot(false);

/* ------------------------------------------------------------ hotspot map */
const MAP = { prods: null, map: null, layer: null, sel: { category: "", style: "", size: "", metric: "demand" }, data: null };
function vMapShell() {
  return `<h1>Product hotspot map</h1><p class="lead">Pick a product (or a whole category) to see where demand is, which stores are short or overstocked after the plan, and the moves planned for it. Store positions are approximate city-centre placements: the data has cities, not coordinates.</p>
  <div class="filters"><select id="m-cat" aria-label="Category"></select><select id="m-style" aria-label="Style"></select><select id="m-size" aria-label="Size"></select>
  <select id="m-metric" aria-label="Metric"><option value="demand">Colour: cover after plan</option><option value="gap">Colour: unmet demand (gap)</option><option value="surplus">Colour: surplus stock</option></select></div>
  <div class="legend" id="m-legend"></div>
  <div class="maplayout"><div id="map" role="application" aria-label="Map of stores"></div><div class="card slist" id="m-list"><span class="muted">Loading…</span></div></div>
  <p class="muted" style="font-size:.8rem">Circle size = forecast demand next 28 days. Lines: blue warehouse → store, purple store → store, grey pull-back (width = units). Map tiles © OpenStreetMap contributors.</p>`;
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
  if (metric === "gap") return x.gap_units <= 0 ? "#1d7a46" : x.gap_units < 5 ? "#e0a100" : "#b3261e";
  if (metric === "surplus") return x.surplus_units <= 0 ? "#8a857c" : x.surplus_units < 5 ? "#6fa8dc" : "#1f5fa8";
  const c = x.cover_after_days; return c === null ? "#8a857c" : c < 14 ? "#b3261e" : c < 30 ? "#e0a100" : c <= 90 ? "#1d7a46" : "#1f5fa8";
}
async function drawMap() {
  const s = MAP.sel;
  const q = s.style ? `style=${encodeURIComponent(s.style)}${s.size ? "&size=" + encodeURIComponent(s.size) : ""}` : `category=${encodeURIComponent(s.category)}`;
  let d; try { d = await api("/api/map?" + q); } catch (e) { $("#m-list").innerHTML = `<div class="note">${esc(e.message)}</div>`; return; }
  MAP.data = d;
  const legends = { demand: [["#b3261e", "cover < 14 d"], ["#e0a100", "14–30 d"], ["#1d7a46", "30–90 d"], ["#1f5fa8", "> 90 d (overstock)"], ["#8a857c", "no demand"]], gap: [["#b3261e", "gap ≥ 5 units"], ["#e0a100", "gap < 5"], ["#1d7a46", "covered"]], surplus: [["#1f5fa8", "surplus ≥ 5 units"], ["#6fa8dc", "< 5"], ["#8a857c", "none"]] };
  $("#m-legend").innerHTML = legends[s.metric].map(([c, t]) => `<span><i style="background:${c}"></i>${t}</span>`).join("") + `<span class="muted">· cover after plan = stock incl. planned moves ÷ forecast daily demand</span>`;
  const rank = [...d.stores].sort((a, b) => (s.metric === "gap" ? b.gap_units - a.gap_units : s.metric === "surplus" ? b.surplus_units - a.surplus_units : b.demand_28d - a.demand_28d));
  $("#m-list").innerHTML = `<b>Stores, ranked by ${s.metric === "gap" ? "unmet demand" : s.metric === "surplus" ? "surplus" : "demand"}</b>` + rank.map((x) => `<div class="row" data-s="${x.store}"><span><span class="sku">${x.store}</span> <span class="muted">${esc(x.city)} · ${x.tier}</span></span><span class="num">${x.demand_28d.toFixed(1)} /28d · ${x.cover_after_days === null ? "∞" : x.cover_after_days.toFixed(0) + "d"} cover</span></div>`).join("") +
    `<div class="row" style="cursor:default"><span><span class="sku">WH-KOL</span> <span class="muted">warehouse</span></span><span class="num">${d.warehouse.on_hand} on hand · ${d.warehouse.plan_out} sent</span></div>`;
  if (typeof L === "undefined" || !MAP.layer) return;
  MAP.layer.clearLayers();
  const pos = {}; d.stores.forEach((x) => (pos[x.store] = [x.lat, x.lon])); pos["WH-KOL"] = [d.warehouse.lat, d.warehouse.lon];
  const agg = {};
  d.moves.forEach((m) => { const k = m.type + m.from + m.to; (agg[k] = agg[k] || { ...m, qty: 0 }).qty += m.qty; });
  Object.values(agg).forEach((m) => { if (pos[m.from] && pos[m.to]) L.polyline([pos[m.from], pos[m.to]], { color: m.type === "WH_TO_STORE" ? "#1f5fa8" : m.type === "STORE_TO_STORE" ? "#7a3fa8" : "#8a857c", weight: Math.min(1 + Math.sqrt(m.qty) * 1.2, 9), opacity: .65, dashArray: m.type === "PULL_BACK" ? "6 6" : null }).bindTooltip(`${m.from} → ${m.to}: ${m.qty} units`).addTo(MAP.layer); });
  const maxD = Math.max(...d.stores.map((x) => x.demand_28d), 1);
  d.stores.forEach((x) => {
    const r = 7 + 22 * Math.sqrt(x.demand_28d / maxD);
    L.circleMarker([x.lat, x.lon], { radius: r, color: "#222", weight: 1, fillColor: metricColour(x, s.metric), fillOpacity: .8 }).bindPopup(`<b>${x.store}</b> · ${esc(x.city)} (${x.tier})<br>Demand next 28d: <b>${x.demand_28d.toFixed(1)}</b> units<br>Stock now: ${x.stock} (+${x.plan_in} in, −${x.plan_out} out) → ${x.stock_after_plan}<br>Cover now / after plan: ${x.cover_days === null ? "∞" : x.cover_days.toFixed(0)}d / ${x.cover_after_days === null ? "∞" : x.cover_after_days.toFixed(0)}d<br>Gap after plan: ${x.gap_units} units · Surplus: ${x.surplus_units}`).addTo(MAP.layer);
  });
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

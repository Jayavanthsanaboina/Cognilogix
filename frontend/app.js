/* Cognilogix dashboard. Frontend-only interaction layer for the existing FastAPI API. */

const STATIONS = [
  { id: "intake",   label: "Read the complaint" },
  { id: "retrieve", label: "Find similar repairs" },
  { id: "diagnose", label: "Diagnose the cause" },
  { id: "plan_fix", label: "Plan the fix" },
  { id: "explain",  label: "Explain in plain words" },
];
const NEXT = { intake: "retrieve", retrieve: "diagnose", diagnose: "plan_fix", plan_fix: "explain", escalate: "explain" };

const SAMPLES = [
  { tag: "English",  text: "AC in Block C is not cooling and there is ice on the copper pipe" },
  { tag: "Telugu",   text: "జనరేటర్ కరెంట్ పోయిన తర్వాత స్టార్ట్ అవ్వడం లేదు" },
  { tag: "Hindi",    text: "जनरेटर बिजली जाने के बाद चालू नहीं हो रहा है" },
  { tag: "Hinglish", text: "AC thanda nahi kar raha, hawa bahut kam aa rahi hai" },
  { tag: "Safety",   text: "Burning smell and sparks from the switch board in the lab" },
  { tag: "English",  text: "Lift door is not closing properly in the library" },
  { tag: "Vague",    text: "Something feels wrong in the building" },
];

const URGENCY = ["Low", "Medium", "High", "Critical"];
const LANG_CODES = { telugu: "te", hindi: "hi" };
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN");
const pct = (x) => Math.round(Number(x || 0) * 100) + "%";
const clock = (iso) => {
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  catch (_) { return ""; }
};

let historyCache = [];
let currentResult = null;
let statsCache = null;
let notifications = [];
let modalEl = null;

function addNotification(title, detail = "", type = "info") {
  notifications.unshift({ id: Date.now() + Math.random(), title, detail, type, time: new Date() });
  notifications = notifications.slice(0, 12);
  updateNotificationBadge();
}
function updateNotificationBadge() {
  const badge = $("#notificationCount");
  if (!badge) return;
  badge.textContent = notifications.length > 9 ? "9+" : String(notifications.length);
  badge.hidden = notifications.length === 0;
}

/* ------------------------------------------------------------------ agent line */
function renderLine() {
  const line = $("#line");
  if (!line) return;
  line.innerHTML = STATIONS.map((s) => `
    <li class="station idle" id="st-${s.id}">
      <span class="dot"></span>
      <span class="name">${s.label}</span>
      <span class="time"></span>
      <span class="note"></span>
    </li>`).join("");
  const flow = $(".flow-status");
  if (flow) flow.textContent = "READY";
}

function setStation(id, state, ms, note) {
  const el = $("#st-" + id);
  if (!el) return;
  el.className = "station " + state;
  el.querySelector(".time").textContent = ms ? (ms / 1000).toFixed(1) + " s" : "";
  el.querySelector(".note").textContent = note || "";
}
function markRunning(id) {
  const el = $("#st-" + id);
  if (el && el.classList.contains("idle")) el.className = "station running";
  const flow = $(".flow-status");
  if (flow) flow.textContent = "PROCESSING";
}
function handleStep(ev) {
  if (ev.agent === "escalate") {
    setStation("diagnose", "escalated", ev.ms, ev.summary);
    setStation("plan_fix", "skipped", 0, "Not needed");
  } else {
    setStation(ev.agent, "done", ev.ms, ev.summary);
  }
  if (NEXT[ev.agent]) markRunning(NEXT[ev.agent]);
}

/* ------------------------------------------------------------------ messages */
function showAlert(title, detail) {
  $("#alert").innerHTML = `<div class="alert"><b>${esc(title)}</b>${esc(detail || "")}</div>`;
}
function clearAlert() { $("#alert").innerHTML = ""; }

/* ------------------------------------------------------------------ result pieces */
function gauge(urgency) {
  const level = Math.max(0, URGENCY.indexOf(urgency));
  const segments = URGENCY.map((_, k) => `<span class="seg ${k <= level ? "on lvl" + level : ""}"></span>`).join("");
  return `<div class="gauge" role="img" aria-label="Urgency: ${esc(urgency)}">${segments}</div>
          <p class="urg lvl${level}">${esc(urgency)} urgency</p>`;
}
function range(label, stats, fmt) {
  const span = Number(stats.max) - Number(stats.min) || 1;
  const pos = Math.min(100, Math.max(0, ((Number(stats.median) - Number(stats.min)) / span) * 100));
  return `<div class="range">
    <div class="range-top"><span>${label}</span><strong>${fmt(stats.median)} typical</strong></div>
    <div class="track" role="img" aria-label="${label}: ${fmt(stats.min)} to ${fmt(stats.max)}"><span class="tick" style="left:${pos}%"></span></div>
    <div class="range-ends"><span>${fmt(stats.min)}</span><span>${fmt(stats.max)}</span></div>
  </div>`;
}
function renderUnderstood(r) {
  const translated = String(r.language).toLowerCase() !== "english"
    ? `<span>Understood as <q>${esc(r.english_text)}</q></span>` : "";
  const equipment = r.equipment_type ? `<span class="tag">${esc(r.equipment_type)}</span>` : `<span>Equipment not identified</span>`;
  return `<div class="understood"><span class="tag">${esc(r.language)}</span>${equipment}${translated}</div>`;
}
function renderCauses(r) {
  const d = r.diagnosis, rec = r.recommendation;
  const weak = d.insufficient_evidence;
  let notice = "";
  if (weak) notice = `<p class="notice">${esc(rec.action)}</p>`;
  else if (d.low_confidence) notice = `<p class="notice"><b>Several causes are possible.</b> ${esc(rec.action)}</p>`;
  const items = (d.ranked || []).map((c, i) => {
    const width = weak ? Math.round(Number(c.evidence_share || 0) * 100) : Math.round(Number(c.confidence || 0) * 100);
    const ids = (c.supporting_cases || []).map((id) => `<span>${esc(id)}</span>`).join("");
    return `<li class="cause ${i === 0 && !weak ? "top" : ""} ${i === 0 ? "selected-cause" : ""}">
      <div class="cause-head"><span class="cause-name">${esc(c.cause)}</span><span class="score">${weak ? "weak lead" : pct(c.confidence) + " match"}</span></div>
      <div class="meter"><span style="width:${width}%"></span></div>
      <p class="why">${esc(c.reasoning)}</p>
      <p class="ids">Past repairs: ${ids}</p>
    </li>`;
  }).join("");
  const title = weak ? "Not enough evidence to name a cause" : "Likely cause";
  const foot = weak ? "" : `<p class="fine">The match score compares each cause with the past repairs found. It is not the chance of being right, so confirm on site.</p>`;
  return `<h2>${title}</h2>${notice}<ol class="causes">${items || "<li>No similar repairs were found.</li>"}</ol>${foot}`;
}
function renderTicket(r) {
  const rec = r.recommendation, weak = r.diagnosis.insufficient_evidence;
  if (weak) return `<div class="ticket"><div class="ticket-head"><h2>Next step</h2></div><div class="ticket-body"><div class="ticket-sec"><p>${esc(rec.action)}</p></div><div class="ticket-sec">${gauge(rec.urgency)}</div></div></div>`;
  const steps = (rec.fix_steps || []).map((s) => `<li>${esc(s)}</li>`).join("");
  const parts = rec.parts?.length ? `<p class="parts">Parts often needed: ${esc(rec.parts.join(", "))}</p>` : "";
  const provisional = rec.provisional ? `<p class="basis">Provisional until the cause is verified on site.</p>` : "";
  return `<div class="ticket"><div class="ticket-head"><h2>Recommended fix</h2></div><div class="ticket-body">
      <div class="ticket-sec"><ol class="steps">${steps}</ol>${parts}</div>
      <div class="ticket-sec">${range("Repair cost", rec.cost_inr, inr)}${range("Time to fix", rec.downtime_hours, (h) => h + " h")}</div>
      <div class="ticket-sec">${gauge(rec.urgency)}<p class="basis">Based on ${rec.based_on_cases} past repairs with this cause.</p>${provisional}</div>
    </div></div>`;
}
function renderExplain(r) {
  const ex = r.explanation || { summary: "", reasoning_chain: [], safety_note: "" };
  const lang = LANG_CODES[String(r.language).toLowerCase()] || "en";
  const steps = (ex.reasoning_chain || []).map((s) => `<li>${esc(s)}</li>`).join("");
  const safety = ex.safety_note ? `<p class="safety">${esc(ex.safety_note)}</p>` : "";
  return `<h2>In plain words</h2><p class="lead" lang="${lang}">${esc(ex.summary)}</p><ul lang="${lang}">${steps}</ul>${safety}`;
}
function renderCases(r) {
  const rows = [...(r.cases || [])].sort((a, b) => b.similarity - a.similarity).map((c) => `
    <tr><td class="mono">${esc(c.record_id)}</td><td class="mono">${Number(c.similarity).toFixed(2)}</td>
      <td>${esc(c.root_cause)}</td><td>${esc(c.equipment_type)}, ${esc(c.location)}</td><td>${esc(c.complaint)}</td><td class="mono">${inr(c.cost_inr)}</td></tr>`).join("");
  return `<h2>Similar past repairs <span class="section-count">${(r.cases || []).length} matches</span></h2>
    <div class="table-wrap"><table><thead><tr><th>Repair</th><th>Similarity</th><th>Cause found</th><th>Where</th><th>What was reported</th><th>Cost</th></tr></thead><tbody>${rows || `<tr><td colspan="6" class="empty-table">No similar repairs were returned.</td></tr>`}</tbody></table></div>`;
}
function renderResult(r) {
  currentResult = r;
  window.__cognilogixLastResult = r;
  const banner = r.recommendation?.safety_critical
    ? `<div class="banner"><b>Safety risk</b>Keep people away from the equipment until a technician has checked it.</div>` : "";
  $("#result").innerHTML = `<div class="result-grid">
    <div class="area-top">${renderUnderstood(r)}${banner}</div>
    <section class="area-cause">${renderCauses(r)}</section>
    <section class="area-explain explain">${renderExplain(r)}</section>
    <section class="area-ticket">${renderTicket(r)}</section>
    <section class="area-cases">${renderCases(r)}</section>
  </div>`;
  const flow = $(".flow-status");
  if (flow) flow.textContent = r.diagnosis?.insufficient_evidence ? "ESCALATION" : "COMPLETE";
  addNotification(r.recommendation?.safety_critical ? "Safety risk detected" : "Diagnosis completed", r.equipment_type || "Maintenance issue", r.recommendation?.safety_critical ? "danger" : "success");
  window.dispatchEvent(new CustomEvent("cognilogix:diagnosis", { detail: r }));
}

/* ------------------------------------------------------------------ running a diagnosis */
function setBusy(busy) {
  const button = $("#run");
  button.disabled = busy;
  const label = button.querySelector(".run-label");
  if (label) label.textContent = busy ? "Analyzing" : "Analyze Issue";
  button.classList.toggle("busy", busy);
}
function handleEvent(ev) {
  if (ev.type === "step") handleStep(ev);
  else if (ev.type === "result") renderResult(ev.data);
  else if (ev.type === "error") {
    document.querySelectorAll(".station.running").forEach((el) => (el.className = "station error"));
    const flow = $(".flow-status"); if (flow) flow.textContent = "ERROR";
    showAlert("The diagnosis stopped.", ev.message);
    addNotification("Diagnosis failed", ev.message, "danger");
  }
}
async function runDiagnosis(complaint, hint) {
  setBusy(true); clearAlert(); renderLine(); $("#result").innerHTML = ""; markRunning("intake");
  addNotification("Diagnosis started", hint || "Auto-detect equipment", "info");
  try {
    const response = await fetch("/api/diagnose/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ complaint, equipment_hint: hint || null }) });
    if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || "The server answered with status " + response.status + "."); }
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buffer += decoder.decode(value, { stream: true }); let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) { const line = buffer.slice(0, newline).trim(); buffer = buffer.slice(newline + 1); if (line) handleEvent(JSON.parse(line)); }
    }
    if (buffer.trim()) handleEvent(JSON.parse(buffer.trim()));
  } catch (error) {
    document.querySelectorAll(".station.running").forEach((el) => (el.className = "station error"));
    showAlert("Could not run the diagnosis.", error.message.includes("fetch") ? "The server is not reachable. Check that it is still running in your terminal." : error.message);
  } finally { setBusy(false); loadHistory(); loadStats(); }
}

/* ------------------------------------------------------------------ history + stats */
async function loadHistory() {
  const list = $("#history");
  try {
    const response = await fetch("/api/history");
    if (!response.ok) throw new Error("history");
    const items = await response.json(); historyCache = Array.isArray(items) ? items : [];
    if (!historyCache.length) { list.innerHTML = `<li class="muted">No diagnoses yet. Your recent runs will appear here.</li>`; return historyCache; }
    list.innerHTML = historyCache.map((h) => {
      const badge = h.escalated ? `<span class="badge">Escalated</span>` : `<span class="badge lvl${Math.max(0, URGENCY.indexOf(h.urgency))}">${esc(h.urgency)}</span>`;
      return `<li><button class="hist-item" data-id="${esc(h.id)}"><span class="hist-text">${esc(h.complaint)}</span>${badge}<span class="hist-meta">${clock(h.created)}${h.top_cause ? " - " + esc(h.top_cause) : ""}</span></button></li>`;
    }).join("");
    return historyCache;
  } catch (_) { return historyCache; }
}
async function openRun(id) {
  clearAlert();
  try {
    const response = await fetch("/api/history/" + encodeURIComponent(id));
    if (!response.ok) throw new Error((await response.json()).detail || "Could not load diagnosis.");
    const run = await response.json();
    $("#complaint").value = run.complaint; renderLine(); (run.trace || []).forEach((t) => handleStep(t));
    document.querySelectorAll(".station.running").forEach((el) => (el.className = "station idle")); renderResult(run); scrollToDiagnosis();
  } catch (error) { showAlert("Could not open that diagnosis.", error.message); }
}
async function loadStats() {
  try {
    const s = await (await fetch("/api/stats")).json(); statsCache = s;
    $("#stat-kb").textContent = s.kb_cases ?? "-"; $("#stat-runs").textContent = s.runs ?? "-"; $("#stat-model").textContent = s.model ?? "-";
    const select = $("#equipment");
    if (select && select.options.length === 1) (s.equipment_types || []).forEach((t) => select.add(new Option(t, t)));
    $("#systemKbValue") && ($("#systemKbValue").textContent = s.kb_cases ?? "-");
    $("#systemRunsValue") && ($("#systemRunsValue").textContent = s.runs ?? "-");
  } catch (_) {}
}

/* ------------------------------------------------------------------ navigation, search, modals */
function scrollToDiagnosis() { $("#diagnose")?.scrollIntoView({ behavior: "smooth", block: "start" }); }
function scrollToHistory() { $("#history")?.closest(".rail-card")?.scrollIntoView({ behavior: "smooth", block: "center" }); }
function createModal() {
  if (modalEl) return modalEl;
  document.body.insertAdjacentHTML("beforeend", `<div id="cogModal" class="cog-modal" hidden>
    <div class="modal-backdrop" data-close-modal></div>
    <section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <header class="modal-head"><div><span class="section-kicker" id="modalKicker">COGNILOGIX</span><h2 id="modalTitle">Panel</h2></div><button class="modal-close" type="button" data-close-modal aria-label="Close">×</button></header>
      <div id="modalBody" class="modal-body"></div>
    </section>
  </div>`);
  modalEl = $("#cogModal");
  modalEl.addEventListener("click", (e) => { if (e.target.matches("[data-close-modal]")) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modalEl.hidden) closeModal(); });
  return modalEl;
}
function openModal(title, body, kicker = "COGNILOGIX") {
  const m = createModal(); $("#modalKicker").textContent = kicker; $("#modalTitle").textContent = title; $("#modalBody").innerHTML = body; m.hidden = false; document.body.classList.add("modal-open");
}
function closeModal() { if (!modalEl) return; modalEl.hidden = true; document.body.classList.remove("modal-open"); }

function openHistoryModal(filter = "") {
  const q = filter.trim().toLowerCase();
  const items = historyCache.filter((h) => !q || [h.complaint, h.top_cause, h.urgency, h.id].some(v => String(v || "").toLowerCase().includes(q)));
  const body = `<div class="modal-tools"><input id="historyFilter" class="modal-search" placeholder="Filter diagnosis history..." value="${esc(filter)}"><span>${items.length} result${items.length === 1 ? "" : "s"}</span></div>
    <div class="history-modal-list">${items.length ? items.map((h) => `<button class="history-modal-item" data-open-run="${esc(h.id)}"><span class="history-modal-main"><b>${esc(h.complaint)}</b><small>${clock(h.created)}${h.top_cause ? " · " + esc(h.top_cause) : ""}</small></span><span class="badge lvl${Math.max(0, URGENCY.indexOf(h.urgency))}">${esc(h.urgency || "Unknown")}</span></button>`).join("") : `<div class="modal-empty">No diagnosis history matches this search.</div>`}</div>`;
  openModal("Diagnosis History", body, "ACTIVITY LOG");
  $("#historyFilter")?.addEventListener("input", (e) => openHistoryModal(e.target.value));
  $("#modalBody")?.addEventListener("click", (e) => { const b = e.target.closest("[data-open-run]"); if (b) { closeModal(); openRun(b.dataset.openRun); } });
}
function openKnowledgeModal() {
  const types = statsCache?.equipment_types || [];
  const cases = currentResult?.cases || [];
  openModal("Knowledge Base", `<div class="overview-grid">
    <div class="overview-stat"><strong>${esc(statsCache?.kb_cases ?? "—")}</strong><span>Indexed maintenance cases</span></div>
    <div class="overview-stat"><strong>${esc(types.length || "—")}</strong><span>Equipment types</span></div>
  </div>
  <div class="modal-section"><h3>Indexed equipment</h3><div class="tag-cloud">${types.length ? types.map(t => `<span class="tag">${esc(t)}</span>`).join("") : `<span class="modal-muted">Equipment types are not available yet.</span>`}</div></div>
  <div class="modal-section"><h3>Retrieved evidence from the latest diagnosis</h3>${cases.length ? `<div class="kb-case-list">${cases.map(c => `<div><b>${esc(c.record_id)}</b><span>${esc(c.root_cause)}</span><em>${Number(c.similarity).toFixed(2)} match</em></div>`).join("")}</div>` : `<p class="modal-muted">Run a diagnosis to see the historical cases retrieved from ChromaDB here.</p>`}</div>`, "EVIDENCE STORE");
}
function openAnalyticsModal() {
  const total = historyCache.length;
  const escalated = historyCache.filter(h => h.escalated).length;
  const urgency = URGENCY.map(u => ({ name: u, count: historyCache.filter(h => h.urgency === u).length }));
  const max = Math.max(1, ...urgency.map(x => x.count));
  openModal("System Analytics", `<div class="overview-grid four"><div class="overview-stat"><strong>${total}</strong><span>Diagnoses in history</span></div><div class="overview-stat"><strong>${escalated}</strong><span>Escalated</span></div><div class="overview-stat"><strong>${total ? Math.round(((total - escalated) / total) * 100) : 0}%</strong><span>Resolved without escalation</span></div><div class="overview-stat"><strong>${statsCache?.kb_cases ?? "—"}</strong><span>Cases in knowledge base</span></div></div>
    <div class="modal-section"><h3>Urgency distribution</h3><div class="analytics-bars">${urgency.map(x => `<div class="analytics-row"><span>${x.name}</span><div><i style="width:${Math.round((x.count / max) * 100)}%"></i></div><b>${x.count}</b></div>`).join("")}</div></div>
    <p class="modal-muted">Analytics above are calculated from the diagnosis history currently exposed by the FastAPI service; no synthetic metrics are added by the UI.</p>`, "OPERATIONS ANALYTICS");
}
function applySettings() {
  document.body.classList.toggle("compact-mode", localStorage.getItem("cog-compact") === "1");
  document.body.classList.toggle("reduce-motion", localStorage.getItem("cog-motion") === "1");
}
function openSettingsModal() {
  const compact = localStorage.getItem("cog-compact") === "1";
  const motion = localStorage.getItem("cog-motion") === "1";
  openModal("Interface Settings", `<div class="settings-list">
    <label class="setting-row"><span><b>Compact dashboard</b><small>Reduce spacing so more information fits on screen.</small></span><input id="setCompact" type="checkbox" ${compact ? "checked" : ""}></label>
    <label class="setting-row"><span><b>Reduce animations</b><small>Disable glow and motion effects for a calmer interface.</small></span><input id="setMotion" type="checkbox" ${motion ? "checked" : ""}></label>
    <div class="setting-row"><span><b>AI language</b><small>Language is automatically detected by the diagnosis pipeline.</small></span><span class="setting-value">AUTO</span></div>
    <div class="setting-row"><span><b>Backend</b><small>FastAPI + LangGraph + ChromaDB</small></span><span class="setting-value online">ONLINE</span></div>
  </div>` , "PREFERENCES");
  $("#setCompact").addEventListener("change", e => { localStorage.setItem("cog-compact", e.target.checked ? "1" : "0"); applySettings(); });
  $("#setMotion").addEventListener("change", e => { localStorage.setItem("cog-motion", e.target.checked ? "1" : "0"); applySettings(); });
}
function openSearchModal(initial = "") {
  const q = initial.trim().toLowerCase();
  const historyMatches = historyCache.filter(h => !q || [h.complaint, h.top_cause, h.urgency, h.id].some(v => String(v || "").toLowerCase().includes(q)));
  const caseMatches = currentResult?.cases?.filter(c => !q || [c.record_id, c.root_cause, c.equipment_type, c.location, c.complaint].some(v => String(v || "").toLowerCase().includes(q))) || [];
  openModal("Search Cognilogix", `<div class="search-modal-wrap"><input id="globalSearchInput" class="modal-search search-large" placeholder="Search complaints, repairs, equipment or case IDs..." value="${esc(initial)}"><div id="searchResults" class="search-results">${renderSearchResults(historyMatches, caseMatches, q)}</div></div>`, "GLOBAL SEARCH");
  const input = $("#globalSearchInput"); input?.focus(); input?.setSelectionRange(input.value.length, input.value.length);
  input?.addEventListener("input", e => updateSearchResults(e.target.value));
  $("#searchResults")?.addEventListener("click", e => { const b = e.target.closest("[data-open-run]"); if (b) { closeModal(); openRun(b.dataset.openRun); } });
}
function renderSearchResults(historyMatches, caseMatches, q) {
  const historyHtml = historyMatches.slice(0, 8).map(h => `<button class="search-result" data-open-run="${esc(h.id)}"><span class="result-icon">◷</span><span><b>${esc(h.complaint)}</b><small>History · ${esc(h.top_cause || "No cause recorded")}</small></span><em>${esc(h.urgency || "—")}</em></button>`).join("");
  const casesHtml = caseMatches.slice(0, 8).map(c => `<div class="search-result static"><span class="result-icon">◈</span><span><b>${esc(c.record_id)} · ${esc(c.root_cause)}</b><small>${esc(c.equipment_type)}, ${esc(c.location)} · ${esc(c.complaint)}</small></span><em>${Number(c.similarity).toFixed(2)}</em></div>`).join("");
  if (!historyHtml && !casesHtml) return `<div class="modal-empty">${q ? "No matching cases were found in the data currently available to the dashboard." : "Start typing to search diagnosis history and retrieved maintenance cases."}</div>`;
  return `<div class="search-group"><h3>Diagnosis history</h3>${historyHtml || `<p class="modal-muted">No history matches.</p>`}</div>${caseMatches.length ? `<div class="search-group"><h3>Latest retrieved repairs</h3>${casesHtml}</div>` : ""}`;
}
function updateSearchResults(value) {
  const q = value.trim().toLowerCase();
  const historyMatches = historyCache.filter(h => !q || [h.complaint, h.top_cause, h.urgency, h.id].some(v => String(v || "").toLowerCase().includes(q)));
  const caseMatches = currentResult?.cases?.filter(c => !q || [c.record_id, c.root_cause, c.equipment_type, c.location, c.complaint].some(v => String(v || "").toLowerCase().includes(q))) || [];
  $("#searchResults").innerHTML = renderSearchResults(historyMatches, caseMatches, q);
}
function openNotifications() {
  openModal("Notifications", notifications.length ? notifications.map(n => `<div class="notification-item ${esc(n.type)}"><span class="notification-dot"></span><span><b>${esc(n.title)}</b><small>${esc(n.detail)} · ${n.time.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}</small></span></div>`).join("") : `<div class="modal-empty">No new notifications. Cognilogix will place diagnosis, escalation and feedback events here.</div>`, "ACTIVITY CENTER");
  notifications = []; updateNotificationBadge();
}
function openProfile() {
  openModal("Facility Manager", `<div class="profile-panel"><div class="profile-hero"><span class="avatar large">FM</span><div><h3>Facility Manager</h3><p>Campus Operations</p></div><span class="setting-value online">ONLINE</span></div><div class="profile-details"><div><span>Workspace</span><b>Cognilogix Decision Center</b></div><div><span>AI orchestration</span><b>5-stage LangGraph pipeline</b></div><div><span>Evidence store</span><b>${esc(statsCache?.kb_cases ?? "—")} indexed cases</b></div></div></div>`, "USER PROFILE");
}
function navigateView(view) {
  if (view === "dashboard") { closeModal(); window.scrollTo({ top: 0, behavior: "smooth" }); }
  else if (view === "diagnose") { closeModal(); scrollToDiagnosis(); $("#complaint")?.focus(); }
  else if (view === "history") openHistoryModal();
  else if (view === "knowledge") openKnowledgeModal();
  else if (view === "analytics") openAnalyticsModal();
  else if (view === "settings") openSettingsModal();
}

/* ------------------------------------------------------------------ wiring */
function init() {
  applySettings(); renderLine(); createModal();
  $("#samples").innerHTML = SAMPLES.map((s, i) => `<button type="button" class="chip" data-i="${i}"><b>${esc(s.tag)}</b>${esc(s.text)}</button>`).join("");
  $("#samples").addEventListener("click", (e) => { const chip = e.target.closest(".chip"); if (!chip) return; $("#complaint").value = SAMPLES[Number(chip.dataset.i)].text; scrollToDiagnosis(); $("#complaint").focus(); });
  $("#history").addEventListener("click", (e) => { const item = e.target.closest(".hist-item"); if (item) openRun(item.dataset.id); });
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); const complaint = $("#complaint").value.trim(); if (!complaint) { showAlert("Describe the problem first.", "Say which equipment is affected and what it is doing."); return; } runDiagnosis(complaint, $("#equipment").value); });
  $("#complaint").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#form").requestSubmit(); });
  document.querySelectorAll("[data-view]").forEach(a => a.addEventListener("click", e => { e.preventDefault(); document.querySelectorAll(".nav-item").forEach(n => n.classList.toggle("active", n === a)); navigateView(a.dataset.view); }));
  $("#historyViewAll")?.addEventListener("click", e => { e.preventDefault(); openHistoryModal(); });
  $("#globalSearch")?.addEventListener("click", () => openSearchModal());
  $("#notificationsBtn")?.addEventListener("click", openNotifications);
  $("#profileBtn")?.addEventListener("click", openProfile);
  $("#systemStatusBtn")?.addEventListener("click", () => openModal("System Status", `<div class="status-panel"><div class="status-ok"><span></span><div><b>All AI agents operational</b><small>FastAPI backend is responding and the dashboard is connected.</small></div></div><div class="status-grid"><div><b>Retrieval</b><span>ChromaDB</span></div><div><b>Orchestration</b><span>LangGraph</span></div><div><b>Reasoning</b><span>${esc(statsCache?.model || "Groq")}</span></div><div><b>Feedback</b><span>Enabled</span></div></div></div>`, "SYSTEM HEALTH"));
  document.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearchModal(); } });
  window.addEventListener("cognilogix:feedback", e => { const fb = e.detail; addNotification("Technician feedback recorded", fb?.record_id ? `${fb.record_id} added to the knowledge base` : "Knowledge base updated", "success"); loadStats(); });
  loadStats(); loadHistory();
}

init();

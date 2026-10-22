const state = { incidents: [], reference: null, integrations: {}, demoData: false, selectedId: null };
const byId = (id) => document.getElementById(id);
const priorityNames = { "1": "Critical", "2": "High", "3": "Moderate", "4": "Low", "5": "Planning" };

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function ageLabel(iso) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / 1440)}d`;
}

function pendingCount(incident) {
  return Number(incident.rca_review === "pending") + Number(incident.kb_review === "pending");
}

function renderMetrics() {
  const active = state.incidents.filter((item) => !["Resolved", "Closed"].includes(item.status));
  byId("metric-open").textContent = active.length;
  byId("metric-critical").textContent = active.filter((item) => Number(item.priority) <= 2).length;
  byId("metric-review").textContent = state.incidents.reduce((total, item) => total + pendingCount(item), 0);
  const reviewTotal = state.incidents.reduce((total, item) => total + pendingCount(item), 0);
  byId("review-total").textContent = reviewTotal;
  const incidentCount = document.querySelector(".nav-count");
  if (incidentCount) incidentCount.textContent = String(active.length).padStart(2, "0");
  document.querySelector(".review-dot")?.classList.toggle("hidden", reviewTotal === 0);
  const status = byId("connection-state");
  const allConfigured = state.integrations.servicenow && state.integrations.llm && state.integrations.application;
  status.classList.toggle("connected", allConfigured);
  status.classList.toggle("partial", !allConfigured);
  status.querySelector("span:last-child").textContent = allConfigured ? "All integrations connected" : "Demo / partial integrations";
  byId("demo-label").classList.toggle("hidden", !state.demoData);
}

function renderIncidents() {
  const query = byId("incident-search").value.trim().toLowerCase();
  const filter = byId("status-filter").value;
  const filtered = state.incidents.filter((incident) => {
    const matchesText = `${incident.number} ${incident.short_description} ${incident.application}`.toLowerCase().includes(query);
    const matchesFilter = filter === "all" || (filter === "open" && !["Resolved", "Closed"].includes(incident.status)) || (filter === "review" && pendingCount(incident) > 0);
    return matchesText && matchesFilter;
  });
  byId("incident-rows").innerHTML = filtered.map((incident) => `
    <tr class="incident-row" data-id="${escapeHtml(incident.id)}" tabindex="0">
      <td><div class="incident-cell"><span class="priority-mark priority-${escapeHtml(incident.priority)}">${escapeHtml(incident.priority)}</span><span class="incident-name"><strong>${escapeHtml(incident.short_description)}</strong><small>${escapeHtml(incident.number)} <span>·</span> ${escapeHtml(incident.category)}</small></span></div></td>
      <td><span class="priority-text p-${escapeHtml(incident.priority)}">P${escapeHtml(incident.priority)} <small>${priorityNames[incident.priority] || ""}</small></span></td>
      <td><span class="application-cell"><span class="service-icon table-service-icon">R</span>${escapeHtml(incident.application)}</span></td>
      <td><span class="status-pill status-${escapeHtml(incident.status.toLowerCase().replaceAll(" ", "-"))}"><i></i>${escapeHtml(incident.status)}</span></td>
      <td class="age-cell">${ageLabel(incident.created_at)}</td>
    </tr>`).join("");
  byId("empty-state").classList.toggle("hidden", filtered.length > 0);
  byId("incident-rows").classList.toggle("hidden", filtered.length === 0);
  byId("results-count").textContent = `${filtered.length} incident${filtered.length === 1 ? "" : "s"}`;
  document.querySelectorAll(".incident-row").forEach((row) => {
    row.addEventListener("click", () => openDetails(row.dataset.id));
    row.addEventListener("keydown", (event) => { if (event.key === "Enter") openDetails(row.dataset.id); });
  });
}

function renderReviews() {
  const tasks = state.incidents.flatMap((incident) => [
    ...(incident.rca_review === "pending" ? [{ incident, type: "rca", title: "Root cause analysis", label: "PROBLEM MANAGEMENT", icon: "⌁" }] : []),
    ...(incident.kb_review === "pending" ? [{ incident, type: "kb", title: "Knowledge article", label: "KNOWLEDGE BASE", icon: "▤" }] : []),
  ]);
  byId("review-list").innerHTML = tasks.length ? tasks.slice(0, 6).map(({ incident, type, title, label, icon }) => `
    <article class="review-item">
      <div class="review-item-top"><span class="review-type-icon">${icon}</span><span class="review-kind">${label}</span><span class="review-time">${ageLabel(incident.created_at)}</span></div>
      <strong>${escapeHtml(title)}</strong><p>${escapeHtml(incident.short_description)}</p>
      <div class="review-actions"><button class="text-action" data-view="${escapeHtml(incident.id)}">Review draft <span>↗</span></button><button class="approve-button" data-approve="${escapeHtml(incident.id)}" data-type="${type}" title="Approve and create in ServiceNow" aria-label="Approve ${title}">✓</button></div>
    </article>`).join("") : `<div class="review-empty"><span>✓</span><strong>Queue is clear</strong><p>New RCA and KB drafts will wait here for your review.</p></div>`;
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => openDetails(button.dataset.view)));
  document.querySelectorAll("[data-approve]").forEach((button) => button.addEventListener("click", () => approve(button.dataset.approve, button.dataset.type)));
}

function render() {
  renderMetrics();
  renderIncidents();
  renderReviews();
}

async function refresh() {
  try {
    const [stateResponse, referenceResponse] = await Promise.all([fetch("/api/state"), fetch("/api/reference")]);
    if (!stateResponse.ok || !referenceResponse.ok) throw new Error("Dashboard data could not be loaded.");
    const data = await stateResponse.json();
    state.reference = await referenceResponse.json();
    state.incidents = data.incidents;
    state.integrations = data.integrations;
    state.demoData = data.demo_data;
    render();
  } catch (error) {
    showToast(error.message, "error");
  }
}

function fillOptions() {
  const categories = state.reference.categories.map((item) => `<option>${escapeHtml(item)}</option>`).join("");
  byId("category").innerHTML = categories;
  byId("impact").innerHTML = Object.entries(state.reference.impact).map(([value, label]) => `<option value="${value}">${value} · ${escapeHtml(label.split(":")[0])}</option>`).join("");
  byId("urgency").innerHTML = Object.entries(state.reference.urgency).map(([value, label]) => `<option value="${value}">${value} · ${escapeHtml(label.split(":")[0])}</option>`).join("");
  byId("impact").value = "2";
  byId("urgency").value = "2";
  updatePriorityPreview();
}

function updatePriorityPreview() {
  const impact = byId("impact").value || "2";
  const urgency = byId("urgency").value || "2";
  const priority = state.reference.priority_matrix[`${impact}-${urgency}`];
  byId("priority-preview").textContent = `P${priority}`;
  byId("priority-preview").className = `preview-priority p-${priority}`;
  byId("priority-explanation").textContent = `${state.reference.impact[impact].split(":")[0]} impact · ${state.reference.urgency[urgency].split(":")[0]} urgency`;
}

function openDetails(id) {
  const incident = state.incidents.find((item) => item.id === id);
  if (!incident) return;
  const evidence = incident.evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  byId("detail-content").innerHTML = `
    <div class="detail-header"><div><p class="section-kicker">${escapeHtml(incident.number)} · ${escapeHtml(incident.application)}</p><h2>${escapeHtml(incident.short_description)}</h2></div><button class="icon-button" id="close-detail" aria-label="Close">×</button></div>
    <div class="detail-badges"><span class="priority-text p-${escapeHtml(incident.priority)}">Priority ${escapeHtml(incident.priority)}</span><span class="status-pill">${escapeHtml(incident.status)}</span><span class="detail-category">${escapeHtml(incident.category)}</span></div>
    ${incident.sync_status === "failed" ? `<div class="sync-warning"><strong>ServiceNow incident not yet created</strong><p>${escapeHtml(incident.sync_error || "The automatic request failed.")}</p><button class="secondary-button" id="retry-sync">Retry ServiceNow sync</button></div>` : ""}
    <section class="detail-section"><span class="detail-label">INCIDENT DESCRIPTION</span><p>${escapeHtml(incident.description)}</p></section>
    <section class="detail-section"><span class="detail-label">EVIDENCE <small>READ-ONLY SNAPSHOT</small></span><ul class="evidence-list">${evidence}</ul></section>
    <section class="detail-section"><span class="detail-label">AI ROOT CAUSE ANALYSIS <span class="pending-badge">${escapeHtml(incident.rca_review)}</span></span><p>${escapeHtml(incident.root_cause)}</p>${incident.rca_review === "pending" ? `<button class="secondary-button" data-detail-approve="rca" data-id="${escapeHtml(incident.id)}">Approve and create Problem</button>` : `<span class="created-note">Created: ${escapeHtml(incident.problem_number)}</span>`}</section>
    <section class="detail-section"><span class="detail-label">KNOWLEDGE ARTICLE DRAFT <span class="pending-badge">${escapeHtml(incident.kb_review)}</span></span><h3>${escapeHtml(incident.knowledge_title)}</h3><p class="pre-line">${escapeHtml(incident.knowledge_body)}</p>${incident.kb_review === "pending" ? `<button class="secondary-button" data-detail-approve="kb" data-id="${escapeHtml(incident.id)}">Approve and create KB draft</button>` : `<span class="created-note">Created: ${escapeHtml(incident.kb_number)} · remains draft in ServiceNow</span>`}</section>`;
  byId("detail-dialog").showModal();
  byId("close-detail").addEventListener("click", () => byId("detail-dialog").close());
  document.querySelectorAll("[data-detail-approve]").forEach((button) => button.addEventListener("click", () => approve(button.dataset.id, button.dataset.detailApprove, true)));
  byId("retry-sync")?.addEventListener("click", () => retrySync(incident.id));
}

async function approve(id, type, reopen = false) {
  const button = document.querySelector(`[data-approve="${CSS.escape(id)}"][data-type="${type}"]`);
  if (button) button.disabled = true;
  try {
    const response = await fetch(`/api/incidents/${encodeURIComponent(id)}/approve/${type}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Approval could not be completed.");
    byId("detail-dialog").close();
    await refresh();
    showToast(type === "rca" ? `Problem record created: ${result.incident.problem_number}` : `KB article created as draft: ${result.incident.kb_number}`, "success");
    if (reopen) openDetails(id);
  } catch (error) {
    showToast(error.message, "error");
    if (button) button.disabled = false;
  }
}

async function retrySync(id) {
  try {
    const response = await fetch(`/api/incidents/${encodeURIComponent(id)}/sync`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "ServiceNow sync failed.");
    byId("detail-dialog").close();
    await refresh();
    showToast(`ServiceNow incident created: ${result.incident.number}`, "success");
    openDetails(id);
  } catch (error) { showToast(error.message, "error"); }
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  byId("toast-region").append(toast);
  setTimeout(() => toast.remove(), 5200);
}

async function submitIncident(event) {
  event.preventDefault();
  const submit = byId("submit-incident");
  const errorBox = byId("form-error");
  submit.disabled = true;
  submit.innerHTML = `<span class="spinner"></span> Agent working…`;
  errorBox.classList.add("hidden");
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    const response = await fetch("/api/incidents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Incident could not be created.");
    byId("incident-dialog").close();
    event.currentTarget.reset();
    byId("impact").value = "2";
    byId("urgency").value = "2";
    updatePriorityPreview();
    await refresh();
    openDetails(result.incident.id);
    showToast(result.incident.sync_status === "failed" ? "Saved locally; ServiceNow creation failed. Retry from incident details." : `Incident created: ${result.incident.number}`, result.incident.sync_status === "failed" ? "error" : "success");
    if (result.llm_note) showToast(result.llm_note, "error");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  } finally {
    submit.disabled = false;
    submit.innerHTML = `<span class="plus">↗</span> Create incident`;
  }
}

byId("new-incident-button").addEventListener("click", () => byId("incident-dialog").showModal());
byId("close-dialog").addEventListener("click", () => byId("incident-dialog").close());
byId("incident-form").addEventListener("submit", submitIncident);
byId("impact").addEventListener("change", updatePriorityPreview);
byId("urgency").addEventListener("change", updatePriorityPreview);
byId("incident-search").addEventListener("input", renderIncidents);
byId("status-filter").addEventListener("change", renderIncidents);
byId("refresh-button").addEventListener("click", refresh);
byId("detail-dialog").addEventListener("click", (event) => { if (event.target === byId("detail-dialog")) byId("detail-dialog").close(); });
byId("incident-dialog").addEventListener("click", (event) => { if (event.target === byId("incident-dialog")) byId("incident-dialog").close(); });

async function boot() {
  byId("dashboard-date-label").textContent = new Intl.DateTimeFormat("en-AU", { weekday: "long", day: "2-digit", month: "long" }).format(new Date()).toUpperCase();
  const response = await fetch("/api/reference");
  state.reference = await response.json();
  fillOptions();
  await refresh();
}
boot().catch((error) => showToast(error.message, "error"));
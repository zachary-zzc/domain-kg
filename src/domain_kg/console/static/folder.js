import {reviewFolder, submitFolder, materialKey, FINISHED} from "./folder-plan.js";

const form = document.querySelector("[data-folder-form]");
const kg = form.dataset.kg, base = `/api/v1/kgs/${kg}`;
const copy = JSON.parse(form.dataset.copy), t = key => copy[key] || key;
const find = name => document.querySelector(`[data-folder-${name}]`);
const status = find("status"), rows = find("rows"), start = find("start"), stop = find("stop");
const picker = document.querySelector("#folder"), review = find("review");
let plan = [], busy = false, stopping = false, epoch = 0, pollTimer, pauseReason = null;
const accessErrors = new Set(["UNAUTHENTICATED", "FORBIDDEN", "POLICY_CHANGED", "RESOURCE_NOT_ACCESSIBLE"]);

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {credentials: "same-origin", signal: AbortSignal.timeout(120000), ...options,
      headers: {"x-csrf-token": form.elements.csrf_token.value, ...options.headers}});
  } catch {
    throw Object.assign(new Error(t("issue.NETWORK_ERROR")), {code: "NETWORK_ERROR"});
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw Object.assign(new Error(error.message || response.statusText),
      {code: error.code || "BACKEND_UNAVAILABLE", request_id: error.request_id});
  }
  return response.json();
}

async function pages(path) {
  const items = []; let cursor = null;
  do {
    const page = await api(`${path}?limit=100${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`);
    items.push(...page.items); cursor = page.next_cursor;
  } while (cursor);
  return items;
}

function lock(value) {
  busy = value; picker.disabled = value; review.disabled = value;
  start.disabled = value || !plan.some(item => item.state === "waiting");
}

function hidePrivateDetails() {
  ++epoch; clearTimeout(pollTimer); stopping = true; plan = [];
  rows.replaceChildren(); document.querySelector("#recent-imports").replaceChildren();
  find("progress").hidden = true; start.disabled = true;
  status.textContent = t("Sign in again before continuing. Source details were hidden because access could not be verified.");
}

function issueText(item) {
  if (item.job?.error && item.state !== "uploading") {
    return t(item.job.error.message) + " " + t(item.job.error.action);
  }
  return item.reason ? t("issue." + item.reason) : "";
}

function renderItem(item) {
  if (!item.row) {
    item.row = document.createElement("tr");
    for (let i = 0; i < 5; i++) item.row.append(document.createElement("td"));
    item.row.children[0].textContent = item.path;
    item.row.children[1].textContent = item.file.size < 1024 ? `${item.file.size} B` :
      item.file.size < 1048576 ? `${(item.file.size / 1024).toFixed(1)} KiB` : `${(item.file.size / 1048576).toFixed(1)} MiB`;
    rows.append(item.row);
  }
  const [, , action, progress, result] = item.row.children;
  item.row.dataset.state = item.state; action.textContent = t(item.action);
  progress.replaceChildren(); result.replaceChildren();
  const stage = item.job?.stage;
  let state = t({waiting: "Waiting", uploading: "Uploading original", skipped: "Skipped",
    invalid: "Invalid", failed: "Failed"}[item.state] || "Awaiting processing");
  if (stage && !["waiting", "invalid", "uploading"].includes(item.state)) state = t("stage." + stage);
  const label = document.createElement("span"); label.textContent = state; progress.append(label);
  if (item.state === "uploading" || (item.job && !FINISHED.has(stage) && item.state !== "invalid")) {
    const bar = document.createElement("progress"); bar.max = 1;
    bar.setAttribute("aria-label", t("File progress"));
    if (typeof item.job?.progress === "number") bar.value = item.job.progress;
    progress.append(bar);
  }
  const message = issueText(item);
  if (message) {
    const explanation = document.createElement("p"); explanation.textContent = message;
    explanation.className = "file-issue"; result.append(explanation);
  }
  if (item.job) {
    const link = document.createElement("a"); link.href = `/admin/kgs/${kg}/jobs/${item.job.job_id}`;
    link.textContent = t("View job") + " →"; result.append(link);
  }
  if (item.requestId) {
    const reference = document.createElement("small"); reference.className = "job-meta";
    reference.textContent = item.requestId; result.append(reference);
  }
  if (!busy && (item.state === "failed" || ["failed", "cancelled"].includes(stage)) && item.state !== "invalid") {
    const retry = document.createElement("button"); retry.type = "button"; retry.className = "quiet";
    retry.textContent = t(item.job ? "Retry job" : "Retry upload");
    retry.addEventListener("click", () => {
      item.state = "waiting"; item.reason = null; item.requestId = null;
      if (item.job) item.action = "Retry";
      run([item]);
    });
    result.append(retry);
  }
}

function summary(phase) {
  if (!plan.length) return;
  const uploaded = plan.filter(item => item.job).length;
  const eligible = plan.filter(item => !["Skip", "Unchanged"].includes(item.action) && item.state !== "invalid").length;
  const finished = plan.filter(item => ["skipped", "invalid", "failed"].includes(item.state) || FINISHED.has(item.job?.stage)).length;
  const errors = plan.filter(item => ["invalid", "failed"].includes(item.state) || item.job?.stage === "failed").length;
  find("progress").hidden = false;
  document.querySelector("[data-upload-count]").textContent = `${uploaded} / ${eligible}`;
  document.querySelector("[data-upload-progress]").max = Math.max(1, eligible);
  document.querySelector("[data-upload-progress]").value = uploaded;
  document.querySelector("[data-finished-count]").textContent = `${finished} / ${plan.length}`;
  document.querySelector("[data-finished-progress]").max = plan.length;
  document.querySelector("[data-finished-progress]").value = finished;
  const waiting = plan.filter(item => item.state === "waiting").length;
  let text = phase || (waiting ? "Uploads paused" : finished === plan.length ?
    "All files have reached a final state. Review any failures below." : "Uploads complete");
  status.textContent = `${t(text)} · ${plan.length} ${t("files")} · ${t("Ready to upload")}: ${waiting} · ${t("Errors")}: ${errors}. ` +
    (pauseReason ? t("issue." + pauseReason) : t("Files already submitted continue in the background. Unuploaded files need this tab or folder reselection."));
  start.textContent = t("Continue uploads");
  start.disabled = busy || !waiting;
}

async function refreshPlan(generation = epoch) {
  for (const item of plan) {
    if (generation !== epoch) return;
    if (!item.job || FINISHED.has(item.job.stage) || item.state === "invalid") continue;
    const job = await api(`${base}/jobs/${item.job.job_id}`);
    if (generation !== epoch) return;
    item.job = job; item.state = job.stage;
    renderItem(item);
  }
  summary();
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (!plan.some(item => item.job && !FINISHED.has(item.job.stage) && item.state !== "invalid")) return;
  const generation = epoch;
  pollTimer = setTimeout(async () => {
    try { await refreshPlan(generation); if (generation === epoch) schedulePoll(); }
    catch (error) {
      if (accessErrors.has(error.code)) hidePrivateDetails();
      else status.textContent = t("Status could not be refreshed. Accepted jobs continue; use Refresh status to reconnect.");
    }
  }, 2000);
}

picker.addEventListener("change", () => {
  if (busy) return;
  ++epoch; clearTimeout(pollTimer); plan = []; rows.replaceChildren(); start.disabled = true;
  find("progress").hidden = true; status.textContent = t("Choose a folder and review its files before uploading.");
});

form.addEventListener("submit", async event => {
  event.preventDefault(); if (busy) return;
  ++epoch; clearTimeout(pollTimer); plan = []; pauseReason = null; rows.replaceChildren(); lock(true);
  const files = Array.from(picker.files);
  status.textContent = `${t("Checking files")} · 0 / ${files.length}`;
  try {
    const [materials, jobs] = await Promise.all([pages(`${base}/materials`), pages(`${base}/jobs`)]);
    plan = await reviewFolder(files, materials, jobs, {
      detail: id => api(`${base}/materials/${id}`),
      onItem: renderItem,
      onProgress: (checked, total) => { status.textContent = `${t("Checking files")} · ${checked} / ${total}`; },
    });
    lock(false); plan.forEach(renderItem); summary("Review complete");
    if (!plan.length) status.textContent = t("No folder selected.");
    schedulePoll();
  } catch (error) {
    if (accessErrors.has(error.code)) hidePrivateDetails();
    else { rows.replaceChildren(); plan = []; status.textContent = t("Review failed. Check access and retry; no files were submitted.") + " " + t("issue." + error.code); }
  } finally { lock(false); }
});

async function submit(item) {
  if (item.job) {
    if (["failed", "cancelled", "repair_required"].includes(item.job.stage)) {
      return api(`${base}/jobs/${item.job.job_id}/retry`, {method: "POST",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify({idempotency_key: "folder-retry-" + crypto.randomUUID()})});
    }
    return item.job;
  }
  const key = await materialKey(item.source, item.material?.active_version_id);
  if (!item.upload) {
    const params = new URLSearchParams({media_type: item.mediaType, sha256: item.sha,
      size_bytes: String(item.file.size), idempotency_key: key + "-upload-" + item.sha.slice(0, 16)});
    if (item.material) params.set("target_material_id", item.material.id);
    item.upload = await api(`${base}/uploads?${params}`, {method: "POST",
      headers: {"Content-Type": item.mediaType}, body: item.file});
  }
  const command = {kg_id: kg, title: item.path, source: item.source,
    upload_id: item.upload.upload_id, idempotency_key: key};
  let endpoint = `${base}/materials`;
  if (item.material) {
    Object.assign(command, {material_id: item.material.id,
      expected_active_version: item.material.active_version_id,
      tags: item.material.metadata.tags, author: item.material.metadata.author,
      language: item.material.metadata.language});
    endpoint += `/${item.material.id}/versions`;
  }
  const receipt = await api(endpoint, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify(command)});
  return {...receipt, stage: "queued", progress: 0};
}

async function run(items = plan) {
  if (busy) return;
  lock(true); stopping = false; pauseReason = null; stop.disabled = false;
  plan.forEach(renderItem);
  try {
    await submitFolder(items, {submit, shouldStop: () => stopping,
      onChange: item => {
        renderItem(item); summary("Uploading files");
        if (item.reason) pauseReason = item.reason;
      }});
    if (items.some(item => accessErrors.has(item.reason))) { hidePrivateDetails(); return; }
  } finally {
    lock(false); stop.disabled = true; plan.forEach(renderItem); summary(); schedulePoll();
  }
}
stop.addEventListener("click", () => { stopping = true; stop.disabled = true; });
start.addEventListener("click", () => run());
find("refresh").addEventListener("click", async () => {
  if (!plan.length) { window.location.reload(); return; }
  try { await refreshPlan(); schedulePoll(); }
  catch (error) {
    if (accessErrors.has(error.code)) hidePrivateDetails();
    else status.textContent = t("Status could not be refreshed. Accepted jobs continue; use Refresh status to reconnect.");
  }
});
window.addEventListener("beforeunload", event => {
  if (busy || plan.some(item => item.state === "waiting" || (item.state === "failed" && !item.job))) {
    event.preventDefault(); event.returnValue = "";
  }
});

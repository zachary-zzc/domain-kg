import {reviewFolder, submitFolder, FINISHED, RETRYABLE, ACCESS_ERRORS,
  importManifest, restoreImport, hasUnsavedUploads, refreshJobs, importPage, importProgress} from "./folder-plan.js";

const workspace = document.querySelector("[data-import-workspace]");
if (workspace) initialize(workspace);

function initialize(workspace) {
  const form = workspace.querySelector("[data-folder-form]");
  const kg = workspace.dataset.kg, base = `/api/v1/kgs/${kg}`;
  const copy = JSON.parse(workspace.dataset.copy), t = key => copy[key] || key;
  const find = name => workspace.querySelector(`[data-folder-${name}]`);
  const rows = find("rows"), status = find("status"), start = find("start"), stop = find("stop");
  const filePicker = form.querySelector("[data-file-picker]"), folderPicker = form.querySelector("[data-directory-picker]");
  const review = find("review"), textForm = workspace.querySelector("[data-import-text]");
  const modeButtons = Array.from(workspace.querySelectorAll("[data-import-mode]"));
  const choose = workspace.querySelector("[data-open-picker]"), selection = workspace.querySelector("[data-picker-selection]");
  const filter = workspace.querySelector("[data-import-filter]"), search = workspace.querySelector("[data-import-search]");
  const previousPages = workspace.querySelectorAll("[data-import-previous]"), nextPages = workspace.querySelectorAll("[data-import-next]");
  let pageNumber = 1, visibleItems = new Set();
  let run = JSON.parse(workspace.dataset.run || "null"), plan = run ? restoreImport(run.items) : [];
  const savedId = workspace.dataset.importId || null, existingRun = Boolean(run || savedId);
  let mode = workspace.dataset.mode || "files", busy = false, stopping = false, epoch = 0;
  let pollTimer, pollFailures = 0, denied = false, creatingKey = crypto.randomUUID(), skipped = [], operationIssue = null;
  const picker = () => mode === "folder" ? folderPicker : filePicker;
  const currentUrl = () => `/admin/kgs/${kg}/materials?view=processing&import_id=${run.id}`;
  const notifyRows = () => document.dispatchEvent(new Event("domain-kg:items-updated"));

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

  const post = (path, body) => api(path, {method: "POST", headers: {"Content-Type": "application/json"},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});

  async function pages(path) {
    const items = []; let cursor = null;
    do {
      const result = await api(`${path}${path.includes("?") ? "&" : "?"}limit=100${cursor ? "&cursor=" + encodeURIComponent(cursor) : ""}`);
      items.push(...result.items); cursor = result.next_cursor;
    } while (cursor);
    return items;
  }

  function canSubmit(item) {
    return !item.job && !item.statusUnavailable && item.state !== "invalid" && !["Skip", "Unchanged"].includes(item.action) &&
      Boolean(item.file || item.canResume || item.existingJob);
  }

  const canStart = () => {
    if (run) return plan.some(canSubmit);
    const eligible = plan.filter(item => item.state !== "invalid" && !["Skip", "Unchanged"].includes(item.action));
    return eligible.length > 0 && eligible.length <= 100;
  };

  function lock(value) {
    busy = value;
    filePicker.disabled = value || denied || mode !== "files";
    folderPicker.disabled = value || denied || mode !== "folder";
    review.disabled = value || denied;
    choose.disabled = value || denied;
    review.disabled ||= !picker().files.length;
    find("refresh").disabled = value || denied;
    for (const button of modeButtons) button.disabled = value || denied;
    start.disabled = value || denied || !canStart();
  }

  function hidePrivateDetails(reload = true) {
    denied = true; ++epoch; clearTimeout(pollTimer); stopping = true; plan = []; skipped = []; run = null;
    rows.replaceChildren(); workspace.removeAttribute("data-run");
    filePicker.value = ""; folderPicker.value = "";
    selection.textContent = ""; search.value = "";
    workspace.querySelector("[data-picker-heading]").textContent = "";
    workspace.querySelector("[data-import-counts]").textContent = "";
    workspace.querySelector("[data-import-guidance]").textContent = "";
    textForm.reset(); textForm.hidden = true; find("progress").hidden = true;
    find("current").hidden = true;
    workspace.querySelector("[data-import-identity]").replaceChildren();
    document.querySelector("[data-source-default-list]")?.replaceChildren();
    document.querySelector("#batch-results")?.remove();
    for (const control of document.querySelectorAll("[data-bulk-form]")) control.hidden = true;
    status.textContent = t("Sign in again before continuing. Source details were hidden because access could not be verified.");
    lock(false); notifyRows();
    if (reload) {
      document.documentElement.classList.add("leaving");
      window.location.reload();
    }
  }

  function useMode(next) {
    if (busy || denied) return;
    if (next !== mode && !run) {
      ++epoch; clearTimeout(pollTimer); plan = []; skipped = []; pageNumber = 1;
      filePicker.value = ""; folderPicker.value = ""; renderAll();
      status.textContent = t("Choose files or a folder and review before uploading.");
    }
    mode = next;
    for (const button of modeButtons) button.setAttribute("aria-pressed", String(button.dataset.importMode === mode));
    form.hidden = mode === "text";
    textForm.hidden = mode !== "text";
    filePicker.hidden = mode !== "files"; folderPicker.hidden = mode !== "folder";
    form.querySelector("[data-picker-label]").htmlFor = mode === "folder" ? folderPicker.id : filePicker.id;
    form.querySelector("[data-picker-label]").textContent = t(mode === "folder" ? "Folder" : "Files");
    choose.textContent = t(mode === "folder" ? "Choose folder" : "Choose files");
    pickerSummary();
    lock(false);
  }

  function pickerSummary() {
    const files = Array.from(picker().files);
    if (!files.length) {
      selection.textContent = t(run ? "Saved import. Reselect originals only for unfinished uploads." : "No files selected.");
      workspace.querySelector("[data-picker-heading]").textContent = "";
      return;
    }
    const name = mode === "folder" ? files[0].webkitRelativePath.split("/")[0] :
      files.length === 1 ? files[0].name : t("Files");
    selection.textContent = `${name} · ${files.length} ${t("files")}`;
    workspace.querySelector("[data-picker-heading]").textContent = `· ${selection.textContent}`;
  }

  function sizeLabel(size) {
    return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KiB` : `${(size / 1048576).toFixed(1)} MiB`;
  }

  function appendLink(container, href, text) {
    const link = document.createElement("a"); link.href = href; link.textContent = t(text) + " →";
    const paragraph = document.createElement("p"); paragraph.append(link); container.append(paragraph);
  }

  function renderItem(item) {
    if (!visibleItems.has(item)) return;
    if (!item.row) {
      item.row = document.createElement("tr");
      for (let i = 0; i < 6; i++) item.row.append(document.createElement("td"));
      const checkbox = document.createElement("input"); checkbox.type = "checkbox";
      item.row.children[0].append(checkbox);
      const name = document.createElement("strong"); name.className = "import-file-name";
      name.textContent = item.path.split("/").pop(); item.row.children[1].append(name);
      if (item.path.includes("/")) {
        const parent = document.createElement("small"); parent.className = "import-file-path";
        parent.textContent = item.path.slice(0, item.path.lastIndexOf("/")); parent.title = item.path;
        item.row.children[1].append(parent);
      }
      item.row.children[1].title = item.path;
      item.row.children[2].textContent = sizeLabel(item.sizeBytes ?? item.file?.size ?? 0);
      rows.append(item.row);
    }
    const [selection, , , action, progress, result] = item.row.children;
    const checkbox = selection.firstElementChild, stage = item.job?.stage;
    item.row.dataset.state = item.state; action.textContent = t(item.action);
    checkbox.setAttribute("aria-label", t("Select") + " " + item.path);
    if (item.job && run) {
      checkbox.hidden = false; checkbox.disabled = false;
      checkbox.dataset.bulkItem = ""; checkbox.dataset.bulkId = item.job.job_id;
      checkbox.dataset.bulkLabel = item.path; checkbox.dataset.bulkStage = stage;
      checkbox.dataset.bulkRetryable = String(RETRYABLE.has(stage));
    } else { checkbox.hidden = true; checkbox.disabled = true; checkbox.removeAttribute("data-bulk-item"); }
    progress.replaceChildren(); result.replaceChildren();
    let state = t({waiting: "Waiting", uploading: item.job || item.existingJob || item.canResume ? "Submitting" : "Uploading original", skipped: "Skipped",
      invalid: "Invalid", failed: "Failed"}[item.state] || "Awaiting processing");
    if (stage && item.state !== "uploading") state = t("stage." + stage);
    const label = document.createElement("span"); label.textContent = state;
    label.className = "badge" + (RETRYABLE.has(stage) || ["invalid", "failed"].includes(item.state) ? " warning" : "");
    progress.append(label);
    if (item.state === "uploading" || (item.job && !FINISHED.has(stage))) {
      const bar = document.createElement("progress"); bar.max = 1; bar.setAttribute("aria-label", t("File progress"));
      if (item.state !== "uploading" && typeof item.job?.progress === "number") bar.value = item.job.progress;
      progress.append(bar);
    }
    let message = item.job?.error ? t(item.job.error.message) + " " + t(item.job.error.action) :
      item.reason ? t("issue." + item.reason) : "";
    if (item.pollError || item.statusUnavailable) message = t("This file’s status could not be refreshed. Other files continue; reconnecting automatically.");
    if (message) {
      const explanation = document.createElement("p"); explanation.textContent = message;
      explanation.className = "file-issue"; result.append(explanation);
    }
    if (stage === "ready_with_warnings") {
      const warning = document.createElement("p"); warning.className = "field-help";
      warning.textContent = t("Published with warnings. Review the job for details."); result.append(warning);
    }
    if (["ready", "ready_with_warnings"].includes(stage) && item.job.source) {
      appendLink(result, `/admin/kgs/${kg}/materials/${item.job.source.material_id}`, "Open source");
      appendLink(result, `/admin/kgs/${kg}/graph`, "View KG");
    } else if (item.job) appendLink(result, `/admin/kgs/${kg}/jobs/${item.job.job_id}`, "View job");
    if (item.requestId) {
      const reference = document.createElement("small"); reference.className = "job-meta";
      reference.textContent = item.requestId; result.append(reference);
    }
    if (!busy && run && RETRYABLE.has(stage)) {
      const button = document.createElement("button"); button.type = "button"; button.className = "quiet";
      button.textContent = t("Retry job"); button.addEventListener("click", () => runWork([item], true)); result.append(button);
    } else if (!busy && run && !item.job && canSubmit(item)) {
      const button = document.createElement("button"); button.type = "button"; button.className = "quiet";
      button.textContent = t(item.canResume ? "Resume file" : "Retry upload");
      button.addEventListener("click", () => runWork([item])); result.append(button);
    } else if (run && !item.job && !item.statusUnavailable && !item.file && !item.canResume && !item.existingJob) {
      const help = document.createElement("p"); help.className = "field-help";
      help.textContent = t("Select the same original files above to finish uploading."); result.append(help);
    }
  }

  function renderAll() {
    const all = [...plan, ...skipped];
    const page = importPage(all, {view: filter.value, query: search.value, page: pageNumber});
    pageNumber = page.page; visibleItems = new Set(page.items);
    rows.replaceChildren();
    for (const item of all) item.row = null;
    for (const item of page.items) renderItem(item);
    if (!page.items.length && all.length) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 6; cell.className = "empty-state";
      cell.textContent = t("No files match this view. Change the filter or search.");
      row.append(cell); rows.append(row);
    }
    for (const option of filter.options) {
      const count = option.value === "all" ? page.counts.total : page.counts[option.value];
      option.textContent = `${t(option.dataset.label)} (${count})`;
    }
    for (const range of workspace.querySelectorAll("[data-import-range]")) range.textContent = `${page.first}–${page.last} / ${page.total}`;
    for (const button of previousPages) button.disabled = pageNumber === 1;
    for (const button of nextPages) button.disabled = pageNumber === page.pages;
    find("current").hidden = plan.length + skipped.length === 0;
    workspace.querySelector("[data-import-details]").hidden = !all.length;
    document.querySelector("[data-source-default-list]")?.toggleAttribute("hidden", all.length > 0);
    notifyRows();
  }

  function showRun() {
    if (!run) return;
    find("current").hidden = false;
    const identity = workspace.querySelector("[data-import-identity]"); identity.replaceChildren();
    const time = document.createElement("time"); time.dateTime = run.created_at;
    time.textContent = new Date(run.created_at).toLocaleString(document.documentElement.lang);
    identity.append(t("Current import") + " · ", time);
    const link = workspace.querySelector("[data-import-permalink]"); link.href = currentUrl(); link.hidden = false;
    document.querySelector("[data-source-default-list]")?.setAttribute("hidden", "");
  }

  function summary(phase) {
    if (denied) return;
    const {submitted, eligible, finished, pending, errors} = importProgress([...plan, ...skipped]);
    find("progress").hidden = !run;
    workspace.querySelector("[data-upload-count]").textContent = `${submitted} / ${eligible.length}`;
    workspace.querySelector("[data-upload-progress]").max = Math.max(1, eligible.length);
    workspace.querySelector("[data-upload-progress]").value = submitted;
    workspace.querySelector("[data-finished-count]").textContent = `${finished} / ${eligible.length}`;
    workspace.querySelector("[data-finished-progress]").max = Math.max(1, eligible.length);
    workspace.querySelector("[data-finished-progress]").value = finished;
    const missingOriginal = eligible.some(item => !item.job && !item.file && !item.canResume && !item.existingJob && !item.statusUnavailable);
    const text = phase || (!run ? "Review complete" : pending ? "Uploads paused" : finished === eligible.length ?
      "All files have reached a final state. Review any failures below." : "Processing continues in the background.");
    const counts = importPage([...plan, ...skipped]).counts;
    workspace.querySelector("[data-import-counts]").textContent = `${t("Found")}: ${counts.total} · ${t("Ready to upload")}: ${pending} · ${t("Skipped")}: ${counts.skipped} · ${t("Errors")}: ${errors}`;
    workspace.querySelector("[data-import-guidance]").textContent = !run && eligible.length > 100 ? t("issue.IMPORT_TOO_MANY") :
      !eligible.length ? t("No files need uploading. Review skipped files or follow existing jobs below.") :
      !run ? t("Review the included files, then upload. Skipped files are not uploaded.") :
      t("Each file runs independently. You can leave after uploads finish.");
    status.textContent = `${t(text)} ` +
      (operationIssue ? t("issue." + operationIssue) : pollFailures ? t("Status could not be refreshed. Accepted jobs continue; use Refresh status to reconnect.") :
        plan.some(item => item.statusUnavailable) ? t("This file’s status could not be refreshed. Other files continue; reconnecting automatically.") :
        run && missingOriginal ? t("Select the same original files above to finish uploading.") : "");
    start.textContent = t(run ? pending ? "Continue uploads" : "Uploads complete" : "Upload and construct KG") + (pending ? ` (${pending})` : "");
    start.disabled = busy || !canStart() || (!run && eligible.length > 100);
    stop.hidden = !busy || !run;
  }

  async function refreshPlan(generation = epoch, reloadRun = false) {
    if (reloadRun && run) {
      const updated = await api(`${base}/import-runs/${run.id}`);
      if (generation !== epoch) return;
      run = updated; plan = restoreImport(run.items, plan); renderAll();
    }
    pollFailures = await refreshJobs(plan, {read: id => api(`${base}/jobs/${id}`),
      shouldStop: () => generation !== epoch || denied,
      onChange: item => { renderItem(item); }});
    if (generation !== epoch) return;
    renderAll(); summary();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (busy || denied || !plan.some(item => item.statusUnavailable || (item.job && !FINISHED.has(item.job.stage)))) return;
    const generation = epoch;
    pollTimer = setTimeout(async () => {
      try { await refreshPlan(generation, plan.some(item => item.statusUnavailable)); }
      catch (error) {
        if (ACCESS_ERRORS.has(error.code)) hidePrivateDetails();
        else { pollFailures++; summary(); }
      } finally { if (generation === epoch) schedulePoll(); }
    }, pollFailures ? 5000 : 2000);
  }

  for (const input of [filePicker, folderPicker]) input.addEventListener("change", () => {
    if (busy) return;
    if (!run) { ++epoch; clearTimeout(pollTimer); plan = []; skipped = []; pageNumber = 1; filter.value = "included"; search.value = ""; renderAll(); find("progress").hidden = true; }
    pickerSummary();
    lock(false);
    if (picker().files.length) form.requestSubmit();
    else status.textContent = t("No files selected.");
  });
  choose.addEventListener("click", () => picker().click());
  filter.addEventListener("change", () => { pageNumber = 1; renderAll(); });
  search.addEventListener("input", () => { pageNumber = 1; renderAll(); });
  for (const button of previousPages) button.addEventListener("click", () => { pageNumber--; renderAll(); });
  for (const button of nextPages) button.addEventListener("click", () => { pageNumber++; renderAll(); });
  for (const button of modeButtons) button.addEventListener("click", () => useMode(button.dataset.importMode));

  form.addEventListener("submit", async event => {
    event.preventDefault(); if (busy || denied) return;
    const files = Array.from(picker().files);
    if (!files.length) { status.textContent = t("No files selected."); return; }
    ++epoch; clearTimeout(pollTimer); lock(true); operationIssue = null;
    status.textContent = `${t("Checking files")} · 0 / ${files.length}`;
    try {
      const [materials, jobs, current] = await Promise.all([pages(`${base}/materials`), pages(`${base}/jobs?view=incomplete&sources_only=true`),
        run ? api(`${base}/import-runs/${run.id}`) : Promise.resolve(null)]);
      const reviewed = await reviewFolder(files, materials, jobs, {detail: id => api(`${base}/materials/${id}`),
        onProgress: (checked, total) => { status.textContent = `${t("Checking files")} · ${checked} / ${total}`; }});
      if (run) {
        run = current; plan = restoreImport(run.items, [...reviewed, ...plan]); skipped = [];
      } else { plan = reviewed; creatingKey = crypto.randomUUID(); }
      renderAll(); summary();
      workspace.querySelector("#import-input").open = false;
      if (!run && !plan.some(item => item.state !== "invalid" && !["Skip", "Unchanged"].includes(item.action)))
        status.textContent = t("No files need uploading. Review skipped files or follow existing jobs below.");
    } catch (error) {
      if (ACCESS_ERRORS.has(error.code)) hidePrivateDetails();
      else status.textContent = t("Review failed. Check access and retry; no files were submitted.") + " " + t("issue." + (error.code || "READ_FAILED"));
    } finally { lock(false); plan.forEach(renderItem); notifyRows(); schedulePoll(); }
  });

  function accept(item, saved) {
    Object.assign(item, restoreImport([saved], [item])[0]);
    if (!item.job) throw Object.assign(new Error(saved.message), {code: saved.code || "BACKEND_UNAVAILABLE"});
    return item.job;
  }

  async function submit(item) {
    if (!item.job && item.existingJob) {
      accept(item, await post(`${base}/import-runs/${run.id}/items/${item.index}`, {job_id: item.existingJob.job_id}));
    } else if (!item.job && item.canResume) {
      accept(item, await post(`${base}/import-runs/${run.id}/items/${item.index}/resume`));
    } else if (!item.job) {
      if (!item.file) throw Object.assign(new Error("Original required"), {code: "ORIGINAL_REQUIRED"});
      if (!item.upload) {
        const params = new URLSearchParams({media_type: item.mediaType, sha256: item.sha,
          size_bytes: String(item.file.size), idempotency_key: `run-${run.id}-${item.index}-upload`});
        if (item.material) params.set("target_material_id", item.material.id);
        item.upload = await api(`${base}/uploads?${params}`, {method: "POST",
          headers: {"Content-Type": item.mediaType}, body: item.file});
      }
      const body = {upload_id: item.upload.upload_id};
      if (item.material) Object.assign(body, {material_id: item.material.id,
        expected_active_version: item.material.active_version_id, expected_revision: item.material.revision});
      accept(item, await post(`${base}/import-runs/${run.id}/items/${item.index}`, body));
    }
    if (item.retryRequested && RETRYABLE.has(item.job.stage)) {
      item.job = await post(`${base}/jobs/${item.job.job_id}/retry`, {idempotency_key: "retry-" + crypto.randomUUID()});
    }
    return item.job;
  }

  async function runWork(items = plan, retry = false) {
    if (busy || denied) return;
    ++epoch; clearTimeout(pollTimer); lock(true); stopping = false; stop.disabled = false; operationIssue = null;
    try {
      if (!run) {
        const files = importManifest(plan);
        if (!files.length) return;
        const previous = plan;
        run = await post(`${base}/import-runs`, {files, idempotency_key: creatingKey});
        const paths = new Set(run.items.map(item => item.path));
        skipped = previous.filter(item => !paths.has(item.path));
        plan = restoreImport(run.items, previous); items = plan;
        history.replaceState(null, "", currentUrl()); showRun(); renderAll();
      }
      for (const item of items) {
        item.retryRequested = retry;
        if (canSubmit(item) || (retry && RETRYABLE.has(item.job?.stage))) item.state = "waiting";
      }
      plan.forEach(renderItem);
      await submitFolder(items, {submit, shouldStop: () => stopping || denied,
        onChange: item => { renderItem(item); summary("Uploading files"); }});
      if (items.some(item => ACCESS_ERRORS.has(item.reason))) { hidePrivateDetails(); return; }
      if (!existingRun && plan.every(item => item.job)) window.location.assign(currentUrl());
    } catch (error) {
      if (ACCESS_ERRORS.has(error.code)) hidePrivateDetails();
      else { operationIssue = error.code || "NETWORK_ERROR"; status.textContent = t("issue." + operationIssue); }
    } finally {
      lock(false); stop.disabled = true;
      for (const item of plan) { item.retryRequested = false; renderItem(item); }
      if (!denied) { summary(); notifyRows(); schedulePoll(); }
    }
  }

  stop.addEventListener("click", () => { stopping = true; stop.disabled = true; });
  start.addEventListener("click", () => runWork());
  async function loadSavedRun() {
    lock(true);
    status.textContent = t("Loading saved import status. Accepted jobs continue in the background.");
    try {
      run = await api(`${base}/import-runs/${savedId}`);
      plan = restoreImport(run.items); showRun(); renderAll(); summary();
      mode = run.items[0].path.includes("/") ? "folder" : "files";
    } catch (error) {
      if (ACCESS_ERRORS.has(error.code)) hidePrivateDetails(false);
      else status.textContent = t("Status could not be refreshed. Accepted jobs continue; use Refresh status to reconnect.");
    } finally {
      lock(false); useMode(mode);
      if (run && !denied) summary();
      schedulePoll();
    }
  }
  find("refresh").addEventListener("click", async () => {
    if (busy || denied) return;
    if (!run && savedId) { await loadSavedRun(); return; }
    try { await refreshPlan(epoch, true); }
    catch (error) {
      if (ACCESS_ERRORS.has(error.code)) hidePrivateDetails();
      else { pollFailures++; summary(); }
    } finally { schedulePoll(); }
  });
  window.addEventListener("beforeunload", event => {
    if (hasUnsavedUploads(plan)) { event.preventDefault(); event.returnValue = ""; }
  });
  useMode(mode);
  if (plan.length) { showRun(); renderAll(); summary(); schedulePoll(); }
  else if (savedId) loadSavedRun();
}

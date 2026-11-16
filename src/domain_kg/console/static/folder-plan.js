// The browser supplies file bytes, never an executable server filesystem path.
import {fileMediaType} from "./file-types.js";

export function sourceFile(file) {
  const path = file.webkitRelativePath || file.name;
  const parts = path.split("/");
  if (parts.some(part => !part || part === "." || part === ".." || part.includes("\\") || /[\u0000-\u001f]/.test(part)) || path.length > 990) {
    throw issue("INVALID_PATH", "A filename is invalid or too long.");
  }
  if (parts.some(part => part.startsWith("."))) return null;
  const mediaType = fileMediaType(file);
  if (!mediaType) return null;
  if (!file.size) return null;
  if (file.size > 50 * 1024 * 1024) {
    throw issue("FILE_TOO_LARGE", "Choose a file up to 50 MiB.");
  }
  return {file, path, source: "folder:" + path, mediaType};
}

export function matchMaterial(source, materials) {
  const matches = materials.filter(item => item.metadata.source === source);
  if (matches.length > 1) throw issue("AMBIGUOUS_SOURCE", "Multiple sources match this path. Resolve them in Sources first.");
  return matches[0] || null;
}

export async function sha256(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function materialKey(source, activeVersion) {
  return "folder-" + await sha256(new TextEncoder().encode(source + (activeVersion ? "\0" + activeVersion : "")));
}

function issue(code, message) { return Object.assign(new Error(message), {code}); }

export const ACCESS_ERRORS = new Set(["UNAUTHENTICATED", "FORBIDDEN", "POLICY_CHANGED", "RESOURCE_NOT_ACCESSIBLE"]);
export const STOP_CODES = new Set([...ACCESS_ERRORS, "QUOTA_EXCEEDED"]);
export const RETRYABLE = new Set(["failed", "cancelled", "repair_required"]);
export const FINISHED = new Set(["ready", "ready_with_warnings", ...RETRYABLE]);

export function importProgress(items) {
  const eligible = items.filter(item => item.state !== "invalid" && !["Skip", "Unchanged"].includes(item.action));
  const submitted = eligible.filter(item => item.job || item.statusUnavailable).length;
  return {eligible, submitted, pending: eligible.length - submitted,
    finished: eligible.filter(item => item.job && FINISHED.has(item.job.stage)).length,
    errors: items.filter(item => ["invalid", "failed"].includes(item.state) || RETRYABLE.has(item.job?.stage)).length};
}

// The visible page never changes the full reviewed plan used for admission.
export function importPage(items, {view = "included", query = "", page = 1} = {}) {
  const skipped = item => item.state === "skipped" && !item.job;
  const issue = item => ["invalid", "failed"].includes(item.state) || RETRYABLE.has(item.job?.stage) ||
    item.job?.stage === "ready_with_warnings" || item.pollError || item.statusUnavailable;
  const counts = {total: items.length, included: items.filter(item => !skipped(item)).length,
    skipped: items.filter(skipped).length, issues: items.filter(issue).length};
  const search = query.trim().toLocaleLowerCase();
  const matches = items.filter(item => (view === "all" || (view === "skipped" ? skipped(item) :
    view === "issues" ? issue(item) : !skipped(item))) && item.path.toLocaleLowerCase().includes(search));
  const total = matches.length, pages = Math.max(1, Math.ceil(total / 20));
  page = Math.max(1, Math.min(pages, page));
  const offset = (page - 1) * 20;
  return {items: matches.slice(offset, offset + 20), counts, total, page, pages,
    first: total ? offset + 1 : 0, last: Math.min(offset + 20, total)};
}

export async function reviewFolder(files, materials, jobs, {detail, onItem = () => {}, onProgress = () => {}}) {
  const plan = [], paths = new Set();
  for (const file of files) {
    let item = {file, path: file.webkitRelativePath || file.name, action: "Skip", state: "skipped"};
    try {
      const source = sourceFile(file);
      if (!source) {
        item.reason = !file.size ? "EMPTY_FILE" : item.path.split("/").some(p => p.startsWith(".")) ? "HIDDEN_FILE" : "UNSUPPORTED_FILE";
      } else {
        Object.assign(item, source, {action: "Add", state: "waiting"});
        if (paths.has(item.path)) throw issue("INVALID_PATH", "Duplicate relative path.");
        paths.add(item.path);
        item.sha = await sha256(await file.arrayBuffer());
        item.material = matchMaterial(item.source, materials);
        if (item.material) {
          const current = (await detail(item.material.id)).versions.find(v => v.id === item.material.active_version_id);
          if (!current) throw issue("VERSION_CONFLICT", "The accepted source changed. Review again.");
          item.action = current.sha256 === item.sha ? "Unchanged" : "Replace";
          if (item.action === "Unchanged") item.state = "skipped";
        }
        const job = jobs.find(j => ["ingest", "replace", "import", "reprocess_material"].includes(j.kind) &&
          j.source?.source === item.source && (!item.material || j.source.material_id === item.material.id) &&
          (!item.material || j.source.expected_active_version === item.material.active_version_id) &&
          (!FINISHED.has(j.stage) || RETRYABLE.has(j.stage)));
        if (job && item.action !== "Unchanged") {
          item.job = job;
          if (job.source.sha256 !== item.sha) throw issue("PENDING_CHANGED", "An earlier job has different file contents. Resolve that job first.");
          item.action = "Track";
          item.state = job.stage;
        }
      }
    } catch (error) {
      if (STOP_CODES.has(error.code)) throw error;
      item.state = "invalid"; item.reason = error.code || "READ_FAILED";
    }
    plan.push(item); onItem(item); onProgress(plan.length, files.length);
  }
  return plan;
}

export async function submitFolder(plan, {submit, shouldStop = () => false, onChange = () => {}}) {
  for (const item of plan) {
    if (shouldStop()) break;
    if (item.state !== "waiting") continue;
    item.state = "uploading"; onChange(item);
    try {
      item.job = await submit(item);
      item.state = item.job.stage; item.reason = null;
    } catch (error) {
      item.state = "failed"; item.reason = error.code || "NETWORK_ERROR";
      item.requestId = error.request_id;
      onChange(item);
      if (STOP_CODES.has(item.reason)) break;
      continue;
    }
    onChange(item);
  }
}

export function importManifest(plan) {
  const eligible = plan.filter(item => !["Skip", "Unchanged"].includes(item.action) && item.state !== "invalid");
  if (eligible.length > 100) throw issue("IMPORT_TOO_MANY", "Choose up to 100 files per import.");
  return eligible.map(item => ({path: item.path, sha256: item.sha,
    size_bytes: item.sizeBytes ?? item.file.size, media_type: item.mediaType}));
}

export function restoreImport(items, previous = []) {
  return items.map(saved => {
    const local = previous.find(item => item.path === saved.path && item.sha === saved.sha256 &&
      (item.sizeBytes ?? item.file?.size) === saved.size_bytes && item.mediaType === saved.media_type);
    return {...local, index: saved.index, path: saved.path, sizeBytes: saved.size_bytes,
      mediaType: saved.media_type, sha: saved.sha256, source: "folder:" + saved.path,
      serverState: saved.state, state: saved.job?.stage || saved.state, job: saved.job,
      existingJob: saved.job ? null : local?.existingJob || local?.job,
      canResume: saved.can_resume, reason: saved.code, serverMessage: saved.message,
      statusUnavailable: saved.status_unavailable === true,
      action: local?.action || (saved.job ? "Track" : "Add"), pollError: null};
  });
}

export function hasUnsavedUploads(plan) {
  return plan.some(item => item.file && !item.job && !item.canResume &&
    !["Skip", "Unchanged"].includes(item.action) && item.state !== "invalid");
}

export async function refreshJobs(plan, {read, onChange = () => {}, shouldStop = () => false}) {
  const pending = plan.filter(item => item.job && !FINISHED.has(item.job.stage) && item.state !== "invalid");
  let cursor = 0, failures = 0, accessError = null;
  async function worker() {
    while (cursor < pending.length && !accessError && !shouldStop()) {
      const item = pending[cursor++];
      try {
        const job = await read(item.job.job_id);
        if (shouldStop() || accessError) return;
        item.job = job; item.state = job.stage; item.pollError = null;
      } catch (error) {
        if (shouldStop()) return;
        if (ACCESS_ERRORS.has(error.code)) { accessError = error; return; }
        item.pollError = error.code || "NETWORK_ERROR";
        failures++;
      }
      onChange(item);
    }
  }
  await Promise.all(Array.from({length: Math.min(3, pending.length)}, worker));
  if (accessError) throw accessError;
  return failures;
}

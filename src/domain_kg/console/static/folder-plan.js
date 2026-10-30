// The browser supplies file bytes, never an executable server filesystem path.
import {fileMediaType} from "./file-types.js";

export function sourceFile(file) {
  const path = file.webkitRelativePath || file.name;
  const parts = path.split("/");
  if (parts.some(part => !part || part === ".." || part.includes("\\")) || path.length > 990) {
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

export const STOP_CODES = new Set(["UNAUTHENTICATED", "FORBIDDEN", "POLICY_CHANGED", "RESOURCE_NOT_ACCESSIBLE",
  "QUOTA_EXCEEDED", "BACKEND_UNAVAILABLE", "NETWORK_ERROR"]);
export const FINISHED = new Set(["ready", "ready_with_warnings", "failed", "cancelled"]);

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
          (!FINISHED.has(j.stage) || ["failed", "cancelled"].includes(j.stage)));
        if (job && item.action !== "Unchanged") {
          item.job = job;
          if (job.source.sha256 !== item.sha) throw issue("PENDING_CHANGED", "An earlier job has different file contents. Resolve that job first.");
          item.action = ["failed", "cancelled"].includes(job.stage) ? "Retry" : "Track";
          item.state = item.action === "Retry" ? "waiting" : "queued";
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
      item.state = "queued"; item.reason = null;
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

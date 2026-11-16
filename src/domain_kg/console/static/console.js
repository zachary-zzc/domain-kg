import {fileMediaType} from "./file-types.js";
"use strict";
window.addEventListener("pageshow", function(event) {
  if (event.persisted) window.location.reload();
  else document.documentElement.classList.remove("leaving");
});
window.addEventListener("pagehide", function() {
  document.documentElement.classList.add("leaving");
});
// A denied or changed read must discard previously rendered protected content.
// Reload the current route so the server can resolve the user's current access.
document.addEventListener("htmx:responseError", function(event) {
  const status = event.detail?.xhr?.status;
  const response = event.detail?.xhr?.responseText || "";
  if ([401, 403, 404].includes(status) ||
      (status === 409 && response.includes("POLICY_CHANGED"))) {
    document.documentElement.classList.add("leaving");
    window.location.reload();
    return;
  }
  showRequestFailure(status === 409 || status === 422);
});
function showRequestFailure(conflict = false) {
  const notice = document.querySelector("[data-request-notice]");
  if (!notice) return;
  const message = notice.querySelector("[data-request-message]");
  if (message) message.textContent = conflict ? notice.dataset.conflictMessage : notice.dataset.failureMessage;
  notice.hidden = false;
}
for (const name of ["htmx:sendError", "htmx:timeout"]) {
  document.addEventListener(name, () => showRequestFailure());
}
document.querySelector("[data-request-retry]")?.addEventListener("click", () => window.location.reload());
document.addEventListener("htmx:afterRequest", function(event) {
  if (!event.detail?.successful) return;
  const notice = document.querySelector("[data-request-notice]");
  if (notice) notice.hidden = true;
});

function uploadFailureText(error, form) {
  const copy = JSON.parse(form.dataset.failureCopy || "{}");
  const code = error.code || "NETWORK_ERROR";
  const known = Object.prototype.hasOwnProperty.call(copy, "issue." + code);
  let text = known ? copy["issue." + code] + " · " + code :
    form.dataset.failureMessage || "Upload could not be completed. Refresh your session or retry the file.";
  if (typeof error.request_id === "string" &&
      /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(error.request_id)) {
    text += " · " + (form.dataset.referenceMessage || "Diagnostic reference") + ": " + error.request_id;
  }
  return text;
}

document.addEventListener("submit", async function(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-source-form")) return;
  const input = form.querySelector("[data-source-file]");
  if (!input || !input.files || !input.files.length) return;
  event.preventDefault();
  if (form.dataset.uploading === "true") return;
  const status = form.querySelector("[data-upload-status]");
  const message = text => { if (status) status.textContent = text; };
  const file = input.files[0];
  if (file.size === 0 || file.size > 50 * 1024 * 1024) {
    message(form.dataset.emptyMessage || "Choose a nonempty file up to 50 MiB."); return;
  }
  if (form.elements.namedItem("inline_text").value.length) {
    message(form.dataset.sourceMessage || "Choose one source input before continuing."); return;
  }
  form.dataset.uploading = "true";
  const button = form.querySelector("button[type=submit]");
  if (button) button.disabled = true;
  try {
    message(form.dataset.uploadMessage || "Checking and uploading the original…");
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    const sha = Array.from(new Uint8Array(digest), x => x.toString(16).padStart(2, "0")).join("");
    const type = fileMediaType(file) || file.type || "application/octet-stream";
    const url = new URL(form.dataset.uploadUrl, window.location.origin);
    if (url.origin !== window.location.origin) throw new Error("invalid upload origin");
    url.search = new URLSearchParams({media_type: type, sha256: sha, size_bytes: String(file.size),
      idempotency_key: form.elements.namedItem("idempotency_key").value + "-upload"});
    if (form.dataset.targetMaterial) url.searchParams.set("target_material_id", form.dataset.targetMaterial);
    const response = await fetch(url, {method: "POST", credentials: "same-origin", signal: AbortSignal.timeout(120000),
      headers: {"Content-Type": type, "x-csrf-token": form.elements.namedItem("csrf_token").value}, body: file});
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw Object.assign(new Error("upload failed"), {code: detail.code || "BACKEND_UNAVAILABLE", request_id: detail.request_id});
    }
    const uploaded = await response.json();
    form.elements.namedItem("upload_id").value = uploaded.upload_id;
    message(form.dataset.preservedMessage || "Original preserved. Starting processing…");
    form.submit();
  } catch (error) {
    message(uploadFailureText(error, form));
    form.dataset.uploading = "false";
    if (button) button.disabled = false;
  }
});

// Disable repeat submission only after native constraint validation has succeeded.
document.addEventListener("submit", function(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-busy-form")) return;
  const button = form.querySelector("button[type=submit]");
  if (button) button.disabled = true;
});

const sidebar = document.querySelector(".sidebar");
const navigationToggle = document.querySelector(".mobile-menu");
if (sidebar && navigationToggle) {
  sidebar.dataset.navReady = "true";
  navigationToggle.addEventListener("click", function() {
    const expanded = navigationToggle.getAttribute("aria-expanded") !== "true";
    navigationToggle.setAttribute("aria-expanded", String(expanded));
    sidebar.classList.toggle("nav-open", expanded);
  });
}

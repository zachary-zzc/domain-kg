"use strict";
window.addEventListener("pageshow", function(event) {
  if (event.persisted) window.location.reload();
  else document.documentElement.classList.remove("leaving");
});
window.addEventListener("pagehide", function() {
  document.documentElement.classList.add("leaving");
});
document.addEventListener("htmx:responseError", function() {
  document.documentElement.classList.add("leaving");
  window.location.assign("/admin");
});
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
    const type = file.type || "application/octet-stream";
    const url = new URL(form.dataset.uploadUrl, window.location.origin);
    if (url.origin !== window.location.origin) throw new Error("invalid upload origin");
    url.search = new URLSearchParams({media_type: type, sha256: sha, size_bytes: String(file.size),
      idempotency_key: form.elements.namedItem("idempotency_key").value + "-upload"});
    if (form.dataset.targetMaterial) url.searchParams.set("target_material_id", form.dataset.targetMaterial);
    const response = await fetch(url, {method: "POST", credentials: "same-origin",
      headers: {"Content-Type": type, "x-csrf-token": form.elements.namedItem("csrf_token").value}, body: file});
    if (!response.ok) throw new Error("upload failed");
    const uploaded = await response.json();
    form.elements.namedItem("upload_id").value = uploaded.upload_id;
    message(form.dataset.preservedMessage || "Original preserved. Starting processing…");
    form.submit();
  } catch (_) {
    message(form.dataset.failureMessage || "Upload could not be completed. Refresh your session or retry the file.");
    form.dataset.uploading = "false";
    if (button) button.disabled = false;
  }
});

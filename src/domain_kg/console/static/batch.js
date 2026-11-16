/* Keep selection tied to the visible page and the revision the user reviewed. */
const retryable = new Set(["failed", "cancelled", "repair_required"]);
const finished = new Set(["ready", "ready_with_warnings", "failed", "cancelled"]);

export function canApply(entry, operation) {
  if (entry.disabled) return false;
  if (!operation) return true;
  if (operation === "retry") return entry.stage ? retryable.has(entry.stage) : Boolean(entry.retryable);
  if (operation === "cancel") return !entry.stage || !finished.has(entry.stage);
  return ["delete", "process", "restore"].includes(operation);
}

export function selectionForPage(selected, available, operation) {
  const current = new Map(available.map(entry => [entry.item.id, entry]));
  const next = new Map();
  for (const [id, previous] of selected) {
    const entry = current.get(id);
    if (entry && canApply(entry, operation) &&
        entry.item.revision === previous.item.revision &&
        entry.item.version_id === previous.item.version_id) next.set(id, entry);
  }
  return next;
}

function initializeForms() {
  for (const form of document.querySelectorAll("[data-bulk-form]")) {
    if (controls.has(form)) continue;
    let selected = new Map(), submitting = false, reviewed = null, confirmed = null;
    const serverDisabled = new WeakMap();
    const scope = () => document.querySelector(form.dataset.bulkScope);
    const rows = () => Array.from(scope()?.querySelectorAll("[data-bulk-item]") || []);
    const all = form.querySelector("[data-bulk-all]");
    const button = form.querySelector("[data-bulk-submit]");
    const operation = form.elements.operation;
    const dialog = form.querySelector("[data-bulk-dialog]");
    const confirmButton = form.querySelector("[data-bulk-confirm]");
    const changed = form.querySelector("[data-bulk-review-changed]");
    const snapshot = () => JSON.stringify({operation: operation.value,
      targets: Array.from(selected.values(), entry => ({...entry.item, label: entry.label}))});
    const reviewMessage = () => operation.value === "delete" ? form.dataset.confirmDelete :
      operation.value === "process" ? form.dataset.confirmProcess : "";
    function review(selectionChanged = false) {
      reviewed = snapshot();
      form.querySelector("[data-bulk-review-message]").textContent =
        reviewMessage().replace("{count}", String(selected.size));
      form.querySelector("[data-bulk-review-names]").replaceChildren(
        ...Array.from(selected.values()).slice(0, 10).map(entry => {
          const name = document.createElement("li");
          name.textContent = entry.label;
          return name;
        }));
      changed.hidden = !selectionChanged;
      confirmButton.disabled = button.disabled;
      if (!dialog.open) dialog.showModal();
    }
    const capture = row => {
      if (!serverDisabled.has(row)) serverDisabled.set(row, row.disabled);
      const item = {id: row.dataset.bulkId};
      if (row.dataset.bulkRevision !== undefined) item.revision = Number(row.dataset.bulkRevision);
      if (row.dataset.bulkVersion) item.version_id = row.dataset.bulkVersion;
      return {item, label: row.dataset.bulkLabel || item.id, disabled: serverDisabled.get(row),
        stage: row.dataset.bulkStage, retryable: row.dataset.bulkRetryable === "true"};
    };
    function update() {
      const current = rows();
      selected = selectionForPage(selected, current.map(capture), operation.value);
      for (const row of current) {
        row.disabled = submitting || !canApply(capture(row), operation.value);
        row.checked = selected.has(row.dataset.bulkId);
      }
      const enabled = current.filter(row => !row.disabled);
      all.disabled = submitting || enabled.length === 0;
      all.checked = enabled.length > 0 && enabled.every(row => row.checked);
      all.indeterminate = enabled.some(row => row.checked) && !all.checked;
      form.elements.items.value = JSON.stringify(Array.from(selected.values(), entry => entry.item));
      form.querySelector("[data-bulk-count]").textContent = form.dataset.selectedMessage.replace("{count}", String(selected.size));
      button.disabled = submitting || selected.size === 0 || selected.size > 100 || !operation.value;
      if (dialog.open) {
        confirmButton.disabled = button.disabled;
        changed.hidden = reviewed === snapshot();
      }
    }
    function choose(row, checked) {
      const entry = capture(row);
      if (checked && selected.size < 100 && canApply(entry, operation.value)) selected.set(entry.item.id, entry);
      else if (!checked) selected.delete(entry.item.id);
    }
    operation.addEventListener("change", update);
    all.addEventListener("change", () => {
      for (const row of rows()) if (!row.disabled) choose(row, all.checked);
      update();
    });
    form.querySelector("[data-bulk-clear]").addEventListener("click", () => { selected.clear(); update(); });
    form.querySelector("[data-bulk-retryable]")?.addEventListener("click", () => {
      operation.value = "retry";
      selected.clear();
      for (const row of rows()) if (canApply(capture(row), "retry")) choose(row, true);
      update();
    });
    form.addEventListener("submit", event => {
      update();
      if (button.disabled) { event.preventDefault(); return; }
      if (reviewMessage() && confirmed !== snapshot()) {
        event.preventDefault(); review(); return;
      }
      confirmed = null;
      submitting = true;
      update();
    });
    form.querySelector("[data-bulk-cancel]").addEventListener("click", () => dialog.close());
    confirmButton.addEventListener("click", () => {
      if (!dialog.open) return;
      update();
      if (button.disabled) return;
      if (reviewed !== snapshot()) { review(true); return; }
      const approved = reviewed;
      dialog.close();
      confirmed = approved;
      form.requestSubmit(button);
    });
    controls.set(form, {update, change(row) {
      if (row.matches?.("[data-bulk-item]") && scope()?.contains(row)) { choose(row, row.checked); update(); }
    }});
    update();
  }
}

const controls = new WeakMap();
if (typeof document !== "undefined") {
  initializeForms();
  document.addEventListener("change", event => {
    for (const form of document.querySelectorAll("[data-bulk-form]")) controls.get(form)?.change(event.target);
  });
  const reconcile = () => {
    initializeForms();
    for (const form of document.querySelectorAll("[data-bulk-form]")) controls.get(form).update();
  };
  document.addEventListener("htmx:afterSwap", reconcile);
  document.addEventListener("domain-kg:items-updated", reconcile);
}

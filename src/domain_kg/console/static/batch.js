/* Selection survives HTMX table refreshes; only typed IDs/revisions enter commands. */
(() => {
  for (const form of document.querySelectorAll("[data-bulk-form]")) {
    const selected = new Map();
    const scope = () => document.querySelector(form.dataset.bulkScope);
    const rows = () => Array.from(scope()?.querySelectorAll("[data-bulk-item]:not(:disabled)") || []);
    const all = form.querySelector("[data-bulk-all]");
    const button = form.querySelector("[data-bulk-submit]");
    const capture = row => {
      const item = {id: row.dataset.bulkId};
      if (row.dataset.bulkRevision !== undefined) item.revision = Number(row.dataset.bulkRevision);
      if (row.dataset.bulkVersion) item.version_id = row.dataset.bulkVersion;
      return {item, label: row.dataset.bulkLabel || item.id};
    };
    function update() {
      const current = rows();
      for (const row of current) row.checked = selected.has(row.dataset.bulkId);
      all.checked = current.length > 0 && current.every(row => row.checked);
      all.indeterminate = current.some(row => row.checked) && !all.checked;
      form.elements.items.value = JSON.stringify(Array.from(selected.values(), entry => entry.item));
      form.querySelector("[data-bulk-count]").textContent = form.dataset.selectedMessage.replace("{count}", String(selected.size));
      button.disabled = selected.size === 0 || selected.size > 100;
    }
    function choose(row, checked) {
      if (checked && selected.size < 100) selected.set(row.dataset.bulkId, capture(row));
      else if (!checked) selected.delete(row.dataset.bulkId);
    }
    document.addEventListener("change", event => {
      const row = event.target;
      if (row.matches?.("[data-bulk-item]") && scope()?.contains(row)) { choose(row, row.checked); update(); }
    });
    all.addEventListener("change", () => { for (const row of rows()) choose(row, all.checked); update(); });
    form.querySelector("[data-bulk-clear]").addEventListener("click", () => { selected.clear(); update(); });
    form.querySelector("[data-bulk-retryable]")?.addEventListener("click", () => {
      selected.clear(); for (const row of rows()) if (row.dataset.bulkRetryable === "true") choose(row, true); update();
    });
    document.addEventListener("htmx:afterSwap", update);
    form.addEventListener("submit", event => {
      const operation = form.elements.operation.value;
      const review = operation === "delete" ? form.dataset.confirmDelete : operation === "process" ? form.dataset.confirmProcess : "";
      if (!selected.size || selected.size > 100 || (review && !window.confirm(
        review.replace("{count}", String(selected.size)) + "\n\n" + Array.from(selected.values()).slice(0, 10).map(entry => entry.label).join("\n")
      ))) { event.preventDefault(); return; }
      button.disabled = true;
    });
    update();
  }
})();

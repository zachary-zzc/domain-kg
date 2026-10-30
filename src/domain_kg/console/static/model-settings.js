const root = document.querySelector("[data-model-settings]");
if (root) {
  const copy = JSON.parse(root.dataset.copy), presets = JSON.parse(root.dataset.presets);
  const t = key => copy[key] || key;
  const form = root.querySelector("[data-model-form]");
  let busy = false;
  function lock(value) {
    busy = value;
    root.querySelectorAll("button, input, select").forEach(element => {
      if (value) { element.dataset.previouslyDisabled = String(element.disabled); element.disabled = true; }
      else { element.disabled = element.dataset.previouslyDisabled === "true"; delete element.dataset.previouslyDisabled; }
    });
  }
  async function post(url, targetForm) {
    const fields = new URLSearchParams();
    for (const [key, value] of new FormData(targetForm)) if (typeof value === "string") fields.append(key, value);
    lock(true);
    try {
      const response = await fetch(url, {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        signal: AbortSignal.timeout(180000), body: fields});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = copy[result.message] || copy["error." + result.code] || t("The request could not be completed. Check your session and try again.");
        throw Object.assign(new Error(message), {code: result.code, requestId: result.request_id});
      }
      return result;
    } finally { lock(false); }
  }
  function resultPanel(target, report) {
    target.replaceChildren();
    for (const [key, title] of [["chat", "Chat model"], ["embedding", "Embedding model"]]) {
      const component = report[key];
      if (!component) continue;
      const line = document.createElement("p");
      line.className = "model-check-" + component.state;
      line.textContent = `${component.state === "passed" ? "✓" : component.state === "failed" ? "×" : "–"} ${t(title)}: ${t(component.message)}`;
      if (component.dimensions) line.textContent += ` · ${component.dimensions}D`;
      if (component.milliseconds) line.textContent += ` · ${component.milliseconds} ms`;
      if (component.code) {
        const reference = document.createElement("code"); reference.textContent = component.code;
        line.append(document.createElement("br"), reference);
      }
      target.append(line);
    }
  }
  function failure(status, error) {
    status.textContent = error.message || t("The request could not be completed. Check your session and try again.");
    status.className = "model-check-failed";
  }
  function updateBudget(budget) {
    if (!budget) return;
    for (const [field, limit] of [["calls", "call_limit"], ["input", "input_limit"], ["output", "output_limit"]]) {
      root.querySelector(`[data-budget="${field}"]`).textContent = `${budget[field]} / ${budget[limit] ?? t("Unlimited")}`;
    }
    const progress = root.querySelector("[data-budget-progress]");
    progress.max = budget.call_limit || 1;
    progress.value = budget.calls;
    progress.hidden = budget.call_limit === null;
    root.querySelector("[data-budget-exhausted]").hidden = !budget.exhausted;
  }
  function validateInputs(discovery = false) {
    for (const element of form.querySelectorAll("input[required], select[required]")) {
      if (discovery && ["extraction_model", "embedding_model"].includes(element.name)) continue;
      if (element.name !== "data_approval" && !element.reportValidity()) return false;
    }
    return true;
  }
  if (form) {
    const source = form.querySelector("[data-model-source]");
    const endpoint = form.elements.endpoint, key = form.elements.api_key;
    const originalEndpoint = endpoint.value, originalFormat = form.elements.api_format.value;
    const originalName = form.elements.name.value;
    form.elements.thinking.disabled = originalFormat === "anthropic";
    let autoName = originalName;
    const embeddingSource = form.querySelector("[data-embedding-source]");
    const connectionFields = form.querySelector("[data-embedding-connection]");
    const status = form.querySelector("[data-model-status]");
    const draftResult = form.querySelector("[data-draft-check]");
    function keyHint() {
      const reuse = key.dataset.savedKey === "true" && endpoint.value === originalEndpoint && form.elements.api_format.value === originalFormat;
      form.querySelector("[data-key-note]").textContent = t(reuse ?
        "Saved key is available. Leave blank to keep it for this endpoint." : "Enter a key for this API. Local servers may leave it blank.");
    }
    function embeddingMode(fill) {
      const choice = embeddingSource.value;
      const reuse = choice.startsWith("reuse:"), same = choice === "same";
      form.elements.embedding_profile_id.value = reuse ? choice.slice("reuse:".length) : "";
      form.querySelector("#embedding-model-list").replaceChildren();
      connectionFields.hidden = reuse || same;
      connectionFields.querySelectorAll("input").forEach(input => { input.disabled = reuse || same; });
      form.elements.embedding_model.readOnly = reuse;
      form.elements.embedding_send_dimensions.disabled = reuse;
      form.elements.embedding_context_tokens.disabled = reuse;
      if (reuse) {
        form.elements.embedding_model.value = embeddingSource.selectedOptions[0].dataset.model;
      } else if (fill && !same) {
        const preset = presets.find(item => item.id === choice);
        if (preset) {
          form.elements.embedding_endpoint.value = preset.endpoint;
          form.elements.embedding_model.value = preset.embedding_model;
          form.elements.embedding_send_dimensions.checked = preset.send_dimensions;
          form.elements.embedding_api_key.value = "";
        }
      }
      if (same) {
        form.elements.embedding_endpoint.value = "";
        form.elements.embedding_api_key.value = "";
        if (fill) {
          const preset = presets.find(item => item.id === source.value);
          if (preset?.embedding_model) form.elements.embedding_model.value = preset.embedding_model;
          form.elements.embedding_send_dimensions.checked = Boolean(preset?.send_dimensions);
        }
      }
      status.textContent = ""; draftResult.replaceChildren();
    }
    source.addEventListener("change", () => {
      const preset = presets.find(item => item.id === source.value);
      if (!preset) return;
      form.querySelector("#chat-model-list").replaceChildren();
      endpoint.value = preset.endpoint; form.elements.extraction_model.value = preset.model;
      form.elements.api_format.value = preset.api_format;
      form.elements.response_format.value = preset.response_format;
      form.elements.supports_images.checked = Boolean(preset.supports_images);
      form.elements.thinking.value = preset.thinking;
      form.elements.thinking.disabled = preset.api_format === "anthropic";
      if (form.elements.name.value === autoName || form.elements.name.value === originalName) {
        form.elements.name.value = preset.label; autoName = preset.label;
      }
      key.value = "";
      if (preset.api_format === "anthropic" && embeddingSource.value === "same") {
        const existing = [...embeddingSource.options].find(option => option.value.startsWith("reuse:"));
        embeddingSource.value = existing?.value || "alibaba";
        embeddingMode(true);
      } else if (embeddingSource.value === "same") embeddingMode(true);
      keyHint(); status.textContent = ""; draftResult.replaceChildren();
    });
    endpoint.addEventListener("input", keyHint);
    embeddingSource.addEventListener("change", () => embeddingMode(true));
    embeddingMode(false);
    async function check(save) {
      if (busy || !(save ? form.reportValidity() : validateInputs())) return;
      status.className = ""; status.textContent = t("Testing chat and embedding connections…");
      draftResult.replaceChildren();
      try {
        const report = await post(form.action.replace("/test-and-save", save ? "/test-and-save" : "/test"), form);
        updateBudget(report.budget);
        resultPanel(draftResult, report);
        status.textContent = t(report.ready ? "Connection test passed." : "Connection test failed. Nothing was saved.");
        if (report.next_url) {
          status.textContent = t("Configuration saved.");
          window.location.assign(report.next_url);
        }
      } catch (error) { failure(status, error); }
    }
    form.addEventListener("submit", event => { event.preventDefault(); check(true); });
    form.querySelector("[data-test-draft]").addEventListener("click", () => check(false));
    form.querySelectorAll("[data-discover]").forEach(button => button.addEventListener("click", async () => {
      if (busy || !validateInputs(true)) return;
      status.className = ""; status.textContent = t("Reading available models…");
      const component = button.dataset.discover;
      try {
        const result = await post(form.action.replace("/test-and-save", `/${component}/discover`), form);
        const list = form.querySelector(component === "chat" ? "#chat-model-list" : "#embedding-model-list");
        list.replaceChildren();
        for (const name of result.models) {
          const option = document.createElement("option"); option.value = name; list.append(option);
        }
        status.textContent = t(result.models.length ? "Model list loaded. Choose a model or type its name." :
          "No models were returned. Load a model locally or enter its exact name.");
      } catch (error) { failure(status, error); }
    }));
  }
  const current = root.querySelector("[data-check-current]");
  if (current) current.addEventListener("submit", async event => {
    event.preventDefault(); if (busy) return;
    const status = current.querySelector("[data-model-status]");
    status.className = ""; status.textContent = t("Testing chat and embedding connections…");
    try {
      const report = await post(current.action, current);
      updateBudget(report.budget);
      resultPanel(root.querySelector("[data-current-check]"), report);
      status.textContent = t(report.ready ? "Connection test passed." : "Connection test failed. Nothing was saved.");
    } catch (error) { failure(status, error); }
  });
}

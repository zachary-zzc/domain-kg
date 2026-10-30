import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import {runInNewContext} from "node:vm";

const script = readFileSync(new URL("../../src/domain_kg/console/static/model-settings.js", import.meta.url), "utf8");
const profile = "00000000-0000-0000-0000-000000000001";
function control(value = "") {
  return {value, dataset: {}, events: {}, disabled: false, addEventListener(name, callback) {
    this.events[name] = callback;
  }, children: ["previous-provider-model"], replaceChildren() {this.children = [];}};
}
function settings() {
  const elements = Object.fromEntries(["endpoint", "api_key", "api_format", "name", "embedding_profile_id",
    "embedding_model", "embedding_send_dimensions", "embedding_endpoint", "embedding_api_key",
    "extraction_model", "supports_images", "response_format", "thinking", "embedding_context_tokens"].map(name => [name, control()]));
  elements.endpoint.value = "https://dashscope.aliyuncs.com/compatible-mode/v1";
  elements.api_format.value = "openai";
  elements.api_key.dataset.savedKey = "true";
  const reuse = {value: "reuse:" + profile, dataset: {model: "text-embedding-v4"}};
  const controls = {"[data-model-source]": control("alibaba"), "[data-embedding-source]": {
    ...control(reuse.value), options: [reuse], selectedOptions: [reuse]},
    "[data-model-status]": control(), "[data-draft-check]": control(), "[data-key-note]": control(),
    "#chat-model-list": control(), "#embedding-model-list": control(),
    "[data-test-draft]": control(), "[data-embedding-connection]": {querySelectorAll: () =>
      [elements.embedding_endpoint, elements.embedding_api_key]}};
  const form = {...control(), elements, querySelector: selector => controls[selector], querySelectorAll: () => []};
  const root = {dataset: {copy: "{}", presets: JSON.stringify([
    {id: "ollama", label: "Ollama", endpoint: "http://localhost:11434/v1", model: "qwen3:8b",
      api_format: "openai", response_format: "json_object", thinking: "default"},
    {id: "anthropic", label: "Claude", endpoint: "https://api.anthropic.com/v1", model: "claude-test",
      api_format: "anthropic", response_format: "json_schema", thinking: "default", supports_images: true}])},
    querySelector: selector => selector === "[data-model-form]" ? form : null};
  runInNewContext(script, {document: {querySelector: () => root}});
  return {elements, controls};
}

test("reusing saved embeddings submits the exact profile UUID and disables unused credentials", () => {
  const {elements} = settings();
  assert.equal(elements.embedding_profile_id.value, profile);
  assert.equal(elements.embedding_model.value, "text-embedding-v4");
  assert.equal(elements.embedding_endpoint.disabled, true);
  assert.equal(elements.embedding_api_key.disabled, true);
  assert.equal(elements.embedding_model.readOnly, true);
  assert.equal(elements.embedding_context_tokens.disabled, true);
});

test("local and Claude presets retain saved embeddings while requiring their own chat connection", () => {
  const {elements, controls} = settings();
  for (const source of ["ollama", "anthropic"]) {
    controls["[data-model-source]"].value = source;
    controls["[data-model-source]"].events.change();
    assert.equal(elements.api_key.value, "");
    assert.equal(elements.embedding_profile_id.value, profile);
    assert.equal(elements.embedding_model.value, "text-embedding-v4");
    assert.deepEqual(controls["#chat-model-list"].children, []);
  }
  assert.equal(elements.api_format.value, "anthropic");
  assert.equal(elements.thinking.disabled, true);
  assert.equal(elements.endpoint.value, "https://api.anthropic.com/v1");
});

test("image capability follows the selected preset rather than the embedding model", () => {
  const {elements, controls} = settings();
  controls["[data-model-source]"].value = "anthropic";
  controls["[data-model-source]"].events.change();
  assert.equal(elements.supports_images.checked, true);
  controls["[data-model-source]"].value = "ollama";
  controls["[data-model-source]"].events.change();
  assert.equal(Boolean(elements.supports_images.checked), false);
});

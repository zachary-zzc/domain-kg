import assert from "node:assert/strict";
import {test} from "node:test";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import {canApply, selectionForPage} from "../../src/domain_kg/console/static/batch.js";

const entry = (id, overrides = {}) => ({item: {id}, label: id, disabled: false, ...overrides});

test("refresh drops missing, disabled, or changed sources instead of submitting stale selection", () => {
  const first = entry("one", {item: {id: "one", revision: 1, version_id: "v1"}});
  const changed = entry("two", {item: {id: "two", revision: 1, version_id: "v2"}});
  const removed = entry("three");
  const disabled = entry("four");
  const selection = new Map([first, changed, removed, disabled].map(value => [value.item.id, value]));
  const result = selectionForPage(selection, [
    first,
    entry("two", {item: {id: "two", revision: 2, version_id: "v3"}}),
    entry("four", {disabled: true}),
  ], "delete");
  assert.deepEqual(Array.from(result.keys()), ["one"]);
  assert.deepEqual(result.get("one").item, {id: "one", revision: 1, version_id: "v1"});
});

test("active and retryable job selections follow their refreshed lifecycle", () => {
  const failed = entry("failed", {stage: "failed"});
  const cancelled = entry("cancelled", {stage: "cancelled"});
  const repair = entry("repair", {stage: "repair_required"});
  const queued = entry("queued", {stage: "queued"});
  const ready = entry("ready", {stage: "ready"});
  const warning = entry("warning", {stage: "ready_with_warnings"});
  const all = [failed, cancelled, repair, queued, ready, warning];
  const selected = new Map(all.map(value => [value.item.id, value]));
  assert.deepEqual(Array.from(selectionForPage(selected, all, "retry").keys()), ["failed", "cancelled", "repair"]);
  assert.deepEqual(Array.from(selectionForPage(selected, all, "cancel").keys()), ["repair", "queued"]);
  assert.equal(canApply(entry("started", {stage: "extracting"}), "cancel"), true);
  assert.equal(canApply(entry("missing", {disabled: true}), "restore"), false);
  assert.equal(selectionForPage(new Map([["failed", failed]]), [entry("failed", {stage: "queued"})], "retry").size, 0);
});

test("selection stays limited to visible rows and server revision changes require reselection", () => {
  const first = entry("one", {item: {id: "one", revision: 7}});
  const selected = new Map([["one", first]]);
  const same = entry("one", {item: {id: "one", revision: 7}, label: "Current visible title"});
  assert.equal(selectionForPage(selected, [same], "process").get("one").label, "Current visible title");
  assert.equal(selectionForPage(selected, [], "process").size, 0);
  assert.equal(selectionForPage(selected, [entry("one", {item: {id: "one", revision: 8}})], "process").size, 0);
  assert.equal(canApply(first, "arbitrary"), false);
});

const batchScript = readFileSync(new URL("../../src/domain_kg/console/static/batch.js", import.meta.url), "utf8")
  .replace(/^export /gm, "");

function batchView() {
  function element(values = {}) {
    const listeners = new Map();
    return {disabled: false, ...values,
      addEventListener(name, handler) {listeners.set(name, [...(listeners.get(name) || []), handler]);},
      emit(name, event = {}) {
        event.preventDefault ||= () => {event.defaultPrevented = true;};
        for (const handler of listeners.get(name) || []) handler(event);
        return event;
      }};
  }
  let rows = [element({dataset: {bulkId: "one", bulkRevision: "7", bulkVersion: "v1",
    bulkLabel: '<img src=x onerror="bad()"> & source.pdf'}, checked: false,
  matches: selector => selector === "[data-bulk-item]"})];
  const scope = {querySelectorAll: () => rows, contains: row => rows.includes(row)};
  const dialog = element({open: false, openings: 0,
    showModal() {this.open = true; this.openings++;}, close() {this.open = false; this.emit("close");}});
  const message = element({textContent: ""});
  const changes = element({hidden: true});
  const names = element({children: [], replaceChildren(...children) {this.children = children;}});
  const confirm = element(), cancel = element(), all = element(), submit = element();
  const operation = element({value: "delete"});
  const controls = new Map([
    ["[data-bulk-all]", all], ["[data-bulk-submit]", submit],
    ["[data-bulk-count]", element()], ["[data-bulk-clear]", element()],
    ["[data-bulk-retryable]", element()], ["[data-bulk-dialog]", dialog],
    ["[data-bulk-confirm]", confirm], ["[data-bulk-cancel]", cancel],
    ["[data-bulk-review-message]", message], ["[data-bulk-review-names]", names],
    ["[data-bulk-review-changed]", changes],
  ]);
  const form = element({dataset: {bulkScope: "#sources", selectedMessage: "Selected: {count}",
    confirmDelete: "Delete {count} selected sources?", confirmProcess: "Process {count}; provider charges apply."},
  elements: {operation, items: {value: ""}}, querySelector: selector => controls.get(selector),
  committed: 0, requestSubmit() {
    if (!this.emit("submit").defaultPrevented) this.committed++;
  }});
  const document = element({querySelectorAll: () => [form], querySelector: () => scope,
    createElement() {
      return {textContent: "", set innerHTML(_) {throw new Error("Source labels must be text");}};
    }});
  runInNewContext(batchScript, {document, window: {confirm() {
    throw new Error("Native JavaScript dialogs block the batch workflow");
  }}});
  const selectAll = () => {all.checked = true; all.emit("change");};
  return {form, dialog, confirm, cancel, message, changes, names, operation, submit, selectAll,
    refresh(next) {rows = next; document.emit("htmx:afterSwap");}, rows: () => rows};
}

test("delete and paid processing review escaped names in a modal and submit exactly once", () => {
  for (const operation of ["delete", "process"]) {
    const view = batchView();
    view.operation.value = operation;
    view.selectAll();
    view.form.requestSubmit();
    assert.equal(view.form.committed, 0);
    assert.equal(view.dialog.open, true);
    assert.equal(view.names.children[0].textContent, '<img src=x onerror="bad()"> & source.pdf');
    assert.match(view.message.textContent, operation === "delete" ? /Delete 1/ : /provider charges/);
    view.confirm.emit("click");
    assert.equal(view.dialog.open, false);
    assert.equal(view.form.committed, 1);
    assert.deepEqual(JSON.parse(view.form.elements.items.value), [{id: "one", revision: 7, version_id: "v1"}]);
    view.confirm.emit("click");
    view.form.requestSubmit();
    assert.equal(view.form.committed, 1);
  }
});

test("Cancel and Escape close the batch review without submitting", () => {
  for (const action of ["cancel", "escape"]) {
    const view = batchView();
    view.selectAll();
    view.form.requestSubmit();
    if (action === "cancel") view.cancel.emit("click");
    else view.dialog.close(); // Native dialog Escape closes without a form submission.
    assert.equal(view.dialog.open, false);
    assert.equal(view.form.committed, 0);
    view.form.requestSubmit();
    assert.equal(view.dialog.open, true);
    assert.equal(view.form.committed, 0);
  }
});

test("an HTMX title change requires reviewing the refreshed selection before confirmation", () => {
  const view = batchView();
  view.selectAll();
  view.form.requestSubmit();
  const row = view.rows()[0];
  view.refresh([{...row, dataset: {...row.dataset, bulkLabel: "Updated visible source.pdf"}}]);
  view.confirm.emit("click");
  assert.equal(view.form.committed, 0);
  assert.equal(view.dialog.open, true);
  assert.equal(view.changes.hidden, false);
  assert.equal(view.names.children[0].textContent, "Updated visible source.pdf");
  view.confirm.emit("click");
  assert.equal(view.form.committed, 1);
});

test("confirmation is bound to the chosen operation and reviews at most ten names", () => {
  const view = batchView();
  const row = view.rows()[0];
  view.refresh(Array.from({length: 12}, (_, index) => ({...row,
    dataset: {...row.dataset, bulkId: String(index), bulkLabel: `Source ${index}`}})));
  view.selectAll();
  view.form.requestSubmit();
  assert.equal(view.names.children.length, 10);
  assert.match(view.message.textContent, /Delete 12/);
  view.operation.value = "process";
  view.operation.emit("change");
  view.confirm.emit("click");
  assert.equal(view.form.committed, 0);
  assert.equal(view.changes.hidden, false);
  assert.match(view.message.textContent, /provider charges/);
  view.confirm.emit("click");
  assert.equal(view.form.committed, 1);
  assert.equal(JSON.parse(view.form.elements.items.value).length, 12);
});

test("a changed revision or removed row cannot be submitted from an older dialog", () => {
  for (const change of ["revision", "removed"]) {
    const view = batchView();
    view.selectAll();
    view.form.requestSubmit();
    const row = view.rows()[0];
    view.refresh(change === "removed" ? [] : [{...row, dataset: {...row.dataset, bulkRevision: "8"}}]);
    view.confirm.emit("click");
    assert.equal(view.form.committed, 0);
    assert.equal(view.confirm.disabled, true);
    assert.equal(view.changes.hidden, false);
  }
});

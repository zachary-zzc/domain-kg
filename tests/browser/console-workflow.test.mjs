import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {test} from "node:test";
import {runInNewContext} from "node:vm";

const script = readFileSync(new URL("../../src/domain_kg/console/static/console.js", import.meta.url), "utf8")
  .replace(/^import .*;\n/, "");

function consoleView() {
  const events = new Map();
  const classes = new Set();
  const message = {textContent: ""};
  const retry = {addEventListener(name, handler) {this[name] = handler;}};
  const notice = {hidden: true, dataset: {failureMessage: "Temporary connection failure",
    conflictMessage: "Refresh before retrying"}, querySelector: () => message};
  const document = {documentElement: {classList: {add: value => classes.add(value),
    remove: value => classes.delete(value)}},
  addEventListener(name, handler) {events.set(name, [...(events.get(name) || []), handler]);},
  querySelector(selector) {return selector === "[data-request-notice]" ? notice :
    selector === "[data-request-retry]" ? retry : null;}};
  let reloads = 0;
  const window = {addEventListener() {}, location: {reload() {reloads++;},
    assign() {throw new Error("The current route must be preserved");}}};
  const context = {document, window};
  runInNewContext(script, context);
  const trigger = (name, detail = {}) => events.get(name)?.forEach(handler => handler({detail}));
  return {notice, message, classes, retry, trigger, reloads: () => reloads,
    uploadFailureText: context.uploadFailureText};
}

test("temporary backend and network failures preserve the current view and provide recovery", () => {
  for (const event of ["htmx:responseError", "htmx:sendError", "htmx:timeout"]) {
    const view = consoleView();
    view.trigger(event, {xhr: {status: 503}});
    assert.equal(view.reloads(), 0);
    assert.equal(view.classes.has("leaving"), false);
    assert.equal(view.notice.hidden, false);
    assert.equal(view.message.textContent, "Temporary connection failure");
    view.retry.click();
    assert.equal(view.reloads(), 1);
  }
});

test("revoked or inaccessible reads discard stale protected content and resolve the same route", () => {
  for (const status of [401, 403, 404]) {
    const view = consoleView();
    view.trigger("htmx:responseError", {xhr: {status}});
    assert.equal(view.classes.has("leaving"), true);
    assert.equal(view.reloads(), 1);
  }
  const view = consoleView();
  view.trigger("htmx:responseError", {xhr: {status: 409, responseText: "POLICY_CHANGED"}});
  assert.equal(view.classes.has("leaving"), true);
  assert.equal(view.reloads(), 1);
});

test("ordinary revision conflicts do not navigate away or discard input", () => {
  const view = consoleView();
  view.trigger("htmx:responseError", {xhr: {status: 409, responseText: "VERSION_CONFLICT"}});
  assert.equal(view.classes.has("leaving"), false);
  assert.equal(view.reloads(), 0);
  assert.equal(view.message.textContent, "Refresh before retrying");
});

test("a successful refresh clears the temporary failure banner", () => {
  const view = consoleView();
  view.trigger("htmx:sendError");
  view.trigger("htmx:afterRequest", {successful: false});
  assert.equal(view.notice.hidden, false);
  view.trigger("htmx:afterRequest", {successful: true});
  assert.equal(view.notice.hidden, true);
});

test("upload failures use configured safe explanations and diagnostic references", () => {
  const {uploadFailureText} = consoleView();
  const reference = "88e98cf4-2659-447d-96a5-b84321a64737";
  const form = {dataset: {failureCopy: JSON.stringify({
    "issue.QUOTA_EXCEEDED": "Storage allowance exceeded. Review storage capacity.",
    "issue.NETWORK_ERROR": "Connection interrupted. Retry this file.",
  }), referenceMessage: "Diagnostic reference", failureMessage: "Unable to upload."}};
  const text = uploadFailureText({code: "QUOTA_EXCEEDED", request_id: reference,
    message: "private provider body"}, form);
  assert.match(text, /Storage allowance exceeded/);
  assert.match(text, /QUOTA_EXCEEDED/);
  assert.match(text, /88e98cf4-2659-447d-96a5-b84321a64737/);
  assert.doesNotMatch(text, /private/);
  assert.match(uploadFailureText(new TypeError("private network details"), form), /Connection interrupted/);
  const unknown = uploadFailureText({code: "UNTRUSTED_CODE", message: "private source",
    request_id: "private-secret"}, form);
  assert.equal(unknown, "Unable to upload.");
});

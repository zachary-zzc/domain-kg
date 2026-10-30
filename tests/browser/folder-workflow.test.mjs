import assert from "node:assert/strict";
import {test} from "node:test";
import * as workflow from "../../src/domain_kg/console/static/folder-plan.js";

const file = (name, size = 3) => ({name, size, webkitRelativePath: `Downloads/${name}`,
  arrayBuffer: async () => new TextEncoder().encode("abc").buffer});
const digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
const options = {detail: async () => {throw new Error("Unexpected detail read");}};

test("review keeps good files when empty, unsupported, oversized and unreadable files coexist", async () => {
  const unreadable = {...file("unreadable.txt"), arrayBuffer: async () => {throw new Error("Read denied");}};
  const plan = await workflow.reviewFolder([
    file("empty.txt", 0), file("unknown.unsupported"), file("large.pdf", 51 * 1024 * 1024),
    unreadable, file("good.txt"),
  ], [], [], options);
  assert.deepEqual(plan.map(item => item.state), ["skipped", "skipped", "invalid", "invalid", "waiting"]);
  assert.equal(plan[4].sha, digest);
  assert.equal(plan[4].source, "folder:Downloads/good.txt");
});

test("a failed source can be retried under the same job while unrelated pending jobs do not block review", async () => {
  const job = {job_id: "job-one", kind: "ingest", stage: "failed", source: {
    source: "folder:Downloads/old.txt", sha256: digest, material_id: "material-one"}};
  const plan = await workflow.reviewFolder([file("old.txt"), file("new.txt")], [], [job], options);
  assert.equal(plan[0].action, "Retry");
  assert.equal(plan[0].job.job_id, "job-one");
  assert.equal(plan[1].action, "Add");
});

test("ambiguous accepted identity and a changed pending source cannot create duplicate materials", async () => {
  const material = {id: "one", metadata: {source: "folder:Downloads/a.txt"}};
  const job = {job_id: "pending", kind: "ingest", stage: "parsing", source: {
    source: "folder:Downloads/b.txt", sha256: "0".repeat(64), material_id: "two"}};
  const plan = await workflow.reviewFolder([file("a.txt"), file("b.txt"), file("c.txt")],
    [material, {...material, id: "three"}], [job], options);
  assert.deepEqual(plan.map(item => item.state), ["invalid", "invalid", "waiting"]);
});

test("one submission failure does not block later uploads or pretend they are published", async () => {
  const plan = [{path: "bad.txt", state: "waiting"}, {path: "good.txt", state: "waiting"}];
  await workflow.submitFolder(plan, {submit: async item => {
    if (item.path === "bad.txt") throw Object.assign(new Error("Unsupported"), {code: "UNSUPPORTED_FORMAT"});
    return {job_id: "good-job", stage: "queued"};
  }});
  assert.deepEqual(plan.map(item => item.state), ["failed", "queued"]);
  assert.equal(plan[1].job.job_id, "good-job");
});

test("explicit stop leaves unsubmitted files waiting and accepted jobs intact", async () => {
  const plan = [{path: "a.txt", state: "waiting"}, {path: "b.txt", state: "waiting"}];
  let stopped = false;
  await workflow.submitFolder(plan, {shouldStop: () => stopped, submit: async () => {
    stopped = true;
    return {job_id: "a-job", stage: "queued"};
  }});
  assert.deepEqual(plan.map(item => item.state), ["queued", "waiting"]);
});

test("loss of session or quota pauses further uploads with files still resumable", async () => {
  for (const code of ["UNAUTHENTICATED", "FORBIDDEN", "POLICY_CHANGED", "RESOURCE_NOT_ACCESSIBLE", "QUOTA_EXCEEDED"]) {
    const plan = [{path: "a.txt", state: "waiting"}, {path: "b.txt", state: "waiting"}];
    await workflow.submitFolder(plan, {submit: async () => {
      throw Object.assign(new Error("Stopped"), {code});
    }});
    assert.deepEqual(plan.map(item => item.state), ["failed", "waiting"]);
  }
});

test("monitoring an accepted file never submits it again", async () => {
  const plan = [{path: "accepted.txt", state: "queued", job: {job_id: "accepted"}},
    {path: "waiting.txt", state: "waiting"}];
  await workflow.submitFolder(plan, {submit: async item => {
    assert.equal(item.path, "waiting.txt");
    return {job_id: "new-job", stage: "queued"};
  }});
  assert.equal(plan[0].job.job_id, "accepted");
  assert.equal(plan[1].state, "queued");
});

test("a failure against an older version does not block replacing the current published version", async () => {
  const material = {id: "one", active_version_id: "current", metadata: {source: "folder:Downloads/a.txt"}};
  const oldJob = {job_id: "obsolete", kind: "replace", stage: "failed", source: {
    source: "folder:Downloads/a.txt", sha256: "0".repeat(64), material_id: "one",
    expected_active_version: "older"}};
  const plan = await workflow.reviewFolder([file("a.txt")], [material], [oldJob], {
    detail: async () => ({versions: [{id: "current", sha256: "1".repeat(64)}]})});
  assert.equal(plan[0].state, "waiting");
  assert.equal(plan[0].action, "Replace");
  assert.equal(plan[0].job, undefined);
});

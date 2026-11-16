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

test("review tracks an existing failed job before a user explicitly retries it", async () => {
  const job = {job_id: "job-one", kind: "ingest", stage: "failed", source: {
    source: "folder:Downloads/old.txt", sha256: digest, material_id: "material-one"}};
  const plan = await workflow.reviewFolder([file("old.txt"), file("new.txt")], [], [job], options);
  assert.equal(plan[0].action, "Track");
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

test("replayed submissions retain ready and warning receipts instead of returning to queued", async () => {
  for (const stage of ["ready", "ready_with_warnings", "failed", "repair_required"]) {
    const plan = [{path: "accepted.txt", state: "waiting"}];
    await workflow.submitFolder(plan, {submit: async () => ({job_id: "saved-job", stage, progress: 1})});
    assert.equal(plan[0].state, stage);
    assert.equal(plan[0].job.stage, stage);
    assert.equal(plan[0].job.progress, 1);
  }
  assert.equal(workflow.FINISHED.has("repair_required"), true);
});

test("one upload connection error leaves later files able to submit", async () => {
  const plan = [{path: "bad.txt", state: "waiting"}, {path: "good.txt", state: "waiting"}];
  await workflow.submitFolder(plan, {submit: async item => {
    if (item.path === "bad.txt") throw Object.assign(new Error("Interrupted"), {code: "NETWORK_ERROR"});
    return {job_id: "good-job", stage: "extracting", progress: 0.4};
  }});
  assert.deepEqual(plan.map(item => item.state), ["failed", "extracting"]);
});

test("a transient material-detail error invalidates only its own review row", async () => {
  const material = {id: "one", active_version_id: "current", metadata: {source: "folder:Downloads/a.txt"}};
  const plan = await workflow.reviewFolder([file("a.txt"), file("b.txt")], [material], [], {
    detail: async () => {throw Object.assign(new Error("Unavailable"), {code: "BACKEND_UNAVAILABLE"});},
  });
  assert.deepEqual(plan.map(item => item.state), ["invalid", "waiting"]);
});

test("job refresh isolates transient failures and limits simultaneous reads to three", async () => {
  const plan = Array.from({length: 7}, (_, index) => ({path: `${index}.txt`, state: "extracting",
    job: {job_id: String(index), stage: "extracting", progress: 0.4}}));
  let active = 0, maximum = 0;
  const called = [];
  const failures = await workflow.refreshJobs(plan, {read: async id => {
    active++; maximum = Math.max(maximum, active); called.push(id);
    await new Promise(resolve => setTimeout(resolve, 2));
    active--;
    if (id === "1") throw Object.assign(new Error("Unavailable"), {code: "NETWORK_ERROR"});
    return {job_id: id, stage: "ready", progress: 1};
  }});
  assert.equal(maximum, 3);
  assert.equal(called.length, 7);
  assert.equal(failures, 1);
  assert.equal(plan[1].job.stage, "extracting");
  assert.equal(plan[1].pollError, "NETWORK_ERROR");
  assert.equal(plan[6].job.stage, "ready");
  await workflow.refreshJobs(plan, {read: async id => ({job_id: id, stage: "ready_with_warnings", progress: 1})});
  assert.equal(plan[1].pollError, null);
  assert.equal(plan[1].job.stage, "ready_with_warnings");
});

test("job refresh rejects lost access while preserving cancellation generations", async () => {
  const plan = [{job: {job_id: "one", stage: "extracting"}, state: "extracting"}];
  await assert.rejects(workflow.refreshJobs(plan, {read: async () => {
    throw Object.assign(new Error("Denied"), {code: "FORBIDDEN"});
  }}), error => error.code === "FORBIDDEN");
  let cancelled = false;
  await workflow.refreshJobs(plan, {shouldStop: () => cancelled, read: async id => {
    cancelled = true;
    return {job_id: id, stage: "ready"};
  }});
  assert.equal(plan[0].job.stage, "extracting");
});

test("durable manifests exclude skipped rows and reject more than one hundred eligible files", () => {
  const plan = [
    {path: "skip.txt", action: "Unchanged", state: "skipped"},
    {path: "unsupported.bin", action: "Skip", state: "skipped"},
    {path: "bad.txt", action: "Add", state: "invalid"},
    {path: "good.txt", action: "Add", state: "waiting", sha: digest, file: {size: 3}, mediaType: "text/plain"},
  ];
  assert.deepEqual(workflow.importManifest(plan), [{path: "good.txt", sha256: digest, size_bytes: 3, media_type: "text/plain"}]);
  assert.throws(() => workflow.importManifest(Array.from({length: 101}, (_, index) => ({
    ...plan[3], path: `${index}.txt`,
  }))), error => error.code === "IMPORT_TOO_MANY");
});

test("a restored import attaches only the same original and preserves server receipts", () => {
  const original = file("a.txt");
  const saved = [{index: 0, path: "Downloads/a.txt", sha256: digest, size_bytes: 3,
    media_type: "text/plain", state: "submitted", job: {job_id: "one", stage: "ready_with_warnings", progress: 1},
    can_resume: false, code: null, message: ""}];
  const restored = workflow.restoreImport(saved, [{path: "Downloads/a.txt", sha: digest, file: original, mediaType: "text/plain"}]);
  assert.equal(restored[0].file, original);
  assert.equal(restored[0].state, "ready_with_warnings");
  assert.equal(restored[0].index, 0);
  assert.equal(workflow.hasUnsavedUploads(restored), false);
  assert.equal(workflow.restoreImport(saved, [{path: "Downloads/a.txt", sha: "0".repeat(64), file: original, mediaType: "text/plain"}])[0].file, undefined);
  assert.equal(workflow.hasUnsavedUploads([{file: original, state: "waiting", action: "Add"}]), true);
  assert.equal(workflow.hasUnsavedUploads([{file: original, state: "failed", action: "Add", canResume: true}]), false);
});

test("an admitted job with unavailable status never becomes a request to re-upload bytes", () => {
  const [item] = workflow.restoreImport([{index: 0, path: "source.pdf", sha256: digest,
    size_bytes: 10, media_type: "application/pdf", state: "failed", job: null,
    code: "BACKEND_UNAVAILABLE", status_unavailable: true, can_resume: false}]);
  assert.equal(item.statusUnavailable, true);
  assert.equal(item.canResume, false);
  assert.equal(workflow.hasUnsavedUploads([item]), false);
});

test("directory review hides skipped noise by default but keeps invalid files visible", () => {
  const plan = [
    {path: "Folder/report.pdf", action: "Add", state: "waiting"},
    {path: "Folder/large.pdf", action: "Skip", state: "invalid", reason: "FILE_TOO_LARGE"},
    {path: "Folder/.DS_Store", action: "Skip", state: "skipped", reason: "HIDDEN_FILE"},
    {path: "Folder/old.txt", action: "Unchanged", state: "skipped"},
  ];
  const view = workflow.importPage(plan);
  assert.deepEqual(view.items.map(item => item.path), ["Folder/report.pdf", "Folder/large.pdf"]);
  assert.deepEqual(view.counts, {total: 4, included: 2, skipped: 2, issues: 1});
  assert.deepEqual(workflow.importPage(plan, {view: "skipped"}).items.map(item => item.path),
    ["Folder/.DS_Store", "Folder/old.txt"]);
  assert.equal(workflow.importPage(plan, {view: "issues"}).items[0].reason, "FILE_TOO_LARGE");
});

test("import pages bound rendered rows without changing the complete upload manifest", () => {
  const plan = Array.from({length: 46}, (_, index) => ({path: `Folder/${index}.txt`,
    action: "Add", state: "waiting", sha: digest, file: {size: 3}, mediaType: "text/plain"}));
  const middle = workflow.importPage(plan, {page: 2});
  assert.equal(middle.items.length, 20);
  assert.equal(middle.items[0].path, "Folder/20.txt");
  assert.equal(middle.items[19].path, "Folder/39.txt");
  const end = workflow.importPage(plan, {page: 99});
  assert.equal(end.page, 3);
  assert.equal(end.first, 41);
  assert.equal(end.last, 46);
  assert.equal(end.total, 46);
  assert.equal(workflow.importManifest(plan).length, 46);
});

test("import search matches nested paths and produces a clear empty page", () => {
  const plan = [{path: "Folder/Research/Alpha.pdf", state: "waiting", action: "Add"},
    {path: "Folder/Notes/Beta.txt", state: "waiting", action: "Add"}];
  assert.equal(workflow.importPage(plan, {query: "research/ALPHA", page: 2}).items[0].path,
    "Folder/Research/Alpha.pdf");
  const empty = workflow.importPage(plan, {query: "missing"});
  assert.equal(empty.items.length, 0);
  assert.equal(empty.first, 0);
  assert.equal(empty.last, 0);
  assert.equal(empty.pages, 1);
});

test("unsafe file paths fail independently before the shared admission manifest is created", async () => {
  const plan = await workflow.reviewFolder([file("good.txt"), file("bad\nname.txt"), file("./bad.txt")], [], [], options);
  assert.deepEqual(plan.map(item => item.state), ["waiting", "invalid", "invalid"]);
  assert.deepEqual(workflow.importManifest(plan).map(item => item.path), ["Downloads/good.txt"]);
});

test("an admitted file with unavailable status still counts as uploaded", () => {
  const progress = workflow.importProgress([
    {action: "Track", state: "ready", job: {stage: "ready"}},
    {action: "Add", state: "failed", statusUnavailable: true},
    {action: "Add", state: "waiting"},
    {action: "Skip", state: "skipped"},
  ]);
  assert.equal(progress.submitted, 2);
  assert.equal(progress.pending, 1);
  assert.equal(progress.finished, 1);
  assert.equal(progress.eligible.length, 3);
});

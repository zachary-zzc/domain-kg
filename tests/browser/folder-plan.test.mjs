import assert from "node:assert/strict";
import {test} from "node:test";
import {sourceFile, matchMaterial, sha256, materialKey} from "../../src/domain_kg/console/static/folder-plan.js";

test("folder files preserve relative identity and infer supported content types", () => {
  const file = {name: "paper.txt", webkitRelativePath: "sources/topic/paper.txt", size: 42};
  assert.equal(sourceFile(file).source, "folder:sources/topic/paper.txt");
  assert.equal(sourceFile(file).mediaType, "text/plain");
  assert.equal(sourceFile({...file, webkitRelativePath: "sources/.private/paper.txt"}), null);
  assert.equal(sourceFile({...file, name: "program.exe"}), null);
  assert.equal(sourceFile({...file, size: 0}), null);
  assert.throws(() => sourceFile({...file, size: 51 * 1024 * 1024}));
  assert.throws(() => sourceFile({...file, webkitRelativePath: "sources/../paper.txt"}));
});

test("ambiguous folder identity fails instead of replacing an arbitrary material", () => {
  const material = {id: "one", metadata: {source: "folder:notes/a.txt"}};
  assert.equal(matchMaterial("folder:notes/a.txt", [material]), material);
  assert.equal(matchMaterial("folder:notes/b.txt", [material]), null);
  assert.throws(() => matchMaterial("folder:notes/a.txt", [material, {...material, id: "two"}]));
});

test("file checksums use SHA256 of original bytes", async () => {
  assert.equal(await sha256(new TextEncoder().encode("abc")),
    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});


test("replacement and upload keys fit the server limit and bind the expected version", async () => {
  const first = await materialKey("folder:notes/a.txt", "00000000-0000-0000-0000-000000000001");
  const second = await materialKey("folder:notes/a.txt", "00000000-0000-0000-0000-000000000002");
  assert.notEqual(first, second);
  assert.ok((first + "-upload-" + "a".repeat(16)).length <= 128);
  assert.equal(await materialKey("folder:notes/a.txt"),
    "folder-" + await sha256(new TextEncoder().encode("folder:notes/a.txt")));
});

test("Office and image uploads infer canonical types when the OS MIME is missing", async () => {
  const {fileMediaType} = await import("../../src/domain_kg/console/static/file-types.js");
  for (const [extension, expected] of Object.entries({
    DOC: "application/msword", XLS: "application/vnd.ms-excel", PPT: "application/vnd.ms-powerpoint",
    XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ODT: "application/vnd.oasis.opendocument.text", ODS: "application/vnd.oasis.opendocument.spreadsheet",
    ODP: "application/vnd.oasis.opendocument.presentation", RTF: "application/rtf",
    JPG: "image/jpeg", TIFF: "image/tiff", PNG: "image/png", TSV: "text/tab-separated-values",
  })) {
    const input = {name: `sample.${extension}`, size: 3, type: "", webkitRelativePath: ""};
    assert.equal(fileMediaType(input), expected);
    assert.equal(sourceFile(input).mediaType, expected);
  }
});

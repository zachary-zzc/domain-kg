// Explicit opt-in: drives the real local browser and makes small paid Alibaba calls.
// NODE_PATH must point to an installed Playwright package. No browser is downloaded.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {createRequire} from "node:module";
const require = createRequire(import.meta.url);
const {chromium} = require("playwright");
if (process.env.DOMAIN_KG_BROWSER_LIVE !== "1") throw new Error("Set DOMAIN_KG_BROWSER_LIVE=1 explicitly.");
const root = process.cwd();
const privatePath = path.join(root, "secrets/local-browser");
const identity = JSON.parse(await fs.readFile(path.join(privatePath, "identity.json"), "utf8"));
const workspace = JSON.parse(await fs.readFile(path.join(privatePath, "workspace.json"), "utf8"));
const reports = path.join(root, ".cache/local-browser");
const sourceDir = path.join(reports, "browser-example");
await fs.mkdir(sourceDir, {recursive: true});
const sourcePath = path.join(sourceDir, "example.txt");
const text = "Alpha uses Beta. Beta is part of Gamma.";
await fs.writeFile(sourcePath, text + "\n");
const browser = await chromium.launch({executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true});
const context = await browser.newContext({viewport: {width: 1440, height: 1100}, locale: "en-US"});
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const report = {passed: false, checks: []};
async function record(name) {report.checks.push(name); console.log("PASS " + name);}
try {
  await page.goto(workspace.url);
  await page.getByRole("link", {name: "Sign in with your organization"}).click();
  await page.locator("#username").fill("owner");
  await page.locator("#password").fill(identity.password);
  await page.locator("#kc-login").click();
  await page.waitForURL("http://127.0.0.1:8000/admin", {timeout: 30000});
  await record("real OIDC authorization-code and PKCE browser login");
  await page.goto(`${workspace.url}/kgs/${workspace.kg_id}/folder`);
  await page.locator("#folder").setInputFiles(sourceDir);
  await page.getByRole("button", {name: /^Review files/}).click();
  await page.waitForFunction(() => document.querySelectorAll("[data-folder-rows] tr[data-state]").length === 1);
  if (await page.locator("[data-folder-start]").isEnabled()) {
    await page.locator("[data-folder-start]").click();
    await page.waitForFunction(() => [...document.querySelectorAll("[data-folder-rows] tr[data-state]")]
      .every(row => ["ready", "ready_with_warnings"].includes(row.dataset.state)), null, {timeout: 180000});
  }
  await record("folder upload and publication through the production worker");
  await page.getByRole("button", {name: /^Review files/}).click();
  await page.waitForFunction(() => document.querySelector("[data-folder-rows] tr[data-state='skipped']") !== null);
  assert.match(await page.locator("[data-folder-rows]").innerText(), /Unchanged/);
  await record("unchanged folder re-selection skips processing");
  await fs.writeFile(sourcePath, text + " Gamma uses Delta.\n");
  await page.locator("#folder").setInputFiles(sourceDir);
  await page.getByRole("button", {name: /^Review files/}).click();
  await page.waitForFunction(() => document.querySelector("[data-folder-start]").disabled === false);
  assert.match(await page.locator("[data-folder-rows]").innerText(), /Replace/);
  await page.locator("[data-folder-start]").click();
  await page.waitForFunction(() => [...document.querySelectorAll("[data-folder-rows] tr[data-state]")]
    .every(row => ["ready", "ready_with_warnings"].includes(row.dataset.state)), null, {timeout: 180000});
  await record("changed file publishes a replacement under its existing material identity");
  await page.goto(`${workspace.url}/kgs/${workspace.kg_id}/graph`);
  await page.locator("#node-graph canvas").waitFor();
  const graph = JSON.parse(await page.locator("[data-graph]").getAttribute("data-graph"));
  assert.ok(graph.nodes.some(node => node.name === "Alpha"));
  assert.ok(graph.edges.length >= 2);
  await page.screenshot({path: path.join(reports, "graph.png"), fullPage: true});
  await record("interactive graph displays source-supported nodes and relations");
  await page.goto(`${workspace.url}/kgs/${workspace.kg_id}/search`);
  await page.locator("#query").fill("What does Alpha use?");
  await page.locator("#mode").selectOption("deep");
  await page.getByRole("button", {name: /^Search/}).click();
  await page.getByRole("link", {name: /^Inspect citation/}).first().waitFor();
  assert.match(await page.locator("main").innerText(), /Alpha uses Beta/);
  await page.screenshot({path: path.join(reports, "rag.png"), fullPage: true});
  await page.getByRole("link", {name: /^Inspect citation/}).first().click();
  assert.match(await page.locator("main").innerText(), /Alpha/);
  await record("deep RAG evidence and source citation navigation");
  await page.goto(`${workspace.url}/kgs/${workspace.kg_id}/mcp-test`);
  await page.locator("#mcp-token-file").setInputFiles(path.join(privatePath, "mcp-token.txt"));
  await page.getByRole("button", {name: "Connect and list tools"}).click();
  await page.waitForFunction(() => document.querySelector("[data-mcp-status]").textContent.startsWith("Connected"));
  await page.getByRole("button", {name: /^Run tool/}).click();
  await page.waitForFunction(() => document.querySelector("[data-mcp-status]").textContent === "Tool completed.");
  const result = JSON.parse(await page.locator("[data-mcp-result]").innerText());
  assert.equal(result.isError, false);
  assert.match(JSON.stringify(result), /Alpha uses Beta/);
  await page.screenshot({path: path.join(reports, "mcp.png"), fullPage: true});
  await record("browser MCP initialize, discovery and live tools/call retrieval");
  const unauthenticated = await context.request.post(workspace.mcp_url, {data: {
    jsonrpc: "2.0", id: 1, method: "tools/list", params: {}}});
  assert.equal(unauthenticated.status(), 401);
  await record("browser session alone cannot authenticate MCP");
  assert.deepEqual(errors, []);
  await page.locator(".account summary").click();
  await page.getByRole("button", {name: "Sign out", exact: true}).click();
  const denied = await page.goto(`${workspace.url}/kgs/${workspace.kg_id}/folder`);
  assert.equal(denied.status(), 401);
  await record("logout revokes access to folder controls");
  report.passed = true;
} catch (error) {
  console.error("Browser status:", await page.locator("[data-folder-status], [data-mcp-status]").allTextContents());
  await page.screenshot({path: path.join(reports, "failure.png"), fullPage: true});
  throw error;
} finally {
  await fs.writeFile(path.join(reports, "browser-acceptance.json"), JSON.stringify(report, null, 2) + "\n");
  await browser.close();
}

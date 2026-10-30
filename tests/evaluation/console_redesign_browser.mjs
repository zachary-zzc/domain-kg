// Read-only local acceptance: existing sources, lexical retrieval and model-free MCP.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import {createRequire} from "node:module";
const require = createRequire(import.meta.url);
const {chromium} = require("playwright");
if (process.env.DOMAIN_KG_BROWSER_UI !== "1") throw new Error("Set DOMAIN_KG_BROWSER_UI=1 explicitly.");
const privatePath = path.join(process.cwd(), "secrets/local-browser");
const identity = JSON.parse(await fs.readFile(path.join(privatePath, "identity.json"), "utf8"));
const workspace = JSON.parse(await fs.readFile(path.join(privatePath, "workspace.json"), "utf8"));
const reports = path.join(process.cwd(), ".cache/local-browser/redesign");
await fs.mkdir(reports, {recursive: true});
const browser = await chromium.launch({executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true});
const context = await browser.newContext({viewport: {width: 1440, height: 1080}, locale: "en-US"});
const page = await context.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const report = {passed: false, checks: []};
async function check(name) {report.checks.push(name); console.log("PASS " + name);}
async function visit(route, screenshot) {
  const response = await page.goto(workspace.url + route);
  assert.equal(response.status(), 200, route);
  await page.locator("h1").waitFor();
  assert.ok(await page.locator("h1").innerText());
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, "horizontal overflow: " + route);
  if (screenshot) await page.screenshot({path: path.join(reports, screenshot + ".png"), fullPage: true});
}
try {
  await page.goto(workspace.url);
  await page.getByRole("link", {name: "Sign in with your organization"}).click();
  await page.locator("#username").fill("owner");
  await page.locator("#password").fill(identity.password);
  await page.locator("#kc-login").click();
  await page.waitForURL(workspace.url);
  await check("real OIDC login");
  await visit("", "catalog");
  await page.getByRole("link", {name: "＋ Create KG", exact: true}).click();
  await page.locator("#kg-name").fill("Browser form review");
  assert.ok(await page.locator("#placement option").count() >= 1);
  assert.equal(await page.locator("#domain-profile option").count(), 3);
  await page.screenshot({path: path.join(reports, "create.png"), fullPage: true});
  await check("guided KG form with approved placement choices");
  const kg = "/kgs/" + workspace.kg_id;
  for (const [route, name] of [[kg,"overview"],[kg+"/materials","sources"],[kg+"/folder","folder"],
    ["/team?kind=groups","groups"],["/team?kind=members","people"],["/storage","storage"],
    ["/infrastructure","databases"],[kg+"/settings","settings"],[kg+"/grants","roles"],
    [kg+"/jobs","jobs"],[kg+"/operations","maintenance"]]) await visit(route,name);
  await check("all workspace, team and storage pages render without overflow");
  await visit(kg+"/graph");
  await page.locator("#node-graph canvas").waitFor();
  const graph = JSON.parse(await page.locator("[data-graph]").getAttribute("data-graph"));
  assert.ok(graph.nodes.length >= 4 && graph.edges.length >= 3);
  await page.screenshot({path:path.join(reports,"graph.png"),fullPage:true});
  await check("existing source-grounded graph still renders");
  await visit(kg+"/search");
  await page.locator("#query").fill("Alpha");
  await page.locator("#mode").selectOption("lexical");
  await page.getByRole("button", {name: /^Search/}).click();
  const citation = page.getByRole("link", {name: /Inspect citation/}).first();
  await citation.waitFor();
  await page.screenshot({path:path.join(reports,"rag.png"),fullPage:true});
  await citation.click();
  assert.match(await page.locator("main").innerText(), /Alpha/);
  await check("lexical RAG search opens original citation without model calls");
  await visit(kg+"/mcp-test");
  await page.locator("#mcp-token-file").setInputFiles(path.join(privatePath,"mcp-token.txt"));
  await page.getByRole("button",{name:"Connect and list tools"}).click();
  await page.waitForFunction(()=>document.querySelector("[data-mcp-status]").textContent.startsWith("Connected"));
  await page.locator("#mcp-tool").selectOption("domain_kg_list_materials");
  await page.getByRole("button",{name:/^Run tool/}).click();
  await page.waitForFunction(()=>document.querySelector("[data-mcp-status]").textContent==="Tool completed.");
  assert.equal(JSON.parse(await page.locator("[data-mcp-result]").innerText()).isError,false);
  await page.screenshot({path:path.join(reports,"mcp.png"),fullPage:true});
  await check("real MCP discovery and material-list action");
  await page.getByRole("button",{name:"Disconnect",exact:true}).click();
  assert.equal(await page.locator("[data-mcp-result]").innerText(),"");
  await page.setViewportSize({width:390,height:844});
  for (const [route,name] of [["","mobile-catalog"],["/kgs/new","mobile-create"],[kg+"/folder","mobile-folder"],[kg+"/graph","mobile-graph"],[kg+"/mcp-test","mobile-mcp"],["/team?kind=groups","mobile-groups"]]) await visit(route,name);
  const toggle=page.getByRole("button",{name:/Navigation/});
  await toggle.focus(); await page.keyboard.press("Enter");
  assert.equal(await toggle.getAttribute("aria-expanded"),"true");
  await page.locator(".sidebar").getByRole("link",{name:"Knowledge graphs",exact:true}).click();
  await check("390px mobile layouts and keyboard-operated navigation");
  await page.getByRole("link",{name:"中文",exact:true}).click();
  assert.equal(await page.locator("html").getAttribute("lang"),"zh-CN");
  assert.match(await page.locator("h1").innerText(),/知识图谱/);
  await page.screenshot({path:path.join(reports,"mobile-zh.png"),fullPage:true});
  await check("Chinese console translation");
  await page.getByRole("link",{name:"EN",exact:true}).click();
  await page.locator(".account summary").click();
  await page.getByRole("button",{name:"Sign out",exact:true}).click();
  assert.equal((await page.goto(workspace.url+kg+"/settings")).status(),401);
  await check("sign-out revokes browser setup access");
  assert.deepEqual(errors,[]);
  report.passed=true;
} catch(error) {
  await page.screenshot({path:path.join(reports,"failure.png"),fullPage:true});
  throw error;
} finally {
  await fs.writeFile(path.join(reports,"acceptance.json"),JSON.stringify(report,null,2)+"\n");
  await browser.close();
}

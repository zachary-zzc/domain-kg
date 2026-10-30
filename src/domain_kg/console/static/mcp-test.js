const connect = document.querySelector("[data-mcp-connect]");
const call = document.querySelector("[data-mcp-call]");
const select = document.querySelector("#mcp-tool");
const input = document.querySelector("#mcp-arguments");
const tokenInput = document.querySelector("#mcp-token");
const fileInput = document.querySelector("#mcp-token-file");
const status = document.querySelector("[data-mcp-status]");
const output = document.querySelector("[data-mcp-result]");
let token = "", protocol = "", session = "", tools = [], sequence = 0, busy = false, connectionEpoch = 0;

function disconnect() {
  connectionEpoch++;
  token = ""; protocol = ""; session = ""; tools = [];
  tokenInput.value = ""; fileInput.value = ""; input.value = "{}";
  select.replaceChildren(); select.disabled = true; call.querySelector("button").disabled = true;
  output.textContent = ""; status.textContent = "Disconnected.";
  document.querySelector("[data-mcp-description]").textContent = "";
  document.querySelector("[data-mcp-schema]").textContent = "";
}
async function rpc(method, params, notification = false) {
  const epoch = connectionEpoch;
  const id = ++sequence;
  const headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
    "Authorization": "Bearer " + token};
  if (protocol) headers["MCP-Protocol-Version"] = protocol;
  if (session) headers["Mcp-Session-Id"] = session;
  const response = await fetch("/mcp", {method: "POST", credentials: "omit", headers,
    body: JSON.stringify({jsonrpc: "2.0", ...(notification ? {} : {id}), method, params}),
    signal: AbortSignal.timeout(90000)});
  if (epoch !== connectionEpoch) throw new Error("Connection closed.");
  if (!response.ok) throw new Error(`MCP HTTP ${response.status}. Check token expiry, audience and permissions.`);
  if (notification) return null;
  session = response.headers.get("Mcp-Session-Id") || session;
  const text = await response.text();
  if (epoch !== connectionEpoch) throw new Error("Connection closed.");
  let message;
  if ((response.headers.get("content-type") || "").includes("text/event-stream")) {
    const events = text.replaceAll("\r\n", "\n").split("\n\n");
    message = events.map(event => event.split("\n").filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).trimStart()).join("\n")).filter(Boolean)
      .map(data => JSON.parse(data)).find(value => value.id === id);
  } else message = JSON.parse(text);
  if (!message || message.id !== id) throw new Error("MCP returned an unmatched response.");
  if (message.error) throw new Error(message.error.message || "MCP protocol error.");
  return message.result;
}
function choose() {
  const tool = tools.find(item => item.name === select.value);
  if (!tool) return;
  document.querySelector("[data-mcp-description]").textContent = tool.description || "";
  document.querySelector("[data-mcp-schema]").textContent = JSON.stringify(tool.inputSchema, null, 2);
  const org = connect.dataset.org, kg = connect.dataset.kg;
  const defaults = {domain_kg_search: {org_id: org, command: {kg_ids: [kg],
    query: "What does Alpha use?", mode: "deep"}}, domain_kg_list_materials: {org_id: org, kg_id: kg}};
  const fields = tool.inputSchema?.properties || {};
  const scope = {...(fields.org_id ? {org_id: org} : {}), ...(fields.kg_id ? {kg_id: kg} : {})};
  input.value = JSON.stringify(defaults[tool.name] || scope, null, 2);
}
select.addEventListener("change", choose);
connect.addEventListener("submit", async event => {
  event.preventDefault(); if (busy) return; busy = true;
  const entered = tokenInput.value.trim(), file = fileInput.files[0];
  disconnect();
  try {
    if (file && file.size > 16384) throw new Error("The token file is too large.");
    token = entered || (file ? (await file.text()).trim() : "");
    if (!token || token.length > 16384 || /\s/.test(token)) throw new Error("Supply one OIDC access token.");
    const initialized = await rpc("initialize", {protocolVersion: "2025-11-25", capabilities: {},
      clientInfo: {name: "domain-kg-browser", version: "0.1.0"}});
    protocol = initialized.protocolVersion;
    await rpc("notifications/initialized", {}, true);
    let cursor;
    do {
      const result = await rpc("tools/list", cursor ? {cursor} : {});
      tools.push(...result.tools); cursor = result.nextCursor;
    } while (cursor);
    for (const tool of tools) select.add(new Option(tool.name, tool.name));
    select.value = tools.some(tool => tool.name === "domain_kg_search") ? "domain_kg_search" : tools[0]?.name;
    select.disabled = false; call.querySelector("button").disabled = false;
    choose(); status.textContent = `Connected over MCP ${protocol}; ${tools.length} tools discovered.`;
  } catch (error) { disconnect(); status.textContent = error.message; }
  finally { busy = false; }
});
call.addEventListener("submit", async event => {
  event.preventDefault(); if (busy || !token) return; busy = true;
  output.textContent = ""; call.querySelector("button").disabled = true;
  try {
    if (new TextEncoder().encode(input.value).length > 1000000) throw new Error("Arguments are too large.");
    const args = JSON.parse(input.value);
    if (!args || Array.isArray(args) || typeof args !== "object") throw new Error("Arguments must be a JSON object.");
    status.textContent = "Calling " + select.value + "…";
    const result = await rpc("tools/call", {name: select.value, arguments: args});
    output.textContent = JSON.stringify(result, null, 2);
    status.textContent = result.isError ? "Tool returned an error. See its result." : "Tool completed.";
  } catch (error) { status.textContent = error.message; }
  finally { busy = false; call.querySelector("button").disabled = !token; }
});
document.querySelector("[data-mcp-disconnect]").addEventListener("click", disconnect);
window.addEventListener("pagehide", disconnect);

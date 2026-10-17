const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'src/domain_kg/console/static/graph.js'), 'utf8');

function harness(data, missingLibrary = false) {
  const events = {}, calls = [], timers = new Map();
  const selection = {textContent: '', replaceChildren(link) {this.link = link;}};
  const button = {dataset: {graphAction: 'in'}, addEventListener(_, callback) {this.click = callback;}};
  const section = {dataset: {graph: JSON.stringify(data), error: 'unavailable', verified: 'Source', manual: 'Manual', imported: 'Unverified'},
    querySelector: () => selection, querySelectorAll: () => [button], removeAttribute(key) {delete this.dataset[key.replace('data-', '')];}};
  class Dataset {
    rows = [];
    add(rows) {this.rows.push(...rows);}
    clear() {this.rows = [];}
  }
  const network = {on: (key, fn) => {events[key] = fn;}, once: (key, fn) => {events[key] = fn;},
    fit: () => calls.push('fit'), stopSimulation: () => calls.push('stop'),
    setOptions: options => calls.push(options), getScale: () => 3, moveTo: options => calls.push(options)};
  let chart;
  class Chart {
    constructor(config) {this.config = config; this.nodes = new Dataset(); this.edges = new Dataset();this.network = network;chart=this;}
    registerOnEvent(key, callback) {this[key] = callback;}
    render() {this.completed();}
  }
  const window = {location: {origin: 'http://test'}, setTimeout(fn) {timers.set(1,fn);return 1;},
    clearTimeout(id) {timers.delete(id);}, addEventListener(key, fn) {events[key] = fn;}};
  const sandbox = {document: {querySelector: () => section, createElement: () => ({})}, window, URL,
    ...(missingLibrary ? {} : {NeoVis: {default: Chart}})};
  vm.runInNewContext(script, sandbox);
  return {chart, events, selection, section, calls, timers, button};
}
const data = {nodes: [{id: 'a', name: '<img src=x onerror=bad>', href: '/admin/kgs/kg/nodes/a'},
  {id: 'b', name: 'Beta', href: '/admin/kgs/kg/nodes/b'}],
  edges: [{id: 'edge', source: 'a', target: 'b', predicate: '<script>uses</script>', status: 'manual', href: '/admin/kgs/kg/relations/r/history'}]};

test('pinned neovis public DataSet needs no database driver in function mode', () => {
  global.self = globalThis;
  const lib = require(path.join(root, 'src/domain_kg/console/static/vendor/neovis-2.1.0.js'));
  global.document = {getElementById: () => ({})};
  global.WebSocket = class {constructor() {assert.fail('No database connection is allowed');}};
  const chart = new lib.default({containerId: 'test', dataFunction: async () => []});
  chart.nodes.add({id:'a',label:'A'});
  assert.equal(chart.nodes.length,1);
  delete global.document; delete global.WebSocket;
});
test('bounded renderer selects plain text and retains server-qualified links', async () => {
  const h=harness(data);
  assert.equal((await h.chart.config.dataFunction()).length,0);
  assert.equal('neo4j' in h.chart.config,false);
  assert.equal(h.chart.nodes.rows.length,2);
  assert.equal(h.chart.edges.rows[0].label,'<script>uses</script>');
  h.events.click({nodes:['a'],edges:[]});
  assert.equal(h.selection.link.textContent,data.nodes[0].name);
  assert.equal(h.selection.link.href,data.nodes[0].href);
  h.events.click({nodes:[],edges:['edge']});
  assert.equal(h.selection.link.textContent,'<script>uses</script> · Manual');
  h.button.click();assert.equal(h.calls.at(-1).scale,3);
  h.timers.get(1)();assert.ok(h.calls.includes('stop'));
  h.events.pagehide();assert.equal(h.timers.size,0);assert.equal(h.chart.nodes.rows.length,0);
  assert.equal('graph' in h.section.dataset,false);
});
test('missing renderer, invalid references and oversized input retain safe fallback', () => {
  for(const h of [harness(data,true),harness({...data,edges:[{...data.edges[0],target:'hidden'}]}),
    harness({...data,nodes:Array(26).fill(data.nodes[0])})]) assert.equal(h.selection.textContent,'unavailable');
});

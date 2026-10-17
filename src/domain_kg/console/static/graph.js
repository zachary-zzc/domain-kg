/* Access-scoped neovis.js adapter. All labels are text; no database connection is made. */
"use strict";
(function () {
  const section = document.querySelector("[data-graph]");
  if (!section) return;
  const selection = section.querySelector("[data-graph-selection]");
  const timers = [];
  let visual;
  let stopped = false;
  const fail = () => { selection.textContent = section.dataset.error; };
  try {
    const data = JSON.parse(section.dataset.graph);
    if (!Array.isArray(data.nodes) || !Array.isArray(data.edges) || data.nodes.length > 25 || data.edges.length > 40) throw new Error("graph budget");
    const nodes = new Map(data.nodes.map(node => [node.id, node]));
    const edges = new Map(data.edges.map(edge => [edge.id, edge]));
    if (nodes.size !== data.nodes.length || edges.size !== data.edges.length || data.edges.some(edge => !nodes.has(edge.source) || !nodes.has(edge.target))) throw new Error("graph references");
    const colors = {verified: "#17654c", manual: "#265ac7", imported_unverified: "#a2650d"};
    const statuses = {verified: section.dataset.verified, manual: section.dataset.manual, imported_unverified: section.dataset.imported};
    const show = (item, text) => {
      const url = new URL(item.href, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith("/admin/kgs/")) return;
      const link = document.createElement("a");
      link.href = item.href; link.textContent = text;
      selection.replaceChildren(link);
    };
    visual = new NeoVis.default({containerId: "node-graph", dataFunction: async () => [],
      consoleDebug: false,
      visConfig: {layout: {randomSeed: 7, improvedLayout: false},
        physics: {enabled: true, stabilization: {enabled: true, iterations: 80}},
        interaction: {hover: false, keyboard: false, navigationButtons: false},
        nodes: {shape: "dot", size: 16, color: "#dcece4", font: {color: "#18302b", size: 14, multi: false}},
        edges: {arrows: {to: {enabled: true, scaleFactor: 0.7}}, font: {size: 12, multi: false}, smooth: {type: "continuous"}}}});
    visual.registerOnEvent("error", fail);
    visual.registerOnEvent("completed", () => {
      if (stopped) return;
      visual.nodes.add(data.nodes.map(node => ({id: node.id, label: [...node.name].slice(0, 80).join("")})));
      visual.edges.add(data.edges.map(edge => ({id: edge.id, from: edge.source, to: edge.target,
        label: [...edge.predicate].slice(0, 80).join(""), color: colors[edge.status], dashes: edge.status === "imported_unverified"})));
      const network = visual.network;
      const settle = () => { network.stopSimulation(); network.setOptions({physics: {enabled: false}}); network.fit({animation: false}); };
      network.once("stabilizationIterationsDone", settle);
      timers.push(window.setTimeout(settle, 1500));
      network.on("click", event => {
        if (event.nodes.length && nodes.has(event.nodes[0])) { const node = nodes.get(event.nodes[0]); show(node, node.name); }
        else if (event.edges.length && edges.has(event.edges[0])) { const edge = edges.get(event.edges[0]); show(edge, edge.predicate + " · " + (statuses[edge.status] || edge.status)); }
      });
      for (const button of section.querySelectorAll("[data-graph-action]")) button.addEventListener("click", () => {
        const action = button.dataset.graphAction;
        if (action === "fit") network.fit({animation: false});
        else network.moveTo({scale: Math.min(3, Math.max(0.2, network.getScale() * (action === "in" ? 1.2 : 1 / 1.2))), animation: false});
      });
    });
    visual.render();
  } catch (_) { fail(); }
  window.addEventListener("pagehide", () => {
    stopped = true;
    for (const timer of timers) window.clearTimeout(timer);
    if (visual && visual.network) { visual.network.stopSimulation(); visual.nodes.clear(); visual.edges.clear(); }
    section.removeAttribute("data-graph");
  }, {once: true});
}());

# Vendored HTMX

Upstream: https://github.com/bigskysoftware/htmx/tree/v2.0.11

Official documentation integrity verified before use.

SHA-384: 2OatzQy1H+Zd/IIrjr1TcuDGqLXeHhbooAyJY1KdQMKnr4LZ22k31GBLdYKHmVjg

Served locally; upstream license retained in HTMX-LICENSE.

# Vendored neovis.js 2.1.0

Upstream: https://github.com/neo4j-contrib/neovis.js/tree/cef0aa16d647ffe0fd9ca457bcffa6e0cb7c55c8

Source archive: https://registry.npmjs.org/neovis.js/-/neovis.js-2.1.0.tgz

npm SHA-512 integrity (verified before extracting): `sha512-Xceu1qcxbDy7BtYnARl6URp8rArVwJBR9lJHvswdIG6a/BCaMBJzc7UtWU8fQGObrwkKhams6kZIFl4iBIMEjg==`

Vendored JS SHA-256: `5a15ad5b04ae481228c8c88f707f0fa83eabac3a868e070ec96eb99951089bf5`

Apache-2.0 licence retained in NEOVIS-LICENSE; bundled dependency notices in
NEOVIS-BUNDLED-LICENSES.txt. No node_modules/examples/source maps are vendored.

The renderer uses dataFunction mode with an empty record iterable, then the
public nodes/edges DataSet and network APIs. The exact pinned source skips driver
creation when dataFunction is provided. All graph data comes from GraphService;
no database connection options, credentials or Cypher are supplied to the browser.
NVL was not selected: https://neo4j.com/docs/reference/license/nvl/ currently limits
its use to Aura or proprietary commercial Neo4j products.

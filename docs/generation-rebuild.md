# Rebuild the current index generation

Local follow-up (18 October 2026): curation-only material dependencies now carry
typed namespace-bound access hashes and participate in capture pins. A completed
source purge no longer blocks rebuilding independently retained human knowledge.
Incomplete retirement, source/ACL fence changes and missing dependencies still
deny publication. The real purge/rebuild test preserves the public human claim
without reviving removed source names or original bytes.

An owner can use **Operations → Rebuild current indexes**, then follow the job
link. Search uses the previous accepted generation while preparation runs. The
new generation becomes accepted only after graph, vector and lexical read-back
checks and a fenced MongoDB commit. This is not a transaction across stores.

Run one native worker against the already configured, capped database stack:

```bash
uv run python -m domain_kg.worker --generation-only --once
```

This dedicated mode does not load the provider configuration or call an LLM or
embedding model. It reads sealed artifacts and reuses their existing vectors,
one document at a time. Docker caps remain in `config/laptop-smoke.json` and
Compose; no additional container is required.

The shared HTTP command is `POST /api/v1/kgs/{kg_id}/reindex`, authenticated as a
current KG owner, with the normal session CSRF protection:

```json
{
  "kind": "generation",
  "mode": "rebuild_current",
  "kg_id": "<current KG UUID>",
  "expected_published_revision": 2,
  "idempotency_key": "rebuild-current-index-1"
}
```

The expected revision must match the current publication. Duplicate requests
with the same intent return the same job. A retry after a transient backend
failure has a new real attempt and isolated projection IDs. If source visibility,
policy, publication, material fences or configuration changed, inspect the new
state and submit a fresh command with a new key; retry does not silently replace
the original frozen inputs. A lost successful response cannot publish twice.

The initial bounded mode accepts at most 1,000 bindings and 8 MiB of input metadata.
It preserves retained hidden sources, manual directives and original artifact
ownership; private rebuild permits do not make those sources visible in search.
Live source pins block purge. Existing imported-unverified trust is preserved.

Schema/model changes, physically purged bindings and curations depending on
retired source bindings need separately verified migration semantics. This mode
refuses those cases instead of dropping records or inventing evidence. It keeps
old and abandoned generation records; automatic retained-generation collection
is a separate unfinished operation. Do not manually erase them while workers or
retained reads may still refer to them.

The owner audit shows admission, cancellation, retries, completion and safe
failure codes. No source text, provider payload or database connection is placed
in the audit. See [operations](operations.md) and [laptop smoke](laptop-smoke.md)
for local checks and the remaining release acceptance gates.

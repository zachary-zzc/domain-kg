# Model-specific source windows

Each approved provider profile declares `extraction_context_tokens`,
`embedding_context_tokens`, `max_completion_tokens`, `embedding_batch_size`, and an
optional `embedding_query_prefix`. These are server configuration, never tool
arguments. Set the context fields to the actual deployed limits, even if the
upstream model supports a larger context. A laptop runtime can use 4,096 chat and
2,048 embedding tokens.

The configured ingestion pipeline first makes the normal 700-token source chunks,
then fits them to both selected model budgets. It measures the complete serialized
extraction messages, reserves the entire response allowance plus 256 tokens for
chat framing, and includes the response schema by default. UTF-8 byte counts form
a conservative input-token bound for the supported byte-backed tokenizers.
`schema_in_context=false` is appropriate only for a verified grammar-based runtime
whose schema is outside the prompt, such as the configured native llama.cpp path.
Other tokenizers need a separately verified counter or a larger safety allowance.

Windows retain original block locations and character offsets, cover every source
character, and use a small overlap capped at 100 cl100k tokens. Unchanged fitting
chunks retain their IDs. Split windows receive deterministic source-scoped IDs.
The chunk checkpoint hash binds the selected profile, schema and selection
revision; a concurrent configuration change cannot seal an incompatible result.
An impossible schema/output budget or excessive window count fails before a model
call with `QUOTA_EXCEEDED`.

Embedding input has a separate 128-token framing reserve. Longer observation or
query text is split on character boundaries and embedded in bounded batches.
Normalized window vectors are combined using UTF-8-length-weighted mean pooling
and normalized again, yielding one vector per canonical record. Query prefixes
apply only to query vectors. Context limits, pooling version and query prefix are
part of the embedding-space fingerprint: changing them on a published KG requires
a new generation. A fresh 1,024-dimensional local deployment can set
`DOMAIN_KG_EMBEDDING_DIMENSIONS=1024`; this does not migrate existing collections.

Windowing manages input size. It does not make an unsuitable extraction model
reliable. The local 0.8B model's evidence-quality gate remains separate, and invalid
claims still fail grounding validation without graph publication.

Run `uv run --no-sync pytest tests/unit/test_model_windows.py
tests/integration/test_model_windows_pipeline.py -q` against the local smoke stack.

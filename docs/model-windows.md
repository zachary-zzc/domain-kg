# Model-specific source windows

Each approved provider profile declares `extraction_context_tokens`,
`embedding_context_tokens`, `max_completion_tokens`, `embedding_batch_size`, and an
optional `embedding_query_prefix`. These are server configuration, never tool
arguments. Set the context fields to the actual deployed limits, even if the
upstream model supports a larger context. A laptop runtime can use 4,096 chat and
2,048 embedding tokens.

The configured ingestion pipeline adapts source-window size to both selected
models. The planning ceiling is the smaller of the chat context minus maximum
output and framing reserve, and the embedding context minus its framing reserve.
This removes the fixed 700-token ceiling from default model-backed imports:
larger contexts allow larger windows, while smaller contexts reduce them. A
caller-supplied `ChunkConfig` keeps its explicit target; preparation without a
provider still defaults to 700 tokens. Source chunk targets have a 32,768-token
resource bound, matching the maximum supported embedding context.

The pipeline then fits each window to the actual request. It measures the complete
serialized extraction messages, reserves the entire response allowance plus 256 tokens for
chat framing, and includes the response schema by default. UTF-8 byte counts form
a conservative input-token bound for the supported byte-backed tokenizers.
`schema_in_context=false` is appropriate only for a verified grammar-based runtime
whose schema is outside the prompt, such as the configured native llama.cpp path.
Other tokenizers need a separately verified counter or a larger safety allowance.
Preparation also reserves the complete evidence-correction prompt, using the
longest allowlisted evidence diagnostic. A fitting initial window must not crowd
out its one corrective request. Both requests still check their actual payload
before transmission and account for usage separately.

Windows retain original block locations and character offsets, cover every source
character, and use a small overlap capped at 100 cl100k tokens. Within a source
section, ordinary chunk boundaries and model-budget repacking retain a suffix of
the previous window. The planning overlap scales with the available budget up to
the existing 100-token cap. Complete source spans are preferred; a longer text span
can contribute just its tail rather than losing all context. Whole table rows are not
cut merely to add overlap. Heading, source-kind and table boundaries reset source
context. Overlap shrinks or is omitted when the next passage needs the remaining
model budget; new source content always makes progress. Already overlapping parts
of a long source block are not repeated inside the same window.

Explicit model output truncation permits at most two splitting levels. The right
child retains up to 20% of the left child's tokens, capped at 100, while both
children remain smaller than the parent. This preserves nearby boundary context
without repeatedly sending the entire parent. Child and merged results must still
pass the same source-evidence checks.
Splits balance source tokens rather than passage counts. A single long paragraph
or PDF line is subdivided when it contains most of the text. Continued truncation
stays a failure and reports the output setting to adjust; it is not reported only
as a generic model-service outage.

Unchanged fitting chunks retain their IDs. Split windows receive deterministic
source-scoped IDs.
The chunk checkpoint hash binds the selected profile, schema and selection
revision; a concurrent configuration change cannot seal an incompatible result.
Chunking, model-window and output-window versions invalidate incompatible retry
artifacts when overlap behavior changes; accepted documents are not rewritten.
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
claims still fail grounding validation. After one corrective response, only
independently validated facts can continue; discarded counts reach the job's
`ready_with_warnings` result. This does not relax source/schema validation or make
unsupported claims searchable. Truncated or malformed output remains a failure.

Run `uv run --no-sync pytest tests/unit/test_model_windows.py
tests/integration/test_model_windows_pipeline.py -q` against the local smoke stack.

# Administration console in progress

The session-bound shell at `/admin` uses the configured external OIDC login.
It lists authorized KGs, accepted/retained materials, scoped search results and
source/manual citations. Team, curation and operations screens are the next implementation steps. Navigation is connected as those services are verified. No local
password or alternative identity provider is bundled.

Current session membership and KG policy are checked before catalog access and
again before returning rendered HTML. HTML templates autoescape source-derived
labels. Pages use `Cache-Control: no-store`, same-origin content restrictions,
disabled HTMX history/script/eval behavior and a reload on restored browser pages.
Logout requires the current session's CSRF token and exact origin.

Forms are limited to 64 KiB and 100 scalar fields; ambiguous duplicate fields,
invalid encodings and unexpected command fields are rejected without echoing
private input. Files will use the existing streamed checksum-verified upload API.
The console adds no Docker service or frontend build process.

Jinja2 is pinned to 3.1.6. HTMX 2.0.11 is served locally; its published SHA-384 was
verified and its upstream license retained in `console/static/vendor/`.

The shell and existing signed HTTP/MCP regression passed four real-store checks
in 26.43 seconds. The full unit/protocol suite passed 173 tests, including 11
console form cases; Ruff and strict mypy passed. Representative browser flows and
external OIDC acceptance remain later gates.

Material and search screens pin the shared accepted read scope before service
reads and revalidate it after HTML rendering, alongside the current session and
policy. Explicit unverified opt-in retains its provenance labels. Reader removal
forms are hidden and forged submissions receive the same denial as the JSON API.
The read/navigation/permission case and withdrawal-after-search race passed two
real-store checks in 34.92 seconds. Editor upload/replacement/restoration workflows
and the complete browser acceptance gate remain in progress.

The job list filters each receipt through current source access before applying
its visible page limit. Opaque cursors pin principal, policy, visibility and the
published view. Job reads verify the canonical version's namespace and material
ownership; missing or mismatched references fail with a consistency error.
Editors can cancel/retry through the shared durable queue; readers cannot.
Status refresh uses a local HTMX request with fresh authorization and clears the
view on an error. There is no automatic background polling. The six-case job
service/browser gate passed in 17.86 seconds; durable recovery and signed HTTP/MCP
regression passed 14 cases in 53.58 seconds.

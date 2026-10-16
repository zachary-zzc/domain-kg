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

Editors can submit uploaded files or inline text, replace the current active
version and edit metadata through explicit typed forms. The file picker computes
SHA-256 and sends bytes to the existing owned-upload API; no filesystem path is
accepted. Replacements retain the prior accepted source until publication, and
metadata-only changes avoid model calls. Forged actor fields are rejected before
job admission. The source form flow passed its dedicated 63.02-second check and
a four-case source/workspace/job regression in 79.09 seconds. The JavaScript
syntax check passed; the full browser acceptance remains separate.

Editors also have explicit pending/removed material metadata views. These views
check current KG role and material ACL, validate canonical ownership, exclude
purged records, and return no original text or graph evidence. Their cursors pin
the current actor, state filter and accepted visibility/view. The shared JSON
management endpoint enforces the same permissions. Restoration uses the existing
lifecycle service: a retained ready source can reopen immediately; an unpublished
source stays hidden while its reconstruction job verifies and publishes.
The five-case management/restoration/workspace gate passed in 110.02 seconds;
the dedicated browser/JSON parity and reconstruction check passed in 51.93 seconds.

Organization administrators can admit external human/service identities, manage
groups and membership, change organization roles and suspend accounts. No
credentials or invitations are generated. KG owners manage role grants and
material ACLs with current policy revisions; material ACLs still narrow KG
membership. Owner forms use validated subject identifiers and do not bypass the
administrator-only organization directory. All actors and organizations come
from the current server-side session.
The team/identity/grant regression passed 20 real-store checks in 26.47 seconds,
including malformed input, service suspension, last-admin/owner safeguards and
revisioned grants. The unit/protocol suite passed 174 tests; Ruff and strict mypy
passed.

Node inspection now follows source/version and unverified filters, with bounded
visible neighborhoods and support-qualified relationship links. Editors load the
current revision for an exact source scope, or an owner explicitly selects a
public override. Typed node/relation fields submit immutable pending revisions;
accepted fields remain unchanged until publication. History renders escaped
before/after values and provenance, distinguishes pending from accepted work,
and lets owners append compensating rollback commands under current authority.
The five-case curation/scope/read-fence/competing-publication gate passed in
221.99 seconds. The unit/protocol suite passed 174 cases in 6.54 seconds, and
Ruff/strict mypy passed. Docker hard limits remained 1.476 GB with swap disabled.
Identity/visibility previews and the operator console are still being added.

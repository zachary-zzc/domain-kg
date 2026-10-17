# Administration console

The session-bound shell at `/admin` uses the configured external OIDC login.
It lists authorized KGs, accepted/retained materials, scoped search results and
source/manual citations, team/role management, curation/history and owner/operator operations. No local
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

Identity and visibility forms now preview the actual typed merge/split/suppression
impact before submission. Merge/split retains explicit original IDs, disjoint
partitions and each original's revision guard. Visibility restoration is available
from authorized suppressed history; public changes require an owner. These
metadata/visibility operations and relationship tags require no model calls.
History's explicit `include_unverified` opt-in is shared by HTTP and MCP and bound
to its cursor, while default imported facts remain excluded.

Owner operations measure graph/vector readiness against accepted receipts and
queue real source repair jobs. The separate operator view lists registered backend
IDs and performs selected capability checks with current CSRF and operator access;
it displays no connection strings, credentials or KG evidence. The four-case
import/identity/visibility/operator/history regression passed in 220.59 seconds.
The unit/protocol suite passed 181 cases in 5.49 seconds; Ruff and strict mypy passed.

Browser forms now stop consuming the request stream at the 64 KiB ceiling instead
of buffering oversized bodies first. Invalid encodings fail before consuming the
body. The bounded-reader and existing form regression passed 13 checks in 0.06
seconds. Owner readiness also includes accepted unverified imports; checking their
projection integrity does not change their fact status.

The repeatable Task 17 laptop gate is:

```bash
uv run python scripts/smoke.py --task 17 --keep-services
```

It uses synthetic fixtures and the same four capped databases, with no added
model or frontend service. `tests/evaluation/test_console_flows.py` exercises five
current roles, including an organization administrator without a KG grant and an
operator without KG access. `tests/evaluation/test_console_browser.py` is an
explicitly opt-in temporary browser fixture: it runs only with
`DOMAIN_KG_BROWSER_PREVIEW=1`, binds loopback port 28080, uses synthetic namespaces
and real session records, and disappears with the pytest process. Its helper
routes are not part of the installed application. It does not establish real
external OIDC acceptance.


The complete Task 17 runner passed 43 checks in 354.84 seconds; 183 unit/protocol
checks passed in 5.60 seconds. Ruff and strict mypy passed. The real laptop browser
verified catalog, keyboard search, source citation navigation, source-qualified
node/edit forms, owner readiness, administrator controls, reader denials and
logout followed by Back. Tab first focused the visible skip link, whose activation
moved focus to the main content. The optional browser file-picker walkthrough
remains unverified: a long browser interaction outlasted the first fixture, and
the controller then timed out on recovery. That fixture reported a timeout
failure; its corrected restart stopped cleanly. API upload, checksum verification
and publication passed their integration flow. No real external OIDC browser
acceptance is claimed.

Owner operations now include retained backup admission with an accepted-revision
check and bounded retention. A completed backup job links to manifest/part
inspection and current-owner-only attachment downloads; damaged bytes are denied
before HTTP success. Run the model-free native worker with `--backup-only`.
The focused backup/operations/jobs/worker regression passed four checks in
55.68 seconds. The synthetic preview also completed a real tiny queued backup
through its dedicated backup worker, without a model call. Restore remains pending.

Owners can also queue a scoped JSONL, CSV or GraphML export from operations.
Material selection constrains every field; imported-unverified evidence requires
an explicit checkbox. A completed export job links to the scope summary and a
checksum-verified attachment. The native `--export-only` worker calls no model.
Access, visibility and publication changes invalidate older files. The dedicated
export/backup/owner transport regression passed four checks in 42.33 seconds;
source filtering, withdrawal and import trust were verified separately.

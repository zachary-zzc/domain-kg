# Packaging and deployment preparation

The application image is now built and verified locally on Linux/arm64. External
CI, image publication, deployment and cross-environment migration remain deferred
until the operator selects that environment. Keep the laptop databases on the
existing capped Compose services.

## Reproducible inputs

`uv.lock` pins application/development dependencies. `requirements.lock` is its
hashed runtime-only export; regenerate it with:

```sh
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

The build backend is pinned to Hatchling 1.32.4. Build on an appropriate build host:

```sh
uv build
uv run --no-sync python scripts/check_distribution.py dist
```

The checker verifies the wheel's entry point, templates and static assets, rejects
excluded/private paths, and prints artifact hashes. The source archive uses an
explicit allowlist; developer logs and local configuration are excluded. Keep
exactly one current wheel/source pair in the checked output directory.

`Dockerfile` pins the official multi-platform Python 3.12 slim Bookworm image by
registry digest. Its build installs hashed binary dependencies and the already
checked application wheel. The runtime uses UID/GID 10001, one API process and no
embedded database or model. `.dockerignore` permits only the Dockerfile, runtime
lock and wheel into the build context. A release operator can build it with
`docker build -t domain-kg:local .` after producing `dist/`. The image downloads
and hash-validates its pinned cl100k vocabulary during construction; source
chunking requires no runtime vocabulary download. The ASGI factory creates
server-owned settings explicitly. Update the image digest and dependency locks
deliberately, then repeat these checks:

```sh
uv build --offline
uv run --no-sync python scripts/check_distribution.py dist
docker build --memory=1g --cpu-quota=100000 -t domain-kg:local .
uv run --no-sync python scripts/check_image.py domain-kg:local --output .cache/image-report.json
```

`check_image.py` exercises the image's actual default command using synthetic
inputs streamed over stdin. Its container has no external network, a read-only
root, UID/GID 10001, no capabilities, no-new-privileges, 64 PIDs, one CPU, a
512-MiB memory/swap ceiling and a 128-MiB temporary filesystem. It verifies
offline bilingual tokenization, sandboxed text and native two-page PDF parsing
with exact citations, console assets, the worker CLI, HTTP liveness and a 401
Bearer challenge from unauthenticated MCP. No private files are mounted and the
probe/testing code is absent from the wheel. The first measured local pass used
image `sha256:393621c88ad6357727f4257c3c7170639287746480e6c6c50e3cec50a72de1fd`;
rebuild after application changes rather than treating that digest as current.

The current local artifact is built from application revision `02a23d8` and
passed the same isolation probe. Its immutable image ID is
`sha256:e328ab790487098116577cc986ae5558ee9020e483001b186173b6b51e045738`.
The ignored `.cache/domain-kg-local-image.tar` archive was exported and actually
loaded again with Docker. Archive SHA256:
`8627514187a312d7e9faa2a6ea590da1a93d501fdb915f70c36ba1975aa49c70`.
The runtime report is `.cache/image-report.json`; `.cache/local-release.json`
records the completed current-configuration local checks on 19 October 2026
(Asia/Shanghai). Later commits change documentation and a test observer only;
their application source tree matches image revision `02a23d8`. The manifest
records the audited test partitions and separately enabled evaluation, physical
restore/repair and browser checks. External deployment remains deferred.

The GitHub workflow pins official checkout/setup-python action commits and uv
0.10.7. It checks Ruff, mypy, unit/protocol behavior, runtime-lock drift and package
contents. It has read-only repository permission and no provider keys, database
fixtures, image publication or deployment steps. It has not run on a remote host.

## Runtime roles and credentials

Use the same image for API and workers. Override its command with, for example,
`python -m domain_kg.worker --maintenance-only` or `--operations-only`; disable the
API-specific container healthcheck for worker-only containers. Choose one worker
role at a time on the laptop. Backups/exports and curation repair need no model.
KG restore uses `--kg-restore-only`, current owner approval and a separately
approved target provider configuration. `--restore-only` is material restoration.

Supply production settings through protected secret references/environment and
mount provider JSON read-only outside the image and Git. Do not copy the laptop
`.env`, development credentials, model files, originals or generated indexes into
an image. Use HTTPS and an external verified OIDC issuer; the preview's fixture
login routes are test code and never part of the application wheel.

Use separate bootstrap/operator and runtime server credentials:

- MongoDB needs a replica set for authority/job/publication transactions. Scope
  runtime access to the registered control/authority databases and their required
  collections. Startup and provisioning perform index/validation setup; account
  permissions must cover those specific operations. Do not supply server-wide
  administrator or user-management credentials to ordinary API/worker processes.
- Neo4j access belongs to the registered graph database. Startup creates scoped
  constraints; allow that schema setup only in the approved database. No database
  creation, unrelated database access or server administration is required by
  normal graph queries.
- Qdrant credentials and network access must cover the registered deployment and
  explicitly approved collection provisioning/read/write operations. Keep keys
  away from clients and unrelated deployments.
- S3 credentials must cover only approved buckets and source/retained prefixes.
  Provisioning and physical cleanup need separately reviewed permissions;
  versioned cleanup requires listing/deleting versions and aborting multipart
  uploads. Never grant cross-account storage administration to the service.

Backend editions and permission policies must actually support these restrictions;
review the final accounts during deployment acceptance. Application policy and
namespace validation remain required even with scoped backend credentials.

## Health and recovery

`/healthz` is process liveness. It is not a promise that every KG/backend is ready.
Use the authenticated owner projection-status endpoint and an authorized tiny
read/write/recovery drill for serving readiness. Keep public health responses free
of source fields, hidden KG counts and connections.

Use a read-only container root filesystem, dropped capabilities, restricted
networks and a bounded temporary filesystem on the deployment host. Set explicit
API/worker memory and CPU limits there. The laptop database limits remain the
separate 1.5 GB budget documented in `config/laptop-smoke.json`; these artifacts do
not add services or increase that budget.

Restart durable workers after an interruption and use authorized job retry rather
than editing job/manifests to ready. MongoDB is the acceptance boundary; no
transaction spans MongoDB, Neo4j, Qdrant and S3. Retain the old accepted generation
while preparing replacements. Restore into a fresh inactive namespace and grant
only the current approved owner after verification. Failed target copies retain
their original-storage reservation until physical cleanup; do not manually refund
it while files remain. The full physical-profile restore drill, generation
migration and retention orchestration are still open release gates.

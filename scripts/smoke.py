"""Run small laptop checks; never prune Docker data or resize its VM."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ["mongo", "neo4j", "qdrant", "object-store"]
MEMORY_BUDGET_BYTES = 1_500_000_000
TASK_FILES = {
    15: [
        "tests/unit/test_smoke_budget.py",
        "tests/protocol",
        "tests/integration/test_grant_management.py",
        "tests/integration/test_material_acl_management.py",
        "tests/integration/test_projection_status.py",
        "tests/integration/test_transport_authorization.py",
        "tests/integration/test_citation_resolution.py",
        "tests/integration/test_material_inspection.py",
        "tests/integration/test_material_chunk_visibility.py",
        "tests/integration/test_identity.py",
    ],
    14: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_identity_commands.py",
        "tests/unit/test_identity_partitions.py",
        "tests/unit/test_identity_graph_views.py",
        "tests/unit/test_identity_evidence.py",
        "tests/unit/test_identity_history.py",
        "tests/unit/test_identity_masks.py",
        "tests/unit/test_identity_manual_types.py",
        "tests/integration/test_material_removal.py",
        "tests/integration/test_curation.py",
        "tests/integration/test_incremental_changes.py",
        "tests/integration/test_identity_curation.py",
        "tests/integration/test_identity_search.py",
        "tests/integration/test_identity_lifecycle.py",
        "tests/integration/test_identity_recovery.py",
    ],
    13: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_fusion.py",
        "tests/unit/test_model_windows.py",
        "tests/unit/test_embeddings.py",
        "tests/unit/test_provider_extraction.py",
        "tests/integration/test_model_windows_pipeline.py",
        "tests/integration/test_federated_search.py",
    ],
    12: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_embedding_space_contracts.py",
        "tests/unit/test_extraction_failures.py",
        "tests/unit/test_embeddings.py",
        "tests/integration/test_embedding_spaces.py",
        "tests/integration/test_vector_filters.py",
        "tests/integration/test_lexical_retrieval.py",
        "tests/integration/test_vector_scope_filters.py",
    ],
    11: [
        "tests/unit/test_smoke_budget.py",
        "tests/integration/test_graph_retrieval.py",
        "tests/integration/test_visible_entity_fields.py",
        "tests/integration/test_material_grants.py",
    ],
    10: [
        "tests/unit/test_blob_memory.py",
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_embeddings.py",
        "tests/unit/test_projection_ids.py",
        "tests/integration/test_publication.py",
        "tests/integration/test_version_races.py",
        "tests/integration/test_lightrag_scope_probe.py::test_real_lightrag_qdrant_upsert_requires_flush[server]",
    ],
    9: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_extraction_validation.py",
        "tests/unit/test_provider_extraction.py",
        "tests/integration/test_entity_resolution.py",
    ],
    8: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_chunking.py",
        "tests/integration/test_parsers.py",
    ],
    7: [
        "tests/unit/test_smoke_budget.py",
        "tests/integration/test_job_recovery.py",
        "tests/integration/test_catalog.py::test_approved_qdrant_connection_ignores_ambient_proxy",
        "tests/integration/test_catalog.py::test_backend_compatibility_probe_uses_the_approved_transport",
        "tests/integration/test_catalog.py::test_direct_compatibility_check_rejects_incompatible_server",
    ],
    6: [
        "tests/unit/test_smoke_budget.py",
        "tests/integration/test_uploads.py",
        "tests/integration/test_material_submission.py",
        "tests/integration/test_identity.py::test_material_http_stream_is_private_and_uses_shared_service",
    ],
    5: [
        "tests/unit/test_smoke_budget.py",
        "tests/integration/test_catalog.py",
        "tests/integration/test_database_routing.py",
        "tests/integration/test_identity.py::test_catalog_http_uses_same_policy_services",
    ],
    4: [
        "tests/unit/test_smoke_budget.py",
        "tests/integration/test_policy_scopes.py",
        "tests/integration/test_material_grants.py",
        "tests/integration/test_lightrag_scope_probe.py",
    ],
    1: [
        "tests/unit/test_smoke_budget.py",
        "tests/protocol/test_transport_smoke.py",
        "tests/integration/test_storage_smoke.py",
        "tests/integration/test_lightrag_scope_probe.py",
    ],
    2: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_contracts.py",
        "tests/integration/test_namespace_constraints.py",
    ],
    3: [
        "tests/unit/test_smoke_budget.py",
        "tests/integration/test_identity.py",
        "tests/protocol/test_auth_discovery.py",
    ],
}


def run(args: list[str], env: dict[str, str]) -> None:
    print("+ " + " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def check_docker_space() -> None:
    subprocess.run(["docker", "info"], check=True, capture_output=True, text=True)
    context = subprocess.check_output(["docker", "context", "show"], text=True).strip()
    if context.startswith("colima") and shutil.which("colima"):
        output = subprocess.check_output(
            ["colima", "ssh", "--", "df", "-Pk", "/var/lib/containerd"], text=True
        )
        available_kib = int(output.splitlines()[-1].split()[3])
        if available_kib < 4 * 1024 * 1024:
            raise RuntimeError(
                f"Docker VM has {available_kib / 1024:.0f} MiB free; allow at least 4 GiB "
                "for smoke images and data. Free Docker space, then rerun. "
                "No images or data were removed."
            )


def validate_memory_limits(limits: list[tuple[int, int]]) -> int:
    if any(memory <= 0 or swap != memory for memory, swap in limits):
        raise RuntimeError("Every smoke service needs a hard RAM limit and disabled swap.")
    total = sum(memory for memory, _ in limits)
    if total > MEMORY_BUDGET_BYTES:
        raise RuntimeError(f"Smoke memory limits total {total} bytes, above the 1.5 GB budget.")
    return total


def check_memory_budget(*, running: bool = False) -> None:
    if running:
        ids = subprocess.check_output(
            ["docker", "compose", "ps", "--quiet"], cwd=ROOT, text=True
        ).split()
        if len(ids) != len(SERVICES):
            raise RuntimeError("The smoke stack requires all four services running.")
        containers = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
        limits = [(c["HostConfig"]["Memory"], c["HostConfig"]["MemorySwap"]) for c in containers]
    else:
        config = json.loads(
            subprocess.check_output(
                ["docker", "compose", "config", "--format", "json"], cwd=ROOT, text=True
            )
        )
        limits = [
            (
                int(config["services"][s].get("mem_limit", 0)),
                int(config["services"][s].get("memswap_limit", -1)),
            )
            for s in SERVICES
        ]
    total = validate_memory_limits(limits)
    label = "Running" if running else "Configured"
    print(f"{label} hard RAM limits: {total / 1_000_000_000:.3f} GB; swap disabled.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, choices=list(TASK_FILES), default=1)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run installed checks that need no database or network",
    )
    parser.add_argument(
        "--keep-services", action="store_true", help="leave started smoke services up"
    )
    args = parser.parse_args()
    env = dict(os.environ)
    env.setdefault("UV_CACHE_DIR", str(ROOT / ".cache" / "uv"))
    env.setdefault("COMPOSE_PARALLEL_LIMIT", "1")
    if args.offline:
        if not (ROOT / ".venv").is_dir():
            parser.error("Run uv sync --locked first to prepare the pinned environment.")
        env["UV_OFFLINE"] = "1"
        run(["uv", "run", "--locked", "--no-sync", "ruff", "check", "."], env)
        run(["uv", "run", "--locked", "--no-sync", "mypy", "src/domain_kg"], env)
        run(
            [
                "uv",
                "run",
                "--locked",
                "--no-sync",
                "pytest",
                *TASK_FILES[args.task],
                "-m",
                "not storage",
                "-q",
            ],
            env,
        )
        print("Offline checks passed. Real-store integration gates remain separate.")
        return 0

    check_docker_space()
    run([sys.executable, "scripts/dev_env.py"], env)
    check_memory_budget()
    run(["uv", "sync", "--locked"], env)
    previous = set(
        subprocess.check_output(
            ["docker", "compose", "ps", "--services", "--status", "running"],
            cwd=ROOT,
            text=True,
        ).split()
    )
    started = [name for name in SERVICES if name not in previous]
    print("Smoke budget: at most 1.5 GB RAM / 2.5 CPU cores; tiny synthetic fixtures.")
    try:
        run(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                *SERVICES,
            ],
            env,
        )
        run(["uv", "run", "--locked", "--no-sync", "python", "scripts/init_replica_set.py"], env)
        check_memory_budget(running=True)
        options = ["--run-physical"] if args.task == 5 else []
        run(
            [
                "uv",
                "run",
                "--locked",
                "--no-sync",
                "pytest",
                *TASK_FILES[args.task],
                *options,
                "-q",
            ],
            env,
        )
    finally:
        if started and not args.keep_services:
            run(["docker", "compose", "stop", *started], env)
    print(f"Task {args.task} smoke passed against real local stores.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Smoke blocked: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

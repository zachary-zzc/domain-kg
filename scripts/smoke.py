"""Run small laptop checks; never prune Docker data or resize its VM."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ["mongo", "neo4j", "qdrant", "object-store"]
MEMORY_BUDGET_BYTES = 1_500_000_000
QUICK_FILES = [
    "tests/unit/test_smoke_budget.py",
    "tests/unit/test_laptop_profile.py",
    "tests/unit/test_model_windows.py",
    "tests/integration/test_storage_smoke.py",
]
TASK_FILES = {
    17: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_console_forms.py",
        "tests/unit/test_bounded_console_forms.py",
        "tests/unit/test_node_action_forms.py",
        "tests/unit/test_job_pages.py",
        "tests/integration/test_console.py",
        "tests/integration/test_console_workspace.py",
        "tests/integration/test_console_jobs.py",
        "tests/integration/test_console_material_commands.py",
        "tests/integration/test_console_restoration.py",
        "tests/integration/test_material_management.py",
        "tests/integration/test_console_team.py",
        "tests/integration/test_console_curation.py",
        "tests/integration/test_console_import.py",
        "tests/integration/test_console_node_actions.py",
        "tests/integration/test_console_operations.py",
        "tests/integration/test_import_projection_status.py",
        "tests/integration/test_curation_inspection.py",
        "tests/evaluation/test_console_flows.py",
    ],
    16: [
        "tests/unit/test_smoke_budget.py",
        "tests/unit/test_legacy_formats.py",
        "tests/unit/test_import_build.py",
        "tests/unit/test_import_origin.py",
        "tests/unit/test_prepared_import.py",
        "tests/unit/test_import_evidence.py",
        "tests/unit/test_import_requests.py",
        "tests/unit/test_engine_capabilities.py",
        "tests/integration/test_legacy_import.py",
        "tests/integration/test_import_admission_races.py",
        "tests/integration/test_import_worker.py",
        "tests/integration/test_import_http.py",
        "tests/integration/test_import_recovery.py",
        "tests/integration/test_import_curation.py",
        "tests/integration/test_import_mappings.py",
        "tests/integration/test_import_purge.py",
        "tests/integration/test_unpublished_import_restore.py",
        "tests/integration/test_lightrag_capabilities.py",
        "tests/integration/test_lightrag_scope_probe.py",
    ],
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


def load_laptop_profile(path: Path = ROOT / "config/laptop-smoke.json") -> dict[str, Any]:
    """Operator-owned local budgets; invalid profiles fail before starting processes."""
    try:
        profile = json.loads(path.read_bytes())
        if profile["version"] != 1:
            raise ValueError
        docker, tests, native = (profile[key] for key in ("docker", "tests", "native"))

        def bounded(area: dict[str, Any], key: str, minimum: int, maximum: int) -> None:
            if type(area[key]) is not int or not minimum <= area[key] <= maximum:
                raise ValueError

        bounded(docker, "memory_cap_bytes", 1, MEMORY_BUDGET_BYTES)
        if set(docker["service_memory_bytes"]) != set(SERVICES):
            raise ValueError
        for service in SERVICES:
            bounded(docker["service_memory_bytes"], service, 1, docker["memory_cap_bytes"])
        if sum(docker["service_memory_bytes"].values()) > docker["memory_cap_bytes"]:
            raise ValueError
        if docker["swap_enabled"] is not False or not 0 < docker["cpu_cap_cores"] <= 2.5:
            raise ValueError
        if tests["provider_mode"] != "synthetic" or any(
            tests[key] is not False
            for key in ("auto_download_models", "ocr_enabled", "load_test_enabled")
        ):
            raise ValueError
        bounded(tests, "parallel_workers", 1, 1)
        bounded(tests, "quick_timeout_seconds", 1, 180)
        bounded(tests, "task_timeout_seconds", 1, 1800)
        bounded(tests, "preview_seconds", 1, 900)
        if type(tests.get("preview_restore_demo", False)) is not bool:
            raise ValueError
        if native["autostart"] is not False or native["stop_owned_process_after_smoke"] is not True:
            raise ValueError
        for field, minimum, maximum in (
            ("rss_cap_per_process_bytes", 1, 1_250_000_000),
            ("max_concurrent_models", 1, 1),
            ("threads", 1, 2),
            ("parallel_slots", 1, 1),
            ("gpu_layers", 0, 0),
            ("prompt_cache_mib", 0, 0),
            ("startup_timeout_seconds", 1, 90),
            ("request_timeout_seconds", 1, 30),
            ("chat_context_tokens", 1024, 4096),
            ("chat_tiny_requests", 1, 2),
            ("chat_tiny_max_input_tokens", 1, 512),
            ("chat_tiny_max_output_tokens", 1, 128),
            ("embedding_context_tokens", 256, 2048),
            ("embedding_tiny_requests", 3, 3),
            ("embedding_bilingual_requests", 6, 6),
            ("embedding_dimensions", 1024, 1024),
            ("embedding_batch_tokens", 1, 2048),
            ("embedding_microbatch_tokens", 1, 2048),
            ("chat_batch_tokens", 1, 128),
            ("chat_microbatch_tokens", 1, 32),
        ):
            bounded(native, field, minimum, maximum)
        if not 0.1 <= native["rss_sample_interval_seconds"] <= 1:
            raise ValueError
        if native["model_manifest"] != "config/model-runtime.proposed.json":
            raise ValueError
        return dict(profile)
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError("Invalid laptop profile; no services or models were started.") from None


def run(args: list[str], env: dict[str, str], *, timeout_seconds: int = 1800) -> None:
    print("+ " + " ".join(args), flush=True)
    # Terminate only this owned process group, including an uv-spawned pytest child.
    process = subprocess.Popen(args, cwd=ROOT, env=env, start_new_session=True)
    try:
        result = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        raise RuntimeError(
            "The smoke deadline expired; its owned process group was stopped."
        ) from None
    if result:
        raise subprocess.CalledProcessError(result, args)


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


def validate_memory_limits(
    limits: list[tuple[int, int]], *, budget_bytes: int = MEMORY_BUDGET_BYTES
) -> int:
    if any(memory <= 0 or swap != memory for memory, swap in limits):
        raise RuntimeError("Every smoke service needs a hard RAM limit and disabled swap.")
    total = sum(memory for memory, _ in limits)
    if total > min(budget_bytes, MEMORY_BUDGET_BYTES):
        raise RuntimeError(f"Smoke memory limits total {total} bytes, above the 1.5 GB budget.")
    return total


def check_memory_budget(*, running: bool = False, profile: dict[str, Any] | None = None) -> None:
    profile = profile or load_laptop_profile()
    if running:
        ids = subprocess.check_output(
            ["docker", "compose", "ps", "--quiet"], cwd=ROOT, text=True
        ).split()
        if len(ids) != len(SERVICES):
            raise RuntimeError("The smoke stack requires all four services running.")
        containers = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True))
        actual = {
            c["Config"]["Labels"]["com.docker.compose.service"]: c["HostConfig"]["Memory"]
            for c in containers
        }
        cpus = sum(c["HostConfig"]["NanoCpus"] / 1e9 for c in containers)
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
        actual = {s: int(config["services"][s].get("mem_limit", 0)) for s in SERVICES}
        cpus = sum(float(config["services"][s].get("cpus", 0)) for s in SERVICES)
    if actual != profile["docker"]["service_memory_bytes"]:
        raise RuntimeError("Docker service memory differs from config/laptop-smoke.json.")
    if not 0 < cpus <= profile["docker"]["cpu_cap_cores"]:
        raise RuntimeError("Docker CPU limits differ from the laptop budget.")
    total = validate_memory_limits(limits, budget_bytes=profile["docker"]["memory_cap_bytes"])
    label = "Running" if running else "Configured"
    print(f"{label} hard RAM limits: {total / 1_000_000_000:.3f} GB; swap disabled.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, choices=list(TASK_FILES), default=1)
    parser.add_argument("--profile", type=Path, default=ROOT / "config/laptop-smoke.json")
    parser.add_argument(
        "--quick", action="store_true", help="tiny budget/window/store checks; not a full task gate"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run installed checks that need no database or network",
    )
    parser.add_argument(
        "--keep-services", action="store_true", help="leave started smoke services up"
    )
    args = parser.parse_args()
    profile = load_laptop_profile(args.profile)
    selected = QUICK_FILES if args.quick else TASK_FILES[args.task]
    deadline = profile["tests"]["quick_timeout_seconds" if args.quick else "task_timeout_seconds"]
    env = dict(os.environ)
    # Ordinary smoke never opts into a live provider or the interactive preview.
    env.pop("DOMAIN_KG_BROWSER_PREVIEW", None)
    env["PYTEST_ADDOPTS"] = ""
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
                *selected,
                "-m",
                "not storage",
                "-q",
            ],
            env,
            timeout_seconds=deadline,
        )
        print("Offline checks passed. Real-store integration gates remain separate.")
        return 0

    check_docker_space()
    run([sys.executable, "scripts/dev_env.py"], env)
    check_memory_budget(profile=profile)
    run(["uv", "sync", "--locked"], env)
    run(["uv", "run", "--locked", "--no-sync", "ruff", "check", "."], env)
    run(["uv", "run", "--locked", "--no-sync", "mypy", "src/domain_kg"], env)
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
        check_memory_budget(running=True, profile=profile)
        options = ["--run-physical"] if args.task == 5 and not args.quick else []
        run(
            [
                "uv",
                "run",
                "--locked",
                "--no-sync",
                "pytest",
                *selected,
                *options,
                "-q",
            ],
            env,
            timeout_seconds=deadline,
        )
    finally:
        if started and not args.keep_services:
            run(["docker", "compose", "stop", *started], env)
    label = "Quick resource smoke" if args.quick else f"Task {args.task} smoke"
    print(f"{label} passed against real local stores; synthetic providers, zero live model calls.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Smoke blocked: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

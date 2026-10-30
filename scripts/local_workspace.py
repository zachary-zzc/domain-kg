"""Configure and run the persistent local browser, worker and authenticated MCP service."""

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from uuid import UUID

import httpx
from pymongo import AsyncMongoClient

from domain_kg.app import create_app
from domain_kg.catalog.backends import BackendRegistry, approved_from_settings, invite_operator
from domain_kg.catalog.models import CreateKG, RegisterBackend, StorageProfile
from domain_kg.catalog.service import CatalogService
from domain_kg.identity.models import Action, Principal
from domain_kg.identity.policy import Policy
from domain_kg.identity.repository import IdentityRepository
from domain_kg.models import Namespace
from domain_kg.providers.profiles import ProviderRegistry
from domain_kg.providers.service import ProviderService
from domain_kg.settings import Settings
from domain_kg.storage.router import KGStoreRouter
from scripts.configure_alibaba import configuration, credential
from scripts.local_identity import (
    ISSUER,
    ORIGIN,
    PRIVATE,
    ROOT,
    SCOPES,
    access_token,
    login_owner,
    write_private,
)
from scripts.local_identity import prepare as prepare_identity
from scripts.smoke import SERVICES, check_docker_space, check_memory_budget

COMPOSE = ["docker", "compose"]
IDP_COMPOSE = ["docker", "compose", "-f", "compose.browser.yaml"]
ENV_FILE = ROOT / ".env.browser"


def environment() -> dict[str, str]:
    # Explicit isolated database; ordinary .env secrets remain read by Settings.
    return dict(os.environ) | {
        "DOMAIN_KG_PUBLIC_URL": ORIGIN,
        "DOMAIN_KG_OIDC_ISSUER": ISSUER,
        "DOMAIN_KG_OIDC_CLIENT_ID": "domain-kg-console",
        "DOMAIN_KG_MONGO_DATABASE": "domain_kg_browser",
        "DOMAIN_KG_EMBEDDING_DIMENSIONS": "1024",
        "DOMAIN_KG_PROVIDER_CONFIG": str(PRIVATE / "providers.json"),
    }


def stores() -> None:
    check_docker_space()
    check_memory_budget()
    subprocess.run(
        [*COMPOSE, "up", "-d", "--wait", "--wait-timeout", "120", *SERVICES],
        cwd=ROOT,
        check=True,
    )
    subprocess.run([sys.executable, "scripts/init_replica_set.py"], cwd=ROOT, check=True)
    subprocess.run([*IDP_COMPOSE, "up", "-d", "keycloak"], cwd=ROOT, check=True)
    # Validate the original four-service cap separately from the opt-in IdP cap.
    check_memory_budget(running=True)
    ids = subprocess.check_output([*IDP_COMPOSE, "ps", "-q", "keycloak"], text=True).strip()
    spec = json.loads(subprocess.check_output(["docker", "inspect", ids]))[0]
    if spec["HostConfig"]["Memory"] != 512 * 1024 * 1024 or (
        spec["HostConfig"]["MemorySwap"] != spec["HostConfig"]["Memory"]
    ):
        raise RuntimeError("The identity provider must retain its 512 MiB cap with no swap.")
    deadline = time.monotonic() + 120
    with httpx.Client(timeout=3, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                if client.get(ISSUER + "/.well-known/openid-configuration").is_success:
                    return
            except httpx.TransportError:
                pass  # The process is still starting; the deadline is authoritative.
            time.sleep(1)
    raise RuntimeError("Keycloak did not become ready. Inspect docker compose logs keycloak.")


async def provision(identity: dict[str, str]) -> dict[str, str]:
    # Verify the real provider before admitting its explicit owner and service subjects.
    await access_token(identity)
    settings = Settings()
    mongo: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000
    )
    database = mongo[settings.mongo_database]
    repository = IdentityRepository(database)
    policy = Policy(database)
    approved = approved_from_settings(settings)
    registry = BackendRegistry(policy, approved)
    router = KGStoreRouter(policy, registry, dimensions=1024)
    try:
        await repository.initialize()
        await policy.initialize()
        await registry.initialize()
        owner = Principal(issuer=ISSUER, subject=identity["owner_subject"])
        existing = await database.members.find_one({"issuer": ISSUER, "subject": owner.subject})
        if existing:
            org = UUID(str(existing["org_id"]))
            member = await repository.require_member(owner, org)
        else:
            if await database.organizations.count_documents({}):
                raise RuntimeError("The browser database is already owned by a different identity.")
            org, member = await repository.bootstrap_organization(owner, "Local workspace")
        await invite_operator(database, ISSUER, owner.subject)
        catalog = CatalogService(policy, registry, router)
        app = create_app(
            settings,
            identity_repository=repository,
            catalog_service=catalog,
            providers=ProviderService(policy, router, ProviderRegistry()),
        )
        async with app.router.lifespan_context(app):
            owner = await login_owner(app, identity)
        ids = {}
        for ref, connection in approved.items():
            access = await policy.resolve_access(owner, [], Action.REGISTER_BACKEND, org_id=org)
            backend = await registry.register(
                access,
                RegisterBackend(kind=connection.kind, secret_ref=ref),
                idempotency_key="browser-backend-" + connection.kind,
            )
            ids[connection.kind] = backend.backend_id
        placement = StorageProfile(
            id=UUID(identity["placement_id"]),
            revision=0,
            allowed_org_ids=frozenset({org}),
            mongo_id=ids["mongo"],
            neo4j_id=ids["neo4j"],
            qdrant_id=ids["qdrant"],
            object_store_id=ids["s3"],
        )
        await registry.register_profile(
            await policy.resolve_access(owner, [], Action.REGISTER_BACKEND, org_id=org),
            placement,
            idempotency_key="browser-placement",
        )
        kg = await catalog.create_kg(
            await policy.resolve_access(owner, [], Action.CREATE_KG, org_id=org),
            CreateKG(
                name="Local source knowledge",
                domain_profile_id=catalog.general_profile_id,
                placement_id=placement.id,
                idempotency_key="browser-kg",
            ),
        )
        key = credential(ROOT / "secrets/dashscope-api-key")
        config = configuration(Namespace(org_id=org, kg_id=kg.kg_id), key)
        providers = ProviderService(policy, router, ProviderRegistry(config))
        await providers.bind(
            await policy.resolve_access(owner, [kg.kg_id], Action.CONFIGURE, org_id=org),
            kg.kg_id,
            config.profiles[0].id,
            expected_revision=0,
            idempotency_key="browser-provider",
        )
        member = await repository.require_member(owner, org)
        service = await repository.admit(
            member,
            Principal(issuer=ISSUER, subject=identity["service_subject"]),
            role="member",
            kind="service",
            scopes=tuple(SCOPES),
            kg_ids=(kg.kg_id,),
            idempotency_key="browser-mcp-service",
        )
        await policy.set_grant(
            await policy.resolve_access(owner, [kg.kg_id], Action.MANAGE_GRANTS, org_id=org),
            kg.kg_id,
            "member",
            service.id,
            "editor",
            idempotency_key="browser-mcp-editor",
        )
        # Browser-managed profiles and KG approvals survive repeated local setup.
        if not (PRIVATE / "providers.json").exists():
            raw = config.model_dump(mode="json")
            raw["connections"][0]["api_key"] = key
            write_private(PRIVATE / "providers.json", json.dumps(raw, indent=2) + "\n")
        result = {
            "org_id": str(org),
            "kg_id": str(kg.kg_id),
            "url": ORIGIN + "/admin",
            "mcp_url": ORIGIN + "/mcp",
        }
        write_private(PRIVATE / "workspace.json", json.dumps(result, indent=2) + "\n")
        overrides = {
            key: value
            for key, value in environment().items()
            if key
            in {
                "DOMAIN_KG_PUBLIC_URL",
                "DOMAIN_KG_OIDC_ISSUER",
                "DOMAIN_KG_OIDC_CLIENT_ID",
                "DOMAIN_KG_MONGO_DATABASE",
                "DOMAIN_KG_EMBEDDING_DIMENSIONS",
                "DOMAIN_KG_PROVIDER_CONFIG",
            }
        }
        write_private(
            ENV_FILE, "".join(key + "=" + value + "\n" for key, value in overrides.items())
        )
        return result
    finally:
        await router.close()
        await mongo.close()


def serve() -> None:
    if not (PRIVATE / "workspace.json").exists():
        raise RuntimeError("Run setup first.")
    stores()
    # Refuse to start workers against an unrelated server already occupying our port.
    import socket

    with socket.socket() as probe:
        # Like Uvicorn, allow restart while the previous server's sockets are in TIME_WAIT.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 8000))
    logs = ROOT / ".cache/local-browser"
    logs.mkdir(parents=True, exist_ok=True)
    children: list[subprocess.Popen[bytes]] = []
    handles = []
    try:
        for name, args in (
            ("api", ["-m", "domain_kg"]),
            ("worker", ["-m", "domain_kg.worker"]),
            ("operations", ["-m", "domain_kg.worker", "--operations-only"]),
        ):
            path = logs / (name + ".log")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            handle = os.fdopen(fd, "ab")
            handles.append(handle)
            children.append(
                subprocess.Popen(
                    [sys.executable, *args],
                    cwd=ROOT,
                    env=environment(),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            )
        deadline = time.monotonic() + 30
        with httpx.Client(timeout=2, trust_env=False) as client:
            while True:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError(
                        f"A workspace process failed during startup. Inspect {logs}."
                    )
                try:
                    if client.get(ORIGIN + "/healthz").is_success:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"The API did not become ready. Inspect {logs}.")
                time.sleep(0.25)
        print(f"Browser: {ORIGIN}/admin\nLogin details: {PRIVATE / 'login.txt'}", flush=True)
        print(
            f"MCP: {ORIGIN}/mcp\nLogs: {logs}\nCtrl+C stops API and workers; data persists.",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError(f"A workspace process exited. Inspect {logs}.")
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        for handle in handles:
            handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "start", "token", "stop-stores"))
    args = parser.parse_args()
    os.chdir(ROOT)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    os.environ.update(environment())
    if args.action == "setup":
        credential(ROOT / "secrets/dashscope-api-key")
        identity = prepare_identity()
        stores()
        result = asyncio.run(provision(identity))
        print(f"Configured {result['url']} with Alibaba 35B. No materials were submitted.")
        print(f"Login details: {PRIVATE / 'login.txt'}")
    elif args.action == "start":

        def interrupted(*_: object) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        try:
            serve()
        except KeyboardInterrupt:
            print("API and workers stopped. Stored materials are retained.")
    elif args.action == "token":
        identity = json.loads((PRIVATE / "identity.json").read_text())
        token = asyncio.run(access_token(identity))
        path = PRIVATE / "mcp-token.txt"
        write_private(path, token + "\n")
        print(f"Token file: {path}\nExpires in five minutes. Load it in the Test MCP page.")
    else:
        subprocess.run([*COMPOSE, "stop", *SERVICES], cwd=ROOT, check=True)
        subprocess.run([*IDP_COMPOSE, "stop", "keycloak"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()

"""Operator registration resolves only connection references approved by the server."""

import asyncio
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import boto3
from botocore.config import Config
from neo4j import AsyncGraphDatabase
from pydantic import SecretStr
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.catalog.models import BackendCapabilities, RegisterBackend, StorageProfile
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.models import FrozenModel, PageRequest
from domain_kg.settings import Settings
from domain_kg.storage.qdrant import open_qdrant


class PrivateConnection(FrozenModel):
    """Server configuration only. Never used as an HTTP or MCP request schema.

    Initial profiles use logical databases on the authority replica set, allowing
    policy and publication transactions across those databases on one client.
    Graph/vector/object endpoints can be physically independent.
    """

    kind: Literal["mongo", "neo4j", "qdrant", "s3"]
    endpoint: str = ""
    username: str = ""
    credential: SecretStr = SecretStr("")
    database: str = ""
    bucket: str = "domain-kg-originals"


def s3_client(connection: PrivateConnection) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=connection.endpoint,
        aws_access_key_id=connection.username,
        aws_secret_access_key=connection.credential.get_secret_value(),
        region_name="us-east-1",
        config=Config(
            proxies={},
            connect_timeout=5,
            read_timeout=5,
            retries={"max_attempts": 1},
            max_pool_connections=2,
        ),
    )


def approved_from_settings(settings: Settings) -> dict[UUID, PrivateConnection]:
    if settings.backend_config is not None:
        try:
            with settings.backend_config.open("rb") as config_file:
                data = config_file.read(65537)
            if len(data) > 65536:
                raise ValueError("configuration is too large")
            return {
                UUID(key): PrivateConnection.model_validate(value)
                for key, value in json.loads(data).items()
            }
        except Exception:
            raise ValueError("Invalid private backend configuration.") from None
    defaults = [
        PrivateConnection(kind="mongo", database=settings.mongo_database),
        PrivateConnection(
            kind="neo4j",
            endpoint=settings.neo4j_uri,
            username=settings.neo4j_user,
            credential=settings.neo4j_password,
        ),
        PrivateConnection(
            kind="qdrant", endpoint=settings.qdrant_url, credential=settings.qdrant_api_key
        ),
        PrivateConnection(
            kind="s3",
            endpoint=settings.s3_endpoint,
            username=settings.s3_access_key,
            credential=settings.s3_secret_key,
        ),
    ]
    return {
        uuid5(NAMESPACE_URL, f"domain-kg/connection/default/{connection.kind}"): connection
        for connection in defaults
    }


class RegistryView(FrozenModel):
    backend_id: UUID
    kind: Literal["mongo", "neo4j", "qdrant", "s3"]
    secret_ref: UUID


async def invite_operator(
    database: AsyncDatabase[dict[str, Any]], issuer: str, subject: str
) -> None:
    """Offline server provisioning hook, never a remote login or API operation."""

    async def transact(session: AsyncClientSession) -> None:
        member = await database.members.find_one(
            {"issuer": issuer, "subject": subject, "kind": "human", "suspended": False},
            session=session,
        )
        if member is None:
            raise inaccessible_reference()
        await database.platform_operators.update_one(
            {"issuer": issuer, "subject": subject},
            {"$set": {"active": True}},
            upsert=True,
            session=session,
        )
        await database.organizations.update_one(
            {"_id": member["org_id"]}, {"$inc": {"policy_revision": 1}}, session=session
        )
        await database.identity_audit.insert_one(
            {
                "org_id": member["org_id"],
                "actor_kind": "server_provisioning",
                "operation": "invite_platform_operator",
                "target_id": member["id"],
                "at": datetime.now(UTC),
            },
            session=session,
        )

    async with database.client.start_session() as session:
        await session.with_transaction(transact)


class BackendRegistry:
    def __init__(self, policy: Policy, approved: dict[UUID, PrivateConnection]) -> None:
        self.policy, self.database = policy, policy.database
        self._approved = dict(approved)

    async def initialize(self) -> None:
        await self.database.platform_operators.create_index(
            [("issuer", 1), ("subject", 1)], unique=True
        )
        await self.database.registry_state.update_one(
            {"_id": "placement"}, {"$setOnInsert": {"revision": 0}}, upsert=True
        )
        await self.database.backends.create_index("id", unique=True)
        await self.database.storage_profiles.create_index("id", unique=True)
        await self.database.registry_commands.create_index(
            [("org_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )

    async def _operator(self, access: AccessContext) -> AccessContext:
        return await self.policy.resolve_access(
            access.principal, [], Action.REGISTER_BACKEND, org_id=access.org_id
        )

    async def inventory(
        self,
        access: AccessContext,
        kind: Literal["backends", "approved", "profiles"],
        page: PageRequest,
    ) -> dict[str, Any]:
        current = await self.policy.resolve_access(
            access.principal,
            [],
            Action.LIST_KGS if kind == "profiles" else Action.REGISTER_BACKEND,
            org_id=access.org_id,
        )
        binding = {
            "org_id": str(current.org_id),
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "kind": f"registry:{kind}",
            "revision": current.policy_revision,
            "limit": page.limit,
        }
        after = ""
        if page.cursor:
            cursor = await self.database.identity_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(
                    ErrorCode.CURSOR_EXPIRED, "The inventory cursor expired or its scope changed."
                )
            after = cursor["after"]
        if kind == "approved":
            raw = [
                {"id": str(reference), "kind": connection.kind, "secret_ref": str(reference)}
                for reference, connection in self._approved.items()
                if str(reference) > after
            ]
            raw = sorted(raw, key=lambda row: row["id"])[: page.limit + 1]
        elif kind == "backends":
            raw = (
                await self.database.backends.find({"id": {"$gt": after}})
                .sort("id", 1)
                .limit(page.limit + 1)
                .to_list()
            )
        else:
            raw = (
                await self.database.storage_profiles.find(
                    {"id": {"$gt": after}, "allowed_org_ids": str(current.org_id)}
                )
                .sort("id", 1)
                .limit(page.limit + 1)
                .to_list()
            )
        items = []
        for row in raw[: page.limit]:
            if kind == "backends":
                items.append(
                    RegistryView.model_validate(
                        {
                            "backend_id": row["id"],
                            "kind": row["kind"],
                            "secret_ref": row["secret_ref"],
                        }
                    ).model_dump(mode="json")
                )
            elif kind == "approved":
                items.append({"kind": row["kind"], "secret_ref": row["secret_ref"]})
            else:
                items.append({"id": row["id"], "revision": row["revision"]})
        cursor_token = None
        if len(raw) > page.limit:
            cursor_token = secrets.token_urlsafe(32)
            await self.database.identity_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(cursor_token.encode()).hexdigest(),
                    "after": raw[page.limit - 1]["id"],
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        await self.policy.revalidate(current)
        return {"items": items, "next_cursor": cursor_token}

    async def list_approved(self, access: AccessContext) -> list[dict[str, str]]:
        await self._operator(access)
        return [
            {"kind": connection.kind, "secret_ref": str(reference)}
            for reference, connection in self._approved.items()
        ]

    async def list_backends(self, access: AccessContext) -> list[RegistryView]:
        current = await self._operator(access)
        records = await self.database.backends.find({}).to_list()
        result = [
            RegistryView(
                backend_id=UUID(record["id"]),
                kind=record["kind"],
                secret_ref=UUID(record["secret_ref"]),
            )
            for record in records
        ]
        await self.policy.revalidate(current)
        return result

    async def available_profiles(self, access: AccessContext) -> list[dict[str, Any]]:
        current = await self.policy.resolve_access(
            access.principal, [], Action.LIST_KGS, org_id=access.org_id
        )
        records = await self.database.storage_profiles.find(
            {"allowed_org_ids": str(current.org_id)}
        ).to_list()
        result = [{"id": row["id"], "revision": row["revision"]} for row in records]
        await self.policy.revalidate(current)
        return result

    def _command(self, access: AccessContext, key: str) -> dict[str, str]:
        return {
            "org_id": str(access.org_id),
            "issuer": access.principal.issuer,
            "subject": access.principal.subject,
            "key": key,
        }

    async def connection(self, backend_id: UUID, kind: str) -> PrivateConnection:
        record = await self.database.backends.find_one({"id": str(backend_id), "kind": kind})
        if record is None:
            raise inaccessible_reference()
        connection = self._approved.get(UUID(record["secret_ref"]))
        if connection is None or connection.kind != kind:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "The approved backend configuration is unavailable."
            )
        return connection

    async def probe(self, identifier: UUID, connection: PrivateConnection) -> BackendCapabilities:
        try:
            if connection.kind == "mongo":
                if not connection.database or connection.endpoint:
                    raise ValueError(
                        "Mongo profiles require a database on the authority replica set"
                    )
                hello = await self.database.client.admin.command("hello")
                if not hello.get("setName"):
                    raise ValueError("replica set required")
            elif connection.kind == "neo4j":
                async with AsyncGraphDatabase.driver(
                    connection.endpoint,
                    auth=(connection.username, connection.credential.get_secret_value()),
                    max_connection_pool_size=2,
                    connection_timeout=5,
                ) as driver:
                    await driver.verify_connectivity()
            elif connection.kind == "qdrant":
                client = await open_qdrant(
                    url=connection.endpoint,
                    api_key=connection.credential.get_secret_value(),
                    timeout_seconds=5,
                )
                try:
                    await client.get_collections()
                finally:
                    await client.close()
            else:
                client = s3_client(connection)
                try:
                    await asyncio.to_thread(client.list_buckets)
                finally:
                    client.close()
        except Exception as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "The approved backend failed its capability check.",
                retryable=True,
            ) from exc
        capabilities = {
            "mongo": {"replica_set", "transactions", "scoped_indexes"},
            "neo4j": {"composite_constraints", "source_projections"},
            "qdrant": {"payload_filters", "source_projections"},
            "s3": {"immutable_objects"},
        }
        return BackendCapabilities(
            backend_id=identifier,
            kind=connection.kind,
            healthy=True,
            capabilities=frozenset(capabilities[connection.kind]),
        )

    async def register(
        self, access: AccessContext, command: RegisterBackend, *, idempotency_key: str
    ) -> BackendCapabilities:
        current = await self._operator(access)
        connection = self._approved.get(command.secret_ref)
        if connection is None or connection.kind != command.kind:
            raise inaccessible_reference()
        digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
        key = self._command(current, idempotency_key)
        previous = await self.database.registry_commands.find_one(key)
        if previous:
            if previous["digest"] != digest:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "The idempotency key was used for different input.",
                )
            return BackendCapabilities.model_validate(previous["result"])
        identifier = uuid4()
        capabilities = await self.probe(identifier, connection)

        async def transact(session: AsyncClientSession) -> None:
            await self.policy.resolve_access(
                current.principal,
                [],
                Action.REGISTER_BACKEND,
                org_id=current.org_id,
                session=session,
            )
            await self.database.backends.insert_one(
                {
                    "_id": str(identifier),
                    "id": str(identifier),
                    "kind": command.kind,
                    "secret_ref": str(command.secret_ref),
                    "schema_version": 1,
                },
                session=session,
            )
            await self.database.registry_commands.insert_one(
                key | {"digest": digest, "result": capabilities.model_dump(mode="json")},
                session=session,
            )
            await self.database.organizations.update_one(
                {"_id": str(current.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )
            await self.database.registry_state.update_one(
                {"_id": "placement"}, {"$inc": {"revision": 1}}, session=session
            )

        async with self.database.client.start_session() as session:
            await session.with_transaction(transact)
        return capabilities

    async def register_profile(
        self, access: AccessContext, profile: StorageProfile, *, idempotency_key: str
    ) -> StorageProfile:
        current = await self._operator(access)
        ids = {
            "mongo": profile.mongo_id,
            "neo4j": profile.neo4j_id,
            "qdrant": profile.qdrant_id,
            "s3": profile.object_store_id,
        }
        for kind, identifier in ids.items():
            await self.connection(identifier, kind)
        for organization in profile.allowed_org_ids:
            if await self.database.organizations.find_one({"_id": str(organization)}) is None:
                raise inaccessible_reference()
        key = self._command(current, idempotency_key)
        payload = profile.model_dump(mode="json")
        payload["allowed_org_ids"] = sorted(str(value) for value in profile.allowed_org_ids)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

        async def transact(session: AsyncClientSession) -> None:
            await self.policy.resolve_access(
                current.principal,
                [],
                Action.REGISTER_BACKEND,
                org_id=current.org_id,
                session=session,
            )
            previous = await self.database.registry_commands.find_one(key, session=session)
            if previous:
                if previous["digest"] != digest:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "The idempotency key was used for different input.",
                    )
                return
            for kind, identifier in ids.items():
                if (
                    await self.database.backends.find_one(
                        {"id": str(identifier), "kind": kind}, session=session
                    )
                    is None
                ):
                    raise inaccessible_reference()
            await self.database.storage_profiles.insert_one(
                payload | {"_id": str(profile.id)}, session=session
            )
            await self.database.registry_commands.insert_one(
                key | {"digest": digest, "result": payload}, session=session
            )
            await self.database.organizations.update_one(
                {"_id": str(current.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )
            await self.database.registry_state.update_one(
                {"_id": "placement"}, {"$inc": {"revision": 1}}, session=session
            )

        async with self.database.client.start_session() as session:
            await session.with_transaction(transact)
        return profile

    async def profile(self, identifier: UUID, org_id: UUID) -> StorageProfile:
        raw = await self.database.storage_profiles.find_one(
            {"id": str(identifier), "allowed_org_ids": str(org_id)}
        )
        if raw is None:
            raise inaccessible_reference()
        return StorageProfile.model_validate(
            {key: value for key, value in raw.items() if key != "_id"}
        )

    async def detach_backend(self, access: AccessContext, identifier: UUID) -> None:
        async def transact(session: AsyncClientSession) -> None:
            current = await self.policy.resolve_access(
                access.principal, [], Action.REGISTER_BACKEND, org_id=access.org_id, session=session
            )
            referenced = await self.database.storage_profiles.find_one(
                {
                    "$or": [
                        {field: str(identifier)}
                        for field in ("mongo_id", "neo4j_id", "qdrant_id", "object_store_id")
                    ]
                },
                session=session,
            )
            if referenced:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "A referenced backend cannot be detached."
                )
            await self.database.backends.delete_one({"id": str(identifier)}, session=session)
            await self.database.registry_state.update_one(
                {"_id": "placement"}, {"$inc": {"revision": 1}}, session=session
            )
            await self.database.organizations.update_one(
                {"_id": str(current.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )

        async with self.database.client.start_session() as session:
            await session.with_transaction(transact)

    async def detach_profile(self, access: AccessContext, identifier: UUID) -> None:
        async def transact(session: AsyncClientSession) -> None:
            current = await self.policy.resolve_access(
                access.principal, [], Action.REGISTER_BACKEND, org_id=access.org_id, session=session
            )
            if await self.database.kgs.find_one(
                {"storage_profile_id": str(identifier)}, session=session
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "A referenced storage profile cannot be detached."
                )
            await self.database.storage_profiles.delete_one(
                {"id": str(identifier)}, session=session
            )
            await self.database.registry_state.update_one(
                {"_id": "placement"}, {"$inc": {"revision": 1}}, session=session
            )
            await self.database.organizations.update_one(
                {"_id": str(current.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )

        async with self.database.client.start_session() as session:
            await session.with_transaction(transact)

"""Operator-owned parser policy; callers can change values, never server paths."""

import asyncio
import fcntl
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.ingestion.models import ParseOptions
from domain_kg.models import FrozenModel, NonNegative


class ParserConfiguration(FrozenModel):
    revision: NonNegative = 0
    options: ParseOptions = ParseOptions()


def unavailable() -> DomainError:
    return DomainError(
        ErrorCode.VALIDATION_ERROR, "Parser configuration is invalid or unavailable."
    )


class ParserConfigFile:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def read(self) -> ParserConfiguration:
        if self.path is None:
            return ParserConfiguration()
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("configuration exceeds its bound")
            return ParserConfiguration.model_validate_json(raw)
        except FileNotFoundError:
            return ParserConfiguration()
        except (OSError, ValueError):
            raise unavailable() from None

    def save(self, options: ParseOptions, expected_revision: int) -> ParserConfiguration:
        options = ParseOptions.model_validate(options)
        expected_revision = TypeAdapter(NonNegative).validate_python(expected_revision)
        if self.path is None:
            raise unavailable()
        try:
            descriptor = os.open(
                self.path.with_suffix(self.path.suffix + ".lock"),
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                current = self.read()
                if current.options == options and self.path.exists():
                    return current
                if current.revision != expected_revision:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "Parser settings changed. Refresh settings."
                    )
                updated = ParserConfiguration(revision=current.revision + 1, options=options)
                descriptor, temporary = tempfile.mkstemp(prefix=".parser-", dir=self.path.parent)
                try:
                    with os.fdopen(descriptor, "w") as output:
                        output.write(updated.model_dump_json(indent=2) + "\n")
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, self.path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
                return updated
        except OSError:
            raise unavailable() from None


class ParserSetup:
    def __init__(self, policy: Policy, path: Path | None) -> None:
        self.policy, self.store = policy, ParserConfigFile(path)

    async def get(self, access: AccessContext, kg_id: UUID) -> ParserConfiguration:
        current = await self.policy.revalidate(access, action=Action.CONFIGURE)
        if kg_id not in current.kg_ids:
            raise inaccessible_reference()
        result = await asyncio.to_thread(self.store.read)
        await self.policy.revalidate(current, action=Action.CONFIGURE)
        return result

    async def save(
        self, access: AccessContext, kg_id: UUID, options: ParseOptions, expected_revision: int
    ) -> ParserConfiguration:
        current = await self.policy.revalidate(access, action=Action.CONFIGURE)
        if kg_id not in current.kg_ids:
            raise inaccessible_reference()
        if not current.platform_operator or current.principal.kind != "human":
            raise DomainError(
                ErrorCode.FORBIDDEN, "A human platform operator must configure parsing."
            )
        await self.policy.resolve_access(
            current.principal, [], Action.REGISTER_BACKEND, org_id=current.org_id
        )
        result = await asyncio.to_thread(self.store.save, options, expected_revision)
        await self.policy.database.policy_audit.insert_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(kg_id),
                "issuer": current.principal.issuer,
                "subject": current.principal.subject,
                "action": "parser_configuration",
                "scope": "server",
                "at": datetime.now(UTC),
                "revision": result.revision,
                "options": result.options.model_dump(mode="json"),
            }
        )
        await self.policy.revalidate(current, action=Action.CONFIGURE)
        return result

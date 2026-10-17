"""Bounded exact-key S3 erasure; callers must prove expired, fenced ownership."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import Field

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.materials.models import BlobRef
from domain_kg.models import FrozenModel
from domain_kg.operations.packages import PackageObject
from domain_kg.operations.storage import PackageStore
from domain_kg.storage.blobs import BlobStore


class ErasureLimits(FrozenModel):
    page_size: Annotated[int, Field(ge=1, le=1000, strict=True)] = 100
    max_pages: Annotated[int, Field(ge=1, le=100, strict=True)] = 20
    max_records: Annotated[int, Field(ge=1, le=10000, strict=True)] = 1000


def inconsistent() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained object inventory differs.")


class PackageErasure:
    """A storage primitive, never an expiry decision or a public deletion capability.

    The required guard must authorize a real live job and its already validated
    expired target. It runs before every I/O and after absence verification.
    No caller-supplied object path, prefix or bucket is accepted.
    """

    def __init__(
        self,
        store: PackageStore,
        *,
        authorize: Callable[[], Awaitable[None]],
        limits: ErasureLimits | None = None,
    ) -> None:
        self.store, self.authorize = store, authorize
        self.limits = limits or ErasureLimits()

    async def _call(self, method: str, **arguments: Any) -> Any:
        await self.authorize()
        action = getattr(self.store.binding.client, method)
        task = asyncio.create_task(
            asyncio.to_thread(action, Bucket=self.store.binding.bucket, **arguments)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not release the caller's fence while a destructive thread is still running.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                task.exception()  # Consume an I/O error while preserving caller cancellation.
            raise

    async def _inventory(
        self, key: str, kind: Literal["versions", "uploads"], *, all_under_prefix: bool = False
    ) -> list[str]:
        identifier = "VersionId" if kind == "versions" else "UploadId"
        method = "list_object_versions" if kind == "versions" else "list_multipart_uploads"
        arguments: dict[str, Any] = {"Prefix": key}
        arguments["MaxKeys" if kind == "versions" else "MaxUploads"] = self.limits.page_size
        result: list[str] = []
        seen: set[tuple[str, str]] = set()
        previous: tuple[str, str] | None = None
        for _ in range(self.limits.max_pages):
            page = await self._call(method, **arguments)
            if not isinstance(page, dict) or type(page.get("IsTruncated")) is not bool:
                raise inconsistent()
            rows = (
                page.get("Versions", []) + page.get("DeleteMarkers", [])
                if kind == "versions"
                else page.get("Uploads", [])
            )
            if not isinstance(rows, list):
                raise inconsistent()
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("Key"), str)
                    or not row["Key"].startswith(key)
                    or not isinstance(row.get(identifier), str)
                    or not row[identifier]
                    or len(row[identifier]) > 2048
                ):
                    raise inconsistent()
                identity = (row["Key"], row[identifier])
                if identity in seen:
                    raise inconsistent()
                seen.add(identity)
                if len(seen) > self.limits.max_records:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED,
                        "Retained object inventory exceeds its cleanup budget.",
                    )
                if all_under_prefix or row["Key"] == key:
                    result.append(row[identifier])
            if not page["IsTruncated"]:
                return result
            next_key = page.get("NextKeyMarker")
            next_id = page.get("Next" + identifier + "Marker", "")
            id_only = (
                kind == "uploads"
                and next_key in (None, "")
                and bool(rows)
                and isinstance(next_id, str)
                and bool(next_id)
                and next_id == rows[-1][identifier]
            )
            if id_only:
                # SeaweedFS 4.48 pages by upload-directory ID and treats KeyMarker
                # as an equality filter. Honor its ID-only response; inventing a
                # key cursor can silently hide a different key in the next page.
                marker = ("", next_id)
            else:
                if (
                    not isinstance(next_key, str)
                    or not next_key
                    or not isinstance(next_id, str)
                    or len(next_id) > 2048
                    or not next_key.startswith(key)
                ):
                    raise inconsistent()
                marker = (next_key, next_id)
            if marker == previous:
                raise inconsistent()
            previous = marker
            if marker[0]:
                arguments["KeyMarker"] = marker[0]
            else:
                arguments.pop("KeyMarker", None)
            if marker[1]:
                arguments[identifier + "Marker"] = marker[1]
            else:
                arguments.pop(identifier + "Marker", None)
        raise DomainError(
            ErrorCode.QUOTA_EXCEEDED, "Retained object pagination exceeds its cleanup budget."
        )

    async def _absent(self, key: str) -> None:
        if await self._inventory(key, "versions") or await self._inventory(key, "uploads"):
            raise inconsistent()
        try:
            await self._call("head_object", Key=key)
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get(
                "HTTPStatusCode"
            ) == 404 or error.response.get("Error", {}).get("Code") in (
                "NoSuchKey",
                "NotFound",
                "404",
            ):
                return
            raise
        raise inconsistent()

    async def erase(self, reference: PackageObject) -> None:
        reference = PackageObject.model_validate(reference)
        await self._erase_key(self.store._key(reference))

    async def erase_original(self, reference: BlobRef) -> None:
        reference = BlobRef.model_validate(reference)
        await self._erase_key(BlobStore(self.store.binding)._key(reference, reference.object_id))

    async def verify_original_namespace_absent(self) -> None:
        namespace = self.store.binding.namespace
        prefix = f"{namespace.org_id}/{namespace.kg_id}/"
        try:
            if await self._inventory(
                prefix, "versions", all_under_prefix=True
            ) or await self._inventory(prefix, "uploads", all_under_prefix=True):
                raise inconsistent()
            await self.authorize()
        except (ClientError, BotoCoreError):
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "Target object cleanup is unavailable.",
                retryable=True,
            ) from None

    async def _erase_key(self, key: str) -> None:
        try:
            async with self.store.binding.upload_slot:
                # Complete bounded inventory before the first destructive request.
                versions = await self._inventory(key, "versions")
                uploads = await self._inventory(key, "uploads")
                for version in versions:
                    await self._call("delete_object", Key=key, VersionId=version)
                for upload in uploads:
                    await self._call("abort_multipart_upload", Key=key, UploadId=upload)
                await self._absent(key)
                await self.authorize()
        except (ClientError, BotoCoreError):
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "Retained object cleanup is unavailable.",
                retryable=True,
            ) from None
        except (KeyError, TypeError, ValueError):
            raise inconsistent() from None

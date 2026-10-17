"""Immutable, bounded retained objects live outside the live source-cleanup prefix."""

import asyncio
import hashlib
from collections.abc import AsyncIterable, AsyncIterator
from tempfile import SpooledTemporaryFile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.operations.packages import PackageObject
from domain_kg.storage.router import BlobBinding

CHUNK = 65536
MULTIPART = 5 * 1024 * 1024


class PackageStore:
    def __init__(self, binding: BlobBinding) -> None:
        self.binding = binding

    def _key(self, reference: PackageObject) -> str:
        if (reference.org_id, reference.kg_id) != (
            self.binding.namespace.org_id,
            self.binding.namespace.kg_id,
        ):
            raise inaccessible_reference()
        return "retained-backups/" + "/".join(
            map(
                str,
                (
                    reference.org_id,
                    reference.kg_id,
                    reference.backup_id,
                    reference.attempt_id,
                    reference.id,
                ),
            )
        )

    async def put(self, reference: PackageObject, stream: AsyncIterable[bytes]) -> None:
        reference = PackageObject.model_validate(reference)
        key = self._key(reference)
        size, checksum = 0, hashlib.sha256()
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            async for part in stream:
                if not isinstance(part, bytes):
                    raise DomainError(ErrorCode.VALIDATION_ERROR, "Package chunks must be bytes.")
                size += len(part)
                if size > reference.size_bytes:
                    raise DomainError(
                        ErrorCode.EVIDENCE_VALIDATION_FAILED, "Package integrity failed."
                    )
                checksum.update(part)
                await asyncio.to_thread(spool.write, part)
            if size != reference.size_bytes or checksum.hexdigest() != reference.sha256:
                raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "Package integrity failed.")
            spool.seek(0)
            try:
                async with self.binding.upload_slot:
                    writing = asyncio.create_task(
                        asyncio.to_thread(self._store, spool, key, reference)
                    )
                    try:
                        await asyncio.shield(writing)
                    except asyncio.CancelledError:
                        await asyncio.gather(writing, return_exceptions=True)
                        raise
            except ClientError as error:
                if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412 or (
                    error.response.get("Error", {}).get("Code") == "PreconditionFailed"
                ):
                    async for _ in self.open(reference):
                        pass
                    return
                raise DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE,
                    "Retained object storage is unavailable.",
                    retryable=True,
                ) from error
            except BotoCoreError as error:
                raise DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE,
                    "Retained object storage is unavailable.",
                    retryable=True,
                ) from error

    def _store(
        self, spool: SpooledTemporaryFile[bytes], key: str, reference: PackageObject
    ) -> None:
        client, bucket = self.binding.client, self.binding.bucket
        metadata = {"sha256": reference.sha256, "backup_id": str(reference.backup_id)}
        if reference.size_bytes <= MULTIPART:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=spool,
                ContentType=reference.media_type,
                ContentLength=reference.size_bytes,
                Metadata=metadata,
                IfNoneMatch="*",
            )
            return
        upload = client.create_multipart_upload(
            Bucket=bucket, Key=key, ContentType=reference.media_type, Metadata=metadata
        )["UploadId"]
        try:
            parts: list[dict[str, Any]] = []
            while chunk := spool.read(MULTIPART):
                number = len(parts) + 1
                result = client.upload_part(
                    Bucket=bucket, Key=key, UploadId=upload, PartNumber=number, Body=chunk
                )
                parts.append({"ETag": result["ETag"], "PartNumber": number})
            client.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
            )
        except BaseException:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload)
            raise

    async def open(self, reference: PackageObject) -> AsyncIterator[bytes]:
        reference = PackageObject.model_validate(reference)
        key = self._key(reference)
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            checksum, size = hashlib.sha256(), 0
            try:
                response = await asyncio.to_thread(
                    self.binding.client.get_object, Bucket=self.binding.bucket, Key=key
                )
                try:
                    while part := await asyncio.to_thread(response["Body"].read, CHUNK):
                        size += len(part)
                        if size > reference.size_bytes:
                            raise DomainError(
                                ErrorCode.EVIDENCE_VALIDATION_FAILED,
                                "Retained object integrity failed.",
                            )
                        checksum.update(part)
                        await asyncio.to_thread(spool.write, part)
                finally:
                    response["Body"].close()
            except (ClientError, BotoCoreError) as error:
                raise DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE, "Retained object is unavailable.", retryable=True
                ) from error
            if size != reference.size_bytes or checksum.hexdigest() != reference.sha256:
                raise DomainError(
                    ErrorCode.EVIDENCE_VALIDATION_FAILED, "Retained object integrity failed."
                )
            spool.seek(0)
            while part := await asyncio.to_thread(spool.read, CHUNK):
                yield part

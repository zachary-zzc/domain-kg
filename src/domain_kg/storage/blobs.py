"""Immutable server-selected original objects, with bounded memory and verified bytes."""

import asyncio
import hashlib
from collections.abc import AsyncIterable, AsyncIterator
from tempfile import SpooledTemporaryFile
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.ingestion.file_types import IMAGE_TYPES, OFFICE_ZIP_TYPES, OLE_TYPES
from domain_kg.ingestion.text import TextValidator
from domain_kg.materials.models import BlobRef, BlobScope
from domain_kg.storage.router import BlobBinding

MAX_BYTES = 50 * 1024 * 1024
CHUNK = 65536
TEXT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/html",
    "text/csv",
    "text/tab-separated-values",
    "application/json",
    "application/graphml+xml",
}
ZIP_TYPES = {
    "application/zip",
    *OFFICE_ZIP_TYPES,
}


class BlobStore:
    def __init__(self, binding: BlobBinding) -> None:
        self.binding = binding

    def _key(self, scope: BlobScope, object_id: UUID) -> str:
        if (scope.org_id, scope.kg_id) != (
            self.binding.namespace.org_id,
            self.binding.namespace.kg_id,
        ):
            raise inaccessible_reference()
        return "/".join(
            str(value)
            for value in (scope.org_id, scope.kg_id, scope.material_id, scope.version_id, object_id)
        )

    @staticmethod
    def _check_type(media_type: str, prefix: bytes) -> None:
        if media_type in IMAGE_TYPES:
            magic = {
                "image/png": (b"\x89PNG\r\n\x1a\n",),
                "image/jpeg": (b"\xff\xd8\xff",),
                "image/webp": (b"RIFF",),
                "image/gif": (b"GIF87a", b"GIF89a"),
                "image/tiff": (b"II*\x00", b"MM\x00*"),
                "image/bmp": (b"BM",),
            }
            if not prefix.startswith(magic[media_type]) or (
                media_type == "image/webp" and prefix[8:12] != b"WEBP"
            ):
                raise DomainError(
                    ErrorCode.UNSUPPORTED_FORMAT, "Image bytes do not match the declared type."
                )
        elif media_type in OLE_TYPES:
            if not prefix.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
                raise DomainError(
                    ErrorCode.UNSUPPORTED_FORMAT, "Content does not match legacy Office."
                )
        elif media_type in ("application/rtf", "text/rtf"):
            if not prefix.startswith(b"{\\rtf"):
                raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Content does not match RTF.")
        elif media_type in TEXT_TYPES:
            if (
                b"\x00" in prefix
                and not prefix.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff"))
            ) or prefix.startswith((b"%PDF-", b"PK\x03\x04")):
                raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Content does not match its type.")
        elif media_type == "application/pdf":
            if not prefix.startswith(b"%PDF-"):
                raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Content does not match PDF.")
        elif media_type in ZIP_TYPES:
            if not prefix.startswith(b"PK\x03\x04"):
                raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Content does not match ZIP.")
        else:
            raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported original file type.")

    async def put(
        self,
        stream: AsyncIterable[bytes],
        expected_hash: str,
        scope: BlobScope,
        *,
        media_type: str = "text/plain",
        expected_size: int | None = None,
        object_id: UUID | None = None,
    ) -> BlobRef:
        scope = BlobScope.model_validate(scope)
        identifier = object_id or uuid4()
        key = self._key(scope, identifier)
        digest, size, prefix = hashlib.sha256(), 0, b""
        decoder = TextValidator() if media_type in TEXT_TYPES else None
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            try:
                async for part in stream:
                    if not isinstance(part, bytes):
                        raise DomainError(
                            ErrorCode.VALIDATION_ERROR, "Upload chunks must be bytes."
                        )
                    size += len(part)
                    if size > MAX_BYTES:
                        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Original exceeds 50 MiB.")
                    digest.update(part)
                    prefix = (prefix + part[:512])[:512]
                    if decoder:
                        decoder.decode(part)
                    await asyncio.to_thread(spool.write, part)
                if decoder:
                    decoder.decode(b"", final=True)
            except UnicodeError as exc:
                raise DomainError(
                    ErrorCode.UNSUPPORTED_FORMAT, "Use UTF-8, BOM-marked Unicode, or GB18030 text."
                ) from exc
            self._check_type(media_type, prefix)
            if (
                not size
                or digest.hexdigest() != expected_hash
                or (expected_size is not None and size != expected_size)
            ):
                raise DomainError(
                    ErrorCode.EVIDENCE_VALIDATION_FAILED, "Original integrity failed."
                )
            reference = BlobRef(
                **scope.model_dump(),
                object_id=identifier,
                sha256=expected_hash,
                size_bytes=size,
                media_type=media_type,
            )
            spool.seek(0)
            try:
                async with self.binding.upload_slot:
                    write = asyncio.create_task(
                        asyncio.to_thread(self._store, spool, key, reference)
                    )
                    try:
                        await asyncio.shield(write)
                    except asyncio.CancelledError:
                        # Cancellation does not stop a synchronous SDK thread. Keep its
                        # spool and backend budget alive until the bounded I/O finishes.
                        await asyncio.gather(write, return_exceptions=True)
                        raise
            except (ClientError, BotoCoreError) as exc:
                raise DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE, "Original could not be stored.", retryable=True
                ) from exc
        return reference

    def _store(self, spool: SpooledTemporaryFile[bytes], key: str, reference: BlobRef) -> None:
        if reference.size_bytes > 5 * 1024 * 1024:
            self._multipart(spool, key, reference)
        else:
            self.binding.client.put_object(
                Bucket=self.binding.bucket,
                Key=key,
                Body=spool,
                ContentLength=reference.size_bytes,
                ContentType=reference.media_type,
                IfNoneMatch="*",
                Metadata={"sha256": reference.sha256},
            )

    def _multipart(self, spool: SpooledTemporaryFile[bytes], key: str, reference: BlobRef) -> None:
        client, bucket = self.binding.client, self.binding.bucket
        upload = client.create_multipart_upload(
            Bucket=bucket,
            Key=key,
            ContentType=reference.media_type,
            Metadata={"sha256": reference.sha256},
        )["UploadId"]
        try:
            parts: list[dict[str, str | int]] = []
            while chunk := spool.read(5 * 1024 * 1024):
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

    async def _verified(self, reference: BlobRef, spool: SpooledTemporaryFile[bytes]) -> None:
        key = self._key(reference, reference.object_id)
        digest, size = hashlib.sha256(), 0
        try:
            response = await asyncio.to_thread(
                self.binding.client.get_object, Bucket=self.binding.bucket, Key=key
            )
            try:
                while part := await asyncio.to_thread(response["Body"].read, CHUNK):
                    size += len(part)
                    if size > reference.size_bytes:
                        raise DomainError(
                            ErrorCode.EVIDENCE_VALIDATION_FAILED, "Original integrity failed."
                        )
                    digest.update(part)
                    await asyncio.to_thread(spool.write, part)
            finally:
                response["Body"].close()
        except (ClientError, BotoCoreError) as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Original is unavailable.", retryable=True
            ) from exc
        if size != reference.size_bytes or digest.hexdigest() != reference.sha256:
            raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "Original integrity failed.")

    async def open(self, reference: BlobRef) -> AsyncIterator[bytes]:
        reference = BlobRef.model_validate(reference)
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            await self._verified(reference, spool)
            spool.seek(0)
            while part := await asyncio.to_thread(spool.read, CHUNK):
                yield part

    async def remove_unclaimed(self, reference: BlobRef) -> None:
        """Internal GC only; the authority must fence the unclaimed upload first."""
        key = self._key(reference, reference.object_id)
        try:
            await asyncio.to_thread(
                self.binding.client.delete_object, Bucket=self.binding.bucket, Key=key
            )
        except (ClientError, BotoCoreError) as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE, "Original cleanup is pending.", retryable=True
            ) from exc

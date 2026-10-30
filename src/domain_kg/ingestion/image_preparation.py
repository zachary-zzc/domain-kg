"""Source-owned image artifacts and resumable selected-model interpretations."""

import base64
import hashlib
from uuid import uuid5

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.models import ParsedBlock, ParsedDocument, SourceImage, SourceLocation
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import BlobRef
from domain_kg.models import SourceVersion
from domain_kg.providers.service import ProviderService
from domain_kg.providers.vision import VERSION, ImageDescription
from domain_kg.storage.blobs import BlobStore


async def store_images(queue: JobQueue, job: LeasedJob, document: ParsedDocument) -> ParsedDocument:
    images = []
    for image in document.images:

        async def store(data: bytes, media_type: str, purpose: str) -> BlobRef:
            key = hashlib.sha256(
                (purpose + "/" + hashlib.sha256(data).hexdigest()).encode()
            ).hexdigest()
            checkpoint = await queue.load_checkpoint(job, "parsing", key)
            if checkpoint is None:
                checkpoint = await queue.store_artifact(
                    job, "parsing", key, data, media_type=media_type
                )
                await queue.checkpoint(job, checkpoint)
            return await queue.artifact_blob(job, checkpoint)

        raw = (
            document.blob
            if document.blob.sha256 == image.sha256 and document.blob.media_type == image.media_type
            else await store(
                base64.b64decode(image.data_base64 or "", validate=True),
                image.media_type,
                "source-image-v1",
            )
        )
        preview = await store(
            base64.b64decode(image.preview_base64 or "", validate=True),
            "image/png",
            "source-image-preview-v1",
        )
        images.append(
            SourceImage.model_validate(
                image.model_dump()
                | {
                    "data_base64": None,
                    "blob": raw,
                    "preview_base64": None,
                    "preview_blob": preview,
                }
            )
        )
    return ParsedDocument.model_validate(document.model_dump() | {"images": tuple(images)})


async def interpret_images(
    queue: JobQueue,
    job: LeasedJob,
    document: ParsedDocument,
    providers: ProviderService,
    input_hash: str,
) -> ParsedDocument:
    from uuid import UUID

    from domain_kg.materials.restoration import reprocessing_permit
    from domain_kg.models import Namespace

    if not document.images:
        return document
    access = await queue.authorize(job)
    selected = await providers.selected(access, job.kg_id)
    profile, _ = providers.registry.resolve(
        UUID(selected["profile_id"]),
        Namespace(org_id=job.org_id, kg_id=job.kg_id),
        restricted=False,
    )
    if not profile.supports_images:
        return ParsedDocument.model_validate(
            document.model_dump()
            | {"warnings": tuple(dict.fromkeys((*document.warnings, "IMAGE_VISION_DISABLED")))}
        )
    stores = await queue.router.for_kg(access, job.kg_id)
    permit = await reprocessing_permit(queue, access, job)
    blocks = list(document.blocks)
    warnings = list(document.warnings)
    for image in document.images:
        source = SourceVersion(**{f: getattr(image, f) for f in SourceVersion.model_fields})
        await providers.authorize_source(access, source, restoration=permit)
        cache_hash = hashlib.sha256(
            (VERSION + input_hash + image.model_dump_json()).encode()
        ).hexdigest()
        cached = await queue.load_checkpoint(job, "chunking", cache_hash)
        if cached is None:
            if image.preview_blob is None:
                raise DomainError(
                    ErrorCode.EVIDENCE_VALIDATION_FAILED, "Stored image preview is missing."
                )
            data = bytearray()
            async for part in BlobStore(stores.blobs).open(image.preview_blob):
                data.extend(part)
                if len(data) > 4 * 1024 * 1024:
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Image preview is too large.")
            description = await providers.describe_image(
                access, image, bytes(data), restoration=permit
            )
            cached = await queue.store_artifact(
                job, "chunking", cache_hash, description.model_dump_json().encode()
            )
            await queue.checkpoint(job, cached)
        else:
            data = bytearray()
            async for part in BlobStore(stores.blobs).open(await queue.artifact_blob(job, cached)):
                data.extend(part)
            description = ImageDescription.model_validate_json(data)
        await providers.authorize_source(access, source, restoration=permit)
        if await providers.selected(access, job.kg_id) != selected:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Image model configuration changed.")
        text = "\n".join(description.sentences)
        if not text.strip():
            warnings.append("IMAGE_DESCRIPTION_EMPTY")
            continue
        identifier = uuid5(image.id, cache_hash + hashlib.sha256(text.encode()).hexdigest())
        origin = image.origins[0]
        blocks.append(
            ParsedBlock(
                **source.model_dump(),
                id=identifier,
                text=text,
                location=SourceLocation(
                    kind="image",
                    block_id=identifier,
                    image_id=image.id,
                    text_origin="vision",
                    interpretation_model=profile.extraction_model,
                    interpretation_profile_id=profile.id,
                    interpretation_profile_revision=profile.revision,
                    page=origin.page,
                    slide=origin.slide,
                    sheet=origin.sheet,
                ),
            )
        )
        warnings.append("IMAGE_MODEL_INTERPRETED")
    result = ParsedDocument.model_validate(
        document.model_dump()
        | {"blocks": tuple(blocks), "warnings": tuple(dict.fromkeys(warnings))}
    )
    return result

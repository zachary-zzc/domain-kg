"""Bounded raster evidence, separate from generated interpretation text."""

import base64
import hashlib
import io
from typing import TYPE_CHECKING
from uuid import uuid5

from PIL import Image, ImageOps, UnidentifiedImageError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.ingestion.models import ImageOrigin, SourceImage
from domain_kg.models import SourceVersion

if TYPE_CHECKING:
    from domain_kg.ingestion.formats import Blocks

VERSION = "raster-sources-v1"
FORMATS = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "TIFF": "image/tiff",
    "BMP": "image/bmp",
}


def capture(blocks: "Blocks", data: bytes, origin: ImageOrigin) -> SourceImage:
    if len(data) > 20 * 1024 * 1024:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Source image exceeds the byte limit.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            media_type = FORMATS.get(image.format or "")
            width, height = image.size
            if getattr(image, "is_animated", False):
                blocks.warnings.append("IMAGE_MULTIFRAME_FIRST_FRAME")
            if media_type is None:
                raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported embedded image type.")
            if width * height > 40_000_000:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Source image exceeds the pixel limit.")
            image.verify()
        with Image.open(io.BytesIO(data)) as original:
            original.draft("RGB", (1024, 1024))
            original.thumbnail((1024, 1024))
            oriented = ImageOps.exif_transpose(original)
            with oriented.convert("RGB") as raster:
                output = io.BytesIO()
                raster.save(output, format="PNG")
                preview = output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "The image could not be decoded.") from exc
    checksum = hashlib.sha256(data).hexdigest()
    identifier = uuid5(blocks.blob.version_id, VERSION + "/" + checksum)
    for index, existing in enumerate(blocks.images):
        if existing.id == identifier:
            updated = SourceImage.model_validate(
                existing.model_dump()
                | {"origins": tuple(dict.fromkeys((*existing.origins, origin)))}
            )
            blocks.images[index] = updated
            return updated
    if (
        len(blocks.images) >= 128
        or sum(i.size_bytes for i in blocks.images) + len(data) > 40 * 1024 * 1024
    ):
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Document images exceed the parser limit.")
    if (
        len(preview) > 4 * 1024 * 1024
        or blocks.bytes
        + sum(i.size_bytes + len(i.preview_base64 or "") for i in blocks.images)
        + len(data)
        + len(preview) * 4 // 3
        > blocks.options.max_expanded_bytes
    ):
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Image previews exceed the parser limit.")
    value = SourceImage.model_validate(
        {
            **{field: getattr(blocks.blob, field) for field in SourceVersion.model_fields},
            "id": identifier,
            "sha256": checksum,
            "size_bytes": len(data),
            "width": width,
            "height": height,
            "media_type": media_type,
            "origins": (origin,),
            "data_base64": base64.b64encode(data).decode("ascii"),
            "preview_base64": base64.b64encode(preview).decode("ascii"),
            "preview_sha256": hashlib.sha256(preview).hexdigest(),
        }
    )
    blocks.images.append(value)
    return value


def embedded(blocks: "Blocks", data: bytes, origin: ImageOrigin) -> None:
    try:
        image = capture(blocks, data, origin)
        if origin.page is None:
            image_ocr(blocks, image)
    except DomainError as exc:
        if exc.code != ErrorCode.UNSUPPORTED_FORMAT:
            raise
        blocks.warnings.append("EMBEDDED_IMAGE_UNSUPPORTED")


def office_images(data: bytes, blocks: "Blocks", prefix: str) -> None:
    from domain_kg.ingestion.archives import office_archive

    with office_archive(data, blocks.options) as archive:
        for name in archive.namelist():
            if name.startswith(prefix) and not name.endswith("/"):
                embedded(blocks, archive.read(name), ImageOrigin(part=name))


def image_ocr(blocks: "Blocks", image: SourceImage) -> None:
    if not blocks.options.ocr:
        blocks.warnings.append("IMAGE_OCR_DISABLED")
        return
    if any(block.location.image_id == image.id for block in blocks.items):
        return
    from domain_kg.ingestion.ocr import recognize_raster

    text = recognize_raster(base64.b64decode(image.preview_base64 or "", validate=True))
    if text.strip():
        origin = image.origins[0]
        blocks.add(
            text,
            "image",
            image_id=str(image.id),
            text_origin="ocr",
            page=origin.page,
            slide=origin.slide,
            sheet=origin.sheet,
        )
        blocks.warnings.append("OCR_APPLIED")
    else:
        blocks.warnings.append("IMAGE_NO_TEXT")

"""Raster sources remain separate immutable evidence, never just caption text."""

import base64
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from domain_kg.ingestion.formats import decode
from domain_kg.ingestion.models import ParsedDocument, ParseOptions
from domain_kg.materials.models import BlobRef
from domain_kg.storage.blobs import BlobStore

FIXTURES = Path(__file__).parents[1] / "fixtures/materials"


def original(data, media_type):
    return BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type=media_type,
    )


def test_standalone_image_retains_original_bytes_and_separate_source_identity():
    data = (FIXTURES / "scanned-bilingual.png").read_bytes()
    BlobStore._check_type("image/png", data[:512])
    blob = original(data, "image/png")
    document = decode(data, blob, ParseOptions(ocr=False))
    image = document.images[0]
    assert image.sha256 == blob.sha256
    assert base64.b64decode(image.data_base64) == data
    assert (image.org_id, image.kg_id, image.material_id, image.version_id) == (
        blob.org_id,
        blob.kg_id,
        blob.material_id,
        blob.version_id,
    )
    assert image.width == 400 and image.height == 100


def test_presentation_image_has_its_own_source_and_exact_slide_origin():
    data = (FIXTURES / "slides.pptx").read_bytes()
    document = decode(
        data,
        original(data, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ParseOptions(ocr=False),
    )
    assert len(document.images) == 1
    assert document.images[0].origins[0].slide == 1
    assert (
        base64.b64decode(document.images[0].data_base64)
        == (FIXTURES / "scanned-bilingual.png").read_bytes()
    )


def test_foreign_image_source_cannot_enter_another_document():
    data = (FIXTURES / "scanned-bilingual.png").read_bytes()
    first = decode(data, original(data, "image/png"), ParseOptions(ocr=False))
    second = decode(data, original(data, "image/png"), ParseOptions(ocr=False))
    with pytest.raises(ValueError):
        ParsedDocument.model_validate(first.model_dump() | {"images": second.images})


def test_text_only_image_fallback_uses_local_ocr_with_image_provenance():
    data = (FIXTURES / "scanned-bilingual.png").read_bytes()
    document = decode(data, original(data, "image/png"), ParseOptions())
    assert document.blocks
    assert any("evidence" in block.text.lower() for block in document.blocks)
    assert all(block.location.text_origin == "ocr" for block in document.blocks)
    assert {block.location.image_id for block in document.blocks} == {document.images[0].id}


def test_chunks_do_not_mix_image_interpretations_with_native_source():
    from domain_kg.ingestion.chunking import chunk_document
    from domain_kg.ingestion.formats import Blocks
    from domain_kg.ingestion.images import capture
    from domain_kg.ingestion.models import ChunkConfig, ImageOrigin

    data = (FIXTURES / "scanned-bilingual.png").read_bytes()
    blocks = Blocks(original(data, "image/png"), ParseOptions(ocr=False), "fixture")
    image = capture(blocks, data, ImageOrigin(part="original"))
    blocks.add("Alpha uses Beta.", "text")
    blocks.add("Alpha uses Beta.", "image", image_id=image.id, text_origin="vision")
    chunks = chunk_document(blocks.document(), ChunkConfig())
    assert len(chunks) == 2
    assert all(len({span.location.text_origin for span in c.spans}) == 1 for c in chunks)


def test_visual_evidence_cannot_be_relabelled_as_verified_original_text():
    from domain_kg.graph.models import SupportRecord
    from domain_kg.ingestion.models import SourceLocation, Span
    from domain_kg.models import ScopedRef

    ns = dict(org_id=uuid4(), kg_id=uuid4())
    location = SourceLocation(
        kind="image", block_id=uuid4(), image_id=uuid4(), text_origin="vision"
    )
    span = Span(location=location, start=0, end=5, quote="Alpha")
    args = dict(
        **ns,
        id=uuid4(),
        assertion=ScopedRef(**ns, id=uuid4()),
        version=ScopedRef(**ns, id=uuid4()),
        spans=(span,),
    )
    assert SupportRecord(**args, status="model_interpreted").status == "model_interpreted"
    with pytest.raises(ValueError):
        SupportRecord(**args, status="verified")


@pytest.mark.parametrize(
    "format_name, media_type",
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
        ("GIF", "image/gif"),
        ("TIFF", "image/tiff"),
        ("BMP", "image/bmp"),
    ],
)
def test_common_raster_formats_preserve_the_independent_original(format_name, media_type):
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(output, format=format_name)
    data = output.getvalue()
    BlobStore._check_type(media_type, data[:512])
    document = decode(data, original(data, media_type), ParseOptions(ocr=False))
    image = document.images[0]
    assert image.media_type == media_type
    assert base64.b64decode(image.data_base64) == data
    assert base64.b64decode(image.preview_base64).startswith(b"\x89PNG")


def test_multiframe_image_keeps_all_original_bytes_and_flags_first_frame_interpretation():
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(
        output, format="GIF", save_all=True, append_images=[Image.new("RGB", (24, 16), "red")]
    )
    data = output.getvalue()
    document = decode(data, original(data, "image/gif"), ParseOptions(ocr=False))
    assert "IMAGE_MULTIFRAME_FIRST_FRAME" in document.warnings
    assert base64.b64decode(document.images[0].data_base64) == data

"""Vision calls use the chosen model, image bytes, policy and existing usage boundary."""

import base64
import json
from pathlib import Path

import httpx
import pytest
from test_image_sources import original
from test_provider_extraction import configured

from domain_kg.errors import DomainError
from domain_kg.ingestion.formats import decode
from domain_kg.ingestion.models import ParseOptions
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile


def image_source():
    data = (Path(__file__).parents[1] / "fixtures/materials/scanned-bilingual.png").read_bytes()
    image = decode(data, original(data, "image/png"), ParseOptions(ocr=False)).images[0]
    return image, base64.b64decode(image.preview_base64)


@pytest.mark.parametrize("api_format", ["openai", "anthropic"])
async def test_visual_sentences_use_selected_model_without_losing_source_or_usage(api_format):
    from domain_kg.providers.vision import VisionClient

    image, data = image_source()
    profile, connection = configured(image)
    profile = ProviderProfile.model_validate(profile.model_dump() | {"supports_images": True})
    connection = ProviderConnection.model_validate(
        connection.model_dump() | {"api_format": api_format}
    )
    authorized, usage = [], []

    async def authorize():
        authorized.append(True)

    async def record(value):
        usage.append(value)

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == profile.extraction_model
        parts = body["messages"][0 if api_format == "anthropic" else 1]["content"]
        picture = next(
            p for p in parts if p["type"] == ("image" if api_format == "anthropic" else "image_url")
        )
        raw = (
            picture["source"]["data"]
            if api_format == "anthropic"
            else picture["image_url"]["url"].split(",", 1)[1]
        )
        assert base64.b64decode(raw).startswith(b"\x89PNG")
        content = json.dumps({"sentences": ["Alpha uses Beta."]})
        if api_format == "anthropic":
            result = {
                "content": [{"type": "text", "text": content}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 123, "output_tokens": 30},
            }
        else:
            result = {
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 123, "completion_tokens": 30},
            }
        return httpx.Response(200, json=result)

    client = VisionClient(
        profile,
        connection,
        authorize=authorize,
        record_usage=record,
        transport=httpx.MockTransport(respond),
    )
    try:
        result = await client.describe(image, data)
        assert result.sentences == ("Alpha uses Beta.",)
        assert len(authorized) >= 2
        assert usage[0].input_tokens == 123 and usage[0].kg_id == image.kg_id
    finally:
        await client.close()


async def test_text_only_model_does_not_receive_images():
    from domain_kg.providers.vision import VisionClient

    image, data = image_source()
    profile, connection = configured(image)

    async def noop(*_):
        pass

    def unexpected(_):
        raise AssertionError("A text-only profile must make no vision request")

    client = VisionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(unexpected),
    )
    try:
        with pytest.raises(DomainError) as failure:
            await client.describe(image, data)
        assert failure.value.code == "PROVIDER_UNAVAILABLE"
    finally:
        await client.close()


@pytest.mark.parametrize("fault", ["context", "checksum", "revoked", "billing", "truncated"])
async def test_vision_bounds_revocation_and_provider_errors_do_not_publish_descriptions(fault):
    from domain_kg.errors import ErrorCode
    from domain_kg.providers.vision import VisionClient

    image, data = image_source()
    profile, connection = configured(image)
    profile = ProviderProfile.model_validate(
        profile.model_dump()
        | {
            "supports_images": True,
            **({"extraction_context_tokens": 1024} if fault == "context" else {}),
        }
    )
    authorizations, calls = [], []

    async def authorize():
        authorizations.append(True)
        if fault == "revoked" and calls:
            raise DomainError(ErrorCode.FORBIDDEN, "Access withdrawn")

    async def usage(_):
        pass

    def respond(request):
        calls.append(True)
        if fault == "billing":
            return httpx.Response(402, json={"error": {"message": "private upstream diagnostic"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"sentences":["Alpha"]}'},
                        "finish_reason": "length" if fault == "truncated" else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    client = VisionClient(
        profile,
        connection,
        authorize=authorize,
        record_usage=usage,
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(DomainError) as error:
            await client.describe(image, b"bad" if fault == "checksum" else data)
        assert "private upstream diagnostic" not in error.value.message
        if fault in ("context", "checksum"):
            assert not calls
        if fault == "revoked":
            assert error.value.code == "FORBIDDEN"
    finally:
        await client.close()

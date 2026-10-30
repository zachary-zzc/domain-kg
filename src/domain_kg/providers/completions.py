"""OpenAI and native Claude messages share one bounded completion result."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import FrozenModel
from domain_kg.providers.http import WireModel
from domain_kg.providers.profiles import ProviderConnection

Tokens = Annotated[int, Field(ge=0, le=1_000_000, strict=True)]


class TextPart(WireModel):
    type: Literal["text"]
    text: str


class ImageURL(WireModel):
    url: str
    detail: Literal["auto", "low", "high"] = "auto"


class ImagePart(WireModel):
    type: Literal["image_url"]
    image_url: ImageURL


class Message(WireModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[TextPart | ImagePart]


class SchemaFormat(WireModel):
    schema_: dict[str, object] = Field(alias="schema")


class CompletionReply(FrozenModel):
    text: str
    finish: Literal["stop", "length", "blocked"]
    input_tokens: Tokens
    output_tokens: Tokens


class ChatUsage(WireModel):
    prompt_tokens: Tokens
    completion_tokens: Tokens


class ChatMessage(WireModel):
    content: str | None = None
    refusal: str | None = None
    tool_calls: list[object] | None = None
    function_call: object | None = None


class ChatChoice(WireModel):
    message: ChatMessage
    finish_reason: str


class ChatReply(WireModel):
    choices: Annotated[list[ChatChoice], Field(min_length=1, max_length=1)]
    usage: ChatUsage


class NativeBlock(WireModel):
    type: str
    text: str | None = None


class NativeUsage(WireModel):
    input_tokens: Tokens
    output_tokens: Tokens
    cache_creation_input_tokens: Tokens = 0
    cache_read_input_tokens: Tokens = 0


class NativeReply(WireModel):
    content: Annotated[list[NativeBlock], Field(min_length=1, max_length=100)]
    usage: NativeUsage
    stop_reason: str


def completion_request(
    payload: dict[str, object], connection: ProviderConnection
) -> tuple[str, dict[str, object]]:
    if connection.api_format == "openai":
        return connection.endpoint + "/chat/completions", payload
    messages = TypeAdapter(list[Message]).validate_python(payload["messages"])
    request: dict[str, object] = {
        "model": payload["model"],
        "max_tokens": payload["max_completion_tokens"],
        "system": "\n".join(
            item.content
            for item in messages
            if item.role == "system" and isinstance(item.content, str)
        ),
        "messages": [],
    }
    native_messages: list[dict[str, object]] = []
    for message in messages:
        if message.role == "system":
            continue
        if isinstance(message.content, str):
            native_messages.append({"role": message.role, "content": message.content})
            continue
        content: list[dict[str, object]] = []
        for part in message.content:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            else:
                import base64

                prefix, encoded = part.image_url.url.split(",", 1)
                mime = prefix.removeprefix("data:").removesuffix(";base64")
                if prefix != "data:" + mime + ";base64" or mime not in (
                    "image/png",
                    "image/jpeg",
                    "image/gif",
                    "image/webp",
                ):
                    raise ValueError("Image messages require supported inline image data")
                base64.b64decode(encoded, validate=True)
                content.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime, "data": encoded},
                    }
                )
        native_messages.append({"role": message.role, "content": content})
    request["messages"] = native_messages
    output = payload.get("response_format")
    if isinstance(output, dict) and output.get("type") == "json_schema":
        shape = SchemaFormat.model_validate(output["json_schema"])
        request["output_config"] = {"format": {"type": "json_schema", "schema": shape.schema_}}
    return connection.endpoint + "/messages", request


def completion_reply(raw: dict[str, object], connection: ProviderConnection) -> CompletionReply:
    if connection.api_format == "anthropic":
        native = NativeReply.model_validate(raw)
        if any(block.type != "text" or block.text is None for block in native.content):
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE, "The model returned unsupported content."
            )
        return CompletionReply(
            text="".join(block.text or "" for block in native.content),
            finish="stop"
            if native.stop_reason == "end_turn"
            else "length"
            if native.stop_reason == "max_tokens"
            else "blocked",
            input_tokens=(
                native.usage.input_tokens
                + native.usage.cache_creation_input_tokens
                + native.usage.cache_read_input_tokens
            ),
            output_tokens=native.usage.output_tokens,
        )
    chat = ChatReply.model_validate(raw)
    choice = chat.choices[0]
    message = choice.message
    blocked = (
        message.refusal or message.tool_calls or message.function_call or message.content is None
    )
    return CompletionReply(
        text=message.content or "",
        finish="blocked"
        if blocked
        else "stop"
        if choice.finish_reason == "stop"
        else "length"
        if choice.finish_reason == "length"
        else "blocked",
        input_tokens=chat.usage.prompt_tokens,
        output_tokens=chat.usage.completion_tokens,
    )

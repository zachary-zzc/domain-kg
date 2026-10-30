"""Editable setup presets; selecting one does not grant access or make a call."""

from typing import Literal

from domain_kg.models import FrozenModel


class ModelPreset(FrozenModel):
    id: str
    label: str
    endpoint: str
    model: str
    embedding_model: str = ""
    api_format: Literal["openai", "anthropic"] = "openai"
    response_format: Literal["json_object", "json_schema"] = "json_object"
    thinking: Literal["default", "disabled"] = "default"
    send_dimensions: bool = False
    supports_images: bool = False


PRESETS = (
    ModelPreset(
        id="alibaba",
        label="Alibaba Cloud / DashScope",
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.6-35b-a3b",
        embedding_model="text-embedding-v4",
        thinking="disabled",
    ),
    ModelPreset(
        id="openai",
        supports_images=True,
        label="OpenAI",
        endpoint="https://api.openai.com/v1",
        model="gpt-4.1-mini",
        embedding_model="text-embedding-3-small",
        response_format="json_schema",
        send_dimensions=True,
    ),
    ModelPreset(
        id="anthropic",
        supports_images=True,
        label="Anthropic / Claude",
        endpoint="https://api.anthropic.com/v1",
        model="claude-sonnet-5-5",
        api_format="anthropic",
        response_format="json_schema",
    ),
    ModelPreset(
        id="ollama", label="Ollama (local)", endpoint="http://127.0.0.1:11434/v1", model=""
    ),
    ModelPreset(
        id="lmstudio", label="LM Studio (local)", endpoint="http://127.0.0.1:1234/v1", model=""
    ),
    ModelPreset(id="custom", label="Other OpenAI-compatible API", endpoint="", model=""),
)

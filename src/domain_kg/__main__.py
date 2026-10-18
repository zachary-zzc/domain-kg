"""Launch the loopback development probe: uv run python -m domain_kg."""

import uvicorn
from fastapi import FastAPI

from domain_kg.app import create_app
from domain_kg.settings import Settings


def create_application() -> FastAPI:
    """ASGI factory for the configured API image and ordinary process launch."""
    return create_app(Settings())


if __name__ == "__main__":
    uvicorn.run(create_application(), host="127.0.0.1", port=8000)

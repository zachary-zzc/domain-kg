"""Launch the loopback development probe: uv run python -m domain_kg."""

import uvicorn

from domain_kg.app import create_app
from domain_kg.settings import Settings

if __name__ == "__main__":
    uvicorn.run(create_app(Settings()), host="127.0.0.1", port=8000)

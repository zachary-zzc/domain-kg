"""Generate the external identity contract without connecting stores."""

import argparse
import json
from pathlib import Path
from typing import Any

from domain_kg.app import create_app
from domain_kg.settings import Settings

OUTPUT = Path(__file__).resolve().parents[1] / "docs/api/identity-bridge.openapi.json"


def contract() -> dict[str, Any]:
    app = create_app(
        Settings(
            oidc_issuer="https://idp.example",
            oidc_client_id="documentation",
            identity_bridge_config=None,
        )
    )
    schema = app.openapi()
    paths = {
        path: value
        for path, value in schema["paths"].items()
        if any("Identity bridge" in operation.get("tags", []) for operation in value.values())
    }
    definitions: dict[str, Any] = {}

    def collect(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[-1]
                if name not in definitions:
                    definitions[name] = schema["components"]["schemas"][name]
                    collect(definitions[name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(paths)
    return {
        "openapi": schema["openapi"],
        "info": {
            "title": "Domain KG Identity Bridge API",
            "version": "1.0.0",
            "description": "Trusted platform provisioning and revocable delegation. "
            "Administrative and own-connection endpoints also accept same-origin browser "
            "sessions with CSRF protection; platform endpoints require Bearer.",
        },
        "paths": paths,
        "components": {
            "schemas": definitions,
            "securitySchemes": schema["components"]["securitySchemes"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    serialized = json.dumps(contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if options.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != serialized:
            raise SystemExit("Identity OpenAPI snapshot is stale; regenerate with this script.")
        print("Identity OpenAPI snapshot matches application routes.")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(serialized)
        print(OUTPUT)


if __name__ == "__main__":
    main()

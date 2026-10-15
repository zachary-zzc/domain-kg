"""Create local-only credentials without overwriting an existing environment."""

import os
import secrets
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    text = (root / ".env.example").read_text()
    for key in ["NEO4J_PASSWORD", "QDRANT_API_KEY", "S3_SECRET_KEY"]:
        text = text.replace(
            f"DOMAIN_KG_{key}=generate-with-scripts-dev-env",
            f"DOMAIN_KG_{key}={secrets.token_urlsafe(32)}",
        )
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Existing .env preserved.")
        return
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    print("Created private .env for the loopback-only laptop smoke stack.")


if __name__ == "__main__":
    main()

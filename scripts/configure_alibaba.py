"""Create an owner-only Alibaba profile for one explicitly selected public-data KG."""

import argparse
import json
import os
import stat
from pathlib import Path
from uuid import UUID

from domain_kg.models import Namespace
from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry

EXAMPLE = Path(__file__).resolve().parents[1] / "config/providers.alibaba.example.json"


def credential(path: Path | None) -> str:
    if path is None:
        key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    else:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("API key must be in an owner-only regular file")
        key = path.read_text().strip()
    if not key or key == "REPLACE_IN_PRIVATE_COPY":
        raise ValueError("Set DASHSCOPE_API_KEY or provide --secret-file")
    return key


def configuration(namespace: Namespace, key: str, *, endpoint: str | None = None) -> ProviderConfig:
    raw = json.loads(EXAMPLE.read_bytes())
    raw["connections"][0]["api_key"] = key
    if endpoint is not None:
        # Reuse the existing official-host check before attaching a credential.
        from eval.cloud_calls import checked_endpoint

        raw["connections"][0]["endpoint"] = checked_endpoint(endpoint)
    raw["profiles"][0].update(
        allowed_namespaces=[namespace.model_dump(mode="json")], data_policy_approved=True
    )
    config = ProviderConfig.model_validate(raw)
    ProviderRegistry(config)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", type=UUID, required=True)
    parser.add_argument("--kg-id", type=UUID, required=True)
    parser.add_argument("--secret-file", type=Path)
    parser.add_argument("--endpoint", help="Official Alibaba endpoint matching the key's region")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = credential(args.secret_file)
    config = configuration(
        Namespace(org_id=args.org_id, kg_id=args.kg_id), key, endpoint=args.endpoint
    )
    raw = config.model_dump(mode="json")
    raw["connections"][0]["api_key"] = key
    # Refuse an overwrite or symlink; never print the serialized credential.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(raw, handle, indent=2)
        handle.write("\n")
    print(f"Created {args.output}; select profile {config.profiles[0].id} as KG owner.")


if __name__ == "__main__":
    main()

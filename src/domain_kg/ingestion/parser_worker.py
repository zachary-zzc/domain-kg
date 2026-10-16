"""Private parser subprocess. Paths are allocated by Parser, never request arguments."""

import hashlib
import json
import resource
import sys
from pathlib import Path
from typing import Never


def main() -> None:
    input_path, output_path, blob_json, options_json = sys.argv[1:]
    timeout = min(300, max(1, int(json.loads(options_json)["timeout_seconds"])))
    resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (110 * 1024 * 1024, 110 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    # Native decoders do not fetch resources. Also deny Python socket creation,
    # including indirect image/XML/model clients, on every supported platform.
    import socket

    def offline(*args: object, **kwargs: object) -> Never:
        raise PermissionError("Parser networking is disabled")

    socket.socket.connect = offline  # type: ignore[method-assign]
    socket.socket.connect_ex = offline  # type: ignore[method-assign]
    socket.create_connection = offline
    socket.getaddrinfo = offline

    from domain_kg.errors import DomainError, ErrorCode
    from domain_kg.ingestion.formats import decode
    from domain_kg.ingestion.models import ParseOptions
    from domain_kg.materials.models import BlobRef

    try:
        blob, options = (
            BlobRef.model_validate_json(blob_json),
            ParseOptions.model_validate_json(options_json),
        )
        data = Path(input_path).read_bytes()
        if len(data) != blob.size_bytes or hashlib.sha256(data).hexdigest() != blob.sha256:
            raise DomainError(ErrorCode.EVIDENCE_VALIDATION_FAILED, "Original length changed.")
        document = decode(data, blob, options)
        result = document.model_dump_json().encode()
        if len(result) > min(100 * 1024 * 1024, options.max_expanded_bytes * 2 + 1024 * 1024):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Parsed output exceeds the resource limit.")
        Path(output_path).write_bytes(result)
        print(json.dumps({"ok": True}))
    except MemoryError:
        print(
            json.dumps(
                {"ok": False, "code": "QUOTA_EXCEEDED", "message": "Parser memory limit exceeded."}
            )
        )
    except DomainError as exc:
        print(json.dumps({"ok": False, "code": str(exc.code), "message": exc.message}))
    except Exception:
        # Exception representations may contain original private text or paths.
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "UNSUPPORTED_FORMAT",
                    "message": "The original is malformed or unsupported by this parser profile.",
                }
            )
        )


if __name__ == "__main__":
    main()

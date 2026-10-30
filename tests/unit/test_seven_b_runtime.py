import hashlib

import pytest

from eval.seven_b_runtime import verified_paths


def manifest(tmp_path):
    shards = []
    for name, body in (("first.gguf", b"first"), ("second.gguf", b"second")):
        (tmp_path / name).write_bytes(body)
        shards.append(
            {"filename": name, "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        )
    return {"shards": shards}


def test_missing_or_corrupted_second_shard_prevents_startup(tmp_path):
    settings = manifest(tmp_path)
    assert verified_paths(settings, tmp_path) == [tmp_path / "first.gguf", tmp_path / "second.gguf"]
    (tmp_path / "second.gguf").write_bytes(b"broken")
    with pytest.raises(ValueError):
        verified_paths(settings, tmp_path)
    (tmp_path / "second.gguf").unlink()
    with pytest.raises(ValueError):
        verified_paths(settings, tmp_path)


def test_shard_paths_cannot_escape_cache(tmp_path):
    settings = manifest(tmp_path)
    settings["shards"][0]["filename"] = "../outside.gguf"
    with pytest.raises(ValueError):
        verified_paths(settings, tmp_path)

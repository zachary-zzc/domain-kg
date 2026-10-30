"""Local IdP configuration preserves real login and limited bearer credentials."""

import json

import pytest

from scripts import local_identity


def test_private_identity_is_stable_and_has_no_password_grant(tmp_path, monkeypatch):
    monkeypatch.setattr(local_identity, "PRIVATE", tmp_path / "identity")
    identity = local_identity.prepare()
    assert local_identity.prepare() == identity
    realm = json.loads((local_identity.PRIVATE / "realm.json").read_text())
    assert all(not client["directAccessGrantsEnabled"] for client in realm["clients"])
    console, mcp = realm["clients"]
    assert console["attributes"]["pkce.code.challenge.method"] == "S256"
    assert console["redirectUris"] == ["http://127.0.0.1:8000/auth/callback"]
    assert mcp["defaultClientScopes"] == ["domain_kg:read", "domain_kg:write"]
    assert mcp["protocolMappers"][0]["config"]["included.custom.audience"].endswith("/mcp")
    for path in local_identity.PRIVATE.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600


def test_private_writer_does_not_follow_symlink_or_overwrite_public_file(tmp_path):
    target = tmp_path / "target"
    target.write_text("preserve")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        local_identity.write_private(link, "secret")
    with pytest.raises(ValueError):
        local_identity.write_private(target, "secret")
    assert target.read_text() == "preserve"

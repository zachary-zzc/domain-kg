"""Known local lease refusal is settled; transport uncertainty remains conservative."""

from types import SimpleNamespace

import pytest
from test_restore_writes import RecordedWrites

from domain_kg.errors import DomainError, ErrorCode


async def test_known_local_lease_refusal_does_not_create_an_uncertain_remote_write():
    writes = RecordedWrites()

    async def refused_before_io():
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The actual lease was withdrawn.")

    with pytest.raises(DomainError):
        await writes.run(SimpleNamespace(), SimpleNamespace(), "blob", refused_before_io)
    assert writes.events == ["pending", "settled"]

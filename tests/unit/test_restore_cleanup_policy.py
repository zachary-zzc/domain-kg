"""Server limits bound inactive target cleanup without accepting paths or secrets."""

import pytest

from domain_kg.errors import DomainError
from domain_kg.operations.restore_cleanup_policy import RestoreCleanupPolicy


def test_restore_cleanup_policy_file_is_bounded_and_strict(tmp_path):
    assert RestoreCleanupPolicy.from_file(None).max_objects == 1000
    path = tmp_path / "cleanup.json"
    path.write_text('{"max_objects":2,"batch_size":1}')
    assert RestoreCleanupPolicy.from_file(path).batch_size == 1
    for body in ('{"max_objects":0}', '{"bucket":"other"}', "x" * 65537):
        path.write_text(body)
        with pytest.raises(DomainError) as error:
            RestoreCleanupPolicy.from_file(path)
        assert str(path) not in error.value.message

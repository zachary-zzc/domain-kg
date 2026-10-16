"""Curation form syntax becomes bounded typed commands; it never establishes identity."""

from uuid import uuid4

import pytest

from domain_kg.console.node_actions import parse_action
from domain_kg.errors import DomainError


def fields():
    source, alpha, beta = uuid4(), uuid4(), uuid4()
    return (
        alpha,
        beta,
        {
            "action": "merge",
            "phase": "preview",
            "expected_revision": "0",
            "curation_scope": "source",
            "source_ids": str(source),
            "reason": "Domain review",
            "idempotency_key": "typed-preview",
            "member_ids": f"{alpha},{beta}",
        },
    )


def test_explicit_merge_and_split_preserve_originals_and_revision_guards():
    alpha, beta, body = fields()
    command, phase = parse_action(body, uuid4(), alpha)
    assert phase == "preview" and command.dry_run and command.member_ids == (alpha, beta)
    split, phase = parse_action(
        body
        | {
            "action": "split",
            "phase": "apply",
            "member_ids": "",
            "partitions": f"{alpha}={alpha}\n{beta}={beta}",
            "member_revisions": f"{beta}:0",
        },
        command.kg_id,
        alpha,
    )
    assert phase == "apply" and not split.dry_run
    assert split.partitions[1].canonical_id == beta and split.member_revisions == ((beta, 0),)


@pytest.mark.parametrize(
    "change",
    [
        {"principal": "owner"},
        {"phase": "execute"},
        {"member_revisions": "broken"},
        {"partitions": "broken", "action": "split", "member_ids": ""},
        {"expected_revision": "-1"},
        {"action": "suppress", "member_ids": "bad"},
    ],
)
def test_malformed_and_forged_curation_arguments_are_safe(change):
    alpha, _, body = fields()
    with pytest.raises(DomainError) as error:
        parse_action(body | change, uuid4(), alpha)
    assert error.value.code.value == "VALIDATION_ERROR"
    assert "broken" not in str(error.value)

"""Reversible human identity groups never rewrite source IDs or create pointer cycles."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.errors import DomainError
from domain_kg.graph.identity_models import IdentityGroup, ManualIdentity
from domain_kg.graph.identity_partition import IdentityPartition
from domain_kg.models import ManualProvenance, Namespace, ScopedRef


def fixture():
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    alpha, beta, gamma, material_a, material_b = (uuid4() for _ in range(5))
    return ns, alpha, beta, gamma, material_a, material_b


def mapping(ns, target, groups, *, material=None, order=0):
    return ManualIdentity(
        curation=ScopedRef(**ns.model_dump(), id=uuid4()),
        target=ScopedRef(**ns.model_dump(), id=target),
        groups=groups,
        public=material is None,
        material_ids=() if material is None else (material,),
        provenance=ManualProvenance(actor="fixture-editor", reason="Explicit identity review"),
        revision=order + 1,
        created_at=datetime.now(UTC) + timedelta(seconds=order),
    )


def test_source_group_does_not_rewrite_independent_or_public_identity():
    ns, alpha, beta, _, a, b = fixture()
    group = IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta))
    manual = mapping(ns, alpha, (group,), material=a)
    partitions = IdentityPartition(ns, (manual,))
    assert partitions.canonical(beta, a) == alpha
    assert partitions.canonical(beta, b) == beta
    assert partitions.canonical(beta, None) == beta
    assert set(partitions.members(alpha, a)) == {alpha, beta}
    assert partitions.witnesses(beta, a) == (manual.curation,)
    assert manual.text == ""  # grouping adds no borrowed label or invented source quote


def test_split_restores_original_ids_and_keeps_prior_mapping_immutable():
    ns, alpha, beta, _, a, _ = fixture()
    original = mapping(
        ns, alpha, (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),), material=a
    )
    split = mapping(
        ns,
        alpha,
        (
            IdentityGroup(canonical_id=alpha, member_ids=(alpha,)),
            IdentityGroup(canonical_id=beta, member_ids=(beta,)),
        ),
        material=a,
        order=1,
    )
    partitions = IdentityPartition(ns, (original, split))
    assert partitions.canonical(alpha, a) == alpha and partitions.canonical(beta, a) == beta
    assert len(original.groups[0].member_ids) == 2


def test_overlapping_scopes_form_partitions_without_transitive_pointer_cycles():
    ns, alpha, beta, gamma, a, b = fixture()
    first = mapping(
        ns, alpha, (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),), material=a
    )
    later = mapping(
        ns, gamma, (IdentityGroup(canonical_id=gamma, member_ids=(alpha, gamma)),), order=1
    )
    partitions = IdentityPartition(ns, (first, later))
    assert partitions.canonical(alpha, a) == gamma
    assert partitions.canonical(gamma, a) == gamma
    assert partitions.canonical(beta, a) == beta
    assert partitions.canonical(beta, b) == beta
    assert set(partitions.members(gamma, a)) == {alpha, gamma}


def test_groups_reject_ambiguous_members_and_foreign_namespace():
    ns, alpha, beta, _, a, _ = fixture()
    with pytest.raises(ValidationError):
        IdentityGroup(canonical_id=alpha, member_ids=(beta,))
    with pytest.raises(ValidationError):
        mapping(
            ns,
            alpha,
            (
                IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),
                IdentityGroup(canonical_id=beta, member_ids=(beta,)),
            ),
            material=a,
        )
    manual = mapping(
        ns, alpha, (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),), material=a
    )
    with pytest.raises(DomainError):
        IdentityPartition(Namespace(org_id=ns.org_id, kg_id=uuid4()), (manual,))


def test_joint_human_claim_requires_identity_scope_covering_all_owners():
    ns, alpha, beta, _, a, b = fixture()
    group = IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta))
    a_only = mapping(ns, alpha, (group,), material=a)
    joint = ManualIdentity.model_validate(a_only.model_dump() | {"material_ids": (a, b)})
    assert IdentityPartition(ns, (a_only,)).canonical(beta, (a, b)) == beta
    assert IdentityPartition(ns, (joint,)).canonical(beta, (a, b)) == alpha


def test_authority_sequence_preserves_causal_identity_order_despite_clock_skew():
    ns, alpha, beta, _, a, _ = fixture()
    merge = mapping(
        ns,
        alpha,
        (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),),
        material=a,
        order=10,
    )
    merge = ManualIdentity.model_validate(merge.model_dump() | {"sequence": 1})
    split = mapping(
        ns,
        alpha,
        (
            IdentityGroup(canonical_id=alpha, member_ids=(alpha,)),
            IdentityGroup(canonical_id=beta, member_ids=(beta,)),
        ),
        material=a,
    )
    split = ManualIdentity.model_validate(split.model_dump() | {"sequence": 2})
    assert split.created_at < merge.created_at
    assert IdentityPartition(ns, (split, merge)).canonical(beta, a) == beta


def control(original, *, enabled=False, order=2, **changes):
    value = original.model_dump() | {
        "curation": original.curation.model_copy(update={"id": uuid4()}),
        "groups": (),
        "retracts": original.curation,
        "enabled": enabled,
        "created_at": original.created_at + timedelta(seconds=order),
        "revision": order + 1,
    }
    return ManualIdentity.model_validate(value | changes)


def test_retraction_restores_different_source_contexts_without_stale_snapshot_replay():
    ns, alpha, beta, gamma, a, b = fixture()
    prior = mapping(
        ns, alpha, (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),), material=a
    )
    joint = ManualIdentity.model_validate(
        mapping(
            ns,
            alpha,
            (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta, gamma)),),
            material=a,
            order=1,
        ).model_dump()
        | {"material_ids": (a, b)}
    )
    retracted = control(joint)
    partitions = IdentityPartition(ns, (prior, joint, retracted))
    assert partitions.canonical(beta, a) == alpha
    assert partitions.canonical(beta, b) == beta
    assert partitions.canonical(gamma, a) == gamma
    assert partitions.canonical(beta, (a, b)) == beta


def test_retracting_earlier_mapping_preserves_later_explicit_group():
    ns, alpha, beta, gamma, a, _ = fixture()
    original = mapping(
        ns, alpha, (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),), material=a
    )
    later = mapping(
        ns,
        gamma,
        (IdentityGroup(canonical_id=gamma, member_ids=(alpha, beta, gamma)),),
        material=a,
        order=1,
    )
    partitions = IdentityPartition(ns, (original, later, control(original)))
    assert all(partitions.canonical(item, a) == gamma for item in (alpha, beta, gamma))
    assert partitions.witnesses(beta, a) == (later.curation,)


def test_reenable_keeps_original_order_and_current_control_provenance():
    ns, alpha, beta, _, a, _ = fixture()
    original = mapping(
        ns, alpha, (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),), material=a
    )
    disabled = control(original, order=1)
    restored = control(original, enabled=True, order=2)
    partitions = IdentityPartition(ns, (original, disabled, restored))
    assert partitions.canonical(beta, a) == alpha
    assert partitions.witnesses(beta, a) == (original.curation, restored.curation)


def test_control_cannot_borrow_another_scope_or_an_unknown_original():
    ns, alpha, beta, _, a, _ = fixture()
    original = mapping(
        ns, alpha, (IdentityGroup(canonical_id=alpha, member_ids=(alpha, beta)),), material=a
    )
    with pytest.raises(DomainError):
        IdentityPartition(ns, (original, control(original, public=True, material_ids=())))
    with pytest.raises(DomainError):
        IdentityPartition(ns, (control(original),))

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

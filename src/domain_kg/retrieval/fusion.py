"""Reciprocal text ranks and graph supplements; preserve complete source passages."""

from itertools import zip_longest
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.retrieval.models import EvidenceCandidate


def evidence_key(item: EvidenceCandidate) -> tuple[object, ...]:
    return (
        item.kg_id,
        item.kind,
        item.material_id,
        item.version_id,
        item.chunk_id,
        item.curation_revision_id,
    )


def fuse(
    candidates: dict[str, list[EvidenceCandidate]], constant: int = 60
) -> list[EvidenceCandidate]:
    if constant < 1:
        raise ValueError("RRF constant must be positive")
    merged: dict[tuple[object, ...], EvidenceCandidate] = {}
    for method, items in candidates.items():
        seen: set[tuple[object, ...]] = set()
        ranks: dict[UUID, int] = {}
        for raw in items:
            item = EvidenceCandidate.model_validate(raw)
            key = evidence_key(item)
            previous = merged.get(key)
            if previous is not None and (
                previous.text != item.text
                or set(previous.citation_ids) != set(item.citation_ids)
                or previous.provenance != item.provenance
                or previous.import_origin != item.import_origin
                or previous.support_status != item.support_status
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Evidence branches are inconsistent."
                )
            if key in seen:
                continue
            seen.add(key)
            ranks[item.kg_id] = ranks.get(item.kg_id, 0) + 1
            rank = ranks[item.kg_id]
            base = previous or item.model_copy(update={"score": 0.0, "method_ranks": {}})
            merged[key] = base.model_copy(
                update={
                    "score": base.score + 1 / (constant + rank),
                    "method_ranks": {**base.method_ranks, method: rank},
                    "entity_ids": tuple(
                        sorted(set(base.entity_ids) | set(item.entity_ids), key=str)
                    ),
                    "assertion_ids": tuple(
                        sorted(set(base.assertion_ids) | set(item.assertion_ids), key=str)
                    ),
                    "graph_paths": tuple(dict.fromkeys((*base.graph_paths, *item.graph_paths))),
                    "identity_mappings": tuple(
                        dict.fromkeys((*base.identity_mappings, *item.identity_mappings))
                    ),
                }
            )
    return sorted(merged.values(), key=lambda item: (-item.score, str(evidence_key(item))))


def rank_pool(items: list[EvidenceCandidate], top_k: int) -> list[EvidenceCandidate]:
    # Reserve one best hit per nonempty KG when k permits, then fill by RRF.
    best: dict[UUID, EvidenceCandidate] = {}
    for item in items:
        best.setdefault(item.kg_id, item)
    reserved = list(best.values()) if len(best) <= top_k else []
    reserved_keys = {evidence_key(item) for item in reserved}
    ordered = reserved + [item for item in items if evidence_key(item) not in reserved_keys]
    return ordered[:top_k]


def fuse_evidence(
    candidates: dict[str, list[EvidenceCandidate]], constant: int = 60
) -> list[EvidenceCandidate]:
    """Graph supplies novel sources and paths, not a correlated extra text vote.

    Vector/text establish the baseline. A graph hit already in that pool keeps
    its baseline score and gains authenticated path annotations. Graph-only hits
    compete by their graph rank; they do not receive a reserved context quota.
    An explicit semantic reranking is final, not another recall vote.
    """
    if "rerank" in candidates:
        recalled = fuse_evidence(
            {method: items for method, items in candidates.items() if method != "rerank"}, constant
        )
        known = {evidence_key(item) for item in recalled}
        keys = [evidence_key(item) for item in candidates["rerank"]]
        if len(keys) != len(set(keys)) or not set(keys) <= known:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Reranked evidence is inconsistent."
            )
        authenticated = {evidence_key(item): item for item in fuse(candidates, constant)}
        ranked = [
            authenticated[evidence_key(item)].model_copy(update={"score": item.score})
            for item in fuse({"rerank": candidates["rerank"]}, constant)
        ]
        return ranked + [item for item in recalled if evidence_key(item) not in set(keys)]
    merged = fuse(candidates, constant)  # Authenticate duplicate consistency first.
    if not candidates.get("graph"):
        return merged
    baseline = {
        evidence_key(item): item.score
        for item in fuse(
            {key: value for key, value in candidates.items() if key != "graph"}, constant
        )
    }
    result = [
        item.model_copy(update={"score": baseline[evidence_key(item)]})
        if evidence_key(item) in baseline
        and any(method in item.method_ranks for method in ("vector", "lexical"))
        else item
        for item in merged
    ]
    return sorted(result, key=lambda item: (-item.score, str(evidence_key(item))))


def recall_pool(
    candidates: dict[str, list[EvidenceCandidate]], limit: int = 50
) -> list[EvidenceCandidate]:
    """Admit independent recall branches before semantic comparison of original passages."""
    if not 1 <= limit <= 50 or "rerank" in candidates:
        raise ValueError("Recall pool requires unranked branches and a limit between 1 and 50.")
    merged = {evidence_key(item): item for item in fuse_evidence(candidates)}
    ordered: dict[tuple[object, ...], EvidenceCandidate] = {}
    for row in zip_longest(*candidates.values()):
        for item in row:
            if item is not None:
                key = evidence_key(item)
                ordered.setdefault(key, merged[key])
    return rank_pool(list(ordered.values()), limit)


def select_evidence(
    items: list[EvidenceCandidate], *, top_k: int, max_chars: int
) -> tuple[tuple[EvidenceCandidate, ...], bool]:
    if top_k < 1 or max_chars < 1:
        raise ValueError("Evidence budgets must be positive")
    unique: dict[tuple[object, ...], EvidenceCandidate] = {}
    for item in items:
        key = evidence_key(item)
        previous = unique.setdefault(key, item)
        if previous.text != item.text or previous.citation_ids != item.citation_ids:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Evidence is inconsistent.")
    pool = list(unique.values())
    groups: dict[UUID, list[EvidenceCandidate]] = {}
    for item in pool:
        groups.setdefault(item.kg_id, []).append(item)
    selected: dict[tuple[object, ...], EvidenceCandidate] = {}
    remaining = max_chars
    # Reserve a complete passage per KG when both count and text budgets permit.
    if len(groups) <= top_k:
        alternatives = list(groups.values())
        minimums = [min(len(item.text) for item in group) for group in alternatives]
        if sum(minimums) <= remaining:
            for index, group in enumerate(alternatives):
                allowance = remaining - sum(minimums[index + 1 :])
                item = next(item for item in group if len(item.text) <= allowance)
                selected[evidence_key(item)] = item
                remaining -= len(item.text)
        elif alternatives:
            # Impossible to represent every KG in full: retain explicit excerpts.
            # No path is presented as supported by a potentially cut-off passage.
            for index, group in enumerate(alternatives):
                allowance = remaining // (len(alternatives) - index)
                item = group[0]
                if allowance:
                    text = item.text[:allowance]
                    selected[evidence_key(item)] = item.model_copy(
                        update={"text": text, "graph_paths": ()}
                    )
                    remaining -= len(text)
    for item in pool:
        if len(selected) >= top_k:
            break
        key = evidence_key(item)
        if key not in selected and len(item.text) <= remaining:
            selected[key] = item
            remaining -= len(item.text)
    if not selected and pool:
        # More KGs than slots and no complete passage fits: return one explicit
        # excerpt instead of silently reporting no visible evidence.
        item = pool[0]
        selected[evidence_key(item)] = item.model_copy(
            update={"text": item.text[:max_chars], "graph_paths": ()}
        )
    result = tuple(selected[evidence_key(item)] for item in pool if evidence_key(item) in selected)
    truncated = len(result) < len(pool) or any(
        item.text != unique[evidence_key(item)].text for item in result
    )
    return result, truncated

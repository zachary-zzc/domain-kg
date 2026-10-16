"""Equal-weight reciprocal ranks; similarity scales never cross embedding spaces."""

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


def select_evidence(
    items: list[EvidenceCandidate], *, top_k: int, max_chars: int
) -> tuple[tuple[EvidenceCandidate, ...], bool]:
    chosen = rank_pool(items, top_k)
    selected = []
    remaining = max_chars
    truncated = len(items) > len(chosen)
    for index, item in enumerate(chosen):
        # Share the remaining text budget so the reserved KG slots remain useful.
        allowance = remaining // (len(chosen) - index)
        text = item.text[:allowance]
        if not text:
            truncated = True
            continue
        truncated |= len(text) < len(item.text)
        selected.append(item.model_copy(update={"text": text}))
        remaining -= len(text)
    return tuple(
        sorted(selected, key=lambda item: (-item.score, str(evidence_key(item))))
    ), truncated

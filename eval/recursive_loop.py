"""Bounded experimental extraction/verification/repair; never publishes a graph."""

import hashlib
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from domain_kg.errors import DomainError
from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.ingestion.models import (
    ExtractedAssertion,
    ExtractedEntity,
    ExtractedQualifier,
    ExtractionBatch,
    ExtractionContent,
    ParsedChunk,
)
from eval.recursive_models import Mentions, Proposal, Proposals, RecursiveResult, Verdicts
from eval.recursive_prompts import context

Complete = Callable[[str, dict, type[BaseModel]], Awaitable[dict]]


def batch(chunk: ParsedChunk, schema: DomainSchema, content: ExtractionContent) -> ExtractionBatch:
    return ExtractionBatch(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        material_id=chunk.material_id,
        version_id=chunk.version_id,
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=schema.revision,
        provider_profile_id=schema.id,
        entities=content.entities,
        assertions=content.assertions,
    )


def references(chunk: ParsedChunk, refs: tuple[int, ...]):
    if len(refs) != len(set(refs)) or any(i >= len(chunk.spans) for i in refs):
        raise ValueError("invalid_source_refs")
    return tuple(chunk.spans[i] for i in refs)


def proposal_id(proposal: Proposal) -> str:
    return hashlib.sha256(proposal.model_dump_json().encode()).hexdigest()[:24]


async def run_recursive(
    chunk: ParsedChunk, schema: DomainSchema, complete: Complete, *, max_repairs: int = 2
) -> RecursiveResult:
    if type(max_repairs) is not int or not 0 <= max_repairs <= 2:
        raise ValueError("At most two repair rounds")
    if (chunk.org_id, chunk.kg_id) != (schema.org_id, schema.kg_id):
        raise ValueError("Source/schema namespace mismatch")
    base = context(chunk, schema)
    trace, issues, snapshots = [], [], {}
    calls = 0

    async def call(stage, payload, response_model):
        nonlocal calls
        calls += 1
        raw = await complete(stage, base | payload, response_model)
        trace.append({"stage": stage, "response": raw})
        return raw

    def content(assertions):
        value = ExtractionContent(entities=tuple(entities.values()), assertions=tuple(assertions))
        validate_extraction(chunk, schema, batch(chunk, schema, value))
        return value

    entities: dict[str, ExtractedEntity] = {}
    mention_data = Mentions.model_validate(await call("mentions", {}, Mentions))
    for mention in mention_data.entities:
        try:
            if mention.name in entities:
                raise ValueError("duplicate_entity_name")
            entity = ExtractedEntity(
                local_id=f"e{len(entities)}",
                name=mention.name,
                entity_type=mention.type,
                spans=references(chunk, mention.refs),
            )
            validate_extraction(
                chunk,
                schema,
                batch(chunk, schema, ExtractionContent(entities=(entity,), assertions=())),
            )
            entities[entity.name] = entity
        except (ValueError, DomainError) as error:
            issues.append(
                {
                    "kind": "entity_invalid",
                    "candidate": mention.model_dump(mode="json"),
                    "detail": str(error)[:200],
                }
            )
    hints = {"entities": [{"name": e.name, "type": e.entity_type} for e in entities.values()]}
    raw = await call("relations", hints, Proposals)
    accepted: dict[tuple, ExtractedAssertion] = {}
    seen: set[str] = set()
    previous_issues = issues[:]

    for round_number in range(max_repairs + 1):
        current_issues = []
        candidates = {}
        proposed = Proposals.model_validate(raw)
        for proposal in proposed.relations:
            identifier = proposal_id(proposal)
            if identifier in seen:
                continue
            seen.add(identifier)
            try:
                subject, target = entities[proposal.subject], entities[proposal.target]
                if subject == target:
                    raise ValueError("self_relation")
                assertion = ExtractedAssertion(
                    subject=subject.local_id,
                    target=target.local_id,
                    predicate=proposal.predicate,
                    polarity=proposal.polarity,
                    confidence=0.5,  # Fixed compatibility value; never a semantic probability.
                    spans=references(chunk, proposal.refs),
                    qualifiers=tuple(
                        ExtractedQualifier(
                            name=q.name, value=q.value, spans=references(chunk, q.refs)
                        )
                        for q in proposal.qualifiers
                    ),
                )
                content([assertion])
                candidates[identifier] = (proposal, assertion)
            except (KeyError, ValueError, DomainError) as error:
                current_issues.append(
                    {
                        "kind": "source_or_schema_invalid",
                        "candidate": proposal.model_dump(mode="json"),
                        "detail": str(error)[:200],
                    }
                )
        if round_number == 0:
            snapshots["decomposed"] = content([a for _, a in candidates.values()])
        if candidates:
            # Long content hashes remain internal; small models copy short scoped labels.
            candidates = {f"c{i}": value for i, value in enumerate(candidates.values())}
            payload = hints | {
                "candidates": [
                    {"id": key, **p.model_dump(mode="json")} for key, (p, _) in candidates.items()
                ]
            }
            try:
                verdicts = Verdicts.model_validate(await call("verify", payload, Verdicts))
                ids = [v.id for v in verdicts.verdicts]
                if len(set(ids)) != len(ids) or set(ids) != set(candidates):
                    raise ValueError("review_ids_must_match_exact_candidates")
                for verdict in verdicts.verdicts:
                    spans = references(chunk, verdict.refs)
                    if verdict.status == "supported":
                        assertion = candidates[verdict.id][1]
                        content([assertion.model_copy(update={"spans": spans})])
                for verdict in verdicts.verdicts:
                    proposal, assertion = candidates[verdict.id]
                    if verdict.status == "supported":
                        key = (
                            assertion.subject,
                            assertion.predicate,
                            assertion.target,
                            assertion.polarity,
                            tuple((q.name, q.value) for q in assertion.qualifiers),
                        )
                        accepted.setdefault(key, assertion)
                    else:
                        current_issues.append(
                            {
                                "kind": "semantic_" + verdict.status,
                                "candidate": proposal.model_dump(mode="json"),
                            }
                        )
            except (ValueError, ValidationError, DomainError) as error:
                current_issues.append({"kind": "invalid_review", "detail": str(error)[:200]})
        snapshots[f"verified_{round_number}"] = content(accepted.values())
        issues.extend(current_issues)
        previous_issues = previous_issues + current_issues
        if round_number == max_repairs:
            break
        # One bounded coverage pass even after a clean draft; later passes only for failures.
        if round_number and not current_issues:
            break
        raw = await call(
            "repair",
            hints
            | {
                "issues": [
                    {key: value for key, value in item.items() if key != "detail"}
                    for item in previous_issues[-8:]
                ],
                "accepted": [
                    {
                        "subject": next(
                            e.name for e in entities.values() if e.local_id == a.subject
                        ),
                        "predicate": a.predicate,
                        "target": next(e.name for e in entities.values() if e.local_id == a.target),
                        "polarity": a.polarity,
                    }
                    for a in accepted.values()
                ],
            },
            Proposals,
        )
    return RecursiveResult(
        accepted=content(accepted.values()),
        snapshots=snapshots,
        issues=tuple(issues),
        calls=calls,
        trace=tuple(trace),
    )

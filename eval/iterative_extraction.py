"""Five-attempt experiment ledger and minimal source-grounded relation protocol."""

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from pydantic import Field

from domain_kg.errors import DomainError
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.ingestion.models import ExtractedAssertion, ExtractedEntity, ExtractionContent
from domain_kg.models import FrozenModel
from domain_kg.providers.compact import References
from eval.recursive_loop import batch, references
from eval.recursive_models import Proposal


class FlatRelation(FrozenModel):
    subject: Annotated[str, Field(min_length=1, max_length=128)]
    predicate: Annotated[str, Field(min_length=1, max_length=40)]
    target: Annotated[str, Field(min_length=1, max_length=128)]
    polarity: Annotated[str, Field(pattern="^(positive|negative|uncertain)$")]
    refs: References


class FlatContent(FrozenModel):
    relations: Annotated[tuple[FlatRelation, ...], Field(max_length=3)]


SURFACE_VERBS = (
    "is a",
    "is an",
    "known as",
    "produces",
    "makes",
    "made by",
    "produced by",
    "uses",
    "does not use",
    "used by",
)


class SurfaceRelation(FrozenModel):
    subject: Annotated[str, Field(min_length=1, max_length=128)]
    verb: Annotated[str, Field(min_length=1, max_length=40)]
    target: Annotated[str, Field(min_length=1, max_length=128)]
    refs: References


class SurfaceContent(FrozenModel):
    relations: Annotated[tuple[SurfaceRelation, ...], Field(max_length=3)]


class Iterations:
    def __init__(self, path: Path):
        self.path = path
        self.records = json.loads(path.read_text()) if path.exists() else []

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + "." + uuid4().hex)
        temp.write_text(json.dumps(self.records, indent=2, ensure_ascii=False) + "\n")
        temp.chmod(0o600)
        temp.replace(self.path)

    def begin(self, number, *, hypothesis):
        if any(r["number"] == number for r in self.records):
            raise RuntimeError("This attempt already exists; it cannot be reset")
        if number > 5 or len(self.records) >= 5:
            raise RuntimeError("Stop after five unsuccessful attempts")
        if number != len(self.records) + 1 or any(r["status"] == "running" for r in self.records):
            raise RuntimeError("Finish the existing attempt before advancing")
        if any(r["status"] == "success" for r in self.records):
            raise RuntimeError("Experiment already succeeded; do not spend further attempts")
        self.records.append({"number": number, "hypothesis": hypothesis, "status": "running"})
        self._save()

    def finish(self, number, *, success, result):
        if (
            not self.records
            or self.records[-1]["number"] != number
            or (self.records[-1]["status"] != "running")
        ):
            raise RuntimeError("No matching running attempt")
        self.records[-1].update(status="success" if success else "failed", result=result)
        self._save()


def source_name(value, chunk):
    value = value.strip()
    if any(
        re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", span.quote) for span in chunk.spans
    ):
        return value
    matches = set()
    for span in chunk.spans:
        for match in re.finditer(r"(?<!\w)" + re.escape(value) + r"(?!\w)", span.quote, re.I):
            matches.add(match.group())
    if len(matches) != 1:
        raise ValueError("Endpoint must uniquely align to an original source spelling")
    return matches.pop()


def ground(raw, chunk, schema):
    proposals = FlatContent.model_validate(raw)
    entities, assertions, rejected = {}, {}, []
    for candidate in proposals.relations:
        try:
            proposal = Proposal.model_validate(candidate.model_dump())
            spans = references(chunk, proposal.refs)
            names = [source_name(name, chunk) for name in (proposal.subject, proposal.target)]
            if names[0] == names[1]:
                raise ValueError("Self relation")
            local = {}
            for name in names:
                # This protocol measures relations only. It does not claim medical type inference.
                entity = entities.get(name) or ExtractedEntity(
                    local_id="e" + hashlib.sha256(name.encode()).hexdigest()[:16],
                    name=name,
                    entity_type="concept",
                    spans=spans,
                )
                local[name] = entity
            assertion = ExtractedAssertion(
                subject=local[names[0]].local_id,
                predicate=proposal.predicate,
                target=local[names[1]].local_id,
                polarity=proposal.polarity,
                confidence=0.5,
                spans=spans,
            )
            check = ExtractionContent(entities=tuple(local.values()), assertions=(assertion,))
            validate_extraction(chunk, schema, batch(chunk, schema, check))
            entities.update(local)
            assertions.setdefault(
                (names[0], proposal.predicate, names[1], proposal.polarity), assertion
            )
        except (ValueError, DomainError) as error:
            rejected.append(
                {"candidate": candidate.model_dump(mode="json"), "reason": str(error)[:200]}
            )
    result = ExtractionContent(
        entities=tuple(entities.values()), assertions=tuple(assertions.values())
    )
    validate_extraction(chunk, schema, batch(chunk, schema, result))
    return result, rejected


def ground_surface(raw, chunk, schema):
    """Narrow English surface patterns, not general entailment or medical validation."""
    proposals = SurfaceContent.model_validate(raw)
    grounded, rejected = [], []
    for candidate in proposals.relations:
        try:
            spans = references(chunk, candidate.refs)
            text = re.sub(r"\s+", " ", " ".join(s.quote for s in spans))
            subject, target = (source_name(n, chunk) for n in (candidate.subject, candidate.target))
            if subject.casefold() in {"it", "this", "that", "they", "he", "she"}:
                raise ValueError("A pronoun requires an explicit named antecedent candidate")
            s, t = re.escape(subject), re.escape(target)
            verb = candidate.verb.casefold()
            polarity = "positive"
            if verb in {"is a", "is an"}:
                predicate, pattern = "is_a", s + r"\s+is\s+an?\s+" + t
            elif verb == "known as":
                predicate = "is_a"
                pattern = s + r"\s+is\s+an?\s+[^.!?;]{1,100}?\s+known as\s+an?\s+" + t
            elif verb in {"made by", "produced by", "used by"}:
                predicate = "uses" if verb == "used by" else "produces"
                passive = r"\s+(?:(?:is|was|are|were)\s+)?(?:mainly\s+)?" + verb
                direct = s + passive + r"\s+(?:the\s+)?" + t
                # Explicitly bounded heuristic: immediately preceding subject sentence only.
                antecedent = s + r"\s+is\s+[^.!?]*\.\s*It" + passive + r"\s+(?:the\s+)?" + t
                pattern = "(?:" + direct + "|" + antecedent + ")"
                subject, target = target, subject
            elif verb in {"produces", "makes", "uses", "does not use"}:
                predicate = "produces" if verb in {"produces", "makes"} else "uses"
                polarity = "negative" if verb == "does not use" else "positive"
                pattern = s + r"\s+" + verb + r"\s+(?:(?:a|an|the)\s+)?" + t
            else:
                raise ValueError("Unknown literal relation phrase")
            if not re.search(r"(?<!\w)" + pattern + r"(?!\w)", text, re.I):
                raise ValueError("No matching source clause; co-occurrence is insufficient")
            grounded.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "target": target,
                    "polarity": polarity,
                    "refs": list(candidate.refs),
                }
            )
        except (ValueError, DomainError) as error:
            rejected.append(
                {"candidate": candidate.model_dump(mode="json"), "reason": str(error)[:200]}
            )
    result, canonical_rejected = ground({"relations": grounded}, chunk, schema)
    return result, rejected + canonical_rejected

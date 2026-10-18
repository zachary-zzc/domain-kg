"""Experimental protocol; no model response can issue a database or tool command."""

from typing import Annotated, Literal

from pydantic import Field

from domain_kg.ingestion.models import ExtractionContent
from domain_kg.models import FrozenModel
from domain_kg.providers.compact import RefEntity, References, RefQualifier


class Mentions(FrozenModel):
    entities: Annotated[tuple[RefEntity, ...], Field(max_length=16)]


class Proposal(FrozenModel):
    subject: Annotated[str, Field(min_length=1, max_length=512)]
    predicate: Annotated[str, Field(min_length=1, max_length=200)]
    target: Annotated[str, Field(min_length=1, max_length=512)]
    polarity: Literal["positive", "negative", "uncertain"]
    refs: References
    qualifiers: Annotated[tuple[RefQualifier, ...], Field(max_length=3)] = ()


class Proposals(FrozenModel):
    relations: Annotated[tuple[Proposal, ...], Field(max_length=8)]


class Verdict(FrozenModel):
    id: Annotated[str, Field(min_length=1, max_length=64)]
    status: Literal["supported", "unsupported", "insufficient"]
    refs: References


class Verdicts(FrozenModel):
    verdicts: Annotated[tuple[Verdict, ...], Field(max_length=8)]


class RecursiveResult(FrozenModel):
    accepted: ExtractionContent
    snapshots: dict[str, ExtractionContent]
    issues: tuple[dict, ...]
    calls: int
    trace: tuple[dict, ...]

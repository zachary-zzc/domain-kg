"""Configured, bounded pipeline steps; an incomplete pipeline never implies publication."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import TypeAdapter

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.models import Checkpoint, LeasedJob, PreparationStage
from domain_kg.jobs.queue import STAGES
from domain_kg.models import SHA256


@dataclass(frozen=True)
class PipelineStep:
    stage: PreparationStage
    input_hash: str
    execute: Callable[[LeasedJob], Awaitable[Checkpoint]]
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        TypeAdapter(SHA256).validate_python(self.input_hash)
        if self.stage not in STAGES or not 1 <= self.timeout_seconds <= 300:
            raise ValueError("invalid bounded pipeline step")


@dataclass(frozen=True)
class Pipeline:
    steps: tuple[PipelineStep, ...] = ()

    def __post_init__(self) -> None:
        order = [STAGES.index(step.stage) for step in self.steps]
        if order != sorted(set(order)):
            raise ValueError("pipeline stages must be unique and ordered")

    @property
    def configured(self) -> bool:
        return tuple(step.stage for step in self.steps) == STAGES

    def require_configured(self) -> None:
        if not self.configured:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Configure all ingestion and publication steps before starting a worker.",
            )

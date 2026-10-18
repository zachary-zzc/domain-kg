"""Bounded operator-owned local evaluation inputs, independent of application APIs."""

import json
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from domain_kg.models import FrozenModel
from domain_kg.retrieval.models import Mode, SearchFilters


class EvaluationCase(FrozenModel):
    id: Annotated[str, Field(min_length=1, max_length=100)]
    split: Literal["development", "held_out"]
    category: Annotated[str, Field(min_length=1, max_length=100)]
    query: Annotated[str, Field(min_length=1, max_length=8000)]
    filters: SearchFilters = SearchFilters()
    expected: tuple[Literal["public", "plain"], ...]
    must_be_empty: bool = False


class EvaluationConfig(FrozenModel):
    version: Literal[1]
    provider_mode: Literal["synthetic"]
    cases: str
    modes: Annotated[tuple[Mode, ...], Field(min_length=1, max_length=5)]
    top_k: Annotated[int, Field(ge=1, le=50, strict=True)]
    max_cases: Annotated[int, Field(ge=1, le=32, strict=True)]
    timeout_seconds: Annotated[int, Field(ge=30, le=1800, strict=True)]


def load(path: Path) -> tuple[EvaluationConfig, tuple[EvaluationCase, ...], bytes]:
    config = EvaluationConfig.model_validate(tomllib.loads(path.read_text()))
    raw = (path.parent / config.cases).read_bytes()
    if len(raw) > 1024 * 1024 or len(set(config.modes)) != len(config.modes):
        raise ValueError("evaluation input exceeds its budget or repeats a mode")
    cases = tuple(EvaluationCase.model_validate(json.loads(line)) for line in raw.splitlines())
    if (
        not cases
        or len(cases) > config.max_cases
        or len({case.id for case in cases}) != len(cases)
        or {case.split for case in cases} != {"development", "held_out"}
        or any(case.must_be_empty and case.expected for case in cases)
    ):
        raise ValueError("evaluation must have distinct development/held-out cases")
    return config, cases, raw

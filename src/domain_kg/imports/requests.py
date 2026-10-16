"""Import operations carry only typed uploads/plans, never credentials or storage paths."""

from typing import Annotated, Literal

from pydantic import Field

from domain_kg.imports.models import ImportPlan, ImportSource
from domain_kg.models import FrozenModel


class PlanImport(FrozenModel):
    operation: Literal["plan"]
    source: ImportSource


class ExecuteImport(FrozenModel):
    operation: Literal["execute"]
    plan: ImportPlan


ImportRequest = Annotated[PlanImport | ExecuteImport, Field(discriminator="operation")]

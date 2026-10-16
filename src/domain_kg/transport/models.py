"""Transport selectors and safe responses; none of these establish caller identity."""

from uuid import UUID

from domain_kg.graph.identity_commands import IdentityImpact
from domain_kg.models import FrozenModel, NonNegative
from domain_kg.revisions.models import CurationImpact, CurationReceipt, RevisionReceipt


class KGSummary(FrozenModel):
    id: UUID
    name: str
    state: str
    revision: NonNegative
    published_revision: NonNegative = 0
    storage_profile_id: UUID | None = None
    domain_profile_id: UUID | None = None
    generation: UUID | None = None
    visibility_revision: NonNegative | None = None


class KGPage(FrozenModel):
    items: tuple[KGSummary, ...]
    next_cursor: str | None = None


class Health(FrozenModel):
    kg_id: UUID
    status: str


class CurationResult(FrozenModel):
    result: CurationReceipt | CurationImpact | IdentityImpact | RevisionReceipt

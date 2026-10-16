"""Prepared imports cannot cross sources or invent document/manual support."""

from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_identity_graph_views import graph_fixture

from domain_kg.graph.schema import schema_for
from domain_kg.imports.prepared import PreparedImport
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.jobs.models import Checkpoint
from domain_kg.models import Namespace


def test_prepared_import_requires_exact_source_origin_and_unverified_rows():
    data, *_ = graph_fixture()
    source = data.scope.visible_sources[0]
    ns = Namespace(org_id=source.org_id, kg_id=source.kg_id)
    origin = ImportOrigin(
        **source.model_dump(),
        plan_id=uuid4(),
        plan_hash="a" * 64,
        graph_hash="b" * 64,
        origin="fixture",
    )
    rows = tuple(row for row in data.rows if row.source == source)
    values = dict(
        **source.model_dump(),
        import_origin=origin,
        domain_schema=schema_for(ns, "general"),
        chunk_checkpoint=Checkpoint(
            **ns.model_dump(),
            stage="chunking",
            job_id=uuid4(),
            artifact_id=uuid4(),
            input_hash="a" * 64,
            output_hash="a" * 64,
        ),
        rows=rows,
    )
    with pytest.raises(ValidationError):
        PreparedImport(**values)
    unverified_rows = []
    for row in rows:
        changes = {}
        for field in ("observation", "subject_observation", "target_observation"):
            observation = getattr(row, field)
            if observation:
                changes[field] = observation.model_copy(update={"spans": (), "description": ""})
        if row.assertion:
            changes["assertion"] = row.assertion.model_copy(
                update={
                    "support": row.assertion.support.model_copy(
                        update={
                            "status": "imported_unverified",
                            "spans": (),
                        }
                    )
                }
            )
        unverified_rows.append(row.model_copy(update=changes))
    unverified = tuple(unverified_rows)
    document = PreparedImport(**(values | {"rows": unverified}))
    assert document.import_origin == origin
    with pytest.raises(ValidationError):
        PreparedImport.model_validate(document.model_copy(update={"version_id": uuid4()}))
    with pytest.raises(ValidationError):
        PreparedImport.model_validate(
            document.model_copy(
                update={
                    "rows": (
                        unverified[0].model_copy(update={"source": data.scope.visible_sources[1]}),
                    )
                }
            )
        )
    invented = unverified[0].model_copy(
        update={
            "observation": unverified[0].observation.model_copy(
                update={
                    "spans": rows[0].observation.spans,
                }
            )
        }
    )
    with pytest.raises(ValidationError):
        PreparedImport.model_validate(
            document.model_copy(
                update={
                    "rows": (invented, *unverified[1:]),
                }
            )
        )

"""A backup envelope cannot normalize strings in an evidence or history ledger."""

from uuid import uuid4

from domain_kg.operations.backup_models import AuthoritySection


def test_authority_package_preserves_whitespace_in_every_nested_literal_field():
    namespace = {"org_id": str(uuid4()), "kg_id": str(uuid4())}
    text = "  original CSV text\n"
    section = AuthoritySection.model_validate(
        namespace
        | dict(
            backup_id=uuid4(),
            area="authority",
            table="source_chunks",
            records=[
                namespace
                | {
                    "id": str(uuid4()),
                    "text": text,
                    "spans": [{"quote": text}],
                    "description": " manual reason ",
                }
            ],
        )
    )
    record = section.records[0]
    assert record["text"] == text
    assert record["spans"][0]["quote"] == text
    assert record["description"] == " manual reason "
    assert AuthoritySection.model_validate_json(section.model_dump_json()) == section

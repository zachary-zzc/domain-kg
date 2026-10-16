"""Identity authority shapes, separate from source/KG projection schemas."""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase


async def install_identity_schema(database: AsyncDatabase[dict[str, Any]]) -> None:
    string = {"bsonType": "string"}
    identifier = {"bsonType": "string", "pattern": "^[0-9a-f]{8}-[0-9a-f-]{27}$"}
    definitions: dict[str, dict[str, Any]] = {
        "organizations": {
            "_id": identifier,
            "name": {"bsonType": "string", "minLength": 1},
            "policy_revision": {"bsonType": ["int", "long"], "minimum": 0},
            "active_admins": {"bsonType": ["int", "long"], "minimum": 1},
            "material_epoch": {"bsonType": ["int", "long"], "minimum": 0},
        },
        "members": {
            "_id": identifier,
            "id": identifier,
            "org_id": identifier,
            "issuer": string,
            "subject": {"bsonType": "string", "minLength": 1},
            "role": {"enum": ["admin", "member"]},
            "kind": {"enum": ["human", "service"]},
            "suspended": {"bsonType": "bool"},
            "revision": {"bsonType": ["int", "long"], "minimum": 0},
            "kg_ids": {"bsonType": "array", "items": identifier},
            "scopes": {"bsonType": "array", "items": string},
        },
        "groups": {
            "_id": identifier,
            "id": identifier,
            "org_id": identifier,
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 200},
            "member_ids": {"bsonType": "array", "items": identifier},
        },
    }
    existing = await database.list_collection_names()
    for name, properties in definitions.items():
        validator = {
            "$jsonSchema": {
                "bsonType": "object",
                "required": [key for key in properties if key != "material_epoch"],
                "properties": properties,
                "additionalProperties": False,
            }
        }
        if name in existing:
            await database.command(
                {
                    "collMod": name,
                    "validator": validator,
                    "validationLevel": "strict",
                    "validationAction": "error",
                }
            )
        else:
            await database.create_collection(
                name, validator=validator, validationLevel="strict", validationAction="error"
            )

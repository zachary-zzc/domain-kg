"""Provision the first organization and invited admin from operator-owned settings.

This CLI creates an invitation; it does not authenticate the subject. The external
provider must verify that exact issuer/subject at login. Subsequent admission uses
the authenticated organization APIs. No passwords or provider tokens are accepted.
"""

import argparse
import asyncio
import json

import httpx
from pymongo import AsyncMongoClient

from domain_kg.identity.models import Principal
from domain_kg.identity.oidc import OIDCVerifier
from domain_kg.identity.repository import IdentityRepository
from domain_kg.settings import Settings


async def provision(name: str, subject: str) -> None:
    settings = Settings()
    if not settings.oidc_client_id or not name.strip() or not subject:
        raise ValueError(
            "Configure an external OIDC client and provide an organization and subject."
        )
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        await OIDCVerifier(settings.oidc_issuer, str(settings.public_url), client).metadata()
    mongo = AsyncMongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000)
    try:
        repository = IdentityRepository(mongo[settings.mongo_database])
        await repository.initialize()
        if await repository.database.organizations.count_documents({}) != 0:
            raise ValueError("An organization already exists; use authenticated member admission.")
        org, member = await repository.bootstrap_organization(
            Principal(issuer=settings.oidc_issuer, subject=subject), name.strip()
        )
        print(json.dumps({"org_id": str(org), "invited_admin_id": str(member.id)}))
    finally:
        await mongo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--subject", required=True, help="Exact subject assigned by the configured IdP"
    )
    args = parser.parse_args()
    asyncio.run(provision(args.name, args.subject))


if __name__ == "__main__":
    main()

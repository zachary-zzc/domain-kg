"""Invite an existing member as an operator from offline server configuration.

The subject is the invitation target, not an authenticated caller. This does not
log in, register infrastructure, or grant KG data access. Login still uses OIDC.
"""

import argparse
import asyncio

from pymongo import AsyncMongoClient

from domain_kg.catalog.backends import invite_operator
from domain_kg.settings import Settings


async def run(subject: str) -> None:
    settings = Settings()
    client = AsyncMongoClient(settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000)
    try:
        await invite_operator(client[settings.mongo_database], settings.oidc_issuer, subject)
        print("Operator invitation recorded; external OIDC login is still required.")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject", required=True, help="Exact subject of an existing human member"
    )
    asyncio.run(run(parser.parse_args().subject))

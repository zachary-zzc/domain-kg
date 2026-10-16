"""Initialize/wait for the laptop replica set without a Node health-check process."""

import asyncio
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure, PyMongoError

from domain_kg.settings import Settings


async def main() -> None:
    settings = Settings()
    uri = urlsplit(settings.mongo_uri.get_secret_value())
    if uri.hostname not in ("127.0.0.1", "localhost") or uri.port != 27027:
        raise RuntimeError("Development initialization only supports the local smoke Mongo.")
    query = [(key, value) for key, value in parse_qsl(uri.query) if key != "replicaSet"]
    client = AsyncMongoClient(
        urlunsplit(uri._replace(query=urlencode(query))),
        serverSelectionTimeoutMS=2000,
        maxPoolSize=2,
    )
    try:
        for _ in range(60):
            try:
                state = await client.admin.command("hello")
                if state.get("setName") == "rs0" and state.get("isWritablePrimary"):
                    print("Local Mongo rs0 primary verified.")
                    return
                if state.get("setName") not in (None, "rs0"):
                    raise RuntimeError("Unexpected replica set; initialization refused.")
                if state.get("setName") is None:
                    try:
                        await client.admin.command(
                            "replSetInitiate",
                            {
                                "_id": "rs0",
                                "members": [{"_id": 0, "host": "mongo:27017"}],
                            },
                        )
                    except OperationFailure as exc:
                        if exc.code != 23:  # AlreadyInitialized during recovery/concurrent init.
                            raise
            except PyMongoError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Local Mongo primary did not become ready within 30 seconds.")
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (RuntimeError, PyMongoError):
        print(
            "Local Mongo replica-set initialization failed; inspect local service state.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

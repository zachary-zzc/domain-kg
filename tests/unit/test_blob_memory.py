"""Concurrent uploads must share one backend writer budget, including cancellation."""

import asyncio
import hashlib
import threading
import time
from uuid import uuid4

from domain_kg.materials.models import BlobRef, BlobScope
from domain_kg.models import Namespace
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.router import BlobBinding


async def test_upload_writer_budget_stays_held_until_cancelled_io_finishes():
    class Client:
        def __init__(self):
            self.active = self.peak = 0
            self.started = threading.Event()
            self.lock = threading.Lock()

        def put_object(self, **_):
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            self.started.set()
            try:
                time.sleep(0.05)
            finally:
                with self.lock:
                    self.active -= 1
            return {}

    client = Client()
    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    binding = BlobBinding(namespace, client, "fixture")
    body = b"Small public fixture."

    async def upload():
        async def stream():
            yield body

        return await BlobStore(binding).put(
            stream(),
            hashlib.sha256(body).hexdigest(),
            BlobScope(**namespace.model_dump(), material_id=uuid4(), version_id=uuid4()),
        )

    first = asyncio.create_task(upload())
    assert await asyncio.to_thread(client.started.wait, 2)
    first.cancel()
    second = asyncio.create_task(upload())
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert isinstance(outcomes[1], BlobRef)
    assert client.peak == 1 and client.active == 0

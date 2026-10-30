"""Evaluation circuit breaker; drain already submitted requests before stopping."""

import asyncio

from eval.scale_transport import Runner as ImmutableRunner


class Runner(ImmutableRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stopped = None

    def check_running(self):
        if self.stopped:
            raise RuntimeError(self.stopped)

    async def throttle(self, model):
        self.check_running()
        await super().throttle(model)
        self.check_running()

    def reserve(self, label, payload):
        self.check_running()
        return super().reserve(label, payload)

    async def call(self, client, label, payload):
        self.check_running()
        try:
            row = await super().call(client, label, payload)
        except RuntimeError:
            self.stopped = "Provider authorization failed; evaluation stopped"
            raise
        error = (row.get("response") or {}).get("error")
        if isinstance(error, dict) and error.get("code") == "Arrearage":
            self.stopped = "Provider Arrearage: account billing prevents evaluation"
            raise RuntimeError(self.stopped)
        return row


async def pool(jobs, worker, concurrency=32):
    iterator = iter(jobs)
    stopped = False

    async def consume():
        nonlocal stopped
        for job in iterator:
            if stopped:
                break
            try:
                await worker(job)
            except Exception:
                stopped = True
                raise

    outcomes = await asyncio.gather(
        *(consume() for _ in range(concurrency)), return_exceptions=True
    )
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome

import json

import httpx
import pytest

from eval.source_role_eval import EXTRACTOR
from eval.source_role_expand import CumulativeLedger
from eval.source_role_production import ReservedTransport
from eval.source_role_run import ENDPOINT, read


async def test_production_probe_reserves_before_http_without_rewriting_wire(tmp_path):
    ledger = CumulativeLedger(tmp_path, [], max_calls=1, max_rmb=1)
    payload = {
        "model": EXTRACTOR,
        "max_completion_tokens": 128,
        "messages": [{"role": "user", "content": "public fixture"}],
    }
    calls = []

    def respond(request):
        assert len(ledger.launches) == 1
        assert json.loads(request.content) == payload
        assert "max_tokens" not in json.loads(request.content)
        calls.append(request)
        return httpx.Response(400, json={"error": "unsupported format"})

    transport = ReservedTransport(ledger, "probe", httpx.MockTransport(respond))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.post(ENDPOINT, json=payload)
        assert response.status_code == 400
        with pytest.raises(ValueError, match="cannot be repeated"):
            await client.post(ENDPOINT, json=payload)
    assert len(calls) == 1 and ledger.reserved > 0
    record = read(next(tmp_path.glob("*.wire.json")))
    assert record["payload_sha256"] != record["ledger_payload_sha256"]

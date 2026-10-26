"""Native traffic counters include source responses and durable upload retries."""

import hashlib
import json

import httpx

from tsetmc_marketdata.config import Settings
from tsetmc_marketdata.providers.http_client import TSETMCClient
from tsetmc_marketdata.simple_delivery import PendingUpload
from tsetmc_marketdata.simple_traffic import TrafficMeter


async def test_source_and_upload_traffic_survive_restart(tmp_path):
    meter = TrafficMeter(tmp_path / "traffic.json")
    body = b'{"price":123}'
    async with TSETMCClient(
        Settings(pacing_seconds=0),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
        traffic=meter,
    ) as source:
        await source.get_json("test")
    assert meter.bytes["source_received"] == len(body)

    requests = []

    def respond(request):
        requests.append(request.content)
        if len(requests) == 1:
            return httpx.Response(503, text="retry")
        batch = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "batch_id": batch["batch_id"],
                "sha256": hashlib.sha256(request.content).hexdigest(),
                "accepted": 1,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        pending = PendingUpload("http://company", tmp_path / "history.json", http, meter)
        pending.stage([{"synthetic": True}])
        try:
            await pending.flush()
        except httpx.HTTPStatusError:
            pass
        assert pending.path.exists()
        await pending.flush()
    assert requests[0] == requests[1]
    assert meter.bytes["api_sent"] == 2 * len(requests[0])
    assert meter.bytes["api_received"] > 0
    meter.report()
    assert TrafficMeter(tmp_path / "traffic.json").bytes == meter.bytes

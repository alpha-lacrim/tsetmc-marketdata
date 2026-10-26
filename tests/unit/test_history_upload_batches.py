import pytest

from tsetmc_marketdata.simple_delivery import history_batches


@pytest.mark.parametrize("count", [0, 1, 99, 100, 101, 501, 2501])
def test_history_batches_prioritize_recent_rows_without_loss(count):
    rows = list(range(count))
    batches = list(history_batches(rows))
    assert all(0 < len(batch) <= 100 and batch == sorted(batch) for batch in batches)
    flattened = [row for batch in batches for row in batch]
    assert sorted(flattened) == rows
    assert len(flattened) == len(set(flattened))
    if batches:
        assert batches[0][-1] == count - 1
        assert [batch[0] for batch in batches] == sorted(
            (batch[0] for batch in batches), reverse=True
        )


async def test_recent_history_reaches_both_instruments_before_deep_backfill(monkeypatch, tmp_path):
    import asyncio
    import hashlib
    import json
    from types import SimpleNamespace

    import httpx

    from tsetmc_marketdata import simple_collector as collector

    class Source:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def instrument(source, code):
        return SimpleNamespace(
            symbol="Synthetic", company_name="Synthetic", instrument_id="test", isin=None
        ), None

    async def history(source, code):
        return [dict(symbol=code, interval="1d", sequence=i, metadata={}) for i in range(251)]

    async def current(source, code):
        return []

    sent = []
    original_client = httpx.AsyncClient

    def response(request):
        batch = json.loads(request.content)
        sent.append(batch["observations"])
        return httpx.Response(
            200,
            json=dict(
                batch_id=batch["batch_id"],
                sha256=hashlib.sha256(request.content).hexdigest(),
                accepted=len(batch["observations"]),
            ),
        )

    rounds = 0

    async def sleep(seconds):
        nonlocal rounds
        if seconds == 60:
            rounds += 1
            if rounds == 1:
                assert len(sent) == 2
                assert not (tmp_path / "history-completed.json").exists()
                return
        raise asyncio.CancelledError

    monkeypatch.setattr(collector, "TSETMCClient", lambda _: Source())
    monkeypatch.setattr(
        collector.httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(response), **kw),
    )
    monkeypatch.setattr(collector, "fetch_instrument", instrument)
    monkeypatch.setattr(collector, "collect_history", history)
    monkeypatch.setattr(collector, "collect_current", current)
    monkeypatch.setattr(collector.asyncio, "sleep", sleep)
    await collector.run(
        SimpleNamespace(
            api_url="http://company",
            state_dir=tmp_path,
            ins_code=["123", "456"],
            once=False,
            history_seconds=86400,
            poll_seconds=15,
        )
    )
    assert [batch[0]["symbol"] for batch in sent[:2]] == ["123", "456"]
    assert [len(batch) for batch in sent] == [100, 100, 100, 100, 51, 100, 100, 51]
    assert sent[0][0]["sequence"] == 151
    assert sent[0][0]["metadata"]["history_delivery"]["full_upload_pending"] is True
    assert "history_delivery" not in sent[2][0]["metadata"]
    completed = json.loads((tmp_path / "history-completed.json").read_text())["completed"]
    assert set(completed) == {"123", "456"}

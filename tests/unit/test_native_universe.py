"""Broad native collection keeps identities isolated and resumes acknowledged history."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from tsetmc_marketdata import simple_collector as collector
from tsetmc_marketdata.errors import ErrorKind, SourceDataError
from tsetmc_marketdata.providers.universe import share_codes, top_share_codes


def test_discovery_excludes_options_rights_and_secondary_lines():
    def row(code, kind=300, instrument="IRO1TEST0001"):
        return dict(insCode=code, yVal=kind, insID=instrument)

    assert share_codes(
        {
            "marketwatch": [
                row("123"),
                row("456", 303, "IRO3TEST0001"),
                row("789", 311, "IRO9TEST0001"),
                row("100", 400, "IRR1TEST0101"),
                row("101", 300, "IRO1TEST0003"),
            ]
        }
    ) == ["123", "456"]
    for payload in (
        {},
        {"marketwatch": []},
        {"marketwatch": [row(False)]},
        {"marketwatch": [row("123"), row("123")]},
        {"marketwatch": [row("0")]},
    ):
        with pytest.raises(SourceDataError):
            share_codes(payload)


@pytest.mark.parametrize("default_watchlist", [False, True])
@pytest.mark.parametrize("bad_history", [False, True, "empty", "partial"])
async def test_two_instruments_delivery_failure_isolation_and_restart(
    monkeypatch, tmp_path, bad_history, default_watchlist
):
    class Source:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    calls, uploads = [], []

    async def instrument(source, code):
        return SimpleNamespace(
            symbol="synthetic " + code,
            company_name="Synthetic",
            instrument_id="test-" + code,
            isin=None,
        ), None

    async def history(source, code):
        calls.append(code)
        if bad_history == "empty" and code == "123":
            return []
        if bad_history is True and code == "123":
            raise SourceDataError(ErrorKind.INVALID_RECORD, "synthetic malformed history")
        metadata = {}
        if bad_history == "partial" and code == "123":
            metadata["history_validation"] = dict(status="partial", rejected_rows=1)
        return [dict(symbol=code, interval="1d", received_at=datetime.now(UTC), metadata=metadata)]

    async def current(source, code):
        return [dict(symbol=code, interval="quote", received_at=datetime.now(UTC), metadata={})]

    def respond(request):
        data = json.loads(request.content)
        uploads.extend(data["observations"])
        return httpx.Response(
            200,
            json=dict(
                batch_id=data["batch_id"],
                sha256=hashlib.sha256(request.content).hexdigest(),
                accepted=len(data["observations"]),
            ),
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(collector, "TSETMCClient", lambda settings: Source())
    monkeypatch.setattr(
        collector.httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw),
    )
    monkeypatch.setattr(collector, "fetch_instrument", instrument)
    monkeypatch.setattr(collector, "collect_history", history)
    monkeypatch.setattr(collector, "collect_current", current)
    args = SimpleNamespace(
        api_url="http://company",
        state_dir=tmp_path,
        once=True,
        history_seconds=86400,
        poll_seconds=15,
        ins_code=None if default_watchlist else ["123", "456"],
        all_shares=False,
    )
    monkeypatch.setattr(collector, "top_share_codes", lambda: ["123", "456"])

    async def forbidden_discovery(source):
        pytest.fail("fixed watchlist must not call market watch")

    monkeypatch.setattr(collector, "discover_shares", forbidden_discovery)
    failed = bad_history is True or bad_history == "empty"
    if failed:
        with pytest.raises(ExceptionGroup):
            await collector.run(args)
    else:
        await collector.run(args)
    assert calls == ["123", "456"]
    assert any(r["symbol"] == "456" and r["interval"] == "1d" for r in uploads)
    assert all(r["metadata"]["display_name"] == "synthetic " + r["symbol"] for r in uploads)
    saved = json.loads((tmp_path / "history-completed.json").read_text())
    assert set(saved["completed"]) == ({"456"} if failed else {"123", "456"})
    assert not (tmp_path / "history.json").exists()
    if bad_history == "partial":
        uploaded = next(r for r in uploads if r["symbol"] == "123" and r["interval"] == "1d")
        assert uploaded["metadata"]["history_validation"]["status"] == "partial"
    if not failed:
        calls.clear()
        args.once = False

        async def stop_after_cycle(seconds):
            raise asyncio.CancelledError

        monkeypatch.setattr(collector.asyncio, "sleep", stop_after_cycle)
        await collector.run(args)
        assert calls == []  # Acknowledged full histories are not redownloaded after restart.


async def test_empty_discovery_recovers_with_bounded_backoff(monkeypatch, capsys):
    attempts, sleeps = [], []

    async def fetch(source):
        attempts.append(1)
        if len(attempts) < 5:
            return share_codes({"marketwatch": []})
        return ["123", "456"]

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(collector, "discover_shares", fetch)
    monkeypatch.setattr(collector.asyncio, "sleep", sleep)
    assert await collector.discover(None, once=False) == ["123", "456"]
    assert sleeps == [60, 120, 240, 300]
    assert "0 marketwatch rows" in capsys.readouterr().out


@pytest.mark.parametrize(
    "kind,once",
    [
        (ErrorKind.NOT_FOUND, True),
        (ErrorKind.SCHEMA, False),
        (ErrorKind.INVALID_RECORD, False),
        (ErrorKind.ACCESS_DENIED, False),
    ],
)
async def test_discovery_does_not_hide_invalid_source_or_one_shot_failure(monkeypatch, kind, once):
    async def fetch(source):
        raise SourceDataError(kind, "synthetic failure")

    async def sleep(seconds):
        pytest.fail("unexpected retry")

    monkeypatch.setattr(collector, "discover_shares", fetch)
    monkeypatch.setattr(collector.asyncio, "sleep", sleep)
    with pytest.raises(SourceDataError) as caught:
        await collector.discover(None, once=once)
    assert caught.value.kind == kind


def test_packaged_top100_is_unique_and_independent_of_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    codes = top_share_codes()
    assert len(codes) == len(set(codes)) == 100
    assert "46348559193224090" in codes
    assert all(code.isascii() and code.isdigit() and code != "0" for code in codes)

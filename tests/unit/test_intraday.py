"""Synthetic trades only: exact sums, cancellations, source clocks and snapshot integrity."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tsetmc_marketdata.errors import RecordError
from tsetmc_marketdata.providers.http_client import JSONResponse
from tsetmc_marketdata.providers.intraday import fetch_intraday, minute_snapshot

DAY = date(2026, 10, 25)
NOW = datetime(2026, 10, 26, tzinfo=UTC)


def trade(seq=1, clock=90000, price=Decimal("10.01"), volume=2, **extra):
    return (
        dict(
            insCode=None, dEven=0, nTran=seq, hEven=clock, pTran=price, qTitTran=volume, canceled=0
        )
        | extra
    )


def summary(**extra):
    return SimpleNamespace(
        ins_code="123",
        trade_count=3,
        traded_volume=Decimal(9),
        traded_value=Decimal("102.04"),
        opening_price=Decimal("10.01"),
        last_traded_price=Decimal("10.01"),
        high=Decimal(14),
        low=Decimal("10.01"),
        source_date=DAY,
        **extra,
    )


def response(rows, current=False):
    return JSONResponse(
        {"trade" if current else "tradeHistory": rows},
        b"synthetic",
        NOW,
        200,
        "Trade/GetTradeHistory/123/20261025/false",
    )


def trades():
    return [
        trade(3, 90600, volume=2),
        trade(2, 90000, Decimal(14), 3),
        trade(1, 90000, volume=4),
        trade(1, 150000, canceled=1),
    ]


def test_reconciled_minutes_keep_exact_value_gaps_and_sequence_order():
    # 6*10.01 + 3*14 = 102.06; cancellation shares sequence with an active trade.
    s = summary()
    s.traded_value = Decimal("102.06")
    bars, snapshot = minute_snapshot(response(trades()), "123", DAY, s, current=False)
    assert len(bars) == 2
    assert bars[0]["open"] == Decimal("10.01") and bars[0]["close"] == Decimal(14)
    assert bars[1]["time"] - bars[0]["time"] == 360
    assert datetime.fromtimestamp(bars[0]["time"], UTC).hour == 5
    assert datetime.fromtimestamp(bars[0]["time"], UTC).minute == 30
    assert sum(b["volume"] for b in bars) == 9
    assert sum(b["value"] for b in bars) == Decimal("102.06")
    assert bars[0]["metadata"]["history_validation"]["cancelled_rows"] == 1
    assert snapshot["trading_date"] == DAY


@pytest.mark.parametrize(
    "change",
    [
        dict(canceled=2),
        dict(hEven=96000),
        dict(pTran=-1),
        dict(qTitTran=Decimal("1.5")),
        dict(insCode="456"),
        dict(dEven=20261024),
        dict(pTran=1.2),
    ],
)
def test_invalid_trade_never_replaces_a_day(change):
    rows = trades()
    rows[0].update(change)
    with pytest.raises(RecordError):
        minute_snapshot(response(rows), "123", DAY, summary(), current=False)


def test_missing_archive_and_duplicate_active_sequence_are_not_complete_snapshots():
    with pytest.raises(RecordError, match="reconcile"):
        minute_snapshot(response([]), "123", DAY, summary(), current=False)
    rows = [trade(), trade(volume=3)]
    with pytest.raises(RecordError, match="duplicate"):
        minute_snapshot(response(rows), "123", DAY, summary(), current=False)


def test_exact_duplicate_rows_are_collapsed_after_cancellation_filtering():
    s = summary()
    s.traded_value = Decimal("102.06")
    rows = trades()
    rows.append(dict(rows[0]))
    bars, snapshot = minute_snapshot(response(rows), "123", DAY, s, current=False)
    assert sum(b["count"] for b in bars) == 3
    assert snapshot["reconciled"]
    assert bars[0]["metadata"]["history_validation"]["duplicate_rows"] == 1


def test_moving_current_summary_is_provisional_and_cannot_remove_missing_bars():
    s = summary()
    s.trade_count = 4  # Summary advanced after fetching the individual trades.
    bars, snapshot = minute_snapshot(response(trades(), True), "123", DAY, s, current=True)
    assert not snapshot["reconciled"]
    assert sum(b["count"] for b in bars) == 3
    assert bars[0]["metadata"]["finality"] == "provisional"
    assert bars[0]["metadata"]["history_validation"]["status"] == "provisional"


def test_all_cancelled_snapshot_can_remove_previously_stored_bars():
    s = summary()
    s.trade_count = s.traded_volume = s.traded_value = 0
    bars, snapshot = minute_snapshot(response([trade(canceled=1)]), "123", DAY, s, current=False)
    assert bars == [] and snapshot["symbol"] == "123"


async def test_fetch_uses_full_history_route_and_logs_unreconciled_raw_evidence(
    monkeypatch, tmp_path
):
    from tsetmc_marketdata.providers import intraday

    paths = []

    class Source:
        diagnostic_dir = tmp_path

        async def get_json(self, path):
            paths.append(path)
            return response([])

    async def daily(*args):
        return summary(), None

    monkeypatch.setattr(intraday, "fetch_daily", daily)
    with pytest.raises(RecordError, match="reconcile"):
        await fetch_intraday(Source(), "123", DAY)
    assert paths == ["Trade/GetTradeHistory/123/20261025/false"]
    entry = json.loads((tmp_path / "validation.jsonl").read_text())
    assert entry["kind"] == "intraday"
    assert entry["diagnostics"][0]["raw"] == {"tradeHistory": []}


async def test_current_rollover_never_assigns_trades_to_wrong_day(monkeypatch):
    from tsetmc_marketdata.providers import intraday

    calls = 0

    async def current(*args):
        nonlocal calls
        calls += 1
        s = summary()
        if calls == 2:
            s.source_date = date(2026, 10, 26)
        return s, None

    class Source:
        async def get_json(self, path):
            assert path == "Trade/GetTrade/123"
            return response(trades(), True)

    monkeypatch.setattr(intraday, "fetch_current", current)
    with pytest.raises(RecordError, match="rolled over"):
        await fetch_intraday(Source(), "123")


async def test_collector_caps_all_loops_at_four_and_uploads_whole_days(monkeypatch, tmp_path):
    import hashlib

    import httpx

    from tsetmc_marketdata import simple_collector as collector

    class Source:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def identity(*args, **kwargs):
        return SimpleNamespace(
            symbol="Synthetic", company_name="Synthetic", instrument_id="synthetic", isin=None
        )

    async def daily(source, code):
        return [dict(market="tsetmc", symbol=code, interval="1d", metadata={})]

    async def latest(source, code):
        return []

    async def calendar(source, code):
        return SimpleNamespace(records=[SimpleNamespace(trading_date=DAY)]), None

    calls = []

    async def intraday(source, code, day):
        calls.append((code, day))
        s = summary()
        s.ins_code = code
        s.traded_value = Decimal("102.06")
        return minute_snapshot(response(trades()), code, DAY, s, current=False)

    uploads = []

    def accept(request):
        batch = json.loads(request.content)
        uploads.append(batch)
        return httpx.Response(
            200,
            json=dict(
                batch_id=batch["batch_id"],
                accepted=len(batch["observations"]),
                sha256=hashlib.sha256(request.content).hexdigest(),
            ),
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(collector, "TSETMCClient", lambda _: Source())
    monkeypatch.setattr(collector, "qualify", identity)
    monkeypatch.setattr(collector, "collect_history", daily)
    monkeypatch.setattr(collector, "collect_current", latest)
    monkeypatch.setattr(collector, "fetch_history", calendar)
    monkeypatch.setattr(collector, "fetch_intraday", intraday)
    monkeypatch.setattr(
        collector.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(accept), **kw),
    )
    await collector.run(
        SimpleNamespace(
            api_url="http://company",
            state_dir=tmp_path,
            ins_code=["123", "124", "125", "126", "127"],
            once=True,
            history_seconds=86400,
            poll_seconds=15,
            intraday_days=1,
            intraday_seconds=300,
        )
    )
    assert calls == [(code, day) for day in (None, DAY) for code in ("123", "124", "125", "126")]
    snapshots = [batch for batch in uploads if "snapshot" in batch]
    assert len(snapshots) == 8 and all(len(b["observations"]) == 2 for b in snapshots)
    assert {row["symbol"] for batch in uploads for row in batch["observations"]} == {
        "123",
        "124",
        "125",
        "126",
    }
    assert not (tmp_path / "intraday.json").exists()

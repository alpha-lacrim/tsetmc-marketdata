"""Reconciled trade snapshots; source HTTP data, never synthesized daily-price candles."""

import hashlib
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from tsetmc_marketdata.config import validate_ins_code
from tsetmc_marketdata.errors import RecordError
from tsetmc_marketdata.models.current import CurrentPrice
from tsetmc_marketdata.models.daily import DailyRecord
from tsetmc_marketdata.providers.closing_prices import fetch_current, fetch_daily
from tsetmc_marketdata.providers.http_client import JSONResponse, TSETMCClient
from tsetmc_marketdata.validation_log import checked

TEHRAN = ZoneInfo("Asia/Tehran")


def number(value: Any, name: str, *, integer: bool = False) -> Decimal:
    if type(value) not in (int, Decimal):
        raise RecordError(f"{name} must be an exact source number")
    result = Decimal(value)
    if not result.is_finite() or result < 0 or (integer and result != result.to_integral_value()):
        raise RecordError(f"invalid {name}")
    return result


def minute_snapshot(
    response: JSONResponse,
    code: str,
    day: date,
    summary: DailyRecord | CurrentPrice,
    *,
    current: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validate_ins_code(code)
    key = "trade" if current else "tradeHistory"
    raw = response.payload.get(key)
    if not isinstance(raw, list):
        raise RecordError(f"{key} must be an array")
    if summary.ins_code != code:
        raise RecordError("intraday summary identity mismatch")
    received = response.received_at
    if day > received.astimezone(TEHRAN).date():
        raise RecordError("future intraday trading date")
    active = []
    sequences: dict[int, dict[str, Any]] = {}
    cancelled = 0
    duplicates = 0
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise RecordError(f"trade row {index}: expected object")
        if row.get("insCode") not in (None, code) or row.get("dEven") not in (
            0,
            int(day.strftime("%Y%m%d")),
        ):
            raise RecordError(f"trade row {index}: source identity/date mismatch")
        if type(row.get("canceled")) is not int or row["canceled"] not in (0, 1):
            raise RecordError(f"trade row {index}: unknown cancellation flag")
        if row["canceled"] == 1:
            cancelled += 1
            continue
        seq = int(number(row.get("nTran"), "nTran", integer=True))
        clock = int(number(row.get("hEven"), "hEven", integer=True))
        price = number(row.get("pTran"), "pTran")
        volume = number(row.get("qTitTran"), "qTitTran", integer=True)
        if seq <= 0 or price <= 0 or volume <= 0:
            raise RecordError(f"trade row {index}: nonpositive trade")
        if seq in sequences:
            if sequences[seq] != row:
                raise RecordError(f"trade row {index}: conflicting duplicate sequence")
            duplicates += 1
            continue
        sequences[seq] = row
        try:
            stamp = datetime.combine(
                day, time(clock // 10000, clock // 100 % 100, clock % 100), TEHRAN
            )
        except ValueError as exc:
            raise RecordError(f"trade row {index}: invalid HHMMSS") from exc
        if stamp > received + timedelta(minutes=5):
            raise RecordError(f"trade row {index}: future source clock")
        active.append((int(stamp.timestamp()), seq, price, volume))
    active.sort(key=lambda item: (item[0], item[1]))
    with localcontext() as context:
        context.prec = 100
        actual = (len(active), sum(t[3] for t in active), sum(t[2] * t[3] for t in active))
        expected = (summary.trade_count, summary.traded_volume, summary.traded_value)
        reconciled = actual == expected
        if not reconciled and (not current or not active):
            raise RecordError(
                "trade count/volume/value do not reconcile with source summary; retry snapshot"
            )
        prices_match = not active or (
            active[0][2],
            active[-1][2],
            min(t[2] for t in active),
            max(t[2] for t in active),
        ) == (summary.opening_price, summary.last_traded_price, summary.low, summary.high)
        reconciled = reconciled and prices_match
        if not current and not prices_match:
            raise RecordError("trade OHLC do not reconcile with source summary; retry snapshot")
        buckets: dict[int, list[tuple[int, int, Decimal, Decimal]]] = defaultdict(list)
        for trade in active:
            buckets[trade[0] // 60 * 60].append(trade)
        metadata = dict(
            origin="tsetmc_trades",
            source_path=response.path,
            source_sha256=hashlib.sha256(response.raw_body).hexdigest(),
            trading_date=day.isoformat(),
            source_timezone="Asia/Tehran",
            finality="provisional" if current else "reconciled_snapshot",
            history_validation=dict(
                status="passed" if reconciled else "provisional",
                active_trades=len(active),
                cancelled_rows=cancelled,
                duplicate_rows=duplicates,
                summary_trade_count=summary.trade_count,
                summary_volume=summary.traded_volume,
                summary_value=summary.traded_value,
            ),
            timestamp_semantics="UTC trade minute start; source clock interpreted in Asia/Tehran",
        )
        bars = [
            dict(
                market="tsetmc",
                symbol=code,
                interval="1m",
                time=stamp,
                received_at=received.astimezone(UTC),
                open=trades[0][2],
                close=trades[-1][2],
                high=max(t[2] for t in trades),
                low=min(t[2] for t in trades),
                count=len(trades),
                volume=sum(t[3] for t in trades),
                value=sum(t[2] * t[3] for t in trades),
                status="unknown",
                metadata=dict(metadata),
            )
            for stamp, trades in sorted(buckets.items())
        ]
    return bars, dict(
        market="tsetmc",
        symbol=code,
        interval="1m",
        trading_date=day,
        received_at=received.astimezone(UTC),
        reconciled=reconciled,
    )


async def fetch_intraday(
    client: TSETMCClient,
    code: str,
    day: date | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validate_ins_code(code)
    current = day is None
    summary: DailyRecord | CurrentPrice
    if current:
        before, _ = await fetch_current(client, code)
        day = before.source_date
        if day is None:
            raise RecordError("current trades require a source-reported trading date")
        response = await client.get_json(f"Trade/GetTrade/{code}")
        summary, _ = await fetch_current(client, code)
        if summary.source_date != day:
            raise RecordError("source session rolled over during intraday fetch; retry")
    else:
        assert day is not None
        response = await client.get_json(f"Trade/GetTradeHistory/{code}/{day:%Y%m%d}/false")
        summary, _ = await fetch_daily(client, code, day)
    return checked(
        client,
        response,
        "intraday",
        code,
        lambda: minute_snapshot(response, code, day, summary, current=current),
    )

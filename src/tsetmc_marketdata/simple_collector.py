"""Native TSETMC collector -> company HTTP API. No source PostgreSQL installation."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from tsetmc_marketdata.config import PILOT_INS_CODE, Settings, validate_ins_code
from tsetmc_marketdata.errors import ErrorKind, SourceDataError, TSETMCError
from tsetmc_marketdata.providers.closing_prices import fetch_current, fetch_history
from tsetmc_marketdata.providers.http_client import TSETMCClient
from tsetmc_marketdata.providers.instruments import fetch_instrument
from tsetmc_marketdata.providers.universe import discover_shares, top_share_codes
from tsetmc_marketdata.simple_delivery import (
    PendingUpload,
    history_batches,
    single_instance,
    sync_directory,
)
from tsetmc_marketdata.simple_traffic import TrafficMeter
from tsetmc_marketdata.utils.dates import daily_unix_seconds


async def collect_history(
    client: TSETMCClient, ins_code: str = PILOT_INS_CODE
) -> list[dict[str, Any]]:
    history, response = await fetch_history(client, ins_code)
    rejected = len({d.index for d in history.diagnostics if d.severity == "error"})
    if rejected and not history.records:
        raise SourceDataError(
            ErrorKind.INVALID_RECORD,
            "source history has no valid rows; flagged rows retained in diagnostics",
        )
    validation = dict(
        status="partial" if rejected else "passed",
        accepted_rows=len(history.records),
        rejected_rows=rejected,
        source_rows=len(history.raw_payload["closingPriceDaily"]),
    )
    if rejected:
        print(
            f"history {ins_code}: partial snapshot; {len(history.records)} valid rows, "
            f"{rejected} rejected source rows retained in diagnostics",
            flush=True,
        )
    return [
        dict(
            market="tsetmc",
            symbol=r.ins_code,
            interval="1d",
            time=daily_unix_seconds(r.trading_date),
            received_at=response.received_at,
            open=r.opening_price,
            close=r.official_close,
            high=r.high,
            low=r.low,
            last=r.last_traded_price,
            official_close=r.official_close,
            count=r.trade_count,
            volume=r.traded_volume,
            value=r.traded_value,
            status="unknown",
            metadata={
                "raw": r.raw,
                "finality": "unverified",
                "history_validation": validation,
                "timestamp_semantics": "Gregorian trading date labeled at UTC midnight",
            },
        )
        for r in history.records
    ]


async def collect_current(
    client: TSETMCClient, ins_code: str = PILOT_INS_CODE
) -> list[dict[str, Any]]:
    current, response = await fetch_current(client, ins_code)
    if current.last_traded_price is None and current.official_close is None:
        return []
    return [
        dict(
            market="tsetmc",
            symbol=current.ins_code,
            interval="quote",
            time=0,
            received_at=response.received_at,
            event_at=current.source_reported_at,
            last=current.last_traded_price,
            official_close=current.official_close,
            count=current.trade_count,
            volume=current.traded_volume,
            value=current.traded_value,
            status="unknown",
            metadata={
                "raw": current.raw,
                "diagnostics": current.diagnostics,
                "instrument_state": current.instrument_state,
                "instrument_state_title": current.instrument_state_title,
                "timestamp_semantics": (
                    "source-reported Tehran dEven/hEven; not verified last-trade time"
                ),
                "source_date": str(current.source_date) if current.source_date else None,
                "source_time": str(current.source_time) if current.source_time else None,
            },
        )
    ]


async def qualify(source: TSETMCClient, ins_code: str = PILOT_INS_CODE, *, once: bool) -> Any:
    delay = 60.0
    while True:
        try:
            instrument, _ = await fetch_instrument(source, ins_code)
            return instrument
        except TSETMCError as error:
            if once or error.kind not in {
                ErrorKind.NETWORK,
                ErrorKind.DNS,
                ErrorKind.TIMEOUT,
                ErrorKind.SERVER,
            }:
                raise
            print(
                f"identity lookup: {error.kind.value} HTTP={error.status}; retry in {delay:g}s",
                flush=True,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


async def discover(source: TSETMCClient, *, once: bool) -> list[str]:
    delay = 60.0
    while True:
        try:
            return await discover_shares(source)
        except TSETMCError as error:
            if once or error.kind not in {
                ErrorKind.NETWORK,
                ErrorKind.DNS,
                ErrorKind.TIMEOUT,
                ErrorKind.SERVER,
                ErrorKind.NOT_FOUND,
            }:
                raise
            # An empty/no-match discovery is not proof that all shares disappeared.
            # Do not start with an empty universe or relax product/identity checks.
            print(f"discovery: {error.kind.value}: {error}; retry in {delay:g}s", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


async def run(args: argparse.Namespace) -> None:
    traffic = TrafficMeter(args.state_dir / "traffic.json")
    headers = (
        {"Authorization": "Bearer " + os.environ["SIMPLE_UPLOAD_TOKEN"]}
        if os.getenv("SIMPLE_UPLOAD_TOKEN")
        else {}
    )
    async with (
        TSETMCClient(Settings.from_env()) as source,
        httpx.AsyncClient(timeout=60, headers=headers, trust_env=False) as http,
    ):
        source.traffic = traffic
        source.diagnostic_dir = args.state_dir / "diagnostics"
        print(f"Validation evidence: {source.diagnostic_dir / 'validation.jsonl'}", flush=True)
        # Previously qualified durable uploads can drain even if the source is offline.
        for kind in ("history", "latest"):
            await PendingUpload(
                args.api_url, args.state_dir / (kind + ".json"), http, traffic
            ).drain()
        codes = getattr(args, "ins_code", None)
        if not codes and getattr(args, "all_shares", False):
            codes = await discover(source, once=args.once)
        codes = list(dict.fromkeys(codes or top_share_codes()))
        if PILOT_INS_CODE in codes:
            codes.remove(PILOT_INS_CODE)
            codes.insert(0, PILOT_INS_CODE)
        print(f"Collecting {len(codes)} instruments; requests share source pacing", flush=True)
        instruments: dict[str, Any] = {}
        identity_lock = asyncio.Lock()
        checkpoint = args.state_dir / "history-completed.json"
        completed: dict[str, str] = {}
        if checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved["endpoint"] != args.api_url.rstrip("/"):
                raise ValueError("history checkpoint belongs to another API URL")
            completed = saved["completed"]
            for code, stamp in completed.items():
                validate_ins_code(code)
                if datetime.fromisoformat(stamp).utcoffset() is None:
                    raise ValueError("history checkpoint timestamp must be timezone-aware")

        async def identify(code: str) -> Any:
            async with identity_lock:
                if code not in instruments:
                    # A bad instrument must not hold every other instrument in a retry loop.
                    instruments[code] = await qualify(source, code, once=True)
                return instruments[code]

        def mark_completed(code: str) -> None:
            completed[code] = datetime.now(UTC).isoformat()
            temporary = checkpoint.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump({"endpoint": args.api_url.rstrip("/"), "completed": completed}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, checkpoint)
            sync_directory(checkpoint.parent)

        async def loop(kind: str, seconds: float) -> None:
            pending = PendingUpload(args.api_url, args.state_dir / (kind + ".json"), http, traffic)
            recent_pass = kind == "history" and not args.once
            while True:
                failed = 0
                delivered = 0
                for code in codes:
                    await pending.drain()
                    if kind == "history" and not args.once and code in completed:
                        elapsed = (
                            datetime.now(UTC) - datetime.fromisoformat(completed[code])
                        ).total_seconds()
                        if 0 <= elapsed < seconds:
                            continue
                    try:
                        instrument = await identify(code)
                        rows = await (
                            collect_history(source, code)
                            if kind == "history"
                            else collect_current(source, code)
                        )
                        if kind == "history" and not rows:
                            raise SourceDataError(
                                ErrorKind.NOT_FOUND, "empty history; retry next cycle"
                            )
                        if recent_pass:
                            snapshot_rows = len(rows)
                            rows = rows[-100:]
                            for row in rows:
                                row["metadata"]["history_delivery"] = dict(
                                    scope="recent_preview",
                                    snapshot_rows=snapshot_rows,
                                    uploaded_rows=len(rows),
                                    full_upload_pending=True,
                                )
                        for row in rows:
                            row["metadata"].update(
                                display_name=instrument.symbol,
                                company_name=instrument.company_name,
                                instrument_id=instrument.instrument_id,
                                isin=instrument.isin,
                            )
                        chunks = history_batches(rows) if kind == "history" else [rows]
                        for chunk in chunks:
                            if not chunk:
                                continue
                            pending.stage(chunk)
                            await pending.drain()
                        delivered += len(rows)
                        if kind == "history" and not recent_pass:
                            mark_completed(code)
                        validation = rows[0]["metadata"].get("history_validation") if rows else None
                        suffix = (
                            f"; partial history ({validation['rejected_rows']} rejected rows)"
                            if validation and validation["status"] == "partial"
                            else ""
                        )
                        if recent_pass:
                            suffix += "; recent preview, full history pending"
                        print(f"{kind} {code}: delivered {len(rows)} records{suffix}", flush=True)
                    except TSETMCError as error:
                        if error.kind == ErrorKind.ACCESS_DENIED:
                            raise
                        failed += 1
                        print(
                            f"{kind} {code}: {error.kind.value} HTTP={error.status}; "
                            "skipped this cycle",
                            flush=True,
                        )
                print(
                    f"{kind} cycle: {delivered} records delivered, {failed} instruments failed",
                    flush=True,
                )
                if args.once:
                    if failed:
                        raise SourceDataError(
                            ErrorKind.INVALID_RECORD, f"{kind}: {failed} instruments failed"
                        )
                    return
                recent_pass = False
                # History scheduling is per instrument and survives a restart. Full
                # snapshots preserve old corrections; no incremental cutoff is invented.
                await asyncio.sleep(min(seconds, 60) if kind == "history" else seconds)

        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(loop("history", args.history_seconds))
                group.create_task(loop("latest", args.poll_seconds))
                if not args.once:
                    group.create_task(traffic.report_loop())
        finally:
            traffic.report()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.getenv("COMPANY_API_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--state-dir", type=Path, default=Path(".local/simple-tsetmc"))
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--history-seconds", type=float, default=86400)
    parser.add_argument(
        "--ins-code",
        action="append",
        type=validate_ins_code,
        help="collect only these IDs; repeat for multiple instruments",
    )
    parser.add_argument(
        "--all-shares",
        action="store_true",
        help="opt in to live discovery instead of the fixed top100 watchlist",
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.all_shares and args.ins_code:
        parser.error("choose --all-shares or explicit --ins-code values")
    if not 5 <= args.poll_seconds <= 86400 or not 60 <= args.history_seconds <= 86400:
        parser.error("poll-seconds must be 5..86400; history-seconds must be 60..86400")
    with single_instance(args.state_dir):
        try:
            asyncio.run(run(args))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

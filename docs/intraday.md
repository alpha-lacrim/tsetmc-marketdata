# Intraday operation

The native collector now collects trade-derived 1m candles. The shared API serves
5m and 15m by aggregating those minutes. Daily close remains official close;
intraday close is last trade. No flat bars are invented for no-trade minutes.

First update Server B, run `uv sync --frozen --no-dev --extra collector`, then run
`uv run marketdata-simple init` with its existing native `DATABASE_URL`, and restart
FastAPI. This adds a snapshot tracking table without deleting data. Then update and
restart both collectors, preserving their state directories. Old APIs reject the
new upload contract and the pending file remains on disk.

Full setup, PowerShell commands, endpoint examples and correction semantics:
[company intraday guide](https://github.com/alpha-lacrim/tradingview-marketdata/blob/main/docs/native-intraday.md).

Current defaults:

- Four shares: 46348559193224090 (فولاد), 58931793851445922 (ومعادن),
  778253364357513 (وبملت), 23293437377896568 (تاصیکو). Explicit/discovered lists
  are also capped at four. Historical stored data is not removed.
- Current trade snapshots, with a 300-second pause after a sweep.
- Five recent completed trading dates, refreshed hourly. `--intraday-days 0`
  disables intraday; 1..30 controls the backfill window. `--intraday-seconds`
  controls current sweep pause (minimum 60).
- Daily/latest loops continue independently, sharing the same paced source client.
- Durable `intraday.json` holds a whole day upload until its exact receipt arrives.
  Short backfills replay safely on restart; refresh timers are in memory.

The parser excludes cancelled records, collapses only exact duplicate rows, and
rejects conflicting duplicates, wrong identity/date, bad clocks or measurements.
Completed snapshots must reconcile trade count, volume, value and trade OHLC with
the native daily record before upload. Invalid snapshots and raw evidence are
logged under `<state-dir>/diagnostics/validation.jsonl`. An empty archive for a
day with positive activity is a gap, never a fabricated empty day.

During trading, separate trade and summary requests can observe different moments.
Valid current trades may be delivered as provisional, with metadata identifying
the mismatch. Those uploads cannot delete absent minutes. Reconciled snapshots
can remove candles after cancellations, retaining audit events and preventing old
retries from restoring them. No current snapshot is claimed final or real-time.

Use `/api/chart?market=tsetmc&symbol=46348559193224090&interval=1m` (also 5m/15m/1d),
and `/api/records?...&interval=1m` for exact values and validation metadata.
Missing archives, including recent dates, are possible. Older dates outside the
configured window are not refreshed. Full trade downloads can cost several MB;
watch `traffic.json`. The source research and measured limitations are in
[the qualification report](intraday-research-2026-10-26.md).

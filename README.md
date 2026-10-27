# TSETMC collector

Collect Iranian daily history, trade-derived minute candles and latest observations, then send them
directly to the shared company FastAPI. No Docker or local PostgreSQL is needed.

**Start here: [native installation](docs/native-install.md).** The company API and
its PostgreSQL database are installed from the companion
[TradingView/company repository](https://github.com/alpha-lacrim/tradingview-marketdata).

```bash
uv sync --frozen --no-dev --python 3.12
export COMPANY_API_URL='http://SERVER:8000'
uv run --no-sync tsetmc-simple
```

The default uses the first four entries of a [packaged primary-share list](docs/top100.md), bundled with
the package and independent of market-watch discovery. Remove `--all-shares` from
older commands to use this list. Each ID is still qualified before collecting prices.
Use repeated `--ins-code` arguments for a smaller watchlist. Full history refreshes
daily; current sweeps are paced. Intraday defaults to the current session and five
recent completed trading dates. All native watchlists are temporarily capped at four.
See [intraday setup and limits](docs/intraday.md). Update the API first and rerun its
native `init` command before starting the new collector. Preserve
`.local/simple-tsetmc/` or your selected `--state-dir` across restarts.

The dashboard reads from the **same company API** as international markets:

```text
/api/chart?market=tsetmc&symbol=46348559193224090&interval=1d
/api/latest?market=tsetmc&symbol=46348559193224090
```

ChartData matches a=time, b=open, c=official close, d=high, e=low, f=count,
g=volume, h=traded value, i=null. Last trade and official close remain separate.
Source decimals, original timestamps, missing values and unverified finality are
preserved. Intraday `c` is the last trade, while daily `c` stays official close.
The collector derives minutes from genuine trades and does not claim all-market
coverage. HTTP/open company access is the default; an upload token is optional.

[Current state/checks](HANDOFF.md) | [Workspace/checkpoints](docs/workspace.md)

Original pilot databases, captures and the old PostgreSQL/outbox pipeline are
preserved. They are not used or migrated by the native command. The new API does
not automatically import old database archives. For older installations see the
[previous README](docs/history/readme-before-native-install.md) and
[legacy delivery guide](docs/company-delivery.md).

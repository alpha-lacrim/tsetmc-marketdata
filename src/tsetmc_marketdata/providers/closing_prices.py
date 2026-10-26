"""Single-request daily and current endpoint services; never schedule collection."""

from datetime import date

from tsetmc_marketdata.config import validate_ins_code
from tsetmc_marketdata.models.current import CurrentPrice
from tsetmc_marketdata.models.daily import DailyRecord
from tsetmc_marketdata.parsing import (
    HistoryResult,
    parse_current,
    parse_history,
    parse_targeted_daily,
)
from tsetmc_marketdata.providers.http_client import JSONResponse, TSETMCClient
from tsetmc_marketdata.validation_log import checked


async def fetch_history(
    client: TSETMCClient, ins_code: str, *, source_limit: int = 0
) -> tuple[HistoryResult, JSONResponse]:
    validate_ins_code(ins_code)
    if type(source_limit) is not int or not 0 <= source_limit <= 100000:
        raise ValueError(
            "source-limit must be between 0 and 100000; its source semantics are tentative"
        )
    response = await client.get_json(
        f"ClosingPrice/GetClosingPriceDailyList/{ins_code}/{source_limit}"
    )
    return checked(
        client, response, "history", ins_code, lambda: parse_history(response.payload, ins_code)
    ), response


async def fetch_current(client: TSETMCClient, ins_code: str) -> tuple[CurrentPrice, JSONResponse]:
    validate_ins_code(ins_code)
    response = await client.get_json(f"ClosingPrice/GetClosingPriceInfo/{ins_code}")
    return checked(
        client,
        response,
        "current",
        ins_code,
        lambda: parse_current(response.payload, ins_code, received_at=response.received_at),
    ), response


async def fetch_daily(
    client: TSETMCClient, ins_code: str, day: date
) -> tuple[DailyRecord, JSONResponse]:
    validate_ins_code(ins_code)
    response = await client.get_json(f"ClosingPrice/GetClosingPriceDaily/{ins_code}/{day:%Y%m%d}")
    return checked(
        client,
        response,
        "daily",
        ins_code,
        lambda: parse_targeted_daily(response.payload, ins_code, day),
    ), response

"""Metadata identity qualification, for explicitly selected instruments."""

from tsetmc_marketdata.config import validate_ins_code
from tsetmc_marketdata.models.instrument import Instrument
from tsetmc_marketdata.parsing import parse_instrument
from tsetmc_marketdata.providers.http_client import JSONResponse, TSETMCClient
from tsetmc_marketdata.validation_log import checked


async def fetch_instrument(client: TSETMCClient, ins_code: str) -> tuple[Instrument, JSONResponse]:
    validate_ins_code(ins_code)
    response = await client.get_json(f"Instrument/GetInstrumentInfo/{ins_code}")
    return checked(
        client,
        response,
        "instrument",
        ins_code,
        lambda: parse_instrument(response.payload, ins_code),
    ), response

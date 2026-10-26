import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tsetmc_marketdata.errors import SourceDataError
from tsetmc_marketdata.parsing import parse_history, parse_instrument
from tsetmc_marketdata.validation_log import checked


def test_flagged_rows_are_logged_exactly_without_changing_validation(tmp_path, history_payload):
    history_payload["closingPriceDaily"][0]["priceFirst"] = Decimal("999999999.123456789")
    response = SimpleNamespace(
        payload=history_payload, received_at=datetime.now(UTC), path="synthetic/history"
    )
    client = SimpleNamespace(diagnostic_dir=tmp_path)
    result = checked(
        client,
        response,
        "history",
        "46348559193224090",
        lambda: parse_history(history_payload, "46348559193224090"),
    )
    assert result.has_errors
    log = json.loads((tmp_path / "validation.jsonl").read_text(encoding="utf-8").splitlines()[0])
    bad = next(d for d in log["diagnostics"] if d["severity"] == "error")
    assert bad["raw"]["priceFirst"] == "999999999.123456789"
    assert "range" in bad["message"]
    assert log["instrument"] == "46348559193224090"


def test_parser_rejection_is_logged_and_still_raised(tmp_path):
    response = SimpleNamespace(
        payload={"bad": "synthetic"}, received_at=datetime.now(UTC), path="synthetic/identity"
    )
    with pytest.raises(SourceDataError):
        checked(
            SimpleNamespace(diagnostic_dir=tmp_path),
            response,
            "instrument",
            "123",
            lambda: parse_instrument(response.payload, "123"),
        )
    log = json.loads((tmp_path / "validation.jsonl").read_text(encoding="utf-8"))
    assert log["diagnostics"][0]["raw"] == {"bad": "synthetic"}

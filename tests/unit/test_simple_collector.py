"""Direct HTTP mode uses existing parsers and preserves native financial semantics."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from tsetmc_marketdata import simple_collector as collector
from tsetmc_marketdata.config import PILOT_INS_CODE
from tsetmc_marketdata.errors import SourceDataError
from tsetmc_marketdata.parsing import parse_current, parse_history
from tsetmc_marketdata.simple_delivery import PendingUpload, single_instance


async def test_native_history_current_and_durable_wire(
    history_payload, current_payload, monkeypatch, tmp_path
):
    received = datetime(2026, 3, 21, 9, 1, tzinfo=UTC)

    async def history(*args):
        return parse_history(history_payload, PILOT_INS_CODE), SimpleNamespace(received_at=received)

    async def current(*args):
        return (
            parse_current(current_payload, PILOT_INS_CODE, received_at=received),
            SimpleNamespace(received_at=received),
        )

    monkeypatch.setattr(collector, "fetch_history", history)
    monkeypatch.setattr(collector, "fetch_current", current)
    rows = await collector.collect_history(None)
    quote = (await collector.collect_current(None))[0]
    assert rows[-1]["close"] == Decimal("1020.25")
    assert rows[-1]["volume"] == Decimal("7000000123")
    assert rows[-1]["value"] == Decimal("7154000125700.25")
    assert rows[-1]["metadata"]["finality"] == "unverified"
    assert rows[-1]["interval"] == "1d"
    assert quote["last"] == Decimal("1030.50")
    assert quote["official_close"] == Decimal("1020.25")
    assert quote["event_at"] == datetime(2026, 3, 21, 9, 0, 45, tzinfo=UTC)
    assert quote["received_at"] == received and quote["status"] == "unknown"
    sent = []

    async def respond(request):
        sent.append(request.content)
        data = json.loads(request.content)
        assert data["observations"][-1]["last"] == "1030.50"
        if len(sent) == 1:
            raise httpx.ReadTimeout("commit response lost")
        return httpx.Response(
            200,
            json={
                "batch_id": data["batch_id"],
                "sha256": hashlib.sha256(request.content).hexdigest(),
                "accepted": len(data["observations"]),
            },
        )

    path = tmp_path / "pending.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        pending = PendingUpload("http://company", path, client)
        pending.stage(rows + [quote])
        with pytest.raises(httpx.ReadTimeout):
            await pending.flush()
        assert path.exists()
        await PendingUpload("http://company", path, client).flush()
    assert sent[0] == sent[1] and not path.exists()


async def test_conflicting_date_is_quarantined_but_other_dates_upload(history_payload, monkeypatch):
    original = len(history_payload["closingPriceDaily"])
    conflicted_date = history_payload["closingPriceDaily"][0]["dEven"]
    history_payload["closingPriceDaily"].append(
        dict(history_payload["closingPriceDaily"][0], pClosing=Decimal("999"))
    )

    async def history(*args):
        return parse_history(history_payload, PILOT_INS_CODE), SimpleNamespace(received_at=None)

    monkeypatch.setattr(collector, "fetch_history", history)
    rows = await collector.collect_history(None)
    assert len(rows) == original - 1
    assert all(row["metadata"]["raw"]["dEven"] != conflicted_date for row in rows)
    assert rows[0]["metadata"]["history_validation"] == dict(
        status="partial", accepted_rows=original - 1, rejected_rows=2, source_rows=original + 1
    )


async def test_one_bad_old_row_does_not_block_thousands_and_source_fix_is_reconsidered(
    history_payload, tmp_path, capsys
):
    from datetime import date, timedelta

    template = history_payload["closingPriceDaily"][0]
    payload = {
        "closingPriceDaily": [
            dict(template, dEven=int((date(2010, 1, 1) + timedelta(days=i)).strftime("%Y%m%d")))
            for i in range(2501)
        ]
    }
    original_open = payload["closingPriceDaily"][0]["priceFirst"]
    payload["closingPriceDaily"][0]["priceFirst"] = Decimal("999999999.123456789")

    class Source:
        diagnostic_dir = tmp_path

        async def get_json(self, path):
            return SimpleNamespace(payload=payload, received_at=datetime.now(UTC), path=path)

    rows = await collector.collect_history(Source())
    assert len(rows) == 2500
    assert rows[0]["metadata"]["history_validation"] == dict(
        status="partial", accepted_rows=2500, rejected_rows=1, source_rows=2501
    )
    assert all(row["metadata"]["raw"]["dEven"] != 20100101 for row in rows)
    evidence = json.loads((tmp_path / "validation.jsonl").read_text(encoding="utf-8"))
    assert evidence["diagnostics"][0]["raw"]["priceFirst"] == "999999999.123456789"
    assert "2500 valid rows" in capsys.readouterr().out

    # The next full refresh can recover a date when the provider corrects it.
    payload["closingPriceDaily"][0]["priceFirst"] = original_open
    repaired = await collector.collect_history(Source())
    assert len(repaired) == 2501
    assert repaired[0]["metadata"]["history_validation"]["status"] == "passed"
    assert repaired[0]["metadata"]["history_validation"]["rejected_rows"] == 0


async def test_all_invalid_history_still_fails(history_payload, monkeypatch):
    for row in history_payload["closingPriceDaily"]:
        row["priceFirst"] = Decimal("999999999")

    async def history(*args):
        return parse_history(history_payload, PILOT_INS_CODE), SimpleNamespace(received_at=None)

    monkeypatch.setattr(collector, "fetch_history", history)
    with pytest.raises(SourceDataError, match="no valid rows"):
        await collector.collect_history(None)


def test_local_owner_lock_is_released(tmp_path):
    with single_instance(tmp_path):
        with pytest.raises(OSError), single_instance(tmp_path):
            pass
    with single_instance(tmp_path):
        pass


async def test_bad_receipt_and_wrong_endpoint_keep_retry_file(tmp_path):
    path = tmp_path / "pending.json"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    ) as client:
        pending = PendingUpload("http://company", path, client)
        pending.stage([{"synthetic": True}])
        with pytest.raises(ValueError, match="receipt"):
            await pending.flush()
        with pytest.raises(ValueError, match="another API URL"):
            await PendingUpload("http://other", path, client).flush()
        assert path.exists()


async def test_one_shot_provider_failure_does_not_report_success(monkeypatch, tmp_path):
    from tsetmc_marketdata.errors import ErrorKind

    class Source:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def instrument(*args):
        return SimpleNamespace(symbol="synthetic"), None

    async def failed(*args):
        raise SourceDataError(ErrorKind.INVALID_RECORD, "synthetic source failure")

    monkeypatch.setattr(collector, "TSETMCClient", lambda _: Source())
    monkeypatch.setattr(collector, "fetch_instrument", instrument)
    monkeypatch.setattr(collector, "collect_history", failed)
    monkeypatch.setattr(collector, "collect_current", failed)
    args = SimpleNamespace(
        api_url="http://localhost:8000",
        ins_code=[PILOT_INS_CODE],
        state_dir=tmp_path,
        once=True,
        history_seconds=60,
        poll_seconds=15,
    )
    with pytest.raises(ExceptionGroup) as caught:
        await collector.run(args)
    assert all(isinstance(error, SourceDataError) for error in caught.value.exceptions)


@pytest.mark.parametrize(
    "kind,once,retries",
    [
        ("network_failure", False, True),
        ("timeout", False, True),
        ("access_denied", False, False),
        ("invalid_record", False, False),
        ("network_failure", True, False),
    ],
)
async def test_initial_identity_lookup_retries_only_transient_failures(
    monkeypatch, kind, once, retries
):
    from tsetmc_marketdata.errors import ErrorKind, TransportError

    attempts, sleeps = [], []

    async def fetch(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise TransportError(ErrorKind(kind), "synthetic failure")
        return SimpleNamespace(symbol="synthetic"), None

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(collector, "fetch_instrument", fetch)
    monkeypatch.setattr(collector.asyncio, "sleep", sleep)
    if retries:
        assert (await collector.qualify(None, once=once)).symbol == "synthetic"
        assert sleeps == [60]
    else:
        with pytest.raises(TransportError):
            await collector.qualify(None, once=once)
        assert sleeps == []

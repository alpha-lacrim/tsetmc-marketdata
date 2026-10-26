"""Bounded, local validation evidence; never change acceptance decisions."""

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from tsetmc_marketdata.errors import SourceDataError
from tsetmc_marketdata.jsonio import diagnostic_default


def record(client: Any, response: Any, kind: str, code: str, details: Any) -> None:
    directory: Path | None = getattr(client, "diagnostic_dir", None)
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "validation.jsonl"
    entry = dict(
        instrument=code,
        kind=kind,
        received_at=response.received_at,
        source_path=response.path,
        diagnostics=details,
        meaning="collector validation finding; not independent proof the source is wrong",
    )
    # One event-loop writer; close immediately so shutdown cannot lose buffered evidence.
    handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    try:
        handler.emit(
            logging.LogRecord(
                "validation",
                logging.WARNING,
                "",
                0,
                json.dumps(entry, default=diagnostic_default, ensure_ascii=False),
                (),
                None,
            )
        )
    finally:
        handler.close()


def checked[T](client: Any, response: Any, kind: str, code: str, parse: Callable[[], T]) -> T:
    try:
        result = parse()
    except SourceDataError as error:
        record(
            client,
            response,
            kind,
            code,
            [
                dict(
                    severity="error",
                    code=error.kind.value,
                    message=str(error),
                    raw=response.payload,
                )
            ],
        )
        raise
    findings = getattr(result, "diagnostics", ())
    if findings:
        details = [
            asdict(item)
            if is_dataclass(item) and not isinstance(item, type)
            else dict(
                severity="warning",
                message=str(item),
                raw=response.payload,
            )
            for item in findings
        ]
        record(client, response, kind, code, details)
    return result

"""Reusable, paced, bounded async HTTP client. No restriction bypass or polling."""

from __future__ import annotations

import asyncio
import random
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Self

import httpx

from tsetmc_marketdata.config import Settings
from tsetmc_marketdata.errors import ErrorKind, SourceDataError, TransportError
from tsetmc_marketdata.jsonio import loads_source


@dataclass(frozen=True)
class JSONResponse:
    payload: dict[str, Any]
    raw_body: bytes
    received_at: datetime
    status_code: int
    path: str
    attempted_at: datetime | None = None


def _dns_failure(exc: BaseException) -> bool:
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending:
        cause = pending.pop()
        if id(cause) in seen:
            continue
        seen.add(id(cause))
        if isinstance(cause, socket.gaierror):
            return True
        for nested in (cause.__cause__, cause.__context__):
            if nested is not None:
                pending.append(nested)
    return False


class TSETMCClient:
    def __init__(
        self, settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings or Settings()
        self.diagnostic_dir: Path | None = None
        self._client = httpx.AsyncClient(
            base_url=self.settings.base_url,
            timeout=httpx.Timeout(
                connect=self.settings.connect_timeout,
                read=self.settings.read_timeout,
                write=self.settings.write_timeout,
                pool=self.settings.pool_timeout,
            ),
            limits=httpx.Limits(
                max_connections=self.settings.max_connections,
                max_keepalive_connections=self.settings.max_connections,
            ),
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "tsetmc-marketdata/0.1.0"},
            trust_env=False,
        )
        self._pace_lock = asyncio.Lock()
        self._next_request = 0.0

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    async def __aenter__(self) -> Self:
        if self.is_closed:
            raise RuntimeError("HTTP client is closed")
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pace(self) -> None:
        async with self._pace_lock:
            # Windows timers can wake early; recompute before reserving a start.
            while (delay := self._next_request - time.monotonic()) > 0:  # noqa: ASYNC110
                await asyncio.sleep(delay)
            self._next_request = time.monotonic() + self.settings.pacing_seconds

    def _retry_delay(self, attempt: int, response: httpx.Response | None) -> float | None:
        delay = min(
            self.settings.max_backoff_seconds,
            self.settings.backoff_seconds * 2.0 ** (attempt - 1) * (1 + random.random()),
        )
        if response is not None and (retry_after := response.headers.get("Retry-After")):
            try:
                seconds = float(retry_after)
            except ValueError:
                try:
                    until = parsedate_to_datetime(retry_after)
                    if until.tzinfo is None:
                        return None
                    seconds = (until - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    return None
            # Never retry earlier than requested. Long waits terminate this bounded probe.
            if not 0 <= seconds <= self.settings.max_backoff_seconds:
                return None
            delay = max(delay, seconds)
        return delay

    async def get_json(self, path: str) -> JSONResponse:
        if not re.fullmatch(r"[A-Za-z0-9_/-]+", path) or path.startswith("/"):
            raise ValueError(
                "request path must be a relative endpoint without query or credentials"
            )
        if self.is_closed:
            raise RuntimeError("HTTP client is closed")
        progress = [0]
        try:
            async with asyncio.timeout(self.settings.total_timeout):
                return await self._request(path, progress)
        except TimeoutError as exc:
            raise TransportError(
                ErrorKind.TIMEOUT, "total request deadline exceeded", attempts=progress[0]
            ) from exc

    async def _request(self, path: str, progress: list[int]) -> JSONResponse:
        for attempt in range(1, self.settings.max_retries + 2):
            await self._pace()
            progress[0] = attempt
            response = None
            attempted_at = datetime.now(UTC)
            try:
                response = await self._client.get(path)
            except httpx.TimeoutException as exc:
                error = TransportError(ErrorKind.TIMEOUT, "HTTP phase timed out", attempts=attempt)
                error.__cause__ = exc
            except httpx.TransportError as exc:
                kind = ErrorKind.DNS if _dns_failure(exc) else ErrorKind.NETWORK
                error = TransportError(kind, "HTTP transport failed", attempts=attempt)
                error.__cause__ = exc
            else:
                status = response.status_code
                if 200 <= status < 300:
                    received_at = datetime.now(UTC)
                    try:
                        payload = loads_source(response.content)
                    except (ValueError, UnicodeError, RecursionError) as exc:
                        raise SourceDataError(
                            ErrorKind.INVALID_JSON,
                            "invalid source JSON",
                            status=status,
                            attempts=attempt,
                        ) from exc
                    if not isinstance(payload, dict):
                        raise SourceDataError(
                            ErrorKind.SCHEMA,
                            "expected JSON object envelope",
                            status=status,
                            attempts=attempt,
                        )
                    return JSONResponse(
                        payload, response.content, received_at, status, path, attempted_at
                    )
                kind = (
                    ErrorKind.RATE_LIMIT
                    if status == 429
                    else ErrorKind.ACCESS_DENIED
                    if status in {401, 403}
                    else ErrorKind.SERVER
                    if status >= 500
                    else ErrorKind.HTTP
                )
                error = TransportError(
                    kind, f"HTTP status {status}", status=status, attempts=attempt
                )
                if status != 429 and status not in {500, 502, 503, 504}:
                    raise error
            if attempt > self.settings.max_retries:
                raise error
            delay = self._retry_delay(attempt, response)
            if delay is None:
                raise error
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

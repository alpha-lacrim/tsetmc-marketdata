"""Approximate collector payload traffic, persisted across restarts."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

FIELDS = ("source_received", "source_sent", "api_received", "api_sent")


class TrafficMeter:
    def __init__(self, path: Path):
        self.path = path
        self.bytes = dict.fromkeys(FIELDS, 0)
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.bytes = {key: saved[key] for key in FIELDS}
                if any(type(value) is not int or value < 0 for value in self.bytes.values()):
                    raise ValueError("invalid traffic counters")
            except (OSError, ValueError, TypeError, KeyError):
                print(f"Traffic counters unreadable at {path}; starting from zero", flush=True)
                self.bytes = dict.fromkeys(FIELDS, 0)
        self.previous = self.bytes.copy()

    def add(self, field: str, size: int) -> None:
        self.bytes[field] += size

    def frame(self, field: str, payload: str | bytes) -> None:
        self.add(field, len(payload.encode("utf-8") if isinstance(payload, str) else payload))

    def report(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"updated_at": datetime.now(UTC).isoformat(), **self.bytes}),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as error:
            print(f"Traffic counters could not be saved: {type(error).__name__}", flush=True)
        delta = {key: self.bytes[key] - self.previous[key] for key in FIELDS}
        self.previous = self.bytes.copy()
        print(
            "Traffic payload MiB total / last minute: "
            + "; ".join(
                f"{key} {self.bytes[key] / 1048576:.2f} / {delta[key] / 1048576:.2f}"
                for key in FIELDS
            ),
            flush=True,
        )

    async def report_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            self.report()

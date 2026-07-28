from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_WIRE_BYTES = 240


@dataclass
class DisplayState:
    status: str = "starting"
    primary: int | None = None
    secondary: int | None = None
    summary: str = "connecting to Codex"

    def wire(self) -> bytes:
        return encode(
            {
                "op": "state",
                "status": self.status,
                "primary": self.primary,
                "secondary": self.secondary,
                "summary": self.summary,
            }
        )


def encode(message: dict[str, Any]) -> bytes:
    candidate = dict(message)
    summary = str(candidate.get("summary", ""))
    while True:
        candidate["summary"] = summary
        raw = (json.dumps(candidate, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if len(raw) <= MAX_WIRE_BYTES:
            return raw
        if not summary:
            raise ValueError("message cannot fit BLE frame")
        summary = summary[:-1]


def decode(line: bytes | str) -> dict[str, Any]:
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    value = json.loads(line)
    if not isinstance(value, dict) or not isinstance(value.get("op"), str):
        raise ValueError("invalid wire message")
    return value


def remaining(window: dict[str, Any] | None) -> int | None:
    if not window or not isinstance(window.get("usedPercent"), int):
        return None
    return max(0, min(100, 100 - window["usedPercent"]))


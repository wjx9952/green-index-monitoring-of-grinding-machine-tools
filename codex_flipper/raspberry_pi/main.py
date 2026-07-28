from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from typing import Any

from .codex_client import CodexAppServer
from .protocol import DisplayState, encode, remaining
from .transport import BleTransport, StdioTransport


class Bridge:
    def __init__(
        self,
        device: str | None,
        stdio: bool,
        codex: str,
        state_callback: Callable[[DisplayState], None] | None = None,
    ) -> None:
        self.state = DisplayState()
        self.state_callback = state_callback
        self.transport = (
            StdioTransport(self.on_remote)
            if stdio
            else BleTransport(device or "", self.on_remote)
        )
        self.codex = CodexAppServer(self.on_codex, codex)

    async def publish_state(self) -> None:
        await self.transport.send(self.state.wire())
        if self.state_callback:
            self.state_callback(self.state)

    async def run(self) -> None:
        await self.transport.start()
        await self.publish_state()
        await self.codex.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.codex.close()

    async def on_remote(self, message: dict[str, Any]) -> None:
        op = message.get("op")
        if op in {"approve", "decline"}:
            handled = await self.codex.decide(op == "approve")
            self.state.status = "working" if handled else self.state.status
            self.state.summary = "approved" if op == "approve" and handled else (
                "declined" if handled else "no pending approval"
            )
            await self.publish_state()

    async def on_codex(self, message: dict[str, Any]) -> None:
        method, params = message.get("method", ""), message.get("params") or {}
        if method == "thread/status/changed":
            status = params.get("status") or {}
            kind = status.get("type", "unknown")
            flags = status.get("activeFlags") or []
            self.state.status = (
                "approval" if "waitingOnApproval" in flags else
                "question" if "waitingOnUserInput" in flags else
                "working" if kind == "active" else kind
            )
            self.state.summary = ", ".join(flags) or f"thread {kind}"
            await self.publish_state()
        elif method == "account/rateLimits/updated":
            limits = params.get("rateLimits") or {}
            self.state.primary = remaining(limits.get("primary"))
            self.state.secondary = remaining(limits.get("secondary"))
            await self.publish_state()
        elif method.endswith("/requestApproval"):
            summary = params.get("command") or params.get("reason") or method.split("/")[-2]
            self.state.status = "approval"
            self.state.summary = str(summary).replace("\n", " ")
            kind = "command" if "commandExecution" in method else "files"
            await self.transport.send(
                encode({"op": "approval", "kind": kind, "summary": self.state.summary})
            )
            if self.state_callback:
                self.state_callback(self.state)
        elif method == "turn/started":
            self.state.status, self.state.summary = "working", "turn running"
            await self.publish_state()
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            self.state.status = "idle" if turn.get("status") == "completed" else "error"
            self.state.summary = f"turn {turn.get('status', 'finished')}"
            await self.publish_state()


async def async_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", help="Flipper BLE name or MAC")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--codex", default="codex", help="Codex executable")
    args = parser.parse_args()
    if args.scan:
        for name, address in await BleTransport.scan():
            print(f"{address}  {name}")
        return
    if not args.stdio and not args.device:
        parser.error("use --device NAME_OR_MAC or --stdio")
    await Bridge(args.device, args.stdio, args.codex).run()


if __name__ == "__main__":
    asyncio.run(async_main())

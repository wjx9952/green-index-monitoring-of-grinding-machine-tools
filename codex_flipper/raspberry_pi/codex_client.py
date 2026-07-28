from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class PendingApproval:
    request_id: int | str
    method: str
    params: dict[str, Any]


class CodexAppServer:
    def __init__(self, handler: EventHandler, command: str = "codex") -> None:
        self.handler = handler
        self.command = command
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 1
        self._waiters: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.pending: PendingApproval | None = None
        self._reader: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.command,
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader = asyncio.create_task(self._read_loop())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-flipper-bridge",
                    "title": "Codex Flipper Bridge",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.notify("initialized", {})
        try:
            reply = await self.request("account/rateLimits/read", None)
            await self.handler(
                {
                    "method": "account/rateLimits/updated",
                    "params": {"rateLimits": (reply or {}).get("rateLimits", {})},
                }
            )
        except RuntimeError:
            pass
        try:
            threads = await self.request(
                "thread/list",
                {
                    "limit": 1,
                    "sortKey": "recency_at",
                    "sortDirection": "desc",
                    "useStateDbOnly": True,
                },
            )
            latest = ((threads or {}).get("data") or [None])[0]
            if latest:
                await self.handler(
                    {
                        "method": "thread/status/changed",
                        "params": {
                            "threadId": latest["id"],
                            "status": latest["status"],
                        },
                    }
                )
        except (KeyError, RuntimeError):
            pass

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        if self._reader:
            self._reader.cancel()

    async def request(self, method: str, params: Any) -> Any:
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters[request_id] = waiter
        await self._send({"id": request_id, "method": method, "params": params})
        response = await asyncio.wait_for(waiter, 15)
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result")

    async def notify(self, method: str, params: Any) -> None:
        await self._send({"method": method, "params": params})

    async def decide(self, accept: bool) -> bool:
        pending, self.pending = self.pending, None
        if not pending:
            return False
        await self._send(
            {"id": pending.request_id, "result": {"decision": "accept" if accept else "decline"}}
        )
        return True

    async def _send(self, value: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("app-server is not running")
        self.process.stdin.write(
            (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")
        )
        await self.process.stdin.drain()

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        while line := await self.process.stdout.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if request_id in self._waiters and ("result" in message or "error" in message):
                waiter = self._waiters.pop(request_id)
                if not waiter.done():
                    waiter.set_result(message)
                continue
            method = message.get("method", "")
            if request_id is not None and method.endswith("/requestApproval"):
                self.pending = PendingApproval(request_id, method, message.get("params") or {})
            await self.handler(message)

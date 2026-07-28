from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

from .protocol import decode

ReceiveHandler = Callable[[dict], Awaitable[None]]

# Flipper SerialSvc characteristics. Pi writes to ...62FE and subscribes to ...61FE.
SERIAL_RX = "19ed82ae-ed21-4c9d-4145-228e62fe0000"
SERIAL_TX = "19ed82ae-ed21-4c9d-4145-228e61fe0000"


class StdioTransport:
    def __init__(self, receive: ReceiveHandler) -> None:
        self.receive = receive

    async def start(self) -> None:
        asyncio.create_task(self._read())

    async def send(self, payload: bytes) -> None:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

    async def _read(self) -> None:
        while line := await asyncio.to_thread(sys.stdin.buffer.readline):
            try:
                await self.receive(decode(line))
            except (UnicodeError, ValueError):
                continue


class BleTransport:
    def __init__(self, device: str, receive: ReceiveHandler) -> None:
        self.device = device
        self.receive = receive
        self.client = None
        self._buffer = bytearray()

    @staticmethod
    async def scan() -> list[tuple[str, str]]:
        from bleak import BleakScanner

        devices = await BleakScanner.discover(timeout=8)
        return [
            (d.name or "(unnamed)", d.address)
            for d in devices
            if d.name and d.name not in {"(unnamed)"}
        ]

    async def start(self) -> None:
        from bleak import BleakClient, BleakScanner

        last_error: Exception | None = None
        for attempt in range(3):
            found = await BleakScanner.find_device_by_filter(
                lambda d, _: d.address.lower() == self.device.lower()
                or (d.name and self.device.lower() in d.name.lower()),
                timeout=20,
            )
            if not found:
                last_error = RuntimeError(f"BLE device not found: {self.device}")
                continue
            try:
                # Codex Monitor advertises a dedicated non-bonded serial
                # profile so a headless Pi needs no PIN agent.
                self.client = BleakClient(found, pair=False, timeout=30)
                await self.client.connect()
                await self.client.start_notify(SERIAL_TX, self._notification)
                return
            except Exception as exc:
                last_error = exc
                if self.client and self.client.is_connected:
                    await self.client.disconnect()
                await asyncio.sleep(2 + attempt * 2)
        raise RuntimeError(
            "Flipper 服务发现失败。请复制最新 Codex_Monitor.fap，"
            "退出旧应用后重新打开，再扫描连接。"
            f" 原始错误：{last_error}"
        )

    async def send(self, payload: bytes) -> None:
        if not self.client:
            raise RuntimeError("BLE not connected")
        for offset in range(0, len(payload), 180):
            await self.client.write_gatt_char(
                SERIAL_RX, payload[offset : offset + 180], response=False
            )

    def _notification(self, _sender, data: bytearray) -> None:
        self._buffer.extend(data)
        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            try:
                message = decode(line)
            except (UnicodeError, ValueError):
                continue
            asyncio.create_task(self.receive(message))

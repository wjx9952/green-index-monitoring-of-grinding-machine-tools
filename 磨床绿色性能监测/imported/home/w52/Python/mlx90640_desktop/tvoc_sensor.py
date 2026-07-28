"""Small UART driver for the Waveshare TVOC sensor."""

from __future__ import annotations

from dataclasses import dataclass
import time

import serial


ACTIVE_MODE_COMMAND = bytes((0xFE, 0x00, 0x78, 0x40, 0, 0, 0, 0, 0xB8))


@dataclass(frozen=True)
class TVOCReading:
    air: int
    co2_ppm: int
    ch2o_ppb: int
    tvoc_ppm: float


class TVOCSensor:
    def __init__(self, port: str, timeout: float = 1.5):
        self.serial = serial.Serial(
            port, 115200, timeout=timeout, write_timeout=timeout
        )
        self.serial.reset_input_buffer()

    def start_active_mode(self) -> None:
        self.serial.write(ACTIVE_MODE_COMMAND)
        self.serial.flush()
        time.sleep(0.1)

    def read(self) -> TVOCReading:
        deadline = time.monotonic() + (self.serial.timeout or 1.5)
        while time.monotonic() < deadline:
            if self.serial.read(1) == b"\xFE":
                break
        else:
            raise TimeoutError("等待数据超时")

        remainder = self.serial.read(10)
        if len(remainder) != 10:
            raise TimeoutError(f"数据帧不完整（{len(remainder)}/10 字节）")
        frame = b"\xFE" + remainder
        checksum = sum(frame[3:9]) & 0xFF
        if checksum != frame[9]:
            raise ValueError(
                f"校验错误（期望 {checksum:02X}，收到 {frame[9]:02X}）"
            )
        return TVOCReading(
            air=frame[1],
            co2_ppm=(frame[3] << 8) | frame[4],
            ch2o_ppb=(frame[5] << 8) | frame[6],
            tvoc_ppm=((frame[7] << 8) | frame[8]) / 1000.0,
        )

    def close(self) -> None:
        if self.serial.is_open:
            self.serial.close()

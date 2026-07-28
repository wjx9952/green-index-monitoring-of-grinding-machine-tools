"""AIR-MOD-001 17-byte UART protocol parser."""

from __future__ import annotations

from dataclasses import dataclass


FRAME_LENGTH = 17
FRAME_HEADER = b"\x3c\x02"


@dataclass(frozen=True, slots=True)
class AirReading:
    co2: int
    formaldehyde: int
    voc: int
    pm25: int
    pm10: int
    temperature: float
    humidity: float


def decode_frame(frame: bytes) -> AirReading:
    """Decode one validated AIR-MOD-001 data frame."""
    if len(frame) != FRAME_LENGTH:
        raise ValueError(f"frame must be {FRAME_LENGTH} bytes")
    if frame[:2] != FRAME_HEADER:
        raise ValueError("invalid frame header or protocol version")
    if sum(frame[:16]) & 0xFF != frame[16]:
        raise ValueError("checksum mismatch")

    def word(offset: int) -> int:
        return (frame[offset] << 8) | frame[offset + 1]

    temperature = (frame[12] & 0x7F) + frame[13] / 10
    if frame[12] & 0x80:
        temperature = -temperature

    return AirReading(
        co2=word(2),
        formaldehyde=word(4),
        voc=word(6),
        pm25=word(8),
        pm10=word(10),
        temperature=temperature,
        humidity=frame[14] + frame[15] / 10,
    )


class FrameParser:
    """Recover complete frames from an arbitrary UART byte stream."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.bad_frames = 0

    def feed(self, data: bytes) -> list[AirReading]:
        self.buffer.extend(data)
        readings: list[AirReading] = []
        while True:
            start = self.buffer.find(FRAME_HEADER)
            if start < 0:
                self.buffer[:] = self.buffer[-1:] if self.buffer[-1:] == b"\x3c" else b""
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < FRAME_LENGTH:
                break
            candidate = bytes(self.buffer[:FRAME_LENGTH])
            try:
                readings.append(decode_frame(candidate))
                del self.buffer[:FRAME_LENGTH]
            except ValueError:
                self.bad_frames += 1
                del self.buffer[0]
        return readings

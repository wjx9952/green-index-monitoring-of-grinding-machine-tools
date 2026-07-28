"""Waveshare UART LoRa HAT 配置与 GREEN1 数据包接收。"""

from __future__ import annotations

import json
import time

try:
    import lgpio
except ImportError:
    lgpio = None

try:
    import serial
except ImportError:
    serial = None


class LoRaReceiver:
    M0 = 22
    M1 = 27
    PREFIX = b"GREEN1:"
    MAX_BUFFER = 8192

    def __init__(self, port="/dev/ttyAMA0", baudrate=9600, frequency=868,
                 address=0, air_speed=2400):
        if lgpio is None:
            raise RuntimeError("缺少 python3-lgpio，请先运行 install.sh")
        if serial is None:
            raise RuntimeError("缺少 pyserial，请先运行 install.sh")
        self.frequency, self.channel, self.address = frequency, frequency - 850, address
        self.serial = None
        self.gpio_handle = None
        try:
            self.gpio_handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(self.gpio_handle, self.M0, 0)
            lgpio.gpio_claim_output(self.gpio_handle, self.M1, 1)
            self.serial = serial.Serial(
                port, baudrate, timeout=0.3, exclusive=True
            )
            self._configure(air_speed)
            self.serial.reset_input_buffer()
        except Exception:
            self.close()
            raise

    def _mode(self, m0, m1):
        lgpio.gpio_write(self.gpio_handle, self.M0, m0)
        lgpio.gpio_write(self.gpio_handle, self.M1, m1)

    def _configure(self, air_speed):
        air_values = {
            1200: 0x01, 2400: 0x02, 4800: 0x03, 9600: 0x04,
            19200: 0x05, 38400: 0x06, 62500: 0x07,
        }
        self._mode(0, 1)
        time.sleep(0.1)
        config = bytes([
            0xC2, 0x00, 0x09,
            (self.address >> 8) & 0xFF, self.address & 0xFF,
            0x00, 0x60 + air_values[air_speed], 0x20, self.channel,
            0x43, 0x00, 0x00,
        ])
        response = b""
        for _ in range(2):
            self.serial.reset_input_buffer()
            self.serial.write(config)
            self.serial.flush()
            time.sleep(0.3)
            response = self.serial.read(self.serial.in_waiting or 12)
            if response[:1] == b"\xC1":
                break
        self._mode(0, 0)
        time.sleep(0.1)
        if response[:1] != b"\xC1":
            raise RuntimeError(
                "LoRa 配置无应答；请启用串口、确认 M0/M1 跳帽已拔除，并检查接线"
            )

    @classmethod
    def extract_packets(cls, buffer: bytes) -> tuple[list[dict], bytes]:
        """从任意串口分块中提取包；前缀前可含模块输出的源地址头。"""
        packets = []
        while True:
            start = buffer.find(cls.PREFIX)
            if start < 0:
                return packets, buffer[-(len(cls.PREFIX) - 1):]
            newline = buffer.find(b"\n", start + len(cls.PREFIX))
            if newline < 0:
                return packets, buffer[start:][-cls.MAX_BUFFER:]
            raw = buffer[start + len(cls.PREFIX):newline].strip()
            buffer = buffer[newline + 1:]
            try:
                packet = json.loads(raw.decode("utf-8"))
                if isinstance(packet, dict) and packet.get("v") == 1:
                    packets.append(packet)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

    def packets(self, stop):
        buffer = b""
        while not stop.is_set():
            chunk = self.serial.read(max(self.serial.in_waiting, 1))
            if not chunk:
                continue
            buffer += chunk
            found, buffer = self.extract_packets(buffer)
            yield from found

    def close(self):
        if self.serial is not None:
            try:
                self.serial.close()
            finally:
                self.serial = None
        if lgpio is not None and self.gpio_handle is not None:
            try:
                for pin in (self.M0, self.M1):
                    try:
                        lgpio.gpio_free(self.gpio_handle, pin)
                    except Exception:
                        pass
                lgpio.gpiochip_close(self.gpio_handle)
            finally:
                self.gpio_handle = None

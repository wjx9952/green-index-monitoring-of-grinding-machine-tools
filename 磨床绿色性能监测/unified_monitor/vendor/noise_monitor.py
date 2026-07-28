#!/usr/bin/env python3
"""HH_07.06 noise monitor for Raspberry Pi GPIO12/13 (UART5)."""
from __future__ import annotations

import argparse
import csv
import json
import os
import select
import signal
import socket
import termios
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "data"


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class State:
    def __init__(self, capacity: int = 12000):
        self.lock = threading.Lock()
        self.samples = deque(maxlen=capacity)
        self.started = time.time()
        self.last_error = ""
        self.protocol = "探测中"
        self.device = ""

    def add(self, db: float, protocol: str):
        now = time.time()
        item = {"time": round(now, 3), "db": round(db, 1)}
        with self.lock:
            self.samples.append(item)
            self.protocol = protocol
            self.last_error = ""
        DATA.mkdir(exist_ok=True)
        filename = DATA / (datetime.fromtimestamp(now).strftime("%Y-%m-%d") + ".csv")
        new = not filename.exists()
        with filename.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(["time", "decibel"])
            writer.writerow([datetime.fromtimestamp(now).isoformat(timespec="milliseconds"), f"{db:.1f}"])

    def snapshot(self, seconds: int = 300):
        cutoff = time.time() - seconds
        with self.lock:
            points = [x for x in self.samples if x["time"] >= cutoff]
            current = self.samples[-1] if self.samples else None
            age = time.time() - current["time"] if current else None
            values = [x["db"] for x in points]
            return {
                "online": age is not None and age < 3,
                "current": current,
                "min": round(min(values), 1) if values else None,
                "max": round(max(values), 1) if values else None,
                "avg": round(sum(values) / len(values), 1) if values else None,
                "protocol": self.protocol,
                "device": self.device,
                "error": self.last_error,
                "points": points,
            }


class SerialMonitor(threading.Thread):
    SPEEDS = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
              57600: termios.B57600, 115200: termios.B115200}

    def __init__(self, state: State, device: str, baud: int, protocol: str, address: int):
        super().__init__(daemon=True)
        self.state, self.device, self.baud = state, device, baud
        self.wanted, self.address = protocol, address
        self.stop_event = threading.Event()

    def open_port(self):
        fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[4] = attrs[5] = self.SPEEDS[self.baud]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 1
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        return fd

    @staticmethod
    def command(address: int, protocol: str) -> bytes:
        body = bytes((address, 2, 0, 0, 0, 0)) if protocol == "passive" else bytes((address, 3, 0, 0, 0, 1))
        return body + crc16(body).to_bytes(2, "little")

    def parse(self, buf: bytearray):
        results = []
        while buf:
            # Active mode: BB AA 01 low high checksum
            pos = buf.find(b"\xbb\xaa\x01")
            if pos >= 0 and len(buf) >= pos + 6:
                if sum(buf[pos:pos + 5]) & 0xFF == buf[pos + 5]:
                    raw = buf[pos + 3] | (buf[pos + 4] << 8)
                    results.append((raw / 10, "active"))
                    del buf[:pos + 6]
                    continue
                del buf[:pos + 1]
                continue
            # Passive mode: address,02,status,low,high,00,crc low,crc high
            if len(buf) >= 8 and buf[0] == self.address and buf[1] == 2:
                frame = bytes(buf[:8])
                if crc16(frame[:6]) == int.from_bytes(frame[6:8], "little") and frame[2] == 0:
                    results.append(((frame[3] | frame[4] << 8) / 10, "passive"))
                del buf[:8]
                continue
            # Modbus: address,03,02,high,low,crc low,crc high
            if len(buf) >= 7 and buf[0] == self.address and buf[1:3] == b"\x03\x02":
                frame = bytes(buf[:7])
                if crc16(frame[:5]) == int.from_bytes(frame[5:7], "little"):
                    results.append((((frame[3] << 8) | frame[4]) / 10, "modbus"))
                del buf[:7]
                continue
            # Preserve possible partial active header, discard unrelated byte.
            if len(buf) < 8 and (buf[0] in (0xBB, self.address)):
                break
            del buf[0]
        return [(v, p) for v, p in results if 0 <= v <= 200]

    def run(self):
        self.state.device = self.device
        while not self.stop_event.is_set():
            fd = None
            try:
                fd = self.open_port()
                buf, last_data, last_poll = bytearray(), time.monotonic(), 0.0
                poll_mode = "passive" if self.wanted == "auto" else self.wanted
                while not self.stop_event.is_set():
                    now = time.monotonic()
                    if self.wanted != "active" and poll_mode != "active" and now - last_poll >= 0.5:
                        # In auto mode alternate both request formats until one replies.
                        if self.wanted == "auto" and now - last_data > 2:
                            poll_mode = "modbus" if poll_mode == "passive" else "passive"
                        os.write(fd, self.command(self.address, poll_mode))
                        last_poll = now
                    ready, _, _ = select.select([fd], [], [], 0.2)
                    if ready:
                        chunk = os.read(fd, 256)
                        if chunk:
                            buf.extend(chunk)
                            for value, mode in self.parse(buf):
                                self.state.add(value, mode)
                                last_data = now
                                if self.wanted == "auto":
                                    poll_mode = mode
            except (OSError, ValueError, termios.error) as exc:
                self.state.last_error = str(exc)
                time.sleep(2)
            finally:
                if fd is not None:
                    os.close(fd)


def handler_factory(state: State):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/data":
                try:
                    seconds = max(10, min(86400, int(parse_qs(parsed.query).get("seconds", [300])[0])))
                except ValueError:
                    seconds = 300
                body = json.dumps(state.snapshot(seconds), ensure_ascii=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            elif parsed.path in ("/", "/index.html"):
                body = (WEB / "index.html").read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            else:
                body = b"Not found"; self.send_response(404); self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass
    return Handler


def main():
    ap = argparse.ArgumentParser(description="HH_07.06 noise monitor")
    ap.add_argument("--device", default="/dev/ttyAMA4")
    ap.add_argument("--baud", type=int, choices=SerialMonitor.SPEEDS, default=115200)
    ap.add_argument("--protocol", choices=("auto", "active", "passive", "modbus"), default="auto")
    ap.add_argument("--address", type=int, default=1)
    ap.add_argument("--host", default="0.0.0.0"); ap.add_argument("--port", type=int, default=8088)
    args = ap.parse_args()
    state = State(); monitor = SerialMonitor(state, args.device, args.baud, args.protocol, args.address); monitor.start()
    server = ThreadingHTTPServer((args.host, args.port), handler_factory(state))
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"Noise monitor: http://{socket.gethostname()}:{args.port}  serial={args.device}@{args.baud}", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: monitor.stop_event.set(); server.server_close()


if __name__ == "__main__":
    main()

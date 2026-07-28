#!/usr/bin/env python3
"""磨床绿色性能指标 LoRa 接收端。"""

from __future__ import annotations

import argparse
import csv
import json
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

from lora_radio import LoRaReceiver


ROOT = Path(__file__).resolve().parent
BG, PANEL, PANEL2 = "#07111f", "#0d1c2d", "#12263a"
TEXT, MUTED, GREEN, BLUE, YELLOW, RED = (
    "#ecf7ff", "#84a0b6", "#47e6ad", "#56c8ff", "#ffc857", "#ff5468"
)

AIR_FIELDS = (
    ("co2", "二氧化碳", "ppm"),
    ("pm25", "PM2.5", "μg/m³"),
    ("pm10", "PM10", "μg/m³"),
    ("formaldehyde", "甲醛", "μg/m³"),
    ("voc", "VOC", "μg/m³"),
    ("temperature", "环境温度", "°C"),
    ("humidity", "相对湿度", "%RH"),
)


class ReceiveWorker(threading.Thread):
    def __init__(self, args: argparse.Namespace, events: queue.Queue, stop: threading.Event):
        super().__init__(daemon=True)
        self.args, self.events, self.stop = args, events, stop

    def emit(self, kind: str, value) -> None:
        self.events.put((kind, value))

    def run(self) -> None:
        if self.args.demo:
            self._demo()
            return
        radio = None
        try:
            radio = LoRaReceiver(
                port=self.args.port,
                baudrate=self.args.baud,
                frequency=self.args.frequency,
                address=self.args.address,
                air_speed=self.args.air_speed,
            )
            self.emit("status", f"在线 · {self.args.port} · {self.args.frequency} MHz")
            for packet in radio.packets(self.stop):
                self.emit("packet", packet)
        except Exception as exc:
            self.emit("error", str(exc))
        finally:
            if radio:
                radio.close()

    def _demo(self) -> None:
        self.emit("status", "演示模式（未使用 LoRa 硬件）")
        while not self.stop.wait(1):
            now = time.time()
            self.emit("packet", {
                "v": 1, "ts": int(now),
                "thermal": {"max": 48.2, "center": 35.6, "min": 24.1},
                "air": {
                    "co2": 486, "pm25": 12, "pm10": 18, "formaldehyde": 6,
                    "voc": 21, "temperature": 26.4, "humidity": 51.2,
                },
                "noise_db": 67.8,
            })


class ReceiverApp:
    def __init__(self, root: tk.Tk, args: argparse.Namespace):
        self.root, self.args = root, args
        self.stop = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.last_received = 0.0
        self.total = 0
        self.values: dict[str, tk.StringVar] = {}

        root.title("磨床绿色性能指标 LoRa 接收端")
        root.geometry("1024x600")
        root.minsize(800, 480)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F11>", self.toggle_fullscreen)
        root.bind("<Escape>", self.leave_fullscreen)
        if not args.windowed:
            root.attributes("-fullscreen", True)

        self.status = tk.StringVar(value="正在初始化 LoRa…")
        self.source_time = tk.StringVar(value="--")
        self.counter = tk.StringVar(value="已接收 0 包")
        self._build()
        self.worker = ReceiveWorker(args, self.events, self.stop)
        self.worker.start()
        root.after(100, self.poll)

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=14, pady=(10, 5))
        tk.Label(header, text="磨床绿色性能指标 LoRa 接收端", bg=BG, fg=TEXT,
                 font=("Noto Sans CJK SC", 18, "bold")).pack(side="left")
        self.clock = tk.Label(header, bg=BG, fg=MUTED, font=("DejaVu Sans", 10))
        self.clock.pack(side="right")

        bar = tk.Frame(self.root, bg=PANEL2)
        bar.pack(fill="x", padx=14, pady=(0, 8))
        self.dot = tk.Label(bar, text="●", bg=PANEL2, fg=YELLOW, font=("Sans", 13))
        self.dot.pack(side="left", padx=(10, 5), pady=5)
        tk.Label(bar, textvariable=self.status, bg=PANEL2, fg=TEXT,
                 font=("Noto Sans CJK SC", 10)).pack(side="left")
        tk.Label(bar, textvariable=self.counter, bg=PANEL2, fg=MUTED).pack(
            side="right", padx=10
        )

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = self._section(body, "温度与噪声")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        for key, title, unit, color in (
            ("tmax", "最高温度", "°C", RED),
            ("tcenter", "中心温度", "°C", YELLOW),
            ("tmin", "最低温度", "°C", BLUE),
            ("noise", "当前噪声", "dB", GREEN),
        ):
            self._card(left, key, title, unit, color)

        right = self._section(body, "空气质量")
        right.grid(row=0, column=1, sticky="nsew")
        cards = tk.Frame(right, bg=PANEL)
        cards.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        for col in range(4):
            cards.grid_columnconfigure(col, weight=1)
        for row in range(2):
            cards.grid_rowconfigure(row, weight=1)
        for index, (key, title, unit) in enumerate(AIR_FIELDS):
            self._card(cards, key, title, unit, BLUE, index // 4, index % 4)

        footer = tk.Frame(right, bg=PANEL2)
        footer.pack(fill="x", padx=5, pady=(0, 5))
        tk.Label(footer, text="发送端采样时间", bg=PANEL2, fg=MUTED).pack(
            side="left", padx=8, pady=6
        )
        tk.Label(footer, textvariable=self.source_time, bg=PANEL2, fg=TEXT).pack(
            side="right", padx=8
        )

    def _section(self, parent, title: str) -> tk.Frame:
        frame = tk.Frame(parent, bg=PANEL, highlightbackground="#27415b",
                         highlightthickness=1)
        tk.Label(frame, text=title, bg=PANEL, fg=TEXT,
                 font=("Noto Sans CJK SC", 12, "bold")).pack(
            anchor="w", padx=10, pady=7
        )
        return frame

    def _card(self, parent, key: str, title: str, unit: str, color: str,
              row: int | None = None, col: int | None = None) -> None:
        card = tk.Frame(parent, bg=PANEL2)
        if row is None:
            card.pack(fill="both", expand=True, padx=7, pady=4)
        else:
            card.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
        tk.Label(card, text=title, bg=PANEL2, fg=MUTED,
                 font=("Noto Sans CJK SC", 10)).pack(pady=(10, 2))
        line = tk.Frame(card, bg=PANEL2)
        line.pack()
        tk.Label(line, textvariable=self.values.setdefault(key, tk.StringVar(value="--")),
                 bg=PANEL2, fg=color, font=("DejaVu Sans", 22, "bold")).pack(side="left")
        tk.Label(line, text=unit, bg=PANEL2, fg=MUTED,
                 font=("Sans", 9)).pack(side="left", padx=(4, 0), pady=(10, 0))

    @staticmethod
    def fmt(value) -> str:
        if value is None:
            return "--"
        if isinstance(value, float):
            return f"{value:.1f}"
        return str(value)

    def poll(self) -> None:
        self.clock.configure(text=time.strftime("%Y-%m-%d  %H:%M:%S"))
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "packet":
                    self.show_packet(value)
                elif kind == "error":
                    self.status.set(f"LoRa 错误：{value}")
                    self.dot.configure(fg=RED)
                else:
                    self.status.set(value)
                    self.dot.configure(fg=GREEN)
        except queue.Empty:
            pass
        if self.last_received and time.monotonic() - self.last_received > 5:
            self.status.set("等待发送端数据（已超过 5 秒）")
            self.dot.configure(fg=YELLOW)
        if not self.stop.is_set():
            self.root.after(100, self.poll)

    def show_packet(self, packet: dict) -> None:
        thermal = packet.get("thermal") or {}
        air = packet.get("air") or {}
        for ui_key, packet_key in (
            ("tmax", "max"), ("tcenter", "center"), ("tmin", "min")
        ):
            self.values[ui_key].set(self.fmt(thermal.get(packet_key)))
        self.values["noise"].set(self.fmt(packet.get("noise_db")))
        for key, _title, _unit in AIR_FIELDS:
            self.values[key].set(self.fmt(air.get(key)))

        timestamp = packet.get("ts")
        try:
            self.source_time.set(datetime.fromtimestamp(float(timestamp)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ))
        except (TypeError, ValueError, OSError):
            self.source_time.set("--")
        self.total += 1
        self.counter.set(f"已接收 {self.total} 包")
        self.last_received = time.monotonic()
        self.status.set(f"接收正常 · {self.args.frequency} MHz · 2400 bps")
        self.dot.configure(fg=GREEN)
        self.save_csv(packet)

    def save_csv(self, packet: dict) -> None:
        data_dir = ROOT / "data"
        data_dir.mkdir(exist_ok=True)
        path = data_dir / f"{time.strftime('%Y-%m-%d')}.csv"
        new_file = not path.exists()
        thermal, air = packet.get("thermal") or {}, packet.get("air") or {}
        row = [
            int(time.time()), packet.get("ts"), thermal.get("max"),
            thermal.get("center"), thermal.get("min"),
            *[air.get(key) for key, _title, _unit in AIR_FIELDS],
            packet.get("noise_db"),
        ]
        try:
            with path.open("a", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                if new_file:
                    writer.writerow([
                        "接收时间戳", "发送时间戳", "最高温度", "中心温度", "最低温度",
                        "CO2", "PM2.5", "PM10", "甲醛", "VOC", "环境温度",
                        "相对湿度", "噪声dB",
                    ])
                writer.writerow(row)
        except OSError as exc:
            self.status.set(f"数据已接收，但 CSV 保存失败：{exc}")

    def toggle_fullscreen(self, _event=None) -> None:
        self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))

    def leave_fullscreen(self, _event=None) -> None:
        self.root.attributes("-fullscreen", False)
        self.root.geometry("1024x600")

    def close(self) -> None:
        self.stop.set()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyAMA0", help="LoRa HAT 串口")
    parser.add_argument("--baud", type=int, default=9600, help="串口波特率")
    parser.add_argument("--frequency", type=int, default=868, choices=range(850, 931))
    parser.add_argument("--address", type=int, default=0, choices=range(0, 65536))
    parser.add_argument("--air-speed", type=int, default=2400,
                        choices=(1200, 2400, 4800, 9600, 19200, 38400, 62500))
    parser.add_argument("--windowed", action="store_true", help="窗口模式启动")
    parser.add_argument("--demo", action="store_true", help="不用硬件，显示模拟数据")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = tk.Tk()
    ReceiverApp(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()

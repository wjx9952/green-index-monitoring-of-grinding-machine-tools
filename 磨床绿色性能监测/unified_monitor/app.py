#!/usr/bin/env python3
"""磨抛机床绿色性能统一监测：热成像、AIR-MOD、噪声与 LoRa。"""

from __future__ import annotations

import argparse
import base64
import json
import queue
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import asdict
from pathlib import Path
from tkinter import ttk

import cv2
import numpy as np
import serial

from vendor.airmod_protocol import FrameParser
from vendor.lora_hat import LoRaHat
from vendor import noise_monitor


ROOT = Path(__file__).resolve().parent
BG, PANEL, PANEL2 = "#07111f", "#0d1c2d", "#12263a"
TEXT, MUTED, GREEN, BLUE, YELLOW, RED = (
    "#ecf7ff", "#84a0b6", "#47e6ad", "#56c8ff", "#ffc857", "#ff5468"
)


class LatestQueue(queue.Queue):
    def put_latest(self, value) -> None:
        try:
            self.put_nowait(value)
        except queue.Full:
            try:
                self.get_nowait()
            except queue.Empty:
                pass
            self.put_nowait(value)


class ThermalWorker(threading.Thread):
    def __init__(self, output: LatestQueue, stop: threading.Event, refresh_rate: int):
        super().__init__(daemon=True)
        self.output, self.stop, self.refresh_rate = output, stop, refresh_rate

    def run(self) -> None:
        try:
            import board
            import busio
            import adafruit_mlx90640

            sensor = adafruit_mlx90640.MLX90640(busio.I2C(board.SCL, board.SDA))
            sensor.refresh_rate = getattr(
                adafruit_mlx90640.RefreshRate,
                f"REFRESH_{self.refresh_rate}_HZ",
            )
            self.output.put_latest(
                ("status", "online", f"MLX90640 已连接 · {self.refresh_rate} Hz")
            )
        except Exception as exc:
            self.output.put_latest(("status", "error", f"MLX90640：{exc}"))
            return
        raw = [0.0] * 768
        while not self.stop.is_set():
            try:
                sensor.getFrame(raw)
                frame = np.asarray(raw, dtype=np.float32).reshape(24, 32).copy()
                frame = np.nan_to_num(frame, nan=0, posinf=120, neginf=-20)
                self.output.put_latest(("frame", frame, time.time()))
            except (RuntimeError, ValueError) as exc:
                self.output.put_latest(("status", "warning", f"热成像重试：{exc}"))
                time.sleep(0.2)


class AirModWorker(threading.Thread):
    def __init__(self, port: str, output: LatestQueue, stop: threading.Event):
        super().__init__(daemon=True)
        self.port, self.output, self.stop = port, output, stop

    def run(self) -> None:
        parser = FrameParser()
        try:
            with serial.Serial(self.port, 9600, timeout=0.3) as connection:
                self.output.put_latest(("status", "online", f"AIR-MOD {self.port}"))
                while not self.stop.is_set():
                    chunk = connection.read(max(connection.in_waiting, 1))
                    for reading in parser.feed(chunk):
                        self.output.put_latest(("reading", reading, time.time()))
        except (serial.SerialException, OSError) as exc:
            self.output.put_latest(("status", "error", f"AIR-MOD：{exc}"))


class LoRaWorker(threading.Thread):
    def __init__(self, port: str, packets: LatestQueue, output: LatestQueue,
                 stop: threading.Event):
        super().__init__(daemon=True)
        self.port, self.packets, self.output, self.stop = port, packets, output, stop

    def run(self) -> None:
        radio = None
        try:
            radio = LoRaHat(port=self.port, frequency=868, address=0,
                            power=22, air_speed=2400)
            self.output.put_latest(("status", "online", f"LoRa {self.port}"))
            while not self.stop.is_set():
                try:
                    packet = self.packets.get(timeout=0.3)
                except queue.Empty:
                    continue
                radio.send(b"GREEN1:" + packet + b"\n", destination=65535)
                self.output.put_latest(("sent", len(packet), time.time()))
        except Exception as exc:
            self.output.put_latest(("status", "error", f"LoRa：{exc}"))
        finally:
            if radio:
                radio.close()


class UnifiedApp:
    def __init__(self, root: tk.Tk, args: argparse.Namespace):
        self.root, self.args = root, args
        root.title("磨抛机床绿色性能指标统一监测系统")
        root.geometry("1024x600")
        root.minsize(800, 480)
        root.attributes("-fullscreen", True)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F11>", self.toggle_fullscreen)
        root.bind("<Escape>", self.leave_fullscreen)

        self.stop = threading.Event()
        self.thermal_q, self.air_q, self.lora_q = (
            LatestQueue(2), LatestQueue(3), LatestQueue(3)
        )
        self.packet_q = LatestQueue(1)
        self.noise_state = noise_monitor.State()
        noise_monitor.DATA = ROOT / "data" / "noise"
        self.noise_worker = noise_monitor.SerialMonitor(
            self.noise_state, args.noise_port, args.noise_baud,
            args.noise_protocol, args.noise_address
        )
        self.thermal_worker = ThermalWorker(
            self.thermal_q, self.stop, args.thermal_rate
        )
        self.air_worker = AirModWorker(args.air_port, self.air_q, self.stop)
        self.lora_worker = None
        self.latest_frame = None
        self.photo = None
        self.air_reading = None
        self.thermal_values = None
        self.last_packet = 0.0
        self.noise_points = deque(maxlen=600)
        self.status_vars = {
            key: tk.StringVar(value="正在启动…")
            for key in ("thermal", "air", "noise", "lora")
        }
        self.values = {}
        self._style()
        self._build()

        self.noise_worker.start()
        self.thermal_worker.start()
        self.air_worker.start()
        if not args.no_lora:
            self.lora_worker = LoRaWorker(args.lora_port, self.packet_q, self.lora_q, self.stop)
            self.lora_worker.start()
        else:
            self.status_vars["lora"].set("已禁用")
        root.after(80, self.poll)

    def _style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL2, foreground=MUTED,
                        padding=(22, 10), font=("Sans", 11, "bold"))
        style.map("TNotebook.Tab", background=[("selected", "#19506d")],
                  foreground=[("selected", "white")])

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=10, pady=(5, 2))
        tk.Label(header, text="磨抛机床绿色性能指标统一监测系统", bg=BG, fg=TEXT,
                 font=("Noto Sans CJK SC", 15, "bold")).pack(side="left")
        self.clock = tk.Label(header, bg=BG, fg=MUTED, font=("DejaVu Sans", 9))
        self.clock.pack(side="right")

        status = tk.Frame(self.root, bg=BG)
        status.pack(fill="x", padx=10, pady=(0, 3))
        for key, title in (("thermal", "热成像"), ("air", "AIR-MOD"),
                           ("noise", "噪声"), ("lora", "LoRa")):
            box = tk.Frame(status, bg=PANEL2)
            box.pack(side="left", padx=(0, 5))
            tk.Label(box, text=title, bg=PANEL2, fg=TEXT,
                     font=("Sans", 8, "bold")).pack(side="left", padx=(6, 3), pady=2)
            tk.Label(
                box, textvariable=self.status_vars[key], bg=PANEL2, fg=MUTED,
                font=("Sans", 7), width=18, anchor="w"
            ).pack(side="left", padx=(0, 6))

        page = tk.Frame(self.root, bg=BG)
        page.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=11)
        page.grid_rowconfigure(1, weight=8)

        self._build_top(page)
        self._build_bottom(page)

    def _section_title(self, parent, text):
        tk.Label(parent, text=text, bg=PANEL, fg=TEXT,
                 font=("Noto Sans CJK SC", 10, "bold")).pack(
                     anchor="w", padx=8, pady=(4, 2)
                 )

    def _build_top(self, parent) -> None:
        top = tk.Frame(parent, bg=BG)
        top.grid(row=0, column=0, sticky="nsew", pady=(0, 3))
        # A roughly 400 px thermal panel matches the MLX90640 4:3 frame at
        # this row height on a 1024×600 display, avoiding both distortion and
        # large letterbox margins.
        top.grid_columnconfigure(0, weight=0, minsize=400)
        top.grid_columnconfigure(1, weight=1)
        top.grid_rowconfigure(0, weight=1)

        thermal = tk.Frame(top, bg=PANEL, highlightbackground="#27415b",
                           highlightthickness=1)
        thermal.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._section_title(thermal, "MLX90640 实时热成像")
        self.image = tk.Label(
            thermal, text="等待 MLX90640 数据…", bg="#020609", fg=MUTED,
            font=("Sans", 11)
        )
        self.image.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self.image.bind("<Configure>", lambda _e: self.render_thermal())

        temperatures = tk.Frame(top, bg=PANEL, highlightbackground="#27415b",
                                highlightthickness=1)
        temperatures.grid(row=0, column=1, sticky="nsew")
        self._section_title(temperatures, "实时温度")
        temperature_cards = tk.Frame(temperatures, bg=PANEL)
        temperature_cards.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        for key, title, color in (
            ("tmax", "最高温度", RED),
            ("tcenter", "中心温度", TEXT),
            ("tmin", "最低温度", BLUE),
        ):
            box = tk.Frame(temperature_cards, bg=PANEL2)
            box.pack(side="left", fill="both", expand=True, padx=2)
            tk.Label(box, text=title, bg=PANEL2, fg=MUTED,
                     font=("Noto Sans CJK SC", 9)).pack(pady=(55, 2))
            row = tk.Frame(box, bg=PANEL2)
            row.pack()
            tk.Label(
                row, textvariable=self.values.setdefault(key, tk.StringVar(value="--")),
                bg=PANEL2, fg=color, font=("DejaVu Sans", 20, "bold")
            ).pack(side="left")
            tk.Label(row, text=" °C", bg=PANEL2, fg=MUTED,
                     font=("Sans", 8)).pack(side="left", pady=(8, 0))

    def _build_bottom(self, parent) -> None:
        bottom = tk.Frame(parent, bg=BG)
        bottom.grid(row=1, column=0, sticky="nsew", pady=(3, 0))
        bottom.grid_columnconfigure(0, weight=3)
        bottom.grid_columnconfigure(1, weight=2)
        bottom.grid_rowconfigure(0, weight=1)

        air_panel = tk.Frame(bottom, bg=PANEL, highlightbackground="#27415b",
                             highlightthickness=1)
        air_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._section_title(air_panel, "AIR-MOD-001 空气质量")
        air_grid = tk.Frame(air_panel, bg=PANEL)
        air_grid.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        for col in range(12):
            air_grid.columnconfigure(col, weight=1)
        for row in range(2):
            air_grid.rowconfigure(row, weight=1)
        cards = (
            ("二氧化碳", "co2", "ppm"), ("PM2.5", "pm25", "μg/m³"),
            ("PM10", "pm10", "μg/m³"), ("甲醛", "formaldehyde", "μg/m³"),
            ("VOC", "voc", "μg/m³"), ("空气温度", "air_temp", "°C"),
            ("相对湿度", "humidity", "%RH"),
        )
        for index, item in enumerate(cards):
            if index < 4:
                row, col, span = 0, index * 3, 3
            else:
                row, col, span = 1, (index - 4) * 4, 4
            self._compact_card(air_grid, *item, row, col, span)

        noise_panel = tk.Frame(bottom, bg=PANEL, highlightbackground="#27415b",
                               highlightthickness=1)
        noise_panel.grid(row=0, column=1, sticky="nsew")
        self._section_title(noise_panel, "HH_07.06 环境噪声")
        stats = tk.Frame(noise_panel, bg=PANEL)
        stats.pack(fill="x", padx=8)
        for key, title in (
            ("noise", "当前"), ("noise_min", "最低"),
            ("noise_avg", "平均"), ("noise_max", "最高"),
        ):
            box = tk.Frame(stats, bg=PANEL2)
            box.pack(side="left", fill="x", expand=True, padx=2)
            tk.Label(box, text=title, bg=PANEL2, fg=MUTED,
                     font=("Sans", 7)).pack(pady=(3, 0))
            tk.Label(
                box, textvariable=self.values.setdefault(key, tk.StringVar(value="--")),
                bg=PANEL2, fg=BLUE, font=("DejaVu Sans", 12, "bold")
            ).pack(pady=(0, 2))
        self.chart = tk.Canvas(noise_panel, bg=PANEL, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, padx=4, pady=3)
        self.chart.bind("<Configure>", lambda _e: self.draw_noise())

    def _compact_card(self, parent, title, key, unit, row, col,
                      columnspan=1) -> None:
        box = tk.Frame(parent, bg=PANEL2)
        box.grid(row=row, column=col, columnspan=columnspan,
                 sticky="nsew", padx=2, pady=2)
        tk.Label(box, text=title, bg=PANEL2, fg=MUTED,
                 font=("Noto Sans CJK SC", 7)).pack(pady=(2, 0))
        line = tk.Frame(box, bg=PANEL2)
        line.pack(pady=(0, 2))
        tk.Label(
            line, textvariable=self.values.setdefault(key, tk.StringVar(value="--")),
            bg=PANEL2, fg=BLUE, font=("DejaVu Sans", 13, "bold")
        ).pack(side="left")
        tk.Label(line, text=" " + unit, bg=PANEL2, fg=MUTED,
                 font=("Sans", 6)).pack(side="left", pady=(5, 0))

    def _card(self, parent, title: str, key: str, unit: str, row: int, col: int):
        box = tk.Frame(parent, bg=PANEL, highlightbackground="#27415b", highlightthickness=1)
        box.grid(row=row, column=col, sticky="nsew", padx=7, pady=7)
        tk.Label(box, text=title, bg=PANEL, fg=MUTED,
                 font=("Noto Sans CJK SC", 11)).pack(anchor="w", padx=16, pady=(14, 2))
        line = tk.Frame(box, bg=PANEL)
        line.pack(anchor="w", padx=15, pady=(0, 14))
        var = self.values.setdefault(key, tk.StringVar(value="--"))
        tk.Label(line, textvariable=var, bg=PANEL, fg=BLUE,
                 font=("DejaVu Sans", 26, "bold")).pack(side="left")
        tk.Label(line, text=unit, bg=PANEL, fg=MUTED,
                 font=("Sans", 10)).pack(side="left", padx=6, pady=(12, 0))

    def _build_overview(self, parent) -> None:
        for c in range(4):
            parent.columnconfigure(c, weight=1)
        for r in range(2):
            parent.rowconfigure(r, weight=1)
        cards = (
            ("当前噪声", "noise", "dB(A)"), ("最高温度", "tmax", "°C"),
            ("中心温度", "tcenter", "°C"), ("CO₂", "co2", "ppm"),
            ("PM2.5", "pm25", "μg/m³"), ("PM10", "pm10", "μg/m³"),
            ("甲醛", "formaldehyde", "μg/m³"), ("VOC", "voc", "μg/m³"),
        )
        for i, item in enumerate(cards):
            self._card(parent, *item, i // 4, i % 4)

    def _build_thermal(self, parent) -> None:
        self.image = tk.Label(parent, text="等待 MLX90640 数据…", bg="#020609", fg=MUTED,
                              font=("Sans", 16))
        self.image.pack(fill="both", expand=True, padx=8, pady=8)
        self.image.bind("<Configure>", lambda _e: self.render_thermal())

    def _build_air(self, parent) -> None:
        for c in range(4):
            parent.columnconfigure(c, weight=1)
        for r in range(2):
            parent.rowconfigure(r, weight=1)
        cards = (
            ("二氧化碳", "co2", "ppm"), ("PM2.5", "pm25", "μg/m³"),
            ("PM10", "pm10", "μg/m³"), ("甲醛", "formaldehyde", "μg/m³"),
            ("VOC", "voc", "μg/m³"), ("温度", "air_temp", "°C"),
            ("相对湿度", "humidity", "%RH"),
        )
        for i, item in enumerate(cards):
            self._card(parent, *item, i // 4, i % 4)

    def _build_noise(self, parent) -> None:
        top = tk.Frame(parent, bg=BG)
        top.pack(fill="x")
        for key, title in (("noise", "当前"), ("noise_min", "最低"),
                           ("noise_avg", "平均"), ("noise_max", "最高")):
            box = tk.Frame(top, bg=PANEL)
            box.pack(side="left", fill="x", expand=True, padx=6, pady=6)
            tk.Label(box, text=title, bg=PANEL, fg=MUTED).pack(pady=(10, 0))
            tk.Label(box, textvariable=self.values.setdefault(key, tk.StringVar(value="--")),
                     bg=PANEL, fg=BLUE, font=("DejaVu Sans", 24, "bold")).pack(pady=(0, 10))
        self.chart = tk.Canvas(parent, bg=PANEL, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, padx=6, pady=6)
        self.chart.bind("<Configure>", lambda _e: self.draw_noise())

    def poll(self) -> None:
        self.clock.configure(text=time.strftime("%Y-%m-%d  %H:%M:%S"))
        self._poll_thermal()
        self._poll_air()
        self._poll_noise()
        self._poll_lora()
        if not self.stop.is_set():
            self.root.after(100, self.poll)

    def _poll_thermal(self) -> None:
        try:
            while True:
                event = self.thermal_q.get_nowait()
                if event[0] == "status":
                    self.status_vars["thermal"].set(event[2])
                else:
                    frame = event[1]
                    self.latest_frame = frame
                    center = float(np.mean(frame[11:13, 15:17]))
                    self.thermal_values = {
                        "max": round(float(np.max(frame)), 1),
                        "min": round(float(np.min(frame)), 1),
                        "center": round(center, 1),
                    }
                    self.values["tmax"].set(f"{self.thermal_values['max']:.1f}")
                    self.values["tcenter"].set(f"{center:.1f}")
                    self.values["tmin"].set(f"{self.thermal_values['min']:.1f}")
                    self.status_vars["thermal"].set(
                        f"在线 · {self.args.thermal_rate} Hz"
                    )
                    self.render_thermal()
        except queue.Empty:
            pass

    def render_thermal(self) -> None:
        if self.latest_frame is None:
            return
        frame = self.latest_frame
        low, high = np.percentile(frame, (2, 98))
        scaled = np.clip((frame - low) * 255 / max(1, high - low), 0, 255).astype(np.uint8)
        image = cv2.applyColorMap(scaled, cv2.COLORMAP_INFERNO)
        width = max(320, self.image.winfo_width() - 2)
        height = max(200, self.image.winfo_height() - 2)
        # MLX90640 is 32×24 (4:3). Scale uniformly so the complete sensor
        # frame remains visible without cropping or geometric distortion.
        scale = min(width / 32, height / 24)
        image = cv2.resize(
            image, (int(32 * scale), int(24 * scale)),
            interpolation=cv2.INTER_CUBIC,
        )
        ok, png = cv2.imencode(".png", image)
        if ok:
            self.photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
            self.image.configure(image=self.photo, text="")

    def _poll_air(self) -> None:
        try:
            while True:
                event = self.air_q.get_nowait()
                if event[0] == "status":
                    self.status_vars["air"].set(event[2])
                    continue
                reading = event[1]
                self.air_reading = asdict(reading)
                mapping = {
                    "co2": reading.co2, "pm25": reading.pm25, "pm10": reading.pm10,
                    "formaldehyde": reading.formaldehyde, "voc": reading.voc,
                    "air_temp": reading.temperature, "humidity": reading.humidity,
                }
                for key, value in mapping.items():
                    self.values[key].set(f"{value:.1f}" if isinstance(value, float) else str(value))
                self.status_vars["air"].set("在线 · 9600 8N1")
        except queue.Empty:
            pass

    def _poll_noise(self) -> None:
        snap = self.noise_state.snapshot(300)
        if snap["online"] and snap["current"]:
            self.status_vars["noise"].set(f"在线 · {snap['protocol']}")
            self.values["noise"].set(f"{snap['current']['db']:.1f}")
            for key in ("min", "avg", "max"):
                value = snap[key]
                self.values[f"noise_{key}"].set("--" if value is None else f"{value:.1f}")
            points = snap["points"]
            if points and (not self.noise_points or points[-1]["time"] != self.noise_points[-1]["time"]):
                self.noise_points = deque(points[-600:], maxlen=600)
                self.draw_noise()
        else:
            self.status_vars["noise"].set("等待数据" if not snap["error"] else snap["error"])

    def draw_noise(self) -> None:
        c = self.chart
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 100 or h < 100:
            return
        left, right, top, bottom = 50, 18, 18, 30
        for db in range(30, 131, 20):
            y = top + (h - top - bottom) * (130 - db) / 100
            c.create_line(left, y, w - right, y, fill="#203b52")
            c.create_text(left - 15, y, text=str(db), fill=MUTED)
        points = list(self.noise_points)
        if len(points) < 2:
            return
        t0, t1 = points[0]["time"], points[-1]["time"]
        coords = []
        for point in points:
            x = left + (w - left - right) * (point["time"] - t0) / max(1, t1 - t0)
            y = top + (h - top - bottom) * (130 - max(30, min(130, point["db"]))) / 100
            coords.extend((x, y))
        c.create_line(*coords, fill=BLUE, width=2, smooth=True)

    def _poll_lora(self) -> None:
        try:
            while True:
                event = self.lora_q.get_nowait()
                self.status_vars["lora"].set(event[2] if event[0] == "status"
                                             else f"已发送 {event[1]} 字节")
        except queue.Empty:
            pass
        if not self.lora_worker or time.monotonic() - self.last_packet < 1:
            return
        snap = self.noise_state.snapshot(10)
        packet = {
            "v": 1, "ts": int(time.time()), "thermal": self.thermal_values,
            "air": self.air_reading,
            "noise_db": snap["current"]["db"] if snap["current"] else None,
        }
        encoded = json.dumps(packet, ensure_ascii=True, separators=(",", ":")).encode()
        self.packet_q.put_latest(encoded)
        self.last_packet = time.monotonic()

    def toggle_fullscreen(self, _event=None) -> None:
        self.root.attributes(
            "-fullscreen", not bool(self.root.attributes("-fullscreen"))
        )

    def leave_fullscreen(self, _event=None) -> None:
        self.root.attributes("-fullscreen", False)
        self.root.geometry("1024x600")

    def close(self) -> None:
        self.stop.set()
        self.noise_worker.stop_event.set()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thermal-rate", type=int, choices=(2, 4, 8), default=4)
    parser.add_argument("--air-port", default="/dev/ttyAMA1")
    parser.add_argument("--noise-port", default="/dev/ttyAMA4")
    parser.add_argument("--noise-baud", type=int, default=115200)
    parser.add_argument("--noise-protocol", choices=("auto", "active", "passive", "modbus"),
                        default="auto")
    parser.add_argument("--noise-address", type=int, default=1)
    parser.add_argument("--lora-port", default="/dev/ttyAMA0")
    parser.add_argument("--no-lora", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = tk.Tk()
    UnifiedApp(root, args)
    root.mainloop()


if __name__ == "__main__":
    main()

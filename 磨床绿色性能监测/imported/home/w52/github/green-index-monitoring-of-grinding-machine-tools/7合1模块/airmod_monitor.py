#!/usr/bin/env python3
"""Desktop monitor for the Kanfur AIR-MOD-001 seven-in-one sensor."""

from __future__ import annotations

import glob
import queue
import threading
import time
import tkinter as tk
from dataclasses import fields
from datetime import datetime
from tkinter import ttk

import serial

from airmod_protocol import AirReading, FrameParser


BG = "#f4f6f8"
PANEL = "#ffffff"
INK = "#18212b"
MUTED = "#687684"
GREEN = "#16835b"
RED = "#c43d3d"
ACCENTS = ("#1a73a8", "#c34f32", "#8b5aa5", "#29776c", "#58712b", "#b06724", "#28739b")
PREFERRED_PORT = "/dev/ttyAMA1"  # UART1: GPIO0/Pin 27 TX, GPIO1/Pin 28 RX

METRICS = (
    ("co2", "二氧化碳", "ppm"),
    ("pm25", "PM2.5", "μg/m³"),
    ("pm10", "PM10", "μg/m³"),
    ("formaldehyde", "甲醛", "μg/m³"),
    ("voc", "VOC", "μg/m³"),
    ("temperature", "温度", "°C"),
    ("humidity", "湿度", "%RH"),
)


def serial_ports() -> list[str]:
    patterns = ("/dev/serial*", "/dev/ttyUSB*", "/dev/ttyACM*", "/dev/ttyAMA*", "/dev/ttyS*")
    ports = {path for pattern in patterns for path in glob.glob(pattern)}
    return sorted(ports, key=lambda path: (path != PREFERRED_PORT, path))


class SerialWorker(threading.Thread):
    def __init__(self, port: str, output: queue.Queue) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.output = output
        self.stop_event = threading.Event()

    def run(self) -> None:
        parser = FrameParser()
        try:
            with serial.Serial(self.port, 9600, timeout=0.3) as connection:
                self.output.put(("connected", self.port))
                while not self.stop_event.is_set():
                    chunk = connection.read(max(connection.in_waiting, 1))
                    for reading in parser.feed(chunk):
                        self.output.put(("reading", reading))
        except (serial.SerialException, OSError) as exc:
            self.output.put(("error", str(exc)))
        finally:
            self.output.put(("stopped", self.port))

    def stop(self) -> None:
        self.stop_event.set()


class MetricCard(ttk.Frame):
    def __init__(self, parent: tk.Widget, title: str, unit: str, accent: str) -> None:
        super().__init__(parent, style="Card.TFrame", padding=(18, 14))
        ttk.Label(self, text=title, style="CardTitle.TLabel").pack(anchor="w")
        value_row = ttk.Frame(self, style="Card.TFrame")
        value_row.pack(anchor="w", fill="x", pady=(9, 0))
        self.value = ttk.Label(value_row, text="--", style="Value.TLabel")
        self.value.pack(side="left")
        ttk.Label(value_row, text=unit, style="Unit.TLabel").pack(side="left", padx=(7, 0), pady=(14, 0))
        tk.Frame(self, bg=accent, height=3).pack(fill="x", side="bottom", pady=(13, 0))

    def set(self, value: int | float) -> None:
        self.value.configure(text=f"{value:.1f}" if isinstance(value, float) else str(value))


class MonitorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AIR-MOD-001 空气质量监测")
        self.geometry("1024x650")
        self.minsize(760, 560)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.events: queue.Queue = queue.Queue()
        self.worker: SerialWorker | None = None
        self.last_reading_at = 0.0
        self.cards: dict[str, MetricCard] = {}
        self._configure_style()
        self._build_ui()
        self.refresh_ports(auto_connect=True)
        self.after(100, self.poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Noto Sans CJK SC", 11))
        style.configure("Header.TLabel", font=("Noto Sans CJK SC", 22, "bold"), foreground=INK)
        style.configure("Sub.TLabel", foreground=MUTED)
        style.configure("Card.TFrame", background=PANEL, relief="solid", borderwidth=1)
        style.configure("CardTitle.TLabel", background=PANEL, foreground=MUTED, font=("Noto Sans CJK SC", 12))
        style.configure("Value.TLabel", background=PANEL, foreground=INK, font=("DejaVu Sans", 30, "bold"))
        style.configure("Unit.TLabel", background=PANEL, foreground=MUTED, font=("DejaVu Sans", 10))
        style.configure("TButton", font=("Noto Sans CJK SC", 10), padding=(12, 7))
        style.configure("Accent.TButton", background="#236b8e", foreground="white")
        style.map("Accent.TButton", background=[("active", "#185775")])

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(28, 22))
        root.pack(fill="both", expand=True)
        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 20))
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text="空气质量监测", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="AIR-MOD-001 · 七合一传感器", style="Sub.TLabel").pack(anchor="w", pady=(3, 0))

        controls = ttk.Frame(header)
        controls.pack(side="right", anchor="e")
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(controls, textvariable=self.port_var, width=19, state="readonly")
        self.port_box.pack(side="left", padx=(0, 7))
        ttk.Button(controls, text="刷新", command=self.refresh_ports).pack(side="left", padx=(0, 7))
        self.connect_button = ttk.Button(controls, text="连接", style="Accent.TButton", command=self.toggle_connection)
        self.connect_button.pack(side="left")

        grid = ttk.Frame(root)
        grid.pack(fill="both", expand=True)
        for col in range(4):
            grid.columnconfigure(col, weight=1, uniform="metric")
        for row in range(2):
            grid.rowconfigure(row, weight=1, uniform="metric")
        for index, ((key, title, unit), accent) in enumerate(zip(METRICS, ACCENTS)):
            card = MetricCard(grid, title, unit, accent)
            card.grid(row=index // 4, column=index % 4, sticky="nsew", padx=6, pady=6)
            self.cards[key] = card

        footer = ttk.Frame(root)
        footer.pack(fill="x", pady=(16, 0))
        self.status_dot = tk.Label(footer, text="●", bg=BG, fg=MUTED, font=("DejaVu Sans", 11))
        self.status_dot.pack(side="left")
        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(footer, textvariable=self.status_var).pack(side="left", padx=(5, 0))
        self.updated_var = tk.StringVar(value="等待传感器数据")
        ttk.Label(footer, textvariable=self.updated_var, style="Sub.TLabel").pack(side="right")

    def refresh_ports(self, auto_connect: bool = False) -> None:
        ports = serial_ports()
        self.port_box["values"] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else "")
        if not ports:
            self.status_var.set("未找到串口，请检查接线和串口设置")
        elif auto_connect and not self.worker:
            self.connect()

    def toggle_connection(self) -> None:
        self.disconnect() if self.worker else self.connect()

    def connect(self) -> None:
        port = self.port_var.get()
        if not port:
            self.refresh_ports()
            return
        self.status_var.set(f"正在连接 {port}…")
        self.worker = SerialWorker(port, self.events)
        self.worker.start()
        self.connect_button.configure(text="断开")
        self.port_box.configure(state="disabled")

    def disconnect(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.status_dot.configure(fg=MUTED)
        self.status_var.set("未连接")
        self.connect_button.configure(text="连接")
        self.port_box.configure(state="readonly")

    def poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "connected":
                    self.status_dot.configure(fg=GREEN)
                    self.status_var.set(f"已连接 {payload} · 9600 8N1")
                elif kind == "reading":
                    self.show_reading(payload)
                elif kind == "error":
                    self.status_dot.configure(fg=RED)
                    self.status_var.set(f"串口错误：{payload}")
                elif kind == "stopped" and self.worker and self.worker.port == payload:
                    self.worker = None
                    self.connect_button.configure(text="连接")
                    self.port_box.configure(state="readonly")
        except queue.Empty:
            pass
        if self.last_reading_at and time.monotonic() - self.last_reading_at > 3:
            self.status_dot.configure(fg="#d69428")
            self.updated_var.set("超过 3 秒未收到有效数据")
        self.after(100, self.poll_events)

    def show_reading(self, reading: AirReading) -> None:
        for field in fields(reading):
            self.cards[field.name].set(getattr(reading, field.name))
        self.last_reading_at = time.monotonic()
        self.status_dot.configure(fg=GREEN)
        self.updated_var.set(f"最后更新 {datetime.now():%H:%M:%S}")

    def close(self) -> None:
        if self.worker:
            self.worker.stop()
        self.destroy()


if __name__ == "__main__":
    MonitorApp().mainloop()

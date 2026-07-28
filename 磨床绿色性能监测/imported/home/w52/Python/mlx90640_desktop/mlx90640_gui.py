#!/usr/bin/env python3
"""A small desktop dashboard for the MLX90640 thermal camera."""

from __future__ import annotations

import queue
import threading
import time
import base64
import glob
import json
import math
import os
import re
import select
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np

from tvoc_sensor import TVOCSensor


APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR.parent))
from lora_hat import LoRaHat

SNAPSHOT_DIR = Path.home() / "Pictures" / "MLX90640"
APP_TITLE = "摩抛机床绿色性能指标监测系统"
DATA_STALE_SECONDS = 3.0
PALETTES = {
    "熔岩": cv2.COLORMAP_INFERNO,
    "彩虹": cv2.COLORMAP_JET,
    "热力": cv2.COLORMAP_HOT,
    "深海": cv2.COLORMAP_OCEAN,
}

AIR_DATA_PATTERN = re.compile(
    r"MQ135:\s*ADC=\s*(?P<mq_adc>\d+),\s*V=(?P<mq_voltage>\d+(?:\.\d+)?),\s*"
    r"DOUT=(?P<dout>LOW|HIGH),\s*alarm=(?P<alarm>YES|NO)\s*\|\s*"
    r"Dust:\s*ADC=\s*(?P<dust_adc>\d+),\s*V=(?P<dust_voltage>\d+(?:\.\d+)?),\s*"
    r"dust=(?P<dust>\d+(?:\.\d+)?)\s*ug/m3"
)


class SensorWorker(threading.Thread):
    def __init__(self, messages: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.messages = messages
        self.stop_event = stop_event

    def put_latest(self, item):
        try:
            self.messages.put_nowait(item)
        except queue.Full:
            try:
                self.messages.get_nowait()
            except queue.Empty:
                pass
            self.messages.put_nowait(item)

    def run(self):
        try:
            import board
            import busio
            import adafruit_mlx90640

            i2c = busio.I2C(board.SCL, board.SDA)
            sensor = adafruit_mlx90640.MLX90640(i2c)
            sensor.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
            serial = "-".join(f"{part:04X}" for part in sensor.serial_number)
            self.put_latest(("status", f"传感器已连接 · SN {serial}"))
        except Exception as exc:
            self.put_latest(("error", f"无法连接 MLX90640：{exc}"))
            return

        frame = [0.0] * 768
        failures = 0
        while not self.stop_event.is_set():
            try:
                sensor.getFrame(frame)
                data = np.asarray(frame, dtype=np.float32).reshape(24, 32).copy()
                if np.isfinite(data).sum() < 700:
                    raise ValueError("本帧有效温度数据不足")
                data = np.nan_to_num(data, nan=0.0, posinf=120.0, neginf=-20.0)
                data = np.clip(data, -20.0, 120.0)
                failures = 0
                self.put_latest(("frame", data, time.monotonic()))
            except (RuntimeError, ValueError) as exc:
                failures += 1
                if failures == 1 or failures % 10 == 0:
                    self.put_latest(("status", f"正在重试读取（{exc}）"))
                time.sleep(0.2)


def serial_ports():
    ports = sorted(set(
        glob.glob("/dev/ttyAMA*") + glob.glob("/dev/ttyS*")
        + glob.glob("/dev/serial*") + glob.glob("/dev/ttyUSB*")
    ))
    return ports or ["/dev/ttyAMA4"]


class TVOCWorker(threading.Thread):
    def __init__(self, port, messages, stop_event):
        super().__init__(daemon=True)
        self.port = port
        self.messages = messages
        self.stop_event = stop_event
        self.sensor = None

    def run(self):
        try:
            self.sensor = TVOCSensor(self.port)
            self.sensor.start_active_mode()
            self.messages.put(("connected", self.port))
            missed = 0
            while not self.stop_event.is_set():
                try:
                    self.messages.put(("reading", self.sensor.read()))
                    missed = 0
                except TimeoutError as exc:
                    missed += 1
                    if missed >= 3:
                        self.messages.put(("warning", str(exc)))
                except ValueError as exc:
                    self.messages.put(("warning", str(exc)))
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            if self.sensor is not None:
                self.sensor.close()
            self.messages.put(("stopped", None))


class AirSensorWorker(threading.Thread):
    """Read the Pico 2 W MQ-135 and dust text stream without extra packages."""

    def __init__(self, port, messages, stop_event):
        super().__init__(daemon=True)
        self.port = port
        self.messages = messages
        self.stop_event = stop_event

    def run(self):
        device = -1
        try:
            device = os.open(
                self.port, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK
            )
            self.messages.put(("connected", self.port))
            pending = b""
            while not self.stop_event.is_set():
                readable, _, _ = select.select([device], [], [], 0.5)
                if not readable:
                    continue
                pending += os.read(device, 4096)
                while b"\n" in pending:
                    raw, pending = pending.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").strip()
                    match = AIR_DATA_PATTERN.search(line)
                    if match:
                        self.messages.put(("reading", match.groupdict()))
        except OSError as exc:
            self.messages.put(("error", str(exc)))
        finally:
            if device >= 0:
                os.close(device)
            self.messages.put(("stopped", None))


class LoRaTransmitWorker(threading.Thread):
    def __init__(self, port, messages, packets, stop_event):
        super().__init__(daemon=True)
        self.port = port
        self.messages = messages
        self.packets = packets
        self.stop_event = stop_event

    def run(self):
        radio = None
        try:
            radio = LoRaHat(
                port=self.port, frequency=868, address=0,
                power=22, air_speed=2400
            )
            self.messages.put(("connected", self.port))
            while not self.stop_event.is_set():
                try:
                    packet = self.packets.get(timeout=0.25)
                except queue.Empty:
                    continue
                radio.send(b"ENV1:" + packet + b"\n", destination=65535)
                self.messages.put(("sent", len(packet)))
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            if radio is not None:
                radio.close()
            self.messages.put(("stopped", None))


class ThermalApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x960")
        self.root.minsize(980, 820)
        self.root.configure(bg="#10141b")

        self.messages: queue.Queue = queue.Queue(maxsize=3)
        self.stop_event = threading.Event()
        self.worker = SensorWorker(self.messages, self.stop_event)
        self.latest_data = None
        self.latest_bgr = None
        self.photo = None
        self.paused = False
        self.last_frame_time = None
        self.tvoc_messages = queue.Queue()
        self.tvoc_stop_event = threading.Event()
        self.tvoc_worker = None
        self.last_tvoc_reading = None
        self.air_messages = queue.Queue()
        self.air_stop_event = threading.Event()
        self.air_worker = None
        self.last_air_reading = None
        self.lora_messages = queue.Queue()
        self.lora_packets = queue.Queue(maxsize=1)
        self.lora_stop_event = threading.Event()
        self.lora_worker = None
        self.last_lora_packet = 0.0
        self.telemetry = {
            "thermal": [None, None, None],
            "tvoc": [None, None, None, None],
            "mq135": [None, None, None, None],
            "dust": [None, None, None],
        }

        self.palette = tk.StringVar(value="熔岩")
        self.auto_range = tk.BooleanVar(value=True)
        self.range_min = tk.DoubleVar(value=15.0)
        self.range_max = tk.DoubleVar(value=45.0)
        self.status = tk.StringVar(value="正在连接传感器…")
        self.min_text = tk.StringVar(value="--.- °C")
        self.max_text = tk.StringVar(value="--.- °C")
        self.center_text = tk.StringVar(value="--.- °C")
        self.tvoc_port = tk.StringVar(value="/dev/ttyAMA4")
        self.tvoc_status = tk.StringVar(value="未连接")
        self.tvoc_value = tk.StringVar(value="--")
        self.air_value = tk.StringVar(value="--")
        self.co2_value = tk.StringVar(value="--")
        self.ch2o_value = tk.StringVar(value="--")
        self.air_port = tk.StringVar(value=self._default_air_port())
        self.air_status = tk.StringVar(value="未连接")
        self.mq_status = tk.StringVar(value="未连接")
        self.dust_status = tk.StringVar(value="未连接")
        self.mq_adc = tk.StringVar(value="----")
        self.mq_voltage = tk.StringVar(value="-.---")
        self.mq_dout = tk.StringVar(value="----")
        self.dust_value = tk.StringVar(value="---.-")
        self.dust_adc = tk.StringVar(value="----")
        self.dust_voltage = tk.StringVar(value="-.---")
        self.lora_port = tk.StringVar(value="/dev/ttyAMA0")
        self.lora_status = tk.StringVar(value="LoRa 未启动")

        self._configure_style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker.start()
        self.root.after(50, self.poll)

    def _configure_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground="#242b36", background="#242b36",
                        foreground="#f1f5f9", arrowcolor="#f1f5f9")
        style.configure("TCheckbutton", background="#171d26", foreground="#dce3ec")

    def _build(self):
        header = tk.Frame(self.root, bg="#10141b")
        header.pack(fill="x", padx=24, pady=(20, 12))
        tk.Label(header, text=APP_TITLE, bg="#10141b", fg="#f8fafc",
                 font=("Sans", 20, "bold")).pack(side="left")
        self.pause_button = tk.Button(
            header, text="暂停", command=self.toggle_pause, bg="#2563eb", fg="white",
            activebackground="#1d4ed8", activeforeground="white", relief="flat",
            padx=20, pady=8, font=("Sans", 11, "bold"), cursor="hand2"
        )
        self.pause_button.pack(side="right")
        tk.Button(
            header, text="保存截图", command=self.save_snapshot, bg="#242b36", fg="#e5e7eb",
            activebackground="#303846", activeforeground="white", relief="flat",
            padx=18, pady=8, font=("Sans", 11), cursor="hand2"
        ).pack(side="right", padx=10)

        content = tk.Frame(self.root, bg="#10141b")
        content.pack(fill="both", expand=True, padx=24)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        image_card = tk.Frame(content, bg="#07090d", highlightbackground="#2a3240",
                              highlightthickness=1)
        image_card.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.image_label = tk.Label(
            image_card, text="正在等待第一帧…", bg="#07090d", fg="#7f8b9b",
            font=("Sans", 15)
        )
        self.image_label.pack(fill="both", expand=True, padx=8, pady=8)
        self.image_label.bind("<Configure>", lambda _event: self.render())

        side = tk.Frame(content, bg="#171d26", width=230)
        side.grid(row=0, column=1, sticky="ns")
        side.grid_propagate(False)

        tk.Label(side, text="实时温度", bg="#171d26", fg="#93a4b8",
                 font=("Sans", 11, "bold")).pack(anchor="w", padx=18, pady=(18, 8))
        self._metric(side, "最高温度", self.max_text, "#fb7185")
        self._metric(side, "中心温度", self.center_text, "#f8fafc")
        self._metric(side, "最低温度", self.min_text, "#60a5fa")

        tk.Frame(side, bg="#303846", height=1).pack(fill="x", padx=18, pady=12)
        tk.Label(side, text="色彩方案", bg="#171d26", fg="#93a4b8",
                 font=("Sans", 10)).pack(anchor="w", padx=18)
        combo = ttk.Combobox(side, textvariable=self.palette, values=list(PALETTES),
                             state="readonly")
        combo.pack(fill="x", padx=18, pady=(5, 12))
        combo.bind("<<ComboboxSelected>>", lambda _event: self.render())

        ttk.Checkbutton(side, text="自动温标", variable=self.auto_range,
                        command=self.toggle_range_mode).pack(anchor="w", padx=16, pady=4)
        range_box = tk.Frame(side, bg="#171d26")
        range_box.pack(fill="x", padx=18, pady=5)
        self.min_spin = self._spin(range_box, self.range_min)
        tk.Label(range_box, text="—", bg="#171d26", fg="#64748b").pack(side="left", padx=5)
        self.max_spin = self._spin(range_box, self.range_max)
        tk.Label(range_box, text="°C", bg="#171d26", fg="#94a3b8").pack(side="left", padx=(5, 0))
        self._range_state()

        self._build_air_sensors()

        footer = tk.Frame(self.root, bg="#10141b")
        footer.pack(fill="x", padx=24, pady=(12, 16))
        self.status_dot = tk.Label(footer, text="●", bg="#10141b", fg="#f59e0b",
                                   font=("Sans", 11))
        self.status_dot.pack(side="left")
        tk.Label(footer, textvariable=self.status, bg="#10141b", fg="#94a3b8",
                 font=("Sans", 10)).pack(side="left", padx=7)

    @staticmethod
    def _default_air_port():
        ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
        return ports[0] if ports else "/dev/ttyACM0"

    def _build_air_sensors(self):
        card = tk.Frame(self.root, bg="#171d26", highlightbackground="#2a3240",
                        highlightthickness=1)
        card.pack(fill="x", padx=24, pady=(14, 0))

        title = tk.Frame(card, bg="#171d26")
        title.pack(fill="x", padx=18, pady=(10, 7))
        tk.Label(title, text="空气质量传感器", bg="#171d26", fg="#f8fafc",
                 font=("Sans", 14, "bold")).pack(side="left")
        self.lora_button = tk.Button(
            title, text="启动发送", command=self.toggle_lora, bg="#7c3aed", fg="white",
            activebackground="#6d28d9", activeforeground="white", relief="flat",
            padx=12, pady=3, cursor="hand2"
        )
        self.lora_button.pack(side="right")
        self.lora_port_box = ttk.Combobox(
            title, textvariable=self.lora_port,
            values=["/dev/ttyAMA0", "/dev/ttyUSB0"], width=13
        )
        self.lora_port_box.pack(side="right", padx=6)
        self.lora_dot = tk.Label(title, text="●", bg="#171d26", fg="#64748b",
                                 font=("Sans", 11))
        self.lora_dot.pack(side="right")
        tk.Label(title, textvariable=self.lora_status, bg="#171d26", fg="#94a3b8",
                 font=("Sans", 9)).pack(side="right", padx=5)

        body = tk.Frame(card, bg="#171d26")
        body.pack(fill="x", padx=13, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)

        tvoc = self._sensor_group(body, 0, "TVOC", "tvoc_dot", self.tvoc_status)
        tvoc_controls = tk.Frame(tvoc, bg="#171d26")
        tvoc_controls.pack(fill="x", padx=7, pady=(0, 7))
        self.port_box = ttk.Combobox(
            tvoc_controls, textvariable=self.tvoc_port, values=serial_ports(), width=13
        )
        self.port_box.pack(side="right", padx=(4, 0))
        self.tvoc_button = tk.Button(
            tvoc_controls, text="连接", command=self.toggle_tvoc, bg="#2563eb", fg="white",
            activebackground="#1d4ed8", activeforeground="white", relief="flat",
            padx=10, pady=3, cursor="hand2"
        )
        self.tvoc_button.pack(side="right")
        metrics = tk.Frame(tvoc, bg="#171d26")
        metrics.pack(fill="x")
        self.tvoc_value_label = self._tvoc_metric(
            metrics, "TVOC", self.tvoc_value, "ppm", "#34d399"
        )
        self._tvoc_metric(metrics, "二氧化碳 CO₂", self.co2_value, "ppm", "#f8fafc")
        self._tvoc_metric(metrics, "甲醛 CH₂O", self.ch2o_value, "ppb", "#f8fafc")
        self._tvoc_metric(metrics, "AIR", self.air_value, "", "#f8fafc")

        mq = self._sensor_group(body, 1, "MQ-135 气体传感器", "mq_dot", self.mq_status)
        self._build_pico_controls(mq)
        mq_metrics = tk.Frame(mq, bg="#171d26")
        mq_metrics.pack(fill="x")
        self._tvoc_metric(mq_metrics, "ADC 原始值", self.mq_adc, "", "#38bdf8")
        self._tvoc_metric(mq_metrics, "输入电压", self.mq_voltage, "V", "#f8fafc")
        self.mq_alarm_label = self._tvoc_metric(
            mq_metrics, "数字输出", self.mq_dout, "", "#f8fafc"
        )

        dust = self._sensor_group(body, 2, "粉尘传感器", "dust_dot", self.dust_status)
        dust_metrics = tk.Frame(dust, bg="#171d26")
        dust_metrics.pack(fill="x")
        self.dust_value_label = self._tvoc_metric(
            dust_metrics, "粉尘浓度", self.dust_value, "μg/m³", "#f59e0b"
        )
        self._tvoc_metric(dust_metrics, "ADC 原始值", self.dust_adc, "", "#f8fafc")
        self._tvoc_metric(dust_metrics, "输入电压", self.dust_voltage, "V", "#f8fafc")

    def _sensor_group(self, parent, row, title, dot_name, status_var):
        group = tk.Frame(parent, bg="#171d26")
        group.grid(row=row, column=0, sticky="ew", padx=5, pady=5)
        header = tk.Frame(group, bg="#171d26")
        header.pack(fill="x", padx=7, pady=(2, 5))
        tk.Label(header, text=title, bg="#171d26", fg="#e2e8f0",
                 font=("Sans", 11, "bold")).pack(side="left")
        dot = tk.Label(header, text="●", bg="#171d26", fg="#64748b",
                       font=("Sans", 11))
        dot.pack(side="right")
        setattr(self, dot_name, dot)
        tk.Label(header, textvariable=status_var, bg="#171d26", fg="#94a3b8",
                 font=("Sans", 9)).pack(side="right", padx=5)
        return group

    def _build_pico_controls(self, parent):
        controls = tk.Frame(parent, bg="#171d26")
        controls.pack(fill="x", padx=7, pady=(0, 7))
        tk.Label(controls, textvariable=self.air_status, bg="#171d26", fg="#94a3b8",
                 font=("Sans", 9)).pack(side="left")
        self.air_button = tk.Button(
            controls, text="连接", command=self.toggle_air, bg="#2563eb", fg="white",
            activebackground="#1d4ed8", activeforeground="white", relief="flat",
            padx=10, pady=3, cursor="hand2"
        )
        self.air_button.pack(side="right")
        self.air_port_box = ttk.Combobox(
            controls, textvariable=self.air_port,
            values=sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
                   or ["/dev/ttyACM0"],
            width=13
        )
        self.air_port_box.pack(side="right", padx=(4, 4))

    @staticmethod
    def _tvoc_metric(parent, title, variable, unit, color):
        box = tk.Frame(parent, bg="#202733")
        box.pack(side="left", fill="x", expand=True, padx=5)
        tk.Label(box, text=title, bg="#202733", fg="#9aa7b7",
                 font=("Sans", 9)).pack(anchor="w", padx=12, pady=(8, 0))
        row = tk.Frame(box, bg="#202733")
        row.pack(anchor="w", padx=11, pady=(0, 8))
        label = tk.Label(row, textvariable=variable, bg="#202733", fg=color,
                         font=("Sans", 15, "bold"))
        label.pack(side="left")
        if unit:
            tk.Label(row, text=" " + unit, bg="#202733", fg="#94a3b8",
                     font=("Sans", 10)).pack(side="left", pady=(7, 0))
        return label

    def toggle_air(self):
        if self.air_worker and self.air_worker.is_alive():
            self.air_stop_event.set()
            self.air_status.set("正在断开…")
            self.mq_status.set("正在断开…")
            self.dust_status.set("正在断开…")
            self.air_button.configure(state="disabled")
            return
        port = self.air_port.get().strip()
        if not port:
            messagebox.showerror("无法连接", "请选择 MQ135 / 粉尘传感器串口。")
            return
        self.air_stop_event.clear()
        self.air_status.set("正在连接…")
        self.mq_status.set("正在连接…")
        self.dust_status.set("正在连接…")
        self.last_air_reading = time.monotonic()
        self.mq_dot.configure(fg="#f59e0b")
        self.dust_dot.configure(fg="#f59e0b")
        self.air_button.configure(state="disabled")
        self.air_worker = AirSensorWorker(
            port, self.air_messages, self.air_stop_event
        )
        self.air_worker.start()

    def toggle_lora(self):
        if self.lora_worker and self.lora_worker.is_alive():
            self.lora_stop_event.set()
            self.lora_status.set("正在停止…")
            self.lora_button.configure(state="disabled")
            return
        port = self.lora_port.get().strip()
        if not port:
            messagebox.showerror("无法启动", "请选择 LoRa 串口。")
            return
        self.lora_stop_event.clear()
        self.lora_status.set("正在初始化…")
        self.lora_button.configure(state="disabled")
        self.lora_worker = LoRaTransmitWorker(
            port, self.lora_messages, self.lora_packets, self.lora_stop_event
        )
        self.lora_worker.start()

    def poll_lora(self):
        try:
            while True:
                event, data = self.lora_messages.get_nowait()
                if event == "connected":
                    self.lora_status.set("LoRa 已连接")
                    self.lora_dot.configure(fg="#34d399")
                    self.lora_button.configure(text="停止发送", state="normal")
                elif event == "sent":
                    self.lora_status.set(f"正在广播 · {data} 字节")
                    self.lora_dot.configure(fg="#34d399")
                elif event == "error":
                    self.lora_status.set("LoRa 错误：" + data)
                    self.lora_dot.configure(fg="#ef4444")
                elif event == "stopped":
                    self.lora_status.set("LoRa 未启动")
                    self.lora_dot.configure(fg="#64748b")
                    self.lora_button.configure(text="启动发送", state="normal")
        except queue.Empty:
            pass

        now = time.monotonic()
        if self.lora_worker and self.lora_worker.is_alive() and now - self.last_lora_packet >= 1:
            packet = {
                "v": 1,
                "t": self.telemetry["thermal"],
                "q": self.telemetry["tvoc"],
                "m": self.telemetry["mq135"],
                "d": self.telemetry["dust"],
            }
            encoded = json.dumps(
                packet, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            ).encode("ascii")
            try:
                self.lora_packets.put_nowait(encoded)
            except queue.Full:
                pass
            self.last_lora_packet = now

    def poll_air(self):
        try:
            while True:
                event, data = self.air_messages.get_nowait()
                if event == "connected":
                    self.air_status.set("已连接 " + data)
                    self.mq_status.set("已连接")
                    self.dust_status.set("已连接")
                    self.last_air_reading = time.monotonic()
                    self.mq_dot.configure(fg="#34d399")
                    self.dust_dot.configure(fg="#34d399")
                    self.air_button.configure(text="断开", state="normal")
                elif event == "reading":
                    self.last_air_reading = time.monotonic()
                    self.mq_adc.set(data["mq_adc"])
                    self.mq_voltage.set(f'{float(data["mq_voltage"]):.3f}')
                    self.mq_dout.set(data["dout"])
                    self.dust_adc.set(data["dust_adc"])
                    self.dust_voltage.set(f'{float(data["dust_voltage"]):.3f}')
                    dust = float(data["dust"])
                    self.dust_value.set(f"{dust:.1f}")
                    alarm = data["alarm"] == "YES"
                    dust_alarm = dust >= 75.0
                    self.telemetry["mq135"] = [
                        int(data["mq_adc"]), float(data["mq_voltage"]),
                        data["dout"], alarm
                    ]
                    self.telemetry["dust"] = [
                        dust, int(data["dust_adc"]), float(data["dust_voltage"])
                    ]
                    self.mq_alarm_label.configure(
                        fg="#fb7185" if alarm else "#34d399"
                    )
                    self.dust_value_label.configure(
                        fg="#fb7185" if dust_alarm else "#f59e0b"
                    )
                    if alarm or dust_alarm:
                        self.air_status.set("空气质量报警")
                        self.mq_status.set("报警" if alarm else "测量正常")
                        self.dust_status.set("报警" if dust_alarm else "测量正常")
                        self.mq_dot.configure(fg="#fb7185" if alarm else "#34d399")
                        self.dust_dot.configure(fg="#fb7185" if dust_alarm else "#34d399")
                    else:
                        self.air_status.set("测量正常")
                        self.mq_status.set("测量正常")
                        self.dust_status.set("测量正常")
                        self.mq_dot.configure(fg="#34d399")
                        self.dust_dot.configure(fg="#34d399")
                elif event == "error":
                    self.air_status.set("连接失败：" + data)
                    self.mq_status.set("连接失败")
                    self.dust_status.set("连接失败")
                    self.mq_dot.configure(fg="#ef4444")
                    self.dust_dot.configure(fg="#ef4444")
                elif event == "stopped":
                    self.air_status.set("未连接")
                    self.mq_status.set("未连接")
                    self.dust_status.set("未连接")
                    self.last_air_reading = None
                    self.mq_dot.configure(fg="#64748b")
                    self.dust_dot.configure(fg="#64748b")
                    self.air_button.configure(text="连接", state="normal")
                    self._clear_air_values()
        except queue.Empty:
            pass
        self._check_air_stale()

    def toggle_tvoc(self):
        if self.tvoc_worker and self.tvoc_worker.is_alive():
            self.tvoc_stop_event.set()
            self.tvoc_status.set("正在断开…")
            self.tvoc_button.configure(state="disabled")
            return
        port = self.tvoc_port.get().strip()
        if not port:
            messagebox.showerror("无法连接", "请选择 TVOC 串口。")
            return
        self.tvoc_stop_event.clear()
        self.tvoc_status.set("正在连接…")
        self.last_tvoc_reading = time.monotonic()
        self.tvoc_button.configure(state="disabled")
        self.tvoc_worker = TVOCWorker(
            port, self.tvoc_messages, self.tvoc_stop_event
        )
        self.tvoc_worker.start()

    def poll_tvoc(self):
        try:
            while True:
                event, data = self.tvoc_messages.get_nowait()
                if event == "connected":
                    self.tvoc_status.set("已连接 " + data)
                    self.last_tvoc_reading = time.monotonic()
                    self.tvoc_dot.configure(fg="#34d399")
                    self.tvoc_button.configure(text="断开", state="normal")
                elif event == "reading":
                    self.last_tvoc_reading = time.monotonic()
                    self.tvoc_value.set(f"{data.tvoc_ppm:.3f}")
                    self.air_value.set(str(data.air))
                    self.co2_value.set(str(data.co2_ppm))
                    self.ch2o_value.set(str(data.ch2o_ppb))
                    self.telemetry["tvoc"] = [
                        data.tvoc_ppm, data.air, data.co2_ppm, data.ch2o_ppb
                    ]
                    alarm = data.tvoc_ppm >= 2.0
                    self.tvoc_value_label.configure(
                        fg="#fb7185" if alarm else "#34d399"
                    )
                    self.tvoc_dot.configure(
                        fg="#fb7185" if alarm else "#34d399"
                    )
                    self.tvoc_status.set(
                        "TVOC 超过 2 ppm" if alarm else "测量正常"
                    )
                elif event == "warning":
                    self.tvoc_status.set("无有效数据：" + data)
                    self.tvoc_dot.configure(fg="#f59e0b")
                elif event == "error":
                    self.tvoc_status.set("连接失败：" + data)
                    self.tvoc_dot.configure(fg="#ef4444")
                elif event == "stopped":
                    self.tvoc_status.set("未连接")
                    self.last_tvoc_reading = None
                    self.tvoc_dot.configure(fg="#64748b")
                    self.tvoc_button.configure(text="连接", state="normal")
                    self._clear_tvoc_values()
        except queue.Empty:
            pass
        self._check_tvoc_stale()

    def _clear_tvoc_values(self):
        self.tvoc_value.set("--")
        self.air_value.set("--")
        self.co2_value.set("--")
        self.ch2o_value.set("--")
        self.tvoc_value_label.configure(fg="#34d399")
        self.telemetry["tvoc"] = [None, None, None, None]

    def _clear_air_values(self):
        self.mq_adc.set("----")
        self.mq_voltage.set("-.---")
        self.mq_dout.set("----")
        self.dust_value.set("---.-")
        self.dust_adc.set("----")
        self.dust_voltage.set("-.---")
        self.mq_alarm_label.configure(fg="#f8fafc")
        self.dust_value_label.configure(fg="#f59e0b")
        self.telemetry["mq135"] = [None, None, None, None]
        self.telemetry["dust"] = [None, None, None]

    def _check_tvoc_stale(self):
        if not (self.tvoc_worker and self.tvoc_worker.is_alive()):
            return
        if self.last_tvoc_reading is None:
            return
        if time.monotonic() - self.last_tvoc_reading <= DATA_STALE_SECONDS:
            return
        self.tvoc_status.set("无数据，检查连接")
        self.tvoc_dot.configure(fg="#f59e0b")
        self._clear_tvoc_values()

    def _check_air_stale(self):
        if not (self.air_worker and self.air_worker.is_alive()):
            return
        if self.last_air_reading is None:
            return
        if time.monotonic() - self.last_air_reading <= DATA_STALE_SECONDS:
            return
        self.air_status.set("无数据，检查 Pico 串口")
        self.mq_status.set("无数据，检查连接")
        self.dust_status.set("无数据，检查连接")
        self.mq_dot.configure(fg="#f59e0b")
        self.dust_dot.configure(fg="#f59e0b")
        self._clear_air_values()

    def _metric(self, parent, title, variable, color):
        box = tk.Frame(parent, bg="#202733")
        box.pack(fill="x", padx=18, pady=5)
        tk.Label(box, text=title, bg="#202733", fg="#9aa7b7",
                 font=("Sans", 9)).pack(anchor="w", padx=12, pady=(9, 0))
        tk.Label(box, textvariable=variable, bg="#202733", fg=color,
                 font=("Sans", 20, "bold")).pack(anchor="w", padx=11, pady=(0, 8))

    def _spin(self, parent, variable):
        spin = tk.Spinbox(parent, from_=-40, to=300, increment=1, width=5,
                          textvariable=variable, command=self.render, bg="#242b36",
                          fg="#e5e7eb", buttonbackground="#303846", relief="flat")
        spin.pack(side="left")
        spin.bind("<Return>", lambda _event: self.render())
        return spin

    def _range_state(self):
        state = "disabled" if self.auto_range.get() else "normal"
        self.min_spin.configure(state=state)
        self.max_spin.configure(state=state)
        self.render()

    def toggle_range_mode(self):
        # When switching to a fixed scale, begin with a useful range based on
        # the current scene instead of the broad 15–45 °C startup default.
        if not self.auto_range.get() and self.latest_data is not None:
            low = math.floor(float(np.min(self.latest_data)) - 1.0)
            high = math.ceil(float(np.max(self.latest_data)) + 1.0)
            if high - low < 3:
                midpoint = (high + low) / 2
                low, high = math.floor(midpoint - 1.5), math.ceil(midpoint + 1.5)
            self.range_min.set(low)
            self.range_max.set(high)
        self._range_state()

    def poll(self):
        try:
            while True:
                message = self.messages.get_nowait()
                if message[0] == "frame" and not self.paused:
                    self.latest_data, self.last_frame_time = message[1], message[2]
                    self.status.set("实时采集中 · 2 Hz")
                    self.status_dot.configure(fg="#22c55e")
                    self.update_metrics()
                    self.render()
                elif message[0] == "status":
                    self.status.set(message[1])
                elif message[0] == "error":
                    self.status.set(message[1])
                    self.status_dot.configure(fg="#ef4444")
        except queue.Empty:
            pass
        self.poll_tvoc()
        self.poll_air()
        self.poll_lora()
        if not self.stop_event.is_set():
            self.root.after(80, self.poll)

    def update_metrics(self):
        data = self.latest_data
        self.min_text.set(f"{np.min(data):.1f} °C")
        self.max_text.set(f"{np.max(data):.1f} °C")
        center = float(np.mean(data[11:13, 15:17]))
        self.center_text.set(f"{center:.1f} °C")
        self.telemetry["thermal"] = [
            round(float(np.max(data)), 1),
            round(float(np.min(data)), 1),
            round(center, 1),
        ]

    def make_image(self):
        data = self.latest_data
        if data is None:
            return None
        if self.auto_range.get():
            low, high = float(np.percentile(data, 2)), float(np.percentile(data, 98))
        else:
            low, high = self.range_min.get(), self.range_max.get()
        if high <= low:
            high = low + 1.0
        normalized = np.clip((data - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
        bgr = cv2.applyColorMap(normalized, PALETTES[self.palette.get()])
        hottest = np.unravel_index(np.argmax(data), data.shape)
        coldest = np.unravel_index(np.argmin(data), data.shape)
        return bgr, hottest, coldest

    def render(self):
        result = self.make_image()
        if result is None:
            return
        bgr, hottest, coldest = result
        width = max(320, self.image_label.winfo_width() - 16)
        height = max(240, self.image_label.winfo_height() - 16)
        scale = min(width / 32, height / 24)
        out_w, out_h = int(32 * scale), int(24 * scale)
        shown = cv2.resize(bgr, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
        center_temperature = float(np.mean(self.latest_data[11:13, 15:17]))
        markers = (
            (int((hottest[1] + 0.5) * out_w / 32),
             int((hottest[0] + 0.5) * out_h / 24),
             (133, 113, 251), f"{self.latest_data[hottest]:.1f}"),  # #fb7185
            (out_w // 2, out_h // 2,
             (255, 255, 255), f"{center_temperature:.1f}"),         # #ffffff
            (int((coldest[1] + 0.5) * out_w / 32),
             int((coldest[0] + 0.5) * out_h / 24),
             (250, 165, 96), f"{self.latest_data[coldest]:.1f}"),   # #60a5fa
        )
        for x, y, color, temperature in markers:
            cv2.drawMarker(shown, (x, y), color, cv2.MARKER_CROSS, 18, 2)
            text_x = min(x + 10, out_w - 48)
            text_y = max(y - 8, 18)
            cv2.putText(shown, f"{temperature}C", (text_x, text_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (15, 18, 24), 3,
                        cv2.LINE_AA)
            cv2.putText(shown, f"{temperature}C", (text_x, text_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1,
                        cv2.LINE_AA)
        self.latest_bgr = shown.copy()
        # Tk 8.6 can display PNG directly. This keeps the app working on minimal
        # Raspberry Pi installations where Pillow's optional ImageTk is absent.
        ok, encoded = cv2.imencode(".png", shown)
        if not ok:
            return
        self.photo = tk.PhotoImage(data=base64.b64encode(encoded).decode("ascii"))
        self.image_label.configure(image=self.photo, text="")

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_button.configure(text="继续" if self.paused else "暂停")
        self.status.set("画面已暂停" if self.paused else "实时采集中 · 2 Hz")
        self.status_dot.configure(fg="#f59e0b" if self.paused else "#22c55e")

    def save_snapshot(self):
        if self.latest_bgr is None:
            messagebox.showinfo("暂无画面", "收到第一帧热成像后才能保存截图。")
            return
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        default = SNAPSHOT_DIR / f"thermal_{datetime.now():%Y%m%d_%H%M%S}.png"
        path = filedialog.asksaveasfilename(
            title="保存热成像截图", initialdir=SNAPSHOT_DIR, initialfile=default.name,
            defaultextension=".png", filetypes=[("PNG 图片", "*.png")]
        )
        if path and cv2.imwrite(path, self.latest_bgr):
            self.status.set(f"截图已保存：{path}")

    def close(self):
        self.stop_event.set()
        self.tvoc_stop_event.set()
        self.air_stop_event.set()
        self.lora_stop_event.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    ThermalApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import queue
import threading
import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk

from .main import Bridge
from .protocol import DisplayState
from .transport import BleTransport


class DesktopApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Codex Flipper Monitor")
        self.root.geometry("560x430")
        self.root.minsize(500, 390)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.bridge: Bridge | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.devices: list[tuple[str, str]] = []

        style = ttk.Style()
        style.configure("Title.TLabel", font=("Sans", 20, "bold"))
        style.configure("State.TLabel", font=("Sans", 15, "bold"))
        style.configure("Quota.TLabel", font=("Sans", 12))

        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Codex × Flipper Zero", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="树莓派实时状态、额度与安全审批桥接",
        ).pack(anchor="w", pady=(2, 18))

        device_row = ttk.Frame(frame)
        device_row.pack(fill="x")
        self.device = ttk.Combobox(device_row, state="readonly")
        self.device.pack(side="left", fill="x", expand=True)
        self.scan_button = ttk.Button(device_row, text="扫描 Flipper", command=self.scan)
        self.scan_button.pack(side="left", padx=(8, 0))
        self.connect_button = ttk.Button(device_row, text="连接", command=self.connect)
        self.connect_button.pack(side="left", padx=(8, 0))

        ttk.Separator(frame).pack(fill="x", pady=18)
        self.status = tk.StringVar(value="未连接")
        ttk.Label(frame, textvariable=self.status, style="State.TLabel").pack(anchor="w")
        self.quota = tk.StringVar(value="5 小时额度：--    周额度：--")
        ttk.Label(frame, textvariable=self.quota, style="Quota.TLabel").pack(
            anchor="w", pady=(8, 4)
        )
        self.summary = tk.StringVar(value="请先在 Flipper 上打开 Codex Monitor 应用")
        ttk.Label(frame, textvariable=self.summary, wraplength=510).pack(
            anchor="w", fill="x", pady=(4, 18)
        )

        approval = ttk.LabelFrame(frame, text="待确认操作", padding=12)
        approval.pack(fill="x")
        ttk.Label(
            approval,
            text="建议在 Flipper 上短按 OK 批准、长按 OK 拒绝。也可以在这里操作。",
            wraplength=485,
        ).pack(anchor="w")
        buttons = ttk.Frame(approval)
        buttons.pack(fill="x", pady=(10, 0))
        self.approve_button = ttk.Button(
            buttons, text="仅批准这一次", command=lambda: self.decide(True), state="disabled"
        )
        self.approve_button.pack(side="left")
        self.decline_button = ttk.Button(
            buttons, text="拒绝", command=lambda: self.decide(False), state="disabled"
        )
        self.decline_button.pack(side="left", padx=8)

        self.root.after(100, self.poll)
        self.scan()

    def run(self) -> None:
        self.root.mainloop()

    def run_async(self, coroutine) -> None:
        def worker() -> None:
            try:
                asyncio.run(coroutine)
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def scan(self) -> None:
        self.scan_button.configure(state="disabled")
        self.status.set("正在扫描蓝牙设备…")

        async def task() -> None:
            result = await BleTransport.scan()
            self.events.put(("devices", result))

        self.run_async(task())

    def connect(self) -> None:
        index = self.device.current()
        if index < 0:
            messagebox.showinfo("Codex Flipper", "请先扫描并选择 Flipper Zero。")
            return
        address = self.devices[index][1]
        self.connect_button.configure(state="disabled")
        self.status.set("正在连接 Flipper 和 Codex…")

        def worker() -> None:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.bridge = Bridge(
                address,
                False,
                "codex",
                lambda state: self.events.put(("state", replace(state))),
            )
            try:
                self.loop.run_until_complete(self.bridge.run())
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.loop.close()

        threading.Thread(target=worker, daemon=True).start()

    def decide(self, accept: bool) -> None:
        if not self.loop or not self.bridge:
            return
        asyncio.run_coroutine_threadsafe(self.bridge.on_remote(
            {"op": "approve" if accept else "decline"}
        ), self.loop)

    def poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "devices":
                    self.devices = payload  # type: ignore[assignment]
                    labels = [f"{name}  ({address})" for name, address in self.devices]
                    self.device.configure(values=labels)
                    if labels:
                        self.device.current(0)
                        self.status.set(f"找到 {len(labels)} 个蓝牙设备")
                    else:
                        self.status.set("未找到设备；请先在 Flipper 上打开应用")
                    self.scan_button.configure(state="normal")
                elif kind == "state":
                    state: DisplayState = payload  # type: ignore[assignment]
                    names = {
                        "working": "Codex 正在工作",
                        "idle": "Codex 空闲",
                        "approval": "等待你的确认",
                        "question": "等待输入",
                        "error": "Codex 出错",
                        "starting": "正在启动",
                    }
                    self.status.set(names.get(state.status, state.status))
                    primary = "--" if state.primary is None else f"{state.primary}%"
                    secondary = "--" if state.secondary is None else f"{state.secondary}%"
                    self.quota.set(f"5 小时额度：{primary}    周额度：{secondary}")
                    self.summary.set(state.summary)
                    button_state = "normal" if state.status == "approval" else "disabled"
                    self.approve_button.configure(state=button_state)
                    self.decline_button.configure(state=button_state)
                elif kind == "error":
                    self.status.set("连接失败")
                    self.summary.set(str(payload))
                    self.scan_button.configure(state="normal")
                    self.connect_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def main() -> None:
    DesktopApp().run()


if __name__ == "__main__":
    main()


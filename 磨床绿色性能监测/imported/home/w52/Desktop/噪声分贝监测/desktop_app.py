#!/usr/bin/env python3
"""Native desktop dashboard for the HH_07.06 noise monitor."""
import json
import queue
import threading
import tkinter as tk
from datetime import datetime
from urllib.request import urlopen

API = "http://127.0.0.1:8088/api/data?seconds=300"
BG, CARD, CARD2 = "#07111f", "#0d1c2d", "#12263a"
TEXT, MUTED, BLUE = "#ecf7ff", "#84a0b6", "#56c8ff"


def noise_level(value):
    if value < 50: return "安静", "#47e6ad"
    if value < 65: return "一般", "#56c8ff"
    if value < 80: return "较吵", "#ffc857"
    if value < 95: return "很吵", "#ff8b5c"
    return "危险", "#ff5468"


class MonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("噪声分贝监测")
        self.geometry("1000x650")
        self.minsize(760, 520)
        self.configure(bg=BG)
        self.data_queue = queue.Queue(maxsize=1)
        self.points = []
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self.after(50, self._poll_queue)
        self._request_data()

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=28, pady=(24, 18))
        tk.Label(header, text="环境噪声监测", bg=BG, fg=TEXT,
                 font=("Noto Sans CJK SC", 24, "bold")).pack(side="left")
        self.status = tk.Label(header, text="●  正在连接", bg=BG, fg="#ffc857",
                               font=("Noto Sans CJK SC", 12))
        self.status.pack(side="right")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=28, pady=(0, 26))
        left = tk.Frame(body, bg=CARD, width=355, highlightthickness=1,
                        highlightbackground="#27415b")
        left.pack(side="left", fill="y", padx=(0, 16))
        left.pack_propagate(False)
        right = tk.Frame(body, bg=CARD, highlightthickness=1, highlightbackground="#27415b")
        right.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="当前声压级", bg=CARD, fg=MUTED,
                 font=("Noto Sans CJK SC", 13)).pack(pady=(34, 8))
        reading = tk.Frame(left, bg=CARD)
        reading.pack(pady=(4, 8))
        self.value = tk.Label(reading, text="--.-", bg=CARD, fg=BLUE,
                              font=("DejaVu Sans", 66, "bold"))
        self.value.pack(side="left")
        tk.Label(reading, text="dB(A)", bg=CARD, fg=MUTED,
                 font=("DejaVu Sans", 16)).pack(side="left", padx=(8, 0), pady=(34, 0))
        self.level = tk.Label(left, text="等待传感器数据", bg=CARD, fg=BLUE,
                              font=("Noto Sans CJK SC", 16, "bold"))
        self.level.pack(pady=(0, 28))

        stats = tk.Frame(left, bg=CARD)
        stats.pack(fill="x", padx=18)
        self.stat_labels = {}
        for key, title in (("min", "最低"), ("avg", "平均"), ("max", "最高")):
            box = tk.Frame(stats, bg=CARD2)
            box.pack(side="left", fill="both", expand=True, padx=4)
            label = tk.Label(box, text="--", bg=CARD2, fg=TEXT, font=("DejaVu Sans", 17, "bold"))
            label.pack(pady=(12, 2)); self.stat_labels[key] = label
            tk.Label(box, text=title, bg=CARD2, fg=MUTED,
                     font=("Noto Sans CJK SC", 10)).pack(pady=(0, 10))
        self.info = tk.Label(left, text="HH_07.06  ·  A 计权", bg=CARD, fg=MUTED,
                             font=("Noto Sans CJK SC", 10), wraplength=310)
        self.info.pack(side="bottom", pady=24)

        chart_header = tk.Frame(right, bg=CARD)
        chart_header.pack(fill="x", padx=22, pady=(20, 0))
        tk.Label(chart_header, text="近 5 分钟声压级趋势", bg=CARD, fg=TEXT,
                 font=("Noto Sans CJK SC", 14, "bold")).pack(side="left")
        self.last_time = tk.Label(chart_header, text="", bg=CARD, fg=MUTED,
                                  font=("Noto Sans CJK SC", 10))
        self.last_time.pack(side="right")
        self.chart = tk.Canvas(right, bg=CARD, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, padx=16, pady=14)
        self.chart.bind("<Configure>", lambda _e: self._draw_chart())
        self.error = tk.Label(right, text="", bg=CARD, fg="#ff8993",
                              font=("Noto Sans CJK SC", 10))
        self.error.pack(pady=(0, 12))

    def _request_data(self):
        def worker():
            try:
                with urlopen(API, timeout=2) as response:
                    result = json.load(response)
            except Exception as exc:
                result = {"online": False, "error": str(exc), "points": []}
            try:
                self.data_queue.put_nowait(result)
            except queue.Full:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        try:
            data = self.data_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self._update(data)
            self.after(850, self._request_data)
        self.after(100, self._poll_queue)

    def _update(self, data):
        online = data.get("online", False)
        self.status.config(text="●  传感器在线" if online else "●  传感器离线",
                           fg="#47e6ad" if online else "#ff5468")
        current = data.get("current")
        if current:
            value = float(current["db"])
            name, color = noise_level(value)
            self.value.config(text=f"{value:.1f}", fg=color)
            self.level.config(text=name, fg=color)
            self.last_time.config(text="更新 " + datetime.fromtimestamp(current["time"]).strftime("%H:%M:%S"))
        for key, label in self.stat_labels.items():
            val = data.get(key)
            label.config(text="--" if val is None else f"{val:.1f}")
        self.info.config(text=f"HH_07.06  ·  A 计权  ·  {data.get('protocol', '探测中')}\n{data.get('device', '')}")
        self.error.config(text=("错误：" + data["error"]) if data.get("error") else "")
        self.points = data.get("points", [])
        self._draw_chart()

    def _draw_chart(self):
        c = self.chart; c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 100 or h < 100: return
        l, r, top, bottom = 48, 16, 18, 36
        iw, ih = w-l-r, h-top-bottom
        for db in range(30, 131, 20):
            y = top + ih * (130-db)/100
            c.create_line(l, y, w-r, y, fill="#203b52")
            c.create_text(l-12, y, text=str(db), fill=MUTED, font=("DejaVu Sans", 9))
        if len(self.points) < 2: return
        t0, t1 = self.points[0]["time"], self.points[-1]["time"]
        span = max(1, t1-t0); coords = []
        for p in self.points:
            x = l + iw*(p["time"]-t0)/span
            y = top + ih*(130-max(30, min(130, p["db"])))/100
            coords.extend((x, y))
        c.create_line(*coords, fill=BLUE, width=3, smooth=True)
        c.create_oval(coords[-2]-4, coords[-1]-4, coords[-2]+4, coords[-1]+4,
                      fill="#47e6ad", outline="")
        c.create_text(l, h-14, anchor="w", text=datetime.fromtimestamp(t0).strftime("%H:%M"),
                      fill=MUTED, font=("DejaVu Sans", 9))
        c.create_text(w-r, h-14, anchor="e", text=datetime.fromtimestamp(t1).strftime("%H:%M"),
                      fill=MUTED, font=("DejaVu Sans", 9))


if __name__ == "__main__":
    MonitorApp().mainloop()

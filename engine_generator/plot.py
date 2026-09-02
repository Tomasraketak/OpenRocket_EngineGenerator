"""Graf tahové křivky. Používá matplotlib, a když není, kreslí na Tk plátno."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import List, Sequence, Tuple

Point = Tuple[float, float]

try:  # matplotlib je volitelný
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    HAS_MATPLOTLIB = True
except Exception:  # pragma: no cover - závisí na instalaci
    HAS_MATPLOTLIB = False


class ThrustPlot:
    """Společné rozhraní obou vykreslovačů."""

    def __init__(self, parent: tk.Misc) -> None:
        self._impl = _MplPlot(parent) if HAS_MATPLOTLIB else _CanvasPlot(parent)
        self.widget = self._impl.widget

    def draw(self, points: Sequence[Point], raw: Sequence[Point] = (), title: str = "") -> None:
        self._impl.draw(list(points), list(raw), title)


class _MplPlot:
    def __init__(self, parent: tk.Misc) -> None:
        self.figure = Figure(figsize=(6.4, 4.2), dpi=100)
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.widget = self.canvas.get_tk_widget()

    def draw(self, points: List[Point], raw: List[Point], title: str) -> None:
        self.axes.clear()
        if raw:
            self.axes.plot([t for t, _ in raw], [f for _, f in raw],
                           color="#c8c8c8", linewidth=0.8, label="naměřená data")
        if points:
            self.axes.plot([t for t, _ in points], [f for _, f in points],
                           color="#1f6feb", linewidth=1.8, marker="o", markersize=3.5,
                           label="křivka pro .eng")
            self.axes.fill_between([t for t, _ in points], [f for _, f in points],
                                   color="#1f6feb", alpha=0.12)
        self.axes.set_xlabel("čas [s]")
        self.axes.set_ylabel("tah [N]")
        self.axes.set_title(title or "Tahová křivka")
        self.axes.grid(True, linestyle=":", alpha=0.6)
        if points or raw:
            self.axes.legend(loc="upper right", fontsize=8)
        self.axes.set_ylim(bottom=0)
        self.figure.tight_layout()
        self.canvas.draw_idle()


class _CanvasPlot:
    """Záložní vykreslení bez matplotlibu."""

    PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 62, 18, 30, 44

    def __init__(self, parent: tk.Misc) -> None:
        self.widget = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.widget, background="white", highlightthickness=1,
                                highlightbackground="#cccccc")
        self.canvas.pack(fill="both", expand=True)
        self.points: List[Point] = []
        self.raw: List[Point] = []
        self.title = ""
        self.canvas.bind("<Configure>", lambda _event: self._render())

    def draw(self, points: List[Point], raw: List[Point], title: str) -> None:
        self.points, self.raw, self.title = points, raw, title
        self._render()

    def _render(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 220)
        x0, y0 = self.PAD_LEFT, self.PAD_TOP
        x1, y1 = width - self.PAD_RIGHT, height - self.PAD_BOTTOM

        canvas.create_text(width / 2, 14, text=self.title or "Tahová křivka",
                           font=("TkDefaultFont", 10, "bold"))
        canvas.create_rectangle(x0, y0, x1, y1, outline="#999999")
        canvas.create_text(width / 2, height - 12, text="čas [s]")
        canvas.create_text(14, height / 2, text="tah [N]", angle=90)

        series = self.points or self.raw
        if not series:
            canvas.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                               text="Zatím žádná data", fill="#888888")
            return

        t_max = max(t for t, _ in (self.points + self.raw)) or 1.0
        f_max = max((f for _, f in (self.points + self.raw)), default=1.0) or 1.0
        t_max = _nice(t_max)
        f_max = _nice(f_max)

        def sx(t: float) -> float:
            return x0 + (x1 - x0) * (t / t_max)

        def sy(f: float) -> float:
            return y1 - (y1 - y0) * (f / f_max)

        for step in range(6):  # mřížka a popisky os
            gx = x0 + (x1 - x0) * step / 5
            gy = y1 - (y1 - y0) * step / 5
            canvas.create_line(gx, y0, gx, y1, fill="#eeeeee")
            canvas.create_line(x0, gy, x1, gy, fill="#eeeeee")
            canvas.create_text(gx, y1 + 12, text="%.2f" % (t_max * step / 5), font=("TkDefaultFont", 8))
            canvas.create_text(x0 - 8, gy, text="%.0f" % (f_max * step / 5), anchor="e",
                               font=("TkDefaultFont", 8))

        if self.raw:
            flat = []
            for t, f in self.raw:
                flat.extend([sx(t), sy(max(f, 0.0))])
            if len(flat) >= 4:
                canvas.create_line(*flat, fill="#c8c8c8", width=1)
        if self.points:
            flat = []
            for t, f in self.points:
                flat.extend([sx(t), sy(f)])
            if len(flat) >= 4:
                canvas.create_line(*flat, fill="#1f6feb", width=2)
            for t, f in self.points:
                px, py = sx(t), sy(f)
                canvas.create_oval(px - 2.5, py - 2.5, px + 2.5, py + 2.5,
                                   fill="#1f6feb", outline="")


def _nice(value: float) -> float:
    """Zaokrouhlí rozsah osy nahoru na hezké číslo."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for factor in (1, 1.5, 2, 2.5, 5, 10):
        candidate = magnitude * factor
        if candidate >= value:
            return candidate
    return value

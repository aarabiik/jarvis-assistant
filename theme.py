"""Тема оформления: космическая палитра, шрифты, кнопки, звёздное небо.

Всё оформление собрано здесь, чтобы вид менялся в одном месте.
Палитра — глубокий космос: почти чёрный фон, фиолетово-синие туманности,
циан и магента как акценты.
"""

from __future__ import annotations

import math
import random
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# --- палитра ---------------------------------------------------------------
BG = "#04050c"          # фон окна — глубина космоса
PANEL = "#080b16"       # шапка и статус
CARD = "#0d1220"        # карточки
CHAT_BG = "#05070f"     # фон чата (звёздное небо)
INPUT_BG = "#0b1020"    # поле ввода
BORDER = "#1a2138"      # тонкие рамки
ACCENT = "#8b7bff"      # фиолетовый — основной акцент
ACCENT_HOVER = "#a99cff"
ACCENT_DIM = "#2b2a5e"
ACCENT_DARK = "#171a3a"
CYAN = "#4fd8ff"        # второй акцент (реактор)
MAGENTA = "#e879f9"     # третий акцент (туманность)
TEXT = "#eef1ff"
TEXT_DIM = "#a9b3d6"
MUTED = "#6a749b"
SELECT = "#232a4d"
WARN = "#ffc857"
ERROR = "#ff6b8a"
TOOL = "#7f8fc4"
RESULT = "#5f6f9e"
USER = "#c4b5fd"

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 8)
FONT_MONO = ("Consolas", 9)
FONT_TITLE = ("Segoe UI Semibold", 15)
FONT_BRAND = ("Segoe UI Semibold", 16)


# --- цвет ------------------------------------------------------------------
def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def blend(color_a: str, color_b: str, t: float) -> str:
    """Смешивает два цвета #rrggbb: t=0 → a, t=1 → b."""
    t = max(0.0, min(1.0, t))
    a = hex_to_rgb(color_a)
    b = hex_to_rgb(color_b)
    return rgb_to_hex(tuple(x + (y - x) * t for x, y in zip(a, b)))


def alpha(color: str, t: float, over: str = BG) -> str:
    """Полупрозрачность на непрозрачном фоне (Tk не умеет alpha)."""
    return blend(over, color, t)


# --- фигуры ----------------------------------------------------------------
def round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **kwargs,
) -> int:
    """Скруглённый прямоугольник на Canvas (сглаженный полигон)."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def gradient(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color_a: str,
    color_b: str,
    steps: int = 64,
    horizontal: bool = False,
    tags: str | tuple[str, ...] = (),
) -> None:
    """Линейный градиент полосками — Tk другого не умеет."""
    steps = max(2, steps)
    for i in range(steps):
        t = i / (steps - 1)
        color = blend(color_a, color_b, t)
        if horizontal:
            xa = x1 + (x2 - x1) * i / steps
            xb = x1 + (x2 - x1) * (i + 1) / steps + 1
            canvas.create_rectangle(xa, y1, xb, y2, fill=color, outline="", tags=tags)
        else:
            ya = y1 + (y2 - y1) * i / steps
            yb = y1 + (y2 - y1) * (i + 1) / steps + 1
            canvas.create_rectangle(x1, ya, x2, yb, fill=color, outline="", tags=tags)


def glow_oval(
    canvas: tk.Canvas,
    cx: float,
    cy: float,
    radius: float,
    color: str,
    layers: int = 7,
    over: str = BG,
    tags: str | tuple[str, ...] = (),
) -> None:
    """Мягкое свечение: несколько полупрозрачных колец вокруг центра."""
    for i in range(layers, 0, -1):
        t = i / layers
        r = radius * (1 + 0.55 * t)
        shade = blend(over, color, 0.10 * (1 - t) + 0.03)
        canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            outline=shade, width=2, tags=tags,
        )


def starfield(
    canvas: tk.Canvas,
    width: float,
    height: float,
    count: int = 150,
    seed: int = 7,
    tags: str | tuple[str, ...] = "stars",
) -> None:
    """Звёзды и туманности на фоне чата."""
    rng = random.Random(seed)
    # туманности — большие размытые пятна
    for _ in range(3):
        cx = rng.uniform(0, width)
        cy = rng.uniform(0, height)
        r = rng.uniform(width * 0.18, width * 0.42)
        tint = rng.choice((ACCENT, CYAN, MAGENTA))
        for i in range(9, 0, -1):
            t = i / 9
            canvas.create_oval(
                cx - r * t, cy - r * t * 0.7, cx + r * t, cy + r * t * 0.7,
                fill=blend(CHAT_BG, tint, 0.012 * (1 - t) + 0.004),
                outline="", tags=tags,
            )
    # звёзды
    for _ in range(count):
        x = rng.uniform(0, width)
        y = rng.uniform(0, height)
        size = rng.choice((1, 1, 1, 2, 2, 3))
        bright = rng.uniform(0.25, 1.0)
        color = blend(CHAT_BG, "#ffffff", bright * 0.85)
        canvas.create_oval(
            x, y, x + size, y + size, fill=color, outline="", tags=tags,
        )
    # редкие яркие звёзды с крестиком
    for _ in range(max(3, count // 40)):
        x = rng.uniform(0, width)
        y = rng.uniform(0, height)
        color = blend(CHAT_BG, CYAN, 0.75)
        canvas.create_line(x - 4, y, x + 4, y, fill=color, tags=tags)
        canvas.create_line(x, y - 4, x, y + 4, fill=color, tags=tags)


# --- кнопки ----------------------------------------------------------------
class RoundedButton(tk.Canvas):
    """Кнопка со скруглёнными углами, наведением и состоянием disabled."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command=None,
        accent: bool = False,
        small: bool = False,
        width: int | None = None,
        height: int | None = None,
        bg: str | None = None,
    ) -> None:
        try:
            parent_bg = parent.cget("bg")
        except Exception:  # noqa: BLE001
            parent_bg = BG
        self._bg = bg or parent_bg
        self._accent = accent
        self._command = command
        self._state = "normal"
        self._hover = False

        font = ("Segoe UI Semibold", 8 if small else 9)
        pad_x = 20 if small else 30
        self._height = height or (26 if small else 34)
        measure = tkfont.Font(font=font)
        self._width = width or measure.measure(text) + pad_x

        super().__init__(
            parent,
            width=self._width,
            height=self._height,
            bg=self._bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._font = font
        self._text = text
        self._draw()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    # -- отрисовка ---------------------------------------------------------
    def _colors(self) -> tuple[str, str, str]:
        if self._state == "disabled":
            return (ACCENT_DARK, MUTED, BORDER)
        if self._accent:
            fill = ACCENT_HOVER if self._hover else ACCENT
            return (fill, "#07080f", fill)
        fill = blend(self._bg, ACCENT, 0.16 if self._hover else 0.07)
        return (fill, ACCENT_HOVER if self._hover else TEXT_DIM, BORDER)

    def _draw(self) -> None:
        self.delete("all")
        fill, fg, outline = self._colors()
        h = self._height
        w = self._width
        if self._accent and self._state != "disabled":
            # мягкое свечение вокруг акцентной кнопки
            for i in range(3, 0, -1):
                round_rect(
                    self, 1 - i, 1 - i, w - 1 + i, h - 1 + i, h / 2,
                    fill="", outline=blend(self._bg, ACCENT, 0.10 * (4 - i)), width=1,
                )
        round_rect(
            self, 1, 1, w - 1, h - 1, h / 2,
            fill=fill, outline=outline, width=1,
        )
        # блик сверху — «стекло»
        round_rect(
            self, 3, 2, w - 3, h / 2, h / 4,
            fill=blend(fill, "#ffffff", 0.10), outline="",
        )
        self.create_text(
            w / 2, h / 2 + 1,
            text=self._text, fill=fg, font=self._font,
        )

    # -- события -----------------------------------------------------------
    def _on_enter(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self.move("all", 0, 1)

    def _on_release(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self.move("all", 0, -1)
        if callable(self._command):
            self._command()

    # -- совместимость с tk.Button ----------------------------------------
    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        state = kwargs.pop("state", None)
        if state is not None:
            self._state = str(state)
            self.configure_cursor()
            self._draw()
        text = kwargs.pop("text", None)
        if text is not None:
            self._text = str(text)
            self._draw()
        if cnf or kwargs:
            try:
                super().configure(cnf, **kwargs)
            except tk.TclError:
                pass

    config = configure

    def configure_cursor(self) -> None:
        self.configure_cursor_raw()

    def configure_cursor_raw(self) -> None:
        try:
            super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
        except tk.TclError:
            pass

    def cget(self, key: str):  # type: ignore[override]
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)


class CircleButton(tk.Canvas):
    """Круглая кнопка со свечением — для отправки и мини-панели."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command=None,
        size: int = 40,
        accent: bool = True,
        bg: str | None = None,
    ) -> None:
        try:
            parent_bg = parent.cget("bg")
        except Exception:  # noqa: BLE001
            parent_bg = BG
        self._bg = bg or parent_bg
        self._accent = accent
        self._command = command
        self._hover = False
        self._size = size
        self._text = text
        self._state = "normal"
        super().__init__(
            parent, width=size, height=size, bg=self._bg,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda _e: self.move("all", 0, 1))
        self.bind("<ButtonRelease-1>", self._on_release)

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        state = kwargs.pop("state", None)
        if state is not None:
            self._state = str(state)
            try:
                super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            except tk.TclError:
                pass
            self._draw()
        if cnf or kwargs:
            try:
                super().configure(cnf, **kwargs)
            except tk.TclError:
                pass

    config = configure

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        c = s / 2
        r = c - 3
        if self._state == "disabled":
            self.create_oval(
                c - r, c - r, c + r, c + r,
                fill=blend(self._bg, ACCENT, 0.12), outline="",
            )
            self.create_text(c, c + 1, text=self._text, fill=MUTED,
                             font=("Segoe UI Semibold", 12))
            return
        if self._accent:
            glow = ACCENT_HOVER if self._hover else ACCENT
            for i in range(4, 0, -1):
                self.create_oval(
                    c - r - i, c - r - i, c + r + i, c + r + i,
                    outline=blend(self._bg, glow, 0.09 * (5 - i)), width=1,
                )
            fill = glow
            fg = "#07080f"
        else:
            fill = blend(self._bg, ACCENT, 0.18 if self._hover else 0.08)
            fg = ACCENT_HOVER if self._hover else TEXT_DIM
        self.create_oval(c - r, c - r, c + r, c + r, fill=fill, outline="")
        self.create_text(c, c + 1, text=self._text, fill=fg, font=("Segoe UI Semibold", 12))

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._draw()

    def _on_release(self, _event=None) -> None:
        self.move("all", 0, -1)
        if callable(self._command):
            self._command()


def button(
    parent: tk.Misc,
    text: str,
    command,
    accent: bool = False,
    small: bool = False,
) -> RoundedButton:
    """Кнопка в стиле интерфейса."""
    return RoundedButton(parent, text, command, accent=accent, small=small)


def listbox(parent: tk.Misc, **kwargs) -> tk.Listbox:
    """Тёмный список без рамки, в стиле остального интерфейса."""
    options = dict(
        bg=CHAT_BG,
        fg=TEXT,
        selectbackground=SELECT,
        selectforeground=TEXT,
        bd=0,
        relief="flat",
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT_DIM,
        font=("Segoe UI", 10),
        activestyle="none",
    )
    options.update(kwargs)
    return tk.Listbox(parent, **options)


def check(parent: tk.Misc, text: str, variable: tk.Variable, command) -> tk.Checkbutton:
    """Тёмный чекбокс без рамки."""
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        bd=0,
        relief="flat",
        bg=BG,
        fg=TEXT_DIM,
        activebackground=BG,
        activeforeground=ACCENT,
        selectcolor=INPUT_BG,
        font=("Segoe UI", 9),
        highlightthickness=0,
        cursor="hand2",
    )


def apply(root: tk.Tk) -> None:
    """Тёмная тема для ttk-виджетов: диалоги, скроллбары, поля."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, fieldbackground=INPUT_BG)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT_DIM, font=("Segoe UI", 9))
    style.configure("TCheckbutton", background=BG, foreground=TEXT_DIM, font=("Segoe UI", 9))
    style.map(
        "TCheckbutton",
        background=[("active", BG)],
        foreground=[("active", ACCENT)],
        indicatorcolor=[("selected", ACCENT), ("!selected", INPUT_BG)],
    )
    style.configure(
        "TButton",
        background=blend(BG, ACCENT, 0.10),
        foreground=TEXT_DIM,
        borderwidth=0,
        focusthickness=0,
        padding=(14, 7),
        font=("Segoe UI Semibold", 9),
    )
    style.map(
        "TButton",
        background=[("active", blend(BG, ACCENT, 0.20)), ("pressed", ACCENT_DARK)],
        foreground=[("active", ACCENT_HOVER)],
    )
    style.configure(
        "TEntry",
        fieldbackground=INPUT_BG,
        foreground=TEXT,
        insertcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        borderwidth=1,
        padding=6,
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT_DIM)])
    style.configure(
        "TCombobox",
        fieldbackground=INPUT_BG,
        background=blend(BG, ACCENT, 0.10),
        foreground=TEXT,
        arrowcolor=TEXT_DIM,
        bordercolor=BORDER,
        padding=5,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", INPUT_BG)],
        foreground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT_DIM)],
    )
    style.configure(
        "Jarvis.Vertical.TScrollbar",
        background=blend(CHAT_BG, ACCENT, 0.22),
        troughcolor=CHAT_BG,
        bordercolor=CHAT_BG,
        arrowcolor=MUTED,
        darkcolor=blend(CHAT_BG, ACCENT, 0.22),
        lightcolor=blend(CHAT_BG, ACCENT, 0.22),
        relief="flat",
        width=10,
    )
    style.map(
        "Jarvis.Vertical.TScrollbar",
        background=[("active", blend(CHAT_BG, ACCENT, 0.45))],
        arrowcolor=[("active", ACCENT)],
    )

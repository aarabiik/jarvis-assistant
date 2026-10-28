"""Мини-режим: маленькая плавающая панель поверх экрана.

Окно без рамки, всегда сверху, перетаскивается за реактор. Enter — отправить,
⤢ — развернуть в полный режим, Esc — закрыть мини.

Важно: окно с overrideredirect(True) на Windows не получает фокус клавиатуры
само по себе, поэтому фокус ставится принудительно через SetForegroundWindow.
"""

from __future__ import annotations

import ctypes
import math
import tkinter as tk
from typing import Callable

import theme

WIDTH = 480
HEIGHT = 68
ORB = 48


def _force_foreground(window: tk.Misc) -> None:
    """Вывести окно вперёд и отдать ему фокус клавиатуры (Windows)."""
    try:
        window.update_idletasks()
        hwnd = int(window.winfo_id())
        user32 = ctypes.windll.user32
        # HWND окна-обёртки Tk: поднимаемся к корню по родителям.
        parent = user32.GetParent(hwnd)
        target = parent or hwnd
        user32.ShowWindow(target, 5)  # SW_SHOW
        user32.SetForegroundWindow(target)
        user32.BringWindowToTop(target)
        user32.SetFocus(hwnd)
    except Exception:  # noqa: BLE001
        pass


class MiniOverlay(tk.Toplevel):
    """Плавающая мини-панель с полем ввода и пульсирующим реактором."""

    def __init__(
        self,
        master: tk.Misc,
        on_send: Callable[[str], None],
        on_expand: Callable[[], None],
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(master)
        self.on_send = on_send
        self.on_expand = on_expand
        self.on_close = on_close
        self._phase = 0.0
        self._busy = False
        self._drag = (0, 0)
        self._job: str | None = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        self.configure(bg=theme.BG)

        # Внешняя «карточка» со скруглением: фон окна = цвет рамки.
        self.card = tk.Canvas(
            self, bg=theme.BG, highlightthickness=0, bd=0,
            width=WIDTH, height=HEIGHT,
        )
        self.card.pack(fill="both", expand=True)
        self._paint_card()

        self.canvas = tk.Canvas(
            self.card, width=ORB, height=ORB, bg=theme.PANEL, highlightthickness=0
        )
        self.canvas.place(x=12, y=(HEIGHT - ORB) // 2)
        for widget in (self.canvas, self.card):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)

        self.entry = tk.Entry(
            self.card,
            bd=0,
            relief="flat",
            bg=theme.INPUT_BG,
            fg=theme.TEXT,
            insertbackground=theme.CYAN,
            font=("Segoe UI", 11),
            highlightthickness=1,
            highlightbackground=theme.blend(theme.BORDER, theme.ACCENT, 0.4),
            highlightcolor=theme.ACCENT,
        )
        self.entry.place(x=ORB + 24, y=16, width=WIDTH - ORB - 140, height=36)
        self.entry.bind("<Return>", lambda _e: self._submit())
        self.entry.bind("<Escape>", lambda _e: self.on_close())
        self.entry.bind("<FocusIn>", lambda _e: self.entry.configure(bg=theme.CARD))
        self.entry.bind("<FocusOut>", lambda _e: self.entry.configure(bg=theme.INPUT_BG))

        self.expand_btn = theme.button(self.card, "⤢", self.on_expand, small=True)
        self.expand_btn.place(x=WIDTH - 100, y=20)
        self.close_btn = theme.button(self.card, "✕", self.on_close, small=True)
        self.close_btn.place(x=WIDTH - 56, y=20)

        self._place_default()
        self._animate()
        # Фокус ставим после того, как окно реально показано.
        self.after(60, self.focus_input)

    # -- фон ---------------------------------------------------------------
    def _paint_card(self) -> None:
        """Карточка: градиент, звёзды и светящаяся рамка."""
        card = self.card
        card.delete("all")
        theme.round_rect(
            card, 1, 1, WIDTH - 1, HEIGHT - 1, 20,
            fill=theme.PANEL, outline="", width=0,
        )
        # внутренний градиент (обрезаем по скруглению полосками)
        theme.gradient(
            card, 4, 4, WIDTH - 4, HEIGHT - 4,
            theme.blend(theme.PANEL, theme.ACCENT, 0.10),
            theme.blend(theme.PANEL, theme.CYAN, 0.06),
            steps=40, horizontal=True,
        )
        # звёзды
        import random

        rng = random.Random(3)
        for _ in range(26):
            x = rng.uniform(6, WIDTH - 6)
            y = rng.uniform(6, HEIGHT - 6)
            size = rng.choice((1, 1, 2))
            card.create_oval(
                x, y, x + size, y + size,
                fill=theme.blend(theme.PANEL, "#ffffff", rng.uniform(0.2, 0.7)),
                outline="",
            )
        # рамка со свечением
        for i in range(3, 0, -1):
            theme.round_rect(
                card, 1 - i, 1 - i, WIDTH - 1 + i, HEIGHT - 1 + i, 20 + i,
                fill="", outline=theme.blend(theme.BG, theme.ACCENT, 0.10 * (4 - i)),
                width=1,
            )
        theme.round_rect(
            card, 1, 1, WIDTH - 1, HEIGHT - 1, 20,
            fill="", outline=theme.blend(theme.BORDER, theme.ACCENT, 0.55), width=1,
        )

    # -- геометрия ---------------------------------------------------------
    def _place_default(self) -> None:
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = screen_w - WIDTH - 32
        y = screen_h - HEIGHT - 80
        self.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    def _start_drag(self, event: tk.Event) -> None:
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _do_drag(self, event: tk.Event) -> None:
        dx, dy = self._drag
        self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    # -- реактор -----------------------------------------------------------
    def _animate(self) -> None:
        self._phase = (self._phase + 0.16) % (2 * math.pi)
        canvas = self.canvas
        canvas.delete("all")
        cx = cy = ORB / 2
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        speed = 2.2 if self._busy else 1.0
        radius = 12 + 2.2 * pulse * speed

        # свечение
        for i in range(5, 0, -1):
            r = radius + i * 2.4
            canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline=theme.blend(theme.PANEL, theme.ACCENT, 0.05 * (6 - i)), width=1,
            )
        # орбиты
        for tilt, color in ((0.5, theme.ACCENT), (0.28, theme.CYAN)):
            rx, ry = radius + 7, (radius + 7) * tilt
            canvas.create_oval(
                cx - rx, cy - ry, cx + rx, cy + ry,
                outline=theme.blend(theme.PANEL, color, 0.35 + 0.35 * pulse), width=1,
            )
        # спутники
        for spd, tilt, color in ((1.0, 0.5, theme.CYAN), (-1.5, 0.28, theme.MAGENTA)):
            angle = self._phase * spd
            sx = cx + (radius + 7) * math.cos(angle)
            sy = cy + (radius + 7) * tilt * math.sin(angle)
            canvas.create_oval(
                sx - 2, sy - 2, sx + 2, sy + 2,
                fill=theme.blend(color, "#ffffff", 0.4), outline="",
            )
        # ядро
        canvas.create_oval(
            cx - radius, cy - radius, cx + radius, cy + radius,
            fill=theme.blend(theme.ACCENT_DARK, theme.ACCENT, 0.55 + 0.25 * pulse),
            outline=theme.blend(theme.CYAN, "#ffffff", 0.25 * pulse), width=1,
        )
        canvas.create_oval(
            cx - 4, cy - 4, cx + 4, cy + 4,
            fill=theme.blend(theme.CYAN, "#ffffff", 0.35 + 0.45 * pulse), outline="",
        )
        self._job = self.after(45, self._animate)

    # -- состояние ---------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.entry.configure(state="disabled" if busy else "normal")
        if not busy:
            self.focus_input()

    def focus_input(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        _force_foreground(self)
        self.entry.focus_set()
        self.entry.focus_force()
        self.entry.icursor("end")

    def _submit(self) -> None:
        text = self.entry.get().strip()
        if not text or self._busy:
            return
        self.entry.delete(0, "end")
        self.on_send(text)

    def destroy(self) -> None:
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:  # noqa: BLE001
                pass
            self._job = None
        super().destroy()

"""Космический чат: звёздное небо, пузыри сообщений, аватар-реактор.

Tk не умеет ни прозрачность, ни скругления у текстовых виджетов, поэтому чат
нарисован на Canvas: фон — звёзды и туманности, сообщения — скруглённые
«капсулы» с мягким свечением, у ассистента слева пульсирующий реактор.
"""

from __future__ import annotations

import math
import random
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Iterable

import theme

PAD_X = 22          # отступ от краёв холста
PAD_Y = 16          # отступ между сообщениями
BUBBLE_PAD = 14     # внутренние поля пузыря
AVATAR = 30         # диаметр аватара
GAP = 10            # между аватаром и пузырём
MAX_RATIO = 0.74    # максимальная ширина пузыря от ширины холста


class ChatView(tk.Frame):
    """Прокручиваемая лента сообщений в стиле космоса."""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, bg=theme.CHAT_BG, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self,
            bg=theme.CHAT_BG,
            highlightthickness=0,
            bd=0,
            yscrollincrement=24,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview,
            style="Jarvis.Vertical.TScrollbar",
        )
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self._messages: list[dict] = []
        self._stick = True          # держаться низа, пока пользователь не ушёл вверх
        self._busy = False
        self._phase = 0.0
        self._twinkle: list[int] = []
        self._width = 0
        self._height = 0
        self._job: str | None = None
        self._resize_job: str | None = None

        self._font_body = tkfont.Font(family="Segoe UI", size=10)
        self._font_bold = tkfont.Font(family="Segoe UI Semibold", size=10)
        self._font_small = tkfont.Font(family="Segoe UI", size=8)
        self._font_mono = tkfont.Font(family="Consolas", size=9)

        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self._scroll(-3))
        self.canvas.bind("<Button-5>", lambda _e: self._scroll(3))
        self.canvas.bind("<Button-1>", lambda _e: self.canvas.focus_set())
        self.canvas.bind("<Control-c>", self._copy_selection)

        self._animate()

    # -- публичный интерфейс ----------------------------------------------
    def add(self, role: str, text: str) -> None:
        """Добавить сообщение: role = user / assistant / tool / result / warn / error."""
        text = (text or "").rstrip()
        if not text:
            return
        self._messages.append(
            {"role": role, "text": text, "ts": time.strftime("%H:%M")}
        )
        self._stick = True
        self.render()

    def clear(self) -> None:
        self._messages.clear()
        self.render()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.render()

    def messages(self) -> list[dict]:
        return list(self._messages)

    def load(self, items: Iterable[dict]) -> None:
        """Заменить всю ленту готовыми сообщениями."""
        self._messages = [dict(item) for item in items]
        self._stick = True
        self.render()

    # -- геометрия и прокрутка --------------------------------------------
    def _on_configure(self, event: tk.Event) -> None:
        if event.width == self._width and event.height == self._height:
            return
        self._width = event.width
        self._height = event.height
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:  # noqa: BLE001
                pass
        self._resize_job = self.after(60, self.render)

    def _on_wheel(self, event: tk.Event) -> None:
        self._scroll(-1 if event.delta > 0 else 1)

    def _scroll(self, steps: int) -> None:
        self.canvas.yview_scroll(steps, "units")
        self._stick = self._at_bottom()

    def _at_bottom(self) -> bool:
        try:
            return self.canvas.yview()[1] >= 0.999
        except tk.TclError:
            return True

    def _copy_selection(self, _event=None) -> None:
        """Ctrl+C копирует выделенный текст (у Canvas своего буфера нет)."""
        try:
            text = self.canvas.itemcget("sel", "text")
        except tk.TclError:
            return
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    # -- отрисовка ---------------------------------------------------------
    def render(self) -> None:
        canvas = self.canvas
        width = self._width or canvas.winfo_width()
        height = self._height or canvas.winfo_height()
        if width <= 1:
            width = 760
        if height <= 1:
            height = 420

        canvas.delete("all")
        self._twinkle.clear()
        self._draw_background(width, height)

        y = PAD_Y
        for message in self._messages:
            y = self._draw_message(message, y, width)

        if self._busy:
            y = self._draw_typing(y, width)

        canvas.configure(scrollregion=(0, 0, width, max(y + PAD_Y, height)))
        if self._stick:
            canvas.yview_moveto(1.0)

    def _draw_background(self, width: int, height: int) -> None:
        canvas = self.canvas
        # глубина: вертикальный градиент от почти чёрного к фиолетовому низу
        theme.gradient(
            canvas, 0, 0, width, height,
            theme.CHAT_BG, theme.blend(theme.CHAT_BG, theme.ACCENT, 0.10),
            steps=48, tags="bg",
        )
        rng = random.Random(11)
        # туманности
        for _ in range(3):
            cx = rng.uniform(0, width)
            cy = rng.uniform(0, height)
            r = rng.uniform(width * 0.20, width * 0.45)
            tint = rng.choice((theme.ACCENT, theme.CYAN, theme.MAGENTA))
            for i in range(10, 0, -1):
                t = i / 10
                canvas.create_oval(
                    cx - r * t, cy - r * t * 0.65,
                    cx + r * t, cy + r * t * 0.65,
                    fill=theme.blend(theme.CHAT_BG, tint, 0.010 * (1 - t) + 0.004),
                    outline="", tags="bg",
                )
        # звёзды
        for _ in range(170):
            x = rng.uniform(0, width)
            y = rng.uniform(0, height)
            size = rng.choice((1, 1, 1, 2, 2, 3))
            bright = rng.uniform(0.20, 0.95)
            color = theme.blend(theme.CHAT_BG, "#ffffff", bright * 0.9)
            item = canvas.create_oval(
                x, y, x + size, y + size, fill=color, outline="", tags="bg",
            )
            if bright > 0.75:
                self._twinkle.append(item)
        # редкие звёзды с крестиком
        for _ in range(5):
            x = rng.uniform(0, width)
            y = rng.uniform(0, height)
            color = theme.blend(theme.CHAT_BG, theme.CYAN, 0.7)
            canvas.create_line(x - 4, y, x + 4, y, fill=color, tags="bg")
            canvas.create_line(x, y - 4, x, y + 4, fill=color, tags="bg")

    def _draw_message(self, message: dict, y: int, width: int) -> int:
        role = message.get("role", "assistant")
        text = message.get("text", "")
        ts = message.get("ts", "")

        if role in ("tool", "result"):
            return self._draw_mono(text, y, width, role)
        if role in ("warn", "error"):
            return self._draw_notice(text, y, width, role)

        is_user = role == "user"
        max_w = int(width * MAX_RATIO)
        font = self._font_bold if is_user else self._font_body
        inner_w = max(120, max_w - BUBBLE_PAD * 2)

        # сначала текст — по его bbox узнаём высоту пузыря
        text_id = self.canvas.create_text(
            0, 0, text=text, font=font, fill=theme.TEXT,
            width=inner_w, anchor="nw", justify="left",
        )
        bbox = self.canvas.bbox(text_id)
        text_w = (bbox[2] - bbox[0]) if bbox else inner_w
        text_h = (bbox[3] - bbox[1]) if bbox else 18

        bubble_w = min(max_w, text_w + BUBBLE_PAD * 2)
        bubble_h = text_h + BUBBLE_PAD * 2

        if is_user:
            bx2 = width - PAD_X
            bx1 = bx2 - bubble_w
        else:
            bx1 = PAD_X + AVATAR + GAP
            bx2 = bx1 + bubble_w
        by1 = y
        by2 = y + bubble_h

        shapes: list[int] = []
        if is_user:
            # «капсула» пользователя: фиолетовый градиент
            shapes.append(theme.round_rect(
                self.canvas, bx1, by1, bx2, by2, 16,
                fill=theme.blend(theme.ACCENT_DARK, theme.ACCENT, 0.55),
                outline=theme.blend(theme.ACCENT, "#ffffff", 0.15), width=1,
            ))
            shapes.append(theme.round_rect(
                self.canvas, bx1 + 2, by1 + 2, bx2 - 2, by1 + bubble_h * 0.45, 14,
                fill=theme.blend(theme.ACCENT, "#ffffff", 0.10), outline="",
            ))
            self.canvas.coords(text_id, bx1 + BUBBLE_PAD, by1 + BUBBLE_PAD)
            self.canvas.itemconfigure(text_id, fill="#ffffff")
        else:
            # карточка ассистента: тёмное стекло с подсветкой
            for i in range(3, 0, -1):
                shapes.append(theme.round_rect(
                    self.canvas, bx1 - i, by1 - i, bx2 + i, by2 + i, 18,
                    fill="", outline=theme.blend(theme.CHAT_BG, theme.CYAN, 0.05 * (4 - i)),
                    width=1,
                ))
            shapes.append(theme.round_rect(
                self.canvas, bx1, by1, bx2, by2, 16,
                fill=theme.blend(theme.CARD, theme.CYAN, 0.05),
                outline=theme.blend(theme.BORDER, theme.CYAN, 0.35), width=1,
            ))
            self.canvas.coords(text_id, bx1 + BUBBLE_PAD, by1 + BUBBLE_PAD)
            self.canvas.itemconfigure(text_id, fill=theme.TEXT)
            self._draw_avatar(bx1 - GAP - AVATAR, by1, AVATAR)

        # фигуры уходят под текст, иначе пузырь закрывает сообщение
        for shape in shapes:
            self.canvas.tag_lower(shape, text_id)

        # время — мелким шрифтом в правом нижнем углу пузыря
        if ts:
            self.canvas.create_text(
                bx2 - 8, by2 - 6, text=ts, anchor="se",
                font=self._font_small, fill=theme.blend(theme.MUTED, theme.CHAT_BG, 0.25),
            )
        return by2 + PAD_Y

    def _draw_avatar(self, x: int, y: int, size: int) -> None:
        """Маленький реактор рядом с сообщением ассистента."""
        c = size / 2
        cx, cy = x + c, y + c
        pulse = 0.5 + 0.5 * math.sin(self._phase)
        for i in range(3, 0, -1):
            self.canvas.create_oval(
                cx - c - i, cy - c - i, cx + c + i, cy + c + i,
                outline=theme.blend(theme.CHAT_BG, theme.CYAN, 0.10 * (4 - i)), width=1,
            )
        self.canvas.create_oval(
            cx - c, cy - c, cx + c, cy + c,
            fill=theme.blend(theme.CARD, theme.ACCENT, 0.35),
            outline=theme.blend(theme.CYAN, "#ffffff", 0.2 * pulse), width=1,
        )
        self.canvas.create_oval(
            cx - c * 0.42, cy - c * 0.42, cx + c * 0.42, cy + c * 0.42,
            fill=theme.blend(theme.CYAN, "#ffffff", 0.35 + 0.35 * pulse), outline="",
        )

    def _draw_mono(self, text: str, y: int, width: int, role: str) -> int:
        """Строка инструмента/результата: моноширинно, с полоской слева."""
        color = theme.TOOL if role == "tool" else theme.RESULT
        x1 = PAD_X + AVATAR + GAP
        inner_w = max(160, width - x1 - PAD_X - 12)
        item = self.canvas.create_text(
            x1 + 10, y + 6, text=text, font=self._font_mono, fill=color,
            width=inner_w, anchor="nw", justify="left",
        )
        bbox = self.canvas.bbox(item)
        h = (bbox[3] - bbox[1]) if bbox else 16
        self.canvas.create_line(
            x1, y + 2, x1, y + h + 10,
            fill=theme.blend(theme.CHAT_BG, color, 0.55), width=2,
        )
        return y + h + 14

    def _draw_notice(self, text: str, y: int, width: int, role: str) -> int:
        color = theme.WARN if role == "warn" else theme.ERROR
        x1 = PAD_X + AVATAR + GAP
        inner_w = max(160, width - x1 - PAD_X - 24)
        item = self.canvas.create_text(
            x1 + 12, y + 8, text=text, font=self._font_body, fill=color,
            width=inner_w, anchor="nw", justify="left",
        )
        bbox = self.canvas.bbox(item)
        h = (bbox[3] - bbox[1]) if bbox else 16
        shape = theme.round_rect(
            self.canvas, x1, y, x1 + inner_w + 24, y + h + 16, 12,
            fill=theme.blend(theme.CHAT_BG, color, 0.10),
            outline=theme.blend(theme.CHAT_BG, color, 0.45), width=1,
        )
        self.canvas.tag_lower(shape, item)
        return y + h + 24

    def _draw_typing(self, y: int, width: int) -> int:
        """Индикатор «печатает»: три пульсирующие точки."""
        x1 = PAD_X + AVATAR + GAP
        self._draw_avatar(PAD_X, y, AVATAR)
        theme.round_rect(
            self.canvas, x1, y, x1 + 74, y + 40, 16,
            fill=theme.blend(theme.CARD, theme.CYAN, 0.05),
            outline=theme.blend(theme.BORDER, theme.CYAN, 0.35), width=1,
        )
        for i in range(3):
            phase = self._phase * 1.6 - i * 0.7
            lift = max(0.0, math.sin(phase)) * 3
            cx = x1 + 22 + i * 15
            cy = y + 20 - lift
            self.canvas.create_oval(
                cx - 3, cy - 3, cx + 3, cy + 3,
                fill=theme.blend(theme.CYAN, "#ffffff", 0.25 + 0.5 * (lift / 3)),
                outline="",
            )
        return y + 40 + PAD_Y

    # -- анимация ----------------------------------------------------------
    def _animate(self) -> None:
        self._phase = (self._phase + 0.14) % (2 * math.pi)
        # мерцание ярких звёзд
        for index, item in enumerate(self._twinkle):
            shift = math.sin(self._phase * 1.3 + index) * 0.5 + 0.5
            try:
                self.canvas.itemconfigure(
                    item, fill=theme.blend(theme.CHAT_BG, "#ffffff", 0.35 + 0.6 * shift)
                )
            except tk.TclError:
                pass
        if self._busy:
            self.render()
        self._job = self.after(90, self._animate)

    def destroy(self) -> None:
        for job in (self._job, self._resize_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:  # noqa: BLE001
                    pass
        super().destroy()

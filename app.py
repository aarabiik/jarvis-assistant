#!/usr/bin/env python3
"""JARVIS — чат с нейросетью, которая управляет мышью и клавиатурой.

Два режима: полный (космическое окно с чатом) и мини — маленькая плавающая
панель поверх экрана, как ассистент в телефоне. Модель получает инструменты:
клики, ввод текста, горячие клавиши, прокрутка и именованные точки экрана.

Запуск:  python app.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable

import actions
import chatstore
import chatview
import files
import github
import guard
import llm
import overlay
import screen
import shell
import theme
import uia
import workspace
from llm import Agent, LLMError

APP_NAME = "JARVIS"
VERSION = "2.0"
APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "settings.json"
POINTS_PATH = APP_DIR / "points.json"

_COORD_PARENS = re.compile(r"\(\s*(\d{1,4})\s*[,;]\s*(\d{1,4})\s*\)")
_COORD_X = re.compile(r"\bx\s*[=:]\s*(\d{1,4})", re.IGNORECASE)
_COORD_Y = re.compile(r"\by\s*[=:]\s*(\d{1,4})", re.IGNORECASE)


def _extract_coords(text: str) -> tuple[int, int] | None:
    """Достаёт координаты из ответа vision-модели.

    Смотрит только описание снимка: в начале ответа look_screen идут
    координаты курсора, и они не должны попасть в клик.
    """
    body = text.split("описание снимка:", 1)[-1]
    width, height = actions.screen_size()
    for match in _COORD_PARENS.finditer(body):
        x, y = int(match.group(1)), int(match.group(2))
        if 0 <= x < width and 0 <= y < height:
            return x, y
    mx, my = _COORD_X.search(body), _COORD_Y.search(body)
    if mx and my:
        x, y = int(mx.group(1)), int(my.group(1))
        if 0 <= x < width and 0 <= y < height:
            return x, y
    return None

DEFAULT_SETTINGS: dict[str, Any] = {
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "deepseek-ai/deepseek-v4-flash",
    "vision_model": "qwen/qwen3.8-27b",
    "vision_base_url": "",
    "vision_api_key": "",
    "reasoning_effort": "",
    "vision": False,
    "max_steps": 0,
    "max_seconds": 1800,
    "max_context_chars": 0,
    "work_dir": str(APP_DIR.parent),
    "shell": "powershell",
    "forbidden_patterns": "",
    "temperature": 0.2,
    "always_on_top": True,
    "confirm_mode": False,
    "minimize_before_typing": True,
    "allow_outside": True,
}


def _screen_context(
    active: dict[str, Any] | None,
    under: str,
    cursor: tuple[int, int],
    region: tuple[int, int, int, int] | None,
    size: tuple[int, int],
) -> str:
    """Точные данные с машины — чтобы модель не гадала по пикселям."""
    lines = [f"разрешение экрана {size[0]}x{size[1]}"]
    lines.append(f"системное время {time.strftime('%H:%M')}")
    lines.append(f"курсор в точке ({cursor[0]}, {cursor[1]})")
    if under:
        lines.append(f"под курсором окно «{under}»")
    if active is not None:
        lines.append(f"активное окно «{active['title']}»")
    if region is None:
        lines.append("снят весь экран")
    else:
        lines.append(
            f"снята только область активного окна "
            f"{region[2]}x{region[3]} в ({region[0]}, {region[1]})"
        )
    lines.append(
        "маркер курсора на снимке — красный кружок с перекрестием в этой точке"
    )
    return "\n".join(lines)


class SettingsDialog(tk.Toplevel):
    """Модальное окно настроек API."""

    def __init__(self, master: tk.Tk, settings: dict[str, Any]) -> None:
        super().__init__(master)
        self.title("Настройки API")
        self.settings = settings
        self.result: dict[str, Any] | None = None
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        self.vars: dict[str, tk.Variable] = {
            "base_url": tk.StringVar(value=str(settings.get("base_url", ""))),
            "api_key": tk.StringVar(value=str(settings.get("api_key", ""))),
            "model": tk.StringVar(value=str(settings.get("model", ""))),
            "vision_model": tk.StringVar(
                value=str(settings.get("vision_model", "qwen/qwen3.8-27b"))
            ),
            "vision_base_url": tk.StringVar(
                value=str(settings.get("vision_base_url", ""))
            ),
            "vision_api_key": tk.StringVar(
                value=str(settings.get("vision_api_key", ""))
            ),
            "max_steps": tk.StringVar(value=str(settings.get("max_steps", 0))),
            "max_seconds": tk.StringVar(value=str(settings.get("max_seconds", 1800))),
            "work_dir": tk.StringVar(value=str(settings.get("work_dir", ""))),
            "forbidden_patterns": tk.StringVar(
                value=str(settings.get("forbidden_patterns", ""))
            ),
            "reasoning_effort": tk.StringVar(
                value=str(settings.get("reasoning_effort", ""))
            ),
            "temperature": tk.StringVar(value=str(settings.get("temperature", 0.2))),
            "vision": tk.BooleanVar(value=bool(settings.get("vision", False))),
            "minimize_before_typing": tk.BooleanVar(
                value=bool(settings.get("minimize_before_typing", True))
            ),
            "allow_outside": tk.BooleanVar(
                value=bool(settings.get("allow_outside", True))
            ),
        }

        rows = [
            ("Base URL", "base_url", "например https://api.openai.com/v1\n"
                                      "или http://localhost:11434/v1 для Ollama"),
            ("API-ключ", "api_key", "можно оставить пустым для локальной Ollama"),
            ("Модель", "model", "например gpt-4o-mini, llama3.1, qwen2.5:7b"),
            ("Vision-модель", "vision_model", "модель для look_screen, видит скриншоты:\n"
                                              "qwen/qwen3.8-27b (рабочая),\n"
                                              "zai-org/glm-5.3-flash (часто пустой ответ)"),
            ("Vision Base URL", "vision_base_url", "свой провайдер для картинок;\n"
                                                   "пусто — как у основной модели"),
            ("Vision API-ключ", "vision_api_key", "пусто — ключ основной модели"),
            ("Лимит шагов", "max_steps", "0 — без ограничения;\n"
                                          "число — сколько действий подряд за один запрос"),
            ("Лимит времени, с", "max_seconds", "0 — без ограничения; иначе задача\n"
                                                "прерывается по времени"),
            ("Рабочая папка", "work_dir", "папка по умолчанию для команд и файлов"),
            ("Запрещённые команды", "forbidden_patterns", "регулярные выражения через ;\n"
                                                          "такие команды не выполняются никогда"),
            ("Температура", "temperature", "0 — предсказуемо, 1 — креативно"),
            ("Reasoning", "reasoning_effort", "low — быстрее и без длинных рассуждений;\n"
                                              "пусто — не передавать параметр"),
        ]
        for index, (label, key, hint) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", pady=6)
            if key == "model":
                self.model_box = ttk.Combobox(
                    body, textvariable=self.vars[key], width=40
                )
                self.model_box.grid(row=index, column=1, sticky="ew", padx=(10, 0), pady=6)
            else:
                ttk.Entry(body, textvariable=self.vars[key], width=42).grid(
                    row=index, column=1, sticky="ew", padx=(10, 0), pady=6
                )
            ttk.Label(body, text=hint, foreground=theme.MUTED, justify="left").grid(
                row=index, column=2, sticky="w", padx=(10, 0)
            )

        ttk.Checkbutton(
            body,
            text="Прикладывать скриншот экрана к сообщению (нужна vision-модель)",
            variable=self.vars["vision"],
        ).grid(row=len(rows), column=0, columnspan=3, sticky="w", pady=(10, 2))
        ttk.Checkbutton(
            body,
            text="Сворачивать окно перед вводом текста",
            variable=self.vars["minimize_before_typing"],
        ).grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w", pady=(2, 2))
        ttk.Checkbutton(
            body,
            text="Работать с файлами вне рабочей папки без предупреждения",
            variable=self.vars["allow_outside"],
        ).grid(row=len(rows) + 2, column=0, columnspan=3, sticky="w", pady=(2, 10))

        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Проверить подключение", command=self._test).pack(
            side="left"
        )
        ttk.Button(buttons, text="Сохранить", command=self._save).pack(side="right")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(
            side="right", padx=(0, 6)
        )

        self.bind("<Return>", lambda _event: self._save())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{master.winfo_rootx() + 60}+{master.winfo_rooty() + 80}")

    def _collect(self) -> dict[str, Any]:
        return {
            "base_url": str(self.vars["base_url"].get()).strip(),
            "api_key": str(self.vars["api_key"].get()).strip(),
            "model": str(self.vars["model"].get()).strip(),
            "vision_model": str(self.vars["vision_model"].get()).strip(),
            "vision_base_url": str(self.vars["vision_base_url"].get()).strip(),
            "vision_api_key": str(self.vars["vision_api_key"].get()).strip(),
            "max_steps": max(0, int(float(self.vars["max_steps"].get() or 0))),
            "max_seconds": max(0, int(float(self.vars["max_seconds"].get() or 0))),
            "work_dir": str(self.vars["work_dir"].get()).strip(),
            "forbidden_patterns": str(self.vars["forbidden_patterns"].get()).strip(),
            "reasoning_effort": str(self.vars["reasoning_effort"].get()).strip(),
            "temperature": float(str(self.vars["temperature"].get()).replace(",", ".")),
            "vision": bool(self.vars["vision"].get()),
            "minimize_before_typing": bool(self.vars["minimize_before_typing"].get()),
            "allow_outside": bool(self.vars["allow_outside"].get()),
        }

    def _show_models(self, models: list[str]) -> None:
        self.model_box["values"] = models
        preview = ", ".join(models[:10]) or "список пуст"
        messagebox.showinfo("Подключение", f"API отвечает.\nМодели: {preview}")

    def _test(self) -> None:
        data = self._collect()

        def worker() -> None:
            probe = Agent({**self.settings, **data}, lambda n, a: "", lambda n, a: True, lambda k, t: None, threading.Event())
            try:
                models = probe.list_models()
                self.after(0, lambda: self._show_models(models))
            except LLMError as exc:
                self.after(0, lambda: messagebox.showerror("Подключение", str(exc)))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("Подключение", f"Ошибка: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _save(self) -> None:
        try:
            self.result = self._collect()
        except ValueError:
            messagebox.showerror(APP_NAME, "Проверьте числовые поля.", parent=self)
            return
        self.destroy()


class PointsDialog(tk.Toplevel):
    """Список сохранённых точек с координатами."""

    def __init__(self, master: tk.Tk, points: actions.PointStore) -> None:
        super().__init__(master)
        self.title("Точки экрана")
        self.points = points
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        self.listbox = theme.listbox(body, width=48, height=14)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._click_selected())

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Добавить под курсором", command=self._add_current).pack(
            side="left"
        )
        ttk.Button(buttons, text="Перейти", command=self._click_selected).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="Удалить", command=self._remove).pack(side="left")
        ttk.Button(buttons, text="Закрыть", command=self.destroy).pack(side="right")

        ttk.Label(
            body,
            text="F8 в главном окне — быстро сохранить точку под курсором.",
            foreground=theme.MUTED,
        ).pack(anchor="w", pady=(6, 0))
        self.refresh()

    def refresh(self) -> None:
        self.listbox.delete(0, "end")
        for name, (x, y) in sorted(self.points.as_dict().items()):
            self.listbox.insert("end", f"{name}  →  ({x}, {y})")

    def _selected_name(self) -> str | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        row = self.listbox.get(selection[0])
        return row.split("  →  ")[0]

    def _add_current(self) -> None:
        name = simpledialog.askstring(
            "Новая точка", "Название точки:", parent=self
        )
        if not name:
            return
        self.points.add(name)
        self.refresh()

    def _click_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        point = self.points.get(name)
        if point is None:
            return
        actions.click(point[0], point[1])
        self.destroy()

    def _remove(self) -> None:
        name = self._selected_name()
        if not name:
            return
        self.points.remove(name)
        self.refresh()


class HistoryDialog(tk.Toplevel):
    """Список сохранённых чатов."""

    def __init__(self, master: tk.Tk, store: chatstore.ChatStore) -> None:
        super().__init__(master)
        self.title("История чатов")
        self.store = store
        self.result: str | None = None
        self.sessions: list[dict[str, Any]] = []
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)

        self.listbox = theme.listbox(body, width=64, height=16)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda _event: self._open())

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Открыть", command=self._open).pack(side="left")
        ttk.Button(buttons, text="Удалить", command=self._delete).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="Новый чат", command=self._new).pack(side="left")
        ttk.Button(buttons, text="Закрыть", command=self.destroy).pack(side="right")

        ttk.Label(
            body,
            text="Чаты сохраняются автоматически после каждого ответа.",
            foreground=theme.MUTED,
        ).pack(anchor="w", pady=(6, 0))
        self.refresh()

    def refresh(self) -> None:
        self.sessions = self.store.list_sessions()
        self.listbox.delete(0, "end")
        for session in self.sessions:
            stamp = time.strftime("%d.%m %H:%M", time.localtime(session["updated"]))
            self.listbox.insert(
                "end", f"{stamp}   {session['title']}   ({session['count']})"
            )

    def _selected(self) -> dict[str, Any] | None:
        selection = self.listbox.curselection()
        if not selection:
            return None
        return self.sessions[selection[0]]

    def _open(self) -> None:
        session = self._selected()
        if session is None:
            return
        self.result = str(session["id"])
        self.destroy()

    def _delete(self) -> None:
        session = self._selected()
        if session is None:
            return
        if not messagebox.askyesno(
            APP_NAME, f"Удалить чат «{session['title']}»?", parent=self
        ):
            return
        self.store.delete(str(session["id"]))
        self.refresh()

    def _new(self) -> None:
        self.result = "new"
        self.destroy()


class JarvisApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = self._load_settings()
        self.points = actions.PointStore(POINTS_PATH)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        # Зажатые клавиши/кнопки отпускаются сразу по F12, а не через N секунд.
        actions.set_stop_check(self.stop_event.is_set)
        self.journal = guard.Journal(APP_DIR / "journal.log")
        self.secrets = guard.load_secrets(APP_DIR)
        # Файл памяти с правилами: создаётся один раз, дальше модель его читает.
        try:
            llm.ensure_memory(workspace.ROOT)
        except Exception:  # noqa: BLE001
            pass
        self.worker: threading.Thread | None = None
        self.mini: overlay.MiniOverlay | None = None
        self._turn_started = 0.0
        self._closed = False
        self._poll_job: str | None = None
        self.store = chatstore.ChatStore(APP_DIR / "chats")
        self.session_id = chatstore.ChatStore.new_id()
        self.session_title = ""

        self.agent = Agent(
            self.settings,
            executor=self._execute_tool,
            confirm=self._confirm,
            on_log=self._log_event,
            stop_event=self.stop_event,
        )

        self._build_ui()
        self.root.update_idletasks()
        try:
            self._hwnd = int(
                screen.user32.GetAncestor(self.root.winfo_id(), screen.GA_ROOT)
            )
        except Exception:  # noqa: BLE001
            self._hwnd = 0
        self._start_hotkeys()
        self._restore_last_session()
        self._poll_job = self.root.after(100, self._poll)

    # -- настройки ---------------------------------------------------------
    def _load_settings(self) -> dict[str, Any]:
        data = dict(DEFAULT_SETTINGS)
        try:
            data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return data

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self.settings, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Не удалось сохранить настройки: {exc}")

    # -- интерфейс ---------------------------------------------------------
    def _build_ui(self) -> None:
        theme.apply(self.root)

        self.root.title(f"{APP_NAME} {VERSION}")
        self.root.geometry("980x760")
        self.root.minsize(720, 560)
        self.root.configure(bg=theme.BG)
        self.root.attributes("-topmost", bool(self.settings.get("always_on_top", True)))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # -- шапка: реактор, название, кнопки ------------------------------
        top = tk.Frame(self.root, bg=theme.PANEL, padx=18, pady=14)
        top.grid(row=0, column=0, sticky="ew")

        self.logo = tk.Canvas(
            top, width=44, height=44, bg=theme.PANEL, highlightthickness=0
        )
        self.logo.pack(side="left", padx=(0, 14))
        self._logo_phase = 0.0
        self._draw_logo()

        title_box = tk.Frame(top, bg=theme.PANEL)
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text=APP_NAME,
            bg=theme.PANEL,
            fg=theme.TEXT,
            font=theme.FONT_BRAND,
        ).pack(anchor="w")
        self.model_label = tk.Label(
            title_box, text="", bg=theme.PANEL, fg=theme.MUTED, font=theme.FONT_SMALL
        )
        self.model_label.pack(anchor="w", pady=(2, 0))
        self._refresh_model_label()

        right = tk.Frame(top, bg=theme.PANEL)
        right.pack(side="right")

        self.mini_btn = theme.button(right, "◱ Мини", self.enter_mini_mode, accent=True)
        self.mini_btn.pack(side="right", padx=(8, 0))
        theme.button(right, "■ Стоп", self.stop).pack(side="right", padx=(8, 0))
        theme.button(right, "Новый", self.new_dialog).pack(side="right", padx=(8, 0))
        theme.button(right, "История", self.open_history).pack(side="right", padx=(8, 0))
        theme.button(right, "GitHub", self.open_github).pack(side="right", padx=(8, 0))
        theme.button(right, "Файлы", self.open_files).pack(side="right", padx=(8, 0))
        theme.button(right, "Точки", self.open_points).pack(side="right", padx=(8, 0))
        theme.button(right, "⚙", self.open_settings).pack(side="right", padx=(8, 0))

        # тонкая светящаяся линия под шапкой
        edge = tk.Canvas(self.root, height=2, bg=theme.PANEL, highlightthickness=0)
        edge.grid(row=0, column=0, sticky="sew")
        def _paint_edge(event) -> None:
            edge.delete("all")
            half = max(2, event.width // 2)
            theme.gradient(
                edge, 0, 0, half, 2,
                theme.PANEL, theme.ACCENT, steps=60, horizontal=True,
            )
            theme.gradient(
                edge, half, 0, event.width, 2,
                theme.ACCENT, theme.PANEL, steps=60, horizontal=True,
            )

        edge.bind("<Configure>", _paint_edge)

        # -- чат -----------------------------------------------------------
        chat_frame = tk.Frame(self.root, bg=theme.BG, padx=14, pady=12)
        chat_frame.grid(row=1, column=0, sticky="nsew")
        chat_frame.columnconfigure(0, weight=1)
        chat_frame.rowconfigure(0, weight=1)

        self.chat = chatview.ChatView(chat_frame)
        self.chat.grid(row=0, column=0, sticky="nsew")

        # -- поле ввода ----------------------------------------------------
        bottom = tk.Frame(self.root, bg=theme.BG, padx=14, pady=12)
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        entry_wrap = tk.Frame(
            bottom, bg=theme.INPUT_BG, highlightthickness=1,
            highlightbackground=theme.blend(theme.BORDER, theme.ACCENT, 0.35),
            highlightcolor=theme.ACCENT,
        )
        entry_wrap.grid(row=0, column=0, sticky="ew")
        entry_wrap.columnconfigure(0, weight=1)

        self.input_var = tk.StringVar()
        self.entry = tk.Entry(
            entry_wrap,
            textvariable=self.input_var,
            font=("Segoe UI", 11),
            bg=theme.INPUT_BG,
            fg=theme.TEXT,
            insertbackground=theme.CYAN,
            bd=0,
            relief="flat",
            highlightthickness=0,
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=16, pady=13)
        self.entry.bind("<Return>", lambda _event: self.send())
        self.send_btn = theme.CircleButton(
            bottom, "➤", self.send, size=44, accent=True, bg=theme.BG
        )
        self.send_btn.grid(row=0, column=1, padx=(10, 0))

        # -- статус --------------------------------------------------------
        self.status_var = tk.StringVar(value="Готов.")
        status = tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            padx=18,
            pady=7,
        )
        status.grid(row=3, column=0, sticky="ew")

        self._append(
            "assistant",
            "Привет. Я JARVIS — управляю мышью, клавиатурой, консолью и файлами.\n\n"
            "◱ Мини — компактный режим поверх экрана (Ctrl+Shift+J).\n"
            "F8 — сохранить точку под курсором, F12 — стоп.",
        )

    # -- логотип-реактор ---------------------------------------------------
    def _draw_logo(self) -> None:
        """Реактор в шапке: ядро, орбиты и вращающиеся спутники."""
        import math

        canvas = self.logo
        canvas.delete("all")
        self._logo_phase = (self._logo_phase + 0.10) % (2 * math.pi)
        phase = self._logo_phase
        pulse = 0.5 + 0.5 * math.sin(phase)
        cx = cy = 22

        # мягкое свечение
        for i in range(6, 0, -1):
            r = 12 + i * 2.2
            canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline=theme.blend(theme.PANEL, theme.ACCENT, 0.035 * (7 - i)), width=1,
            )
        # две орбиты-эллипса
        for tilt, color, width in ((0.55, theme.ACCENT, 1), (0.30, theme.CYAN, 1)):
            rx, ry = 19, 19 * tilt
            canvas.create_oval(
                cx - rx, cy - ry, cx + rx, cy + ry,
                outline=theme.blend(theme.PANEL, color, 0.45 + 0.3 * pulse), width=width,
            )
        # спутники на орбитах
        for speed, radius, tilt, color in (
            (1.0, 19, 0.55, theme.CYAN),
            (-1.6, 19, 0.30, theme.MAGENTA),
        ):
            angle = phase * speed
            sx = cx + radius * math.cos(angle)
            sy = cy + radius * tilt * math.sin(angle)
            canvas.create_oval(
                sx - 2, sy - 2, sx + 2, sy + 2,
                fill=theme.blend(color, "#ffffff", 0.35), outline="",
            )
        # ядро
        core_r = 6 + 1.2 * pulse
        canvas.create_oval(
            cx - core_r, cy - core_r, cx + core_r, cy + core_r,
            fill=theme.blend(theme.ACCENT, theme.CYAN, 0.5 + 0.3 * pulse), outline="",
        )
        canvas.create_oval(
            cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5,
            fill=theme.blend(theme.CYAN, "#ffffff", 0.5 + 0.4 * pulse), outline="",
        )
        self._logo_job = self.root.after(60, self._draw_logo)

    # -- режимы: полный и мини --------------------------------------------
    def enter_mini_mode(self) -> None:
        """Свернуть полное окно и показать плавающую мини-панель."""
        if getattr(self, "mini", None) is not None:
            self.mini.focus_input()
            return
        self.root.withdraw()
        self.mini = overlay.MiniOverlay(
            self.root,
            on_send=self._mini_send,
            on_expand=self.exit_mini_mode,
            on_close=self.exit_mini_mode,
        )
        busy = self.worker is not None and self.worker.is_alive()
        self.mini.set_busy(busy)
        self.mini.focus_input()

    def exit_mini_mode(self) -> None:
        """Закрыть мини-панель и вернуть полное окно."""
        mini = getattr(self, "mini", None)
        if mini is not None:
            self.mini = None
            try:
                mini.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.root.deiconify()
        self.root.lift()
        self.entry.focus_set()

    def _mini_send(self, text: str) -> None:
        self.input_var.set(text)
        self.send()

    def _append(self, tag: str, text: str) -> None:
        """Добавить сообщение в космическую ленту (пузыри, аватары, звёзды)."""
        text = (text or "").strip()
        for prefix in ("Вы: ", "JARVIS: ", "⚙ ", "↳ ", "! "):
            if text.startswith(prefix):
                text = text[len(prefix):]
        self.chat.add(tag, text)

    def _refresh_model_label(self) -> None:
        model = str(self.settings.get("model", "")) or "модель не выбрана"
        self.model_label.configure(text=model)

    # -- обработчики кнопок ------------------------------------------------
    def _toggle_topmost(self) -> None:
        value = bool(self.topmost_var.get())
        self.root.attributes("-topmost", value)
        self.settings["always_on_top"] = value
        self._save_settings()

    def _toggle_confirm(self) -> None:
        self.settings["confirm_mode"] = bool(self.confirm_var.get())
        self._save_settings()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.root, self.settings)
        self.root.wait_window(dialog)
        if dialog.result is None:
            return
        self.settings.update(dialog.result)
        self._save_settings()
        self.agent.settings = self.settings
        self._refresh_model_label()
        self.status_var.set("Настройки сохранены.")

    def open_points(self) -> None:
        dialog = PointsDialog(self.root, self.points)
        self.root.wait_window(dialog)

    def open_files(self) -> None:
        """Окно рабочей папки: что ассистент пишет по ходу работы."""
        win = tk.Toplevel(self.root)
        win.title("Файлы ассистента")
        win.geometry("760x480")
        win.transient(self.root)
        win.columnconfigure(1, weight=1)
        win.rowconfigure(0, weight=1)

        left = ttk.Frame(win, padding=(8, 8, 4, 8))
        left.grid(row=0, column=0, sticky="ns")
        left.rowconfigure(0, weight=1)
        listbox = theme.listbox(left, width=30, height=20)
        listbox.grid(row=0, column=0, sticky="ns")

        right = ttk.Frame(win, padding=(4, 8, 8, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        preview = tk.Text(right, wrap="word", font=("Consolas", 9), state="disabled")
        preview.grid(row=0, column=0, sticky="nsew")

        status = ttk.Label(win, text=str(workspace.folder()), foreground=theme.MUTED)
        status.grid(row=1, column=0, columnspan=2, sticky="w", padx=10)

        buttons = ttk.Frame(win, padding=(8, 0, 8, 8))
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            buttons,
            text="Открыть папку",
            command=lambda: actions.open_app(str(workspace.folder())),
        ).pack(side="left")
        ttk.Button(buttons, text="Обновить", command=lambda: refresh()).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="Закрыть", command=win.destroy).pack(side="right")

        shown: list[str] = []

        def refresh() -> None:
            items = workspace.list_files()
            names = [str(item["name"]) for item in items]
            if names == shown:
                return
            shown[:] = names
            selection = listbox.curselection()
            listbox.delete(0, "end")
            for item in items:
                size = int(item["size"])
                listbox.insert("end", f"{item['name']}  ({size} Б)")
            if selection:
                listbox.selection_set(min(selection[0], max(0, len(names) - 1)))

        def show(_event: object = None) -> None:
            selection = listbox.curselection()
            if not selection:
                return
            name = shown[selection[0]] if selection[0] < len(shown) else ""
            if not name:
                return
            try:
                text = workspace.read_file(name)
            except Exception as exc:  # noqa: BLE001
                text = f"не прочитать: {exc}"
            preview.configure(state="normal")
            preview.delete("1.0", "end")
            preview.insert("1.0", text)
            preview.configure(state="disabled")

        listbox.bind("<<ListboxSelect>>", show)

        def tick() -> None:
            if not win.winfo_exists():
                return
            refresh()
            win.after(2000, tick)

        refresh()
        tick()

    def open_github(self) -> None:
        """Статус GitHub; если не подключён — вход через gh (браузер + код)."""
        text = github.status_text()
        if github.installed() and "не подключён" not in text:
            messagebox.showinfo("GitHub", text[:1500])
            return
        if not github.installed():
            messagebox.showerror(
                "GitHub",
                "gh не установлен.\n\nУстанови один раз:\n"
                "winget install --id GitHub.cli -e\n\n"
                "Потом нажми «GitHub» снова.",
            )
            return
        if not messagebox.askyesno(
            "GitHub",
            "Войти в GitHub?\n\nОткроется браузер, код для вставки покажу в чате.",
        ):
            return
        session_id = github.start_login()
        threading.Thread(
            target=self._github_login, args=(session_id,), daemon=True
        ).start()
        self.events.put(
            ("assistant", "Открываю вход в GitHub… Код появится здесь через пару секунд.")
        )

    def _github_login(self, session_id: str) -> None:
        code = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.stop_event.is_set():
                shell.close_session(session_id)
                return
            github.answer_git_prompt(session_id)
            code = github.login_code(session_id)
            if code:
                break
            time.sleep(0.5)
        if code:
            session = shell.get_session(session_id)
            if session is not None:
                session.send("", submit=True)  # gh: открыть браузер

            def copy_code() -> None:
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append(code)
                except Exception:  # noqa: BLE001
                    pass

            self._ui_sync(copy_code)
            self.events.put(
                (
                    "assistant",
                    f"Код для входа в GitHub: {code} (скопирован в буфер).\n"
                    f"Вставь его в открывшемся браузере и подтверди вход.",
                )
            )
        else:
            self.events.put(
                ("assistant", "Код не появился — проверь окно входа в GitHub.")
            )
        github.answer_git_prompt(session_id)
        ok = False
        for _ in range(150):
            if self.stop_event.is_set():
                break
            ok, _text = github.auth_status()
            if ok:
                break
            github.answer_git_prompt(session_id)
            time.sleep(2)
        shell.close_session(session_id)
        if ok:
            github.setup_git()
            self.events.put(
                ("assistant", "GitHub подключён. Могу работать с репозиториями, issue и PR.")
            )
        else:
            self.events.put(
                ("error", "Вход в GitHub не завершён. Можно повторить кнопкой «GitHub».")
            )

    def new_dialog(self) -> None:
        self._save_session()
        self.agent.reset()
        self.session_id = chatstore.ChatStore.new_id()
        self.session_title = ""
        self.chat.clear()
        self._append("assistant", "Новый диалог. Чем помочь?")
        self.status_var.set("Новый диалог.")

    def open_history(self) -> None:
        dialog = HistoryDialog(self.root, self.store)
        self.root.wait_window(dialog)
        if dialog.result == "new":
            self.new_dialog()
            return
        if not dialog.result:
            return
        data = self.store.load(dialog.result)
        if not data:
            self.status_var.set("Не удалось открыть чат.")
            return
        self._save_session()
        self.session_id = str(data.get("id") or dialog.result)
        self.session_title = str(data.get("title") or "")
        self.agent.messages = list(data.get("messages") or [])
        self._render_history(self.agent.messages)
        self.status_var.set(f"Открыт чат: {self.session_title or 'без названия'}")

    # -- история чатов ------------------------------------------------------
    def _restore_last_session(self) -> None:
        sessions = self.store.list_sessions()
        if not sessions:
            return
        data = self.store.load(sessions[0]["id"])
        if not data or not data.get("messages"):
            return
        self.session_id = str(data.get("id") or sessions[0]["id"])
        self.session_title = str(data.get("title") or "")
        self.agent.messages = list(data["messages"])
        self._render_history(self.agent.messages)
        self.status_var.set(f"Загружен чат: {self.session_title or 'без названия'}")

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return " ".join(parts)
        return "" if content is None else str(content)

    def _render_history(self, messages: list[dict[str, Any]]) -> None:
        self.chat.clear()
        for message in messages:
            role = message.get("role")
            text = self._content_text(message.get("content"))
            if role == "user":
                self._append("user", "Вы: " + text)
            elif role == "assistant":
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    args = str(function.get("arguments", ""))[:80]
                    self._append("tool", f"  ⚙ {function.get('name', '')}({args})")
                if text:
                    self._append("assistant", "JARVIS: " + text)
            elif role == "tool":
                self._append("result", "    ↳ " + text[:400])

    @staticmethod
    def _make_title(messages: list[dict[str, Any]]) -> str:
        for message in messages:
            if message.get("role") == "user":
                text = JarvisApp._content_text(message.get("content")).strip()
                if text:
                    return text[:50]
        return "Без названия"

    def _save_session(self) -> None:
        messages = self.agent.messages
        if not messages:
            return
        if not self.session_title:
            self.session_title = self._make_title(messages)
        try:
            self.store.save(self.session_id, self.session_title, messages)
        except OSError:
            pass

    def stop(self) -> None:
        self.stop_event.set()
        actions.release_all()
        try:
            shell.close_all()
        except Exception:  # noqa: BLE001
            pass
        self.status_var.set("Остановлено. Жду завершения текущего шага…")

    # -- чат ---------------------------------------------------------------
    def send(self) -> None:
        text = self.input_var.get().strip()
        if not text:
            return
        if self.worker is not None and self.worker.is_alive():
            self.status_var.set("Подождите: ассистент ещё работает (F12 — стоп).")
            return
        self.input_var.set("")
        self._append("user", "Вы: " + text)
        self.stop_event.clear()
        self._turn_started = time.time()
        self.send_btn.configure(state="disabled")
        if self.mini is not None:
            self.mini.set_busy(True)
        self.status_var.set("Думаю…")
        self.worker = threading.Thread(target=self._turn, args=(text,), daemon=True)
        self.worker.start()

    def _turn(self, text: str) -> None:
        try:
            image = self._screenshot_b64() if self.settings.get("vision") else None
            answer = self.agent.ask(text, image)
            self.events.put(("assistant", answer))
        except LLMError as exc:
            self.events.put(("error", f"Ошибка API: {exc}"))
        except Exception as exc:  # noqa: BLE001
            self.events.put(("error", f"Ошибка: {exc}"))
        finally:
            self.events.put(("idle", ""))

    def _screenshot_b64(self) -> str | None:
        try:
            from PIL import ImageGrab

            shot = ImageGrab.grab(all_screens=True)
            buffer = io.BytesIO()
            shot.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001
            return None

    # -- события из рабочих потоков ---------------------------------------
    def _log_event(self, kind: str, text: str) -> None:
        self.events.put((kind, text))

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "hotkey":
                    if text == "stop":
                        self.stop()
                    elif text == "point":
                        self._quick_point()
                    elif text == "mini":
                        if self.mini is None:
                            self.enter_mini_mode()
                        else:
                            self.exit_mini_mode()
                    continue
                if kind == "idle":
                    self.send_btn.configure(state="normal")
                    self.status_var.set("Готов.")
                    if self.mini is not None:
                        self.mini.set_busy(False)
                    self._save_session()
                    continue
                if kind == "assistant":
                    self._append("assistant", "JARVIS: " + text)
                elif kind == "user":
                    self._append("user", text)
                elif kind == "tool":
                    self._append("tool", "  ⚙ " + text)
                elif kind == "result":
                    self._append("result", "    ↳ " + text)
                elif kind == "warn":
                    self._append("warn", "  ! " + text)
                elif kind == "error":
                    self._append("error", text)
                elif kind == "status":
                    self.status_var.set(text)
        except queue.Empty:
            pass

        try:
            x, y = actions.cursor_position()
            busy = self.worker is not None and self.worker.is_alive()
            if busy:
                elapsed = time.time() - self._turn_started if self._turn_started else 0.0
                state = f"работаю… {elapsed:.0f} с (F12 — стоп)"
            else:
                state = "готов"
            self.status_var.set(f"курсор: X={x}, Y={y}   |   {state}")
        except Exception:  # noqa: BLE001
            pass
        self._poll_job = self.root.after(150, self._poll)

    # -- горячие клавиши ---------------------------------------------------
    def _start_hotkeys(self) -> None:
        from pynput import keyboard

        self._hotkeys = keyboard.GlobalHotKeys(
            {
                "<f12>": lambda: self.events.put(("hotkey", "stop")),
                "<f8>": lambda: self.events.put(("hotkey", "point")),
                "<ctrl>+<shift>+j": lambda: self.events.put(("hotkey", "mini")),
            }
        )
        self._hotkeys.daemon = True
        self._hotkeys.start()

    def _quick_point(self) -> None:
        name = simpledialog.askstring(
            APP_NAME, "Название точки под курсором:", parent=self.root
        )
        if not name:
            return
        x, y = self.points.add(name)
        self.status_var.set(f"Точка «{name}» сохранена: ({x}, {y})")

    # -- подтверждение действий -------------------------------------------
    def _confirm(
        self,
        name: str,
        args: dict[str, Any],
        reason: str = "",
        force: bool = False,
    ) -> bool:
        if name in {"get_state", "list_points", "wait"}:
            return True
        if not force and not self.settings.get("confirm_mode", True):
            return True

        pretty = Agent._pretty(args)[:600]
        question = f"Разрешить действие?\n\n{name}({pretty})"
        if reason:
            question = (
                f"ОПАСНОЕ ДЕЙСТВИЕ: {reason}.\n\n{question}\n\n"
                f"Оно может изменить систему или данные. Продолжить?"
            )
        result: dict[str, bool] = {}
        done = threading.Event()

        def ask() -> None:
            try:
                result["ok"] = messagebox.askyesno(APP_NAME, question, parent=self.root)
            finally:
                done.set()

        self.root.after(0, ask)
        deadline = time.time() + 120
        while time.time() < deadline:
            if done.wait(0.2):
                break
            if self.stop_event.is_set():
                return False
        return bool(result.get("ok", False))

    # -- исполнение инструментов -------------------------------------------
    def _ui_sync(self, func: Callable[[], None], timeout: float = 2.0) -> None:
        done = threading.Event()

        def wrapper() -> None:
            try:
                func()
            finally:
                done.set()

        self.root.after(0, wrapper)
        done.wait(timeout)

    def _self_is_foreground(self) -> bool:
        """Наше окно сейчас активно?"""
        try:
            return int(screen.user32.GetForegroundWindow()) == int(self._root_hwnd())
        except Exception:  # noqa: BLE001
            return False

    def _root_hwnd(self) -> int:
        return self._hwnd

    def _target_window(self) -> dict[str, Any] | None:
        """Окно, с которым работает ассистент: не своё, а нижнее видимое."""
        active = screen.active_window()
        if active is not None and int(active["hwnd"]) != int(self._root_hwnd()):
            return active
        for window in screen.list_windows():
            if int(window["hwnd"]) == int(self._root_hwnd()):
                continue
            if window.get("minimized"):
                continue
            return window
        return active

    def _with_minimize(self, func: Callable[[], None]) -> None:
        """Сворачивает окно только если фокус у нас, иначе действует напрямую."""
        if not self.settings.get("minimize_before_typing", True):
            func()
            return
        if not self._self_is_foreground():
            func()
            return
        self._ui_sync(self.root.iconify)
        time.sleep(0.35)
        try:
            func()
        finally:
            self._ui_sync(self.root.deiconify)

    def _click_at(
        self,
        x: int,
        y: int,
        button: str = "left",
        count: int = 1,
        modifiers: str = "",
    ) -> None:
        """Клик по экранной точке.

        Если точку закрывает наше окно (оно всегда поверх), оно убирается
        на время клика: иначе клик попадёт в ассистента, а не в цель.
        """
        hidden = False
        target = 0
        try:
            if screen.hwnd_under_point(x, y) == int(self._root_hwnd()):
                self._ui_sync(self.root.withdraw)
                hidden = True
                time.sleep(0.2)
            target = screen.hwnd_under_point(x, y)
            screen.focus_point(x, y)
            actions.click(x, y, button, count, modifiers)
        finally:
            if hidden:
                self._ui_sync(self.root.deiconify)
                # Фокус возвращаем туда, куда кликнули, а не себе.
                if target:
                    screen.activate_hwnd(target)

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Разрешение → выполнение → журнал."""
        verdict = guard.classify_tool(name, args, self.settings)
        if verdict.level == "forbidden":
            self.journal.write(name, args, verdict.level, "refused", verdict.reason)
            return f"ЗАПРЕЩЕНО: {verdict.reason}. Команда не выполнена."
        if verdict.level == "dangerous" and not self._confirm(
            name, args, reason=verdict.reason, force=True
        ):
            self.journal.write(
                name, args, verdict.level, "rejected", "пользователь отказал"
            )
            return "Пользователь не разрешил опасное действие."
        started = time.time()
        try:
            result = self._run_tool(name, args)
        except Exception as exc:  # noqa: BLE001
            self.journal.write(
                name, args, verdict.level, "error", str(exc), time.time() - started
            )
            raise
        self.journal.write(
            name, args, verdict.level, "allowed", result, time.time() - started
        )
        return guard.mask(result)

    def _run_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "get_state":
            x, y = actions.cursor_position()
            width, height = actions.screen_size()
            names = ", ".join(self.points.names()) or "нет"
            return f"курсор=({x}, {y}), экран={width}x{height}, точки: {names}"

        if name == "list_points":
            data = self.points.as_dict()
            if not data:
                return "точек пока нет"
            return "; ".join(f"{n}=({x}, {y})" for n, (x, y) in sorted(data.items()))

        if name == "move_mouse":
            actions.move_mouse(int(args["x"]), int(args["y"]))
            return f"курсор в ({args['x']}, {args['y']})"

        if name == "click":
            x = args.get("x")
            y = args.get("y")
            modifiers = str(args.get("modifiers", "")).strip()
            if x is not None and y is not None:
                self._click_at(
                    int(x),
                    int(y),
                    str(args.get("button", "left")),
                    int(args.get("count", 1)),
                    modifiers,
                )
                return "клик выполнен"
            if modifiers:
                self._with_minimize(
                    lambda: actions.click(
                        None,
                        None,
                        str(args.get("button", "left")),
                        int(args.get("count", 1)),
                        modifiers,
                    )
                )
                return f"клик с зажатым {modifiers} выполнен"
            actions.click(
                None,
                None,
                str(args.get("button", "left")),
                int(args.get("count", 1)),
            )
            return "клик выполнен в текущей позиции курсора"

        if name == "click_point":
            point_name = str(args.get("name", ""))
            point = self.points.get(point_name)
            if point is None:
                available = ", ".join(self.points.names()) or "нет"
                return f"точка {point_name!r} не найдена. Доступные: {available}"
            self._click_at(point[0], point[1])
            return f"клик в «{point_name}» ({point[0]}, {point[1]})"

        if name == "save_point":
            point_name = str(args.get("name", "")).strip() or f"точка {len(self.points.names()) + 1}"
            x, y = self.points.add(point_name)
            return f"сохранена точка «{point_name}» = ({x}, {y})"

        if name == "type_text":
            text = str(args.get("text", ""))
            if not text:
                return "пустой текст, ничего не введено"
            self._with_minimize(lambda: actions.type_text(text, 0.004))
            return f"введено символов: {len(text)}"

        if name == "press_keys":
            combo = str(args.get("combo", "")).strip()
            self._with_minimize(lambda: actions.press_combo(combo))
            return f"нажато: {combo}"

        if name == "hold_keys":
            combo = str(args.get("keys") or args.get("combo") or "").strip()
            if not combo:
                return "не указано, что зажать"
            seconds = max(0.05, min(30.0, float(args.get("seconds", 1.0))))
            self._with_minimize(lambda: actions.hold_keys(combo, seconds))
            return f"удержал {combo} {seconds:.1f} с"

        if name == "mouse_hold":
            button = str(args.get("button", "left"))
            seconds = max(0.05, min(30.0, float(args.get("seconds", 1.0))))
            actions.mouse_hold(button, seconds)
            return f"держал кнопку {button} {seconds:.1f} с"

        if name == "drag":
            x1, y1 = int(args["x1"]), int(args["y1"])
            x2, y2 = int(args["x2"]), int(args["y2"])
            hidden = False
            target = 0
            try:
                if screen.hwnd_under_point(x1, y1) == int(self._root_hwnd()):
                    self._ui_sync(self.root.withdraw)
                    hidden = True
                    time.sleep(0.2)
                target = screen.hwnd_under_point(x1, y1)
                screen.focus_point(x1, y1)
                actions.drag(x1, y1, x2, y2, float(args.get("seconds", 0.4)))
            finally:
                if hidden:
                    self._ui_sync(self.root.deiconify)
                    if target:
                        screen.activate_hwnd(target)
            return f"перетащил ({x1}, {y1}) → ({x2}, {y2})"

        if name == "download_file":
            self.events.put(("status", "скачиваю файл…"))
            saved = actions.download_file(
                str(args.get("url", "")), str(args.get("filename", ""))
            )
            return (
                f"скачал: {saved}. Чтобы установить, вызови open_app с полным "
                f"путём из ответа (без размера в скобках)."
            )

        if name == "find_and_click":
            description = str(
                args.get("description") or args.get("text") or ""
            ).strip()
            if not description:
                return "не указано, что искать"
            self.events.put(("status", f"ищу «{description}»…"))
            shot = self._run_tool(
                "look_screen",
                {
                    "question": (
                        f"Найди на экране: {description}. Ответь ТОЛЬКО "
                        f"координатами центра этого элемента в формате (x, y), "
                        f"без описания и пояснений."
                    ),
                    "coords": True,
                },
            )
            coords = _extract_coords(shot)
            if coords is None:
                return (
                    "не нашёл координаты элемента на снимке. Что видно:\n"
                    + shot[:400]
                )
            x, y = coords
            self._click_at(
                x,
                y,
                str(args.get("button", "left")),
                int(args.get("count", 1)),
                str(args.get("modifiers", "")).strip(),
            )
            return f"нашёл «{description}» в ({x}, {y}) и кликнул"

        if name == "run_plan":
            steps = args.get("steps")
            if isinstance(steps, str):
                try:
                    steps = json.loads(steps)
                except ValueError:
                    steps = None
            if not isinstance(steps, list) or not steps:
                return "план пуст: передай steps списком объектов {tool, args}"
            keep_going = bool(args.get("continue_on_error", False))
            lines: list[str] = []
            for index, step in enumerate(steps[:40], 1):
                if self.stop_event.is_set():
                    lines.append(f"остановлено пользователем на шаге {index}")
                    break
                if not isinstance(step, dict):
                    lines.append(f"{index}. пропуск: шаг не объект")
                    continue
                tool = str(
                    step.get("tool") or step.get("name") or step.get("action") or ""
                ).strip()
                step_args = step.get("args") or step.get("arguments") or {}
                if isinstance(step_args, str):
                    try:
                        step_args = json.loads(step_args)
                    except ValueError:
                        step_args = {}
                if not isinstance(step_args, dict):
                    step_args = {}
                if not tool:
                    lines.append(f"{index}. пропуск: не указан tool")
                    continue
                if tool == "run_plan":
                    lines.append(f"{index}. run_plan: вложенный план запрещён")
                    continue
                self.events.put(("status", f"план {index}/{len(steps)}: {tool}"))
                try:
                    result = self._run_tool(tool, step_args)
                except Exception as exc:  # noqa: BLE001
                    lines.append(f"{index}. {tool}: ОШИБКА {exc}")
                    if not keep_going:
                        lines.append(f"план прерван на шаге {index}")
                        break
                    continue
                lines.append(f"{index}. {tool}: {str(result)[:300]}")
            return "\n".join(lines)

        if name == "save_file":
            file_name = str(args.get("name", "")).strip() or "note.txt"
            saved = workspace.write_file(
                file_name,
                str(args.get("content", "")),
                bool(args.get("append", False)),
            )
            return f"{saved}. Папка: {workspace.folder()}"

        if name == "list_files":
            return workspace.describe_files()

        if name == "scroll":
            actions.scroll(int(args.get("amount", 0)))
            return "прокрутка выполнена"

        if name == "wait":
            seconds = max(0.0, min(30.0, float(args.get("seconds", 1))))
            time.sleep(seconds)
            return f"подождал {seconds:.1f} с"

        if name == "open_url":
            address = actions.open_url(str(args.get("url", "")))
            return f"открыл {address} в браузере по умолчанию"

        if name == "open_app":
            launched = actions.open_app(str(args.get("name", "")))
            return f"запустил {launched}"

        if name == "run_command":
            cmd = str(args.get("cmd", "")).strip()
            if not cmd:
                return "пустая команда"
            cwd = str(args.get("cwd") or guard.base_dir(self.settings))
            timeout = float(args.get("timeout", 120) or 120)
            self.events.put(("status", "выполняю команду…"))
            result = shell.run_command(
                cmd,
                cwd=cwd,
                timeout=timeout,
                shell=str(self.settings.get("shell", "powershell")),
                env=guard.secret_env(self.secrets),
            )
            return shell.format_result(result)

        if name == "gh":
            self.events.put(("status", "GitHub…"))
            try:
                result = github.run(
                    args.get("args") or [],
                    cwd=guard.base_dir(self.settings),
                    timeout=float(args.get("timeout", 90) or 90),
                    env=guard.secret_env(self.secrets),
                )
            except Exception as exc:  # noqa: BLE001
                return f"GitHub недоступен: {exc}"
            return shell.format_result(result)

        if name in {
            "read_file",
            "write_file",
            "edit_file",
            "list_dir",
            "delete_file",
            "move_file",
            "grep",
            "glob",
        }:
            raw = str(args.get("path") or args.get("name") or args.get("src") or ".")
            path, inside = guard.resolve_path(raw, self.settings)
            if name == "read_file":
                if not path.exists() and not Path(raw).is_absolute():
                    candidate = workspace.ROOT / raw
                    if candidate.exists():
                        path, inside = candidate, True
                try:
                    text = files.read_text(
                        path,
                        int(args.get("offset", 1) or 1),
                        int(args.get("limit", 400) or 400),
                    )
                except (OSError, ValueError) as exc:
                    return str(exc)
                return text

            if name == "write_file":
                try:
                    return files.write_text(
                        path,
                        str(args.get("content", "")),
                        bool(args.get("append", False)),
                    )
                except (OSError, ValueError) as exc:
                    return f"не записалось: {exc}"

            if name == "edit_file":
                try:
                    return files.edit_text(
                        path,
                        str(args.get("old", "")),
                        str(args.get("new", "")),
                        bool(args.get("replace_all", False)),
                    )
                except (OSError, ValueError) as exc:
                    return f"не изменилось: {exc}"

            if name == "grep":
                return files.grep(
                    path if path.exists() else guard.base_dir(self.settings),
                    str(args.get("pattern", "")),
                    str(args.get("glob", "")),
                    int(args.get("max_results", 100) or 100),
                    bool(args.get("case_sensitive", False)),
                )

            if name == "glob":
                return files.glob_paths(
                    path if path.exists() else guard.base_dir(self.settings),
                    str(args.get("pattern", "*")),
                    int(args.get("limit", 200) or 200),
                )

            if name == "list_dir":
                if not path.exists():
                    return f"папки нет: {path}"
                if path.is_file():
                    return "это файл — используй read_file"
                entries = sorted(
                    path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())
                )[:200]
                lines = []
                for entry in entries:
                    try:
                        if entry.is_dir():
                            lines.append(f"[папка] {entry.name}")
                        else:
                            lines.append(f"{entry.name} ({entry.stat().st_size} Б)")
                    except OSError:
                        lines.append(entry.name)
                body = "\n".join(lines) or "(пусто)"
                return f"{path}:\n" + body[:8000]

            if name == "delete_file":
                if not path.exists():
                    return f"файла нет: {path}"
                if path.is_dir():
                    return "это папка: удаляй файлы по одному"
                path.unlink()
                return f"удалил {path}"

            if name == "move_file":
                dst, _dst_inside = guard.resolve_path(
                    str(args.get("dst", "")), self.settings
                )
                if not path.exists():
                    return f"файла нет: {path}"
                if dst.is_dir():
                    dst = dst / path.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(dst))
                return f"перенёс {path} → {dst}"

        if name == "get_screen_state":
            self.events.put(("status", "читаю элементы окна…"))
            target = self._target_window()
            return uia.describe(int(target["hwnd"]) if target else None)

        if name == "click_element":
            target = self._target_window()
            if target is not None:
                screen.activate_hwnd(int(target["hwnd"]))
                time.sleep(0.15)
            return uia.click_element(
                str(args.get("element_id", "")),
                str(args.get("name", "")),
                str(args.get("kind", "")),
            )

        if name == "start_session":
            cmd = str(args.get("cmd", "")).strip()
            if not cmd:
                return "пустая команда"
            cwd = str(args.get("cwd") or guard.base_dir(self.settings))
            session = shell.start_session(
                cmd,
                cwd,
                int(args.get("cols", 120)),
                int(args.get("rows", 40)),
            )
            time.sleep(float(args.get("wait", 0.8)))
            return f"сессия {session.id} запущена\n" + session.read(3000)

        if name == "send_input":
            session_id = str(args.get("session_id", "")).strip()
            session = shell.get_session(session_id)
            if session is None:
                available = ", ".join(item["id"] for item in shell.list_sessions()) or "нет"
                return f"сессии {session_id} нет. Открытые: {available}"
            session.send(str(args.get("text", "")), bool(args.get("submit", True)))
            time.sleep(float(args.get("wait", 0.4)))
            return session.read(5000)

        if name == "read_session":
            session_id = str(args.get("session_id", "")).strip()
            session = shell.get_session(session_id)
            if session is None:
                available = ", ".join(item["id"] for item in shell.list_sessions()) or "нет"
                return f"сессии {session_id} нет. Открытые: {available}"
            return session.read(int(args.get("max_chars", 6000)))

        if name == "close_session":
            session_id = str(args.get("session_id", "")).strip()
            if not shell.close_session(session_id):
                return f"сессии {session_id} нет"
            return f"сессия {session_id} закрыта"

        if name == "list_sessions":
            sessions = shell.list_sessions()
            if not sessions:
                return "открытых сессий нет"
            return "; ".join(
                f"{item['id']}: {item['cmd']} "
                f"({'жива' if item['alive'] else 'завершена'}, {item['age']} с)"
                for item in sessions
            )

        if name == "list_windows":
            windows = screen.list_windows()
            if not windows:
                return "видимых окон нет"
            parts = []
            for window in windows[:15]:
                mark = " [АКТИВНО]" if window["active"] else ""
                process = window.get("process") or "?"
                parts.append(
                    f"{window['title']}{mark} [{process}] "
                    f"({window['rect'][2]}x{window['rect'][3]})"
                )
            return "; ".join(parts)

        if name == "switch_window":
            wanted = str(args.get("title", "")).strip()
            window = screen.activate_window(wanted)
            if window is None:
                opened = "; ".join(
                    f"{w['title']} [{w.get('process') or '?'}]"
                    for w in screen.list_windows()[:10]
                ) or "нет"
                return (
                    f"окно по «{wanted}» не найдено (искал по заголовку и имени "
                    f"программы). Открытые окна: {opened}"
                )
            return f"активно окно «{window['title']}» [{window.get('process') or '?'}]"

        if name == "look_screen":
            question = str(args.get("question", "")).strip()
            only_active = bool(args.get("only_active_window", False))
            self.events.put(("status", "смотрю на экран…"))

            # Наше окно всегда поверх: на снимке оно закрыло бы рабочий стол
            # и модель описывала бы собственный чат вместо нужного приложения.
            hidden = False
            prev_active = screen.active_window()
            if self.root.state() == "normal":
                self._ui_sync(self.root.withdraw)
                hidden = True
                time.sleep(0.25)

            def restore() -> None:
                """Вернуть своё окно и фокус. Вызывается сразу после снимка."""
                nonlocal hidden
                if not hidden:
                    return
                hidden = False
                self._ui_sync(self.root.deiconify)
                if prev_active is not None and int(prev_active["hwnd"]) != int(
                    self._root_hwnd()
                ):
                    screen.activate_hwnd(prev_active["hwnd"])

            active: dict[str, Any] | None = None
            region: tuple[int, int, int, int] | None = None
            try:
                active = screen.active_window()
                if only_active and active is not None:
                    region = (
                        active["rect"][0],
                        active["rect"][1],
                        active["rect"][2],
                        active["rect"][3],
                    )
                cursor = actions.cursor_position()
                under = screen.window_under_point(cursor[0], cursor[1])
                width, height = screen.screen_size()
                taskbar = any(
                    word in question.lower()
                    for word in (
                        "панел",
                        "трей",
                        "час",
                        "врем",
                        "иконк",
                        "пуск",
                        "задач",
                    )
                )
                wants_coords = bool(args.get("coords", False)) or any(
                    word in question.lower()
                    for word in (
                        "координат",
                        "где находится",
                        "где кнопка",
                        "где поле",
                        "нажми",
                        "клик",
                        "кнопк",
                        "ссылк",
                        "меню",
                        "элемент",
                    )
                )
                description = screen.describe(
                    self.settings,
                    question,
                    region=region,
                    cursor=cursor,
                    context=_screen_context(
                        active, under, cursor, region, (width, height)
                    ),
                    taskbar=taskbar,
                    grid=wants_coords,
                    after_capture=restore,
                )
            finally:
                restore()

            active_title = (active or {}).get("title", "—")
            return (
                f"экран {width}x{height}, курсор в ({cursor[0]}, {cursor[1]}), "
                f"под курсором: «{under}», активное окно: {active_title}\n"
                f"описание снимка:\n{description}"
            )

        return f"неизвестный инструмент: {name}"

    # -- завершение --------------------------------------------------------
    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._save_session()
        if getattr(self, "mini", None) is not None:
            try:
                self.mini.destroy()
            except Exception:  # noqa: BLE001
                pass
            self.mini = None
        try:
            self.root.after_cancel(self._logo_job)
        except Exception:  # noqa: BLE001
            pass
        self.stop_event.set()
        actions.release_all()
        try:
            shell.close_all()
        except Exception:  # noqa: BLE001
            pass
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._hotkeys.stop()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()


def _log_crash(exc_type, exc_value, exc_tb) -> None:
    """Пишет необработанные ошибки в crash.log рядом с приложением."""
    import traceback

    try:
        with open(Path(__file__).with_name("crash.log"), "a", encoding="utf-8") as fh:
            fh.write(
                f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"pid={os.getpid()} ===\n"
            )
            traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    sys.excepthook = _log_crash
    threading.excepthook = lambda args: _log_crash(  # type: ignore[attr-defined]
        args.exc_type, args.exc_value, args.exc_traceback
    )
    root = tk.Tk()
    app = JarvisApp(root)
    root.report_callback_exception = _log_crash
    if "--selftest" in sys.argv:
        root.after(1500, app.shutdown)
    root.protocol("WM_DELETE_WINDOW", app.shutdown)
    root.mainloop()


if __name__ == "__main__":
    main()

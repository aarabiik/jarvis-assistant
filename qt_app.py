#!/usr/bin/env python3
"""JARVIS — Qt-версия: нейросеть управляет мышью и клавиатурой.

Интерфейс на PySide6 (Qt): гладкие кнопки, скругления, тени, анимации.
Вся логика инструментов (мышь, клавиатура, консоль, файлы, GitHub) —
из модулей actions / llm / screen / shell / files / guard / uia / workspace.

Запуск:  python qt_app.py
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
from pathlib import Path
from typing import Any, Callable

import actions
import chatstore
import files
import github
import guard
import llm
import screen
import shell
import uia
import workspace
from llm import Agent, LLMError
from app import DEFAULT_SETTINGS

import qt_theme as theme

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QBrush, QFont, QAction
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QGraphicsDropShadowEffect,
    QDialog,
    QFormLayout,
    QComboBox,
    QCheckBox,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QFileDialog,
)

APP_NAME = "JARVIS"
VERSION = "3.0"
APP_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = APP_DIR / "settings.json"
POINTS_PATH = APP_DIR / "points.json"

_COORD_PARENS = re.compile(r"\(\s*(\d{1,4})\s*[,;]\s*(\d{1,4})\s*\)")
_COORD_X = re.compile(r"\bx\s*[=:]\s*(\d{1,4})", re.IGNORECASE)
_COORD_Y = re.compile(r"\by\s*[=:]\s*(\d{1,4})", re.IGNORECASE)


def _extract_coords(text: str) -> tuple[int, int] | None:
    """Достаёт координаты из ответа vision-модели."""
    body = text.split("описание снимка:", 1)[-1]
    width, height = actions.screen_size()
    for pattern in (_COORD_PARENS, _COORD_X, _COORD_Y):
        match = pattern.search(body)
        if match:
            try:
                x = int(match.group(1))
                y = int(match.group(2))
            except (IndexError, ValueError):
                continue
            if 0 <= x <= width and 0 <= y <= height:
                return x, y
    return None


def _screen_context(
    active: dict[str, Any] | None,
    under: str,
    cursor: tuple[int, int],
    region: tuple[int, int, int, int] | None,
    size: tuple[int, int],
) -> str:
    active_title = (active or {}).get("title", "—")
    active_proc = (active or {}).get("process", "?")
    region_text = (
        f"область снимка: {region}" if region else "снимок всего экрана"
    )
    return (
        f"экран {size[0]}x{size[1]}, курсор в ({cursor[0]}, {cursor[1]}), "
        f"под курсором: «{under}», активное окно: {active_title} "
        f"[{active_proc}], {region_text}"
    )


# --- пузырь сообщения ------------------------------------------------------
class Bubble(QFrame):
    """Сообщение в чате: пузырь с текстом и тенью."""

    def __init__(self, role: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("bubble")
        self.setStyleSheet(
            {
                "user": theme.bubble_user(),
                "assistant": theme.bubble_assistant(),
                "tool": theme.bubble_tool(),
                "result": theme.bubble_result(),
                "warn": theme.bubble_warn(),
                "error": theme.bubble_error(),
            }.get(role, theme.bubble_assistant())
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(label)

        # тень
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(shadow)

        # выравнивание
        if role == "user":
            self.setMaximumWidth(560)
        else:
            self.setMaximumWidth(720)


# --- звёздный фон ----------------------------------------------------------
class StarField(QWidget):
    """Фон чата: градиент, туманности и мерцающие звёзды."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._phase = 0.0
        self._stars: list[tuple[float, float, float, float]] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(90)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.1) % 6.283
        self.update()

    def _ensure_stars(self) -> None:
        if self._stars:
            return
        import random

        rng = random.Random(11)
        w = max(self.width(), 100)
        h = max(self.height(), 100)
        self._stars = [
            (rng.uniform(0, w), rng.uniform(0, h), rng.choice((1, 1, 2, 2, 3)),
             rng.uniform(0.2, 1.0))
            for _ in range(120)
        ]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        if w <= 1 or h <= 1:
            return

        # градиент фона
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(theme.BG))
        grad.setColorAt(1, QColor(theme.blend(theme.BG, theme.ACCENT, 0.10)))
        painter.fillRect(0, 0, w, h, grad)

        # туманности
        import random

        rng = random.Random(11)
        for _ in range(3):
            cx = rng.uniform(0, w)
            cy = rng.uniform(0, h)
            r = rng.uniform(w * 0.2, w * 0.45)
            tint = QColor(rng.choice((theme.ACCENT, theme.CYAN, theme.MAGENTA)))
            for i in range(10, 0, -1):
                t = i / 10
                tint.setAlpha(int(8 * (1 - t) + 3))
                painter.setBrush(tint)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(
                    int(cx - r * t), int(cy - r * t * 0.65),
                    int(r * t * 2), int(r * t * 1.3),
                )

        # звёзды
        self._ensure_stars()
        for x, y, size, bright in self._stars:
            shift = 0.5 + 0.5 * __import__("math").sin(self._phase * 1.3 + x)
            alpha = int((0.35 + 0.6 * shift) * 255 * bright)
            color = QColor(255, 255, 255, alpha)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(int(x), int(y), int(size), int(size))


class StarScrollArea(QScrollArea):
    """QScrollArea со звёздным фоном, нарисованным в paintEvent."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._phase = 0.0
        self._stars: list[tuple[float, float, float, float]] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(90)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.1) % 6.283
        self.viewport().update()

    def _ensure_stars(self) -> None:
        if self._stars:
            return
        import random

        rng = random.Random(11)
        w = max(self.viewport().width(), 100)
        h = max(self.viewport().height(), 100)
        self._stars = [
            (rng.uniform(0, w), rng.uniform(0, h), rng.choice((1, 1, 2, 2, 3)),
             rng.uniform(0.2, 1.0))
            for _ in range(120)
        ]

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.viewport().width()
        h = self.viewport().height()
        if w <= 1 or h <= 1:
            return

        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(theme.BG))
        grad.setColorAt(1, QColor(theme.blend(theme.BG, theme.ACCENT, 0.10)))
        painter.fillRect(0, 0, w, h, grad)

        import random

        rng = random.Random(11)
        for _ in range(3):
            cx = rng.uniform(0, w)
            cy = rng.uniform(0, h)
            r = rng.uniform(w * 0.2, w * 0.45)
            tint = QColor(rng.choice((theme.ACCENT, theme.CYAN, theme.MAGENTA)))
            for i in range(10, 0, -1):
                t = i / 10
                tint.setAlpha(int(8 * (1 - t) + 3))
                painter.setBrush(tint)
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(
                    int(cx - r * t), int(cy - r * t * 0.65),
                    int(r * t * 2), int(r * t * 1.3),
                )

        self._ensure_stars()
        for x, y, size, bright in self._stars:
            shift = 0.5 + 0.5 * __import__("math").sin(self._phase * 1.3 + x)
            alpha = int((0.35 + 0.6 * shift) * 255 * bright)
            color = QColor(255, 255, 255, alpha)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(int(x), int(y), int(size), int(size))

        painter.end()
        super().paintEvent(event)


# --- главное окно ----------------------------------------------------------
class JarvisApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = self._load_settings()
        self.points = actions.PointStore(POINTS_PATH)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        actions.set_stop_check(self.stop_event.is_set)
        self.journal = guard.Journal(APP_DIR / "journal.log")
        self.secrets = guard.load_secrets(APP_DIR)
        try:
            llm.ensure_memory(workspace.ROOT)
        except Exception:  # noqa: BLE001
            pass
        self.worker: threading.Thread | None = None
        self._turn_started = 0.0
        self._closed = False
        self.attachments: list[str] = []
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
        self._start_hotkeys()
        self._restore_last_session()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(150)

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
            QMessageBox.critical(self, APP_NAME, f"Не удалось сохранить настройки: {exc}")

    # -- интерфейс ---------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.resize(980, 760)
        self.setMinimumSize(720, 560)
        self.setStyleSheet(theme.qss())

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- шапка ---------------------------------------------------------
        header = QWidget()
        header.setObjectName("header")
        header.setFixedHeight(74)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(18, 10, 18, 10)
        h_layout.setSpacing(10)

        # логотип-реактор
        self.logo = ReactorLogo(44)
        h_layout.addWidget(self.logo)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        title_box.addWidget(brand)
        self.model_label = QLabel("")
        self.model_label.setObjectName("model")
        title_box.addWidget(self.model_label)
        h_layout.addLayout(title_box)
        h_layout.addStretch()

        def header_btn(text: str, slot: Callable, accent: bool = False) -> QPushButton:
            btn = QPushButton(text)
            if accent:
                btn.setObjectName("accent")
            btn.clicked.connect(slot)
            h_layout.addWidget(btn)
            return btn

        self.mini_btn = header_btn("◱ Мини", self.enter_mini_mode, accent=True)
        header_btn("■ Стоп", self.stop)
        header_btn("Новый", self.new_dialog)
        header_btn("История", self.open_history)
        header_btn("GitHub", self.open_github)
        header_btn("Файлы", self.open_files)
        header_btn("Точки", self.open_points)
        header_btn("⚙", self.open_settings)
        layout.addWidget(header)

        # -- чат -----------------------------------------------------------
        self.chat_scroll = StarScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setStyleSheet("background: transparent; border: none;")
        self.chat_scroll.setAutoFillBackground(False)
        self.chat_scroll.viewport().setAutoFillBackground(False)
        self.chat_scroll.viewport().setStyleSheet("background: transparent;")
        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background: transparent;")
        self.chat_container.setAutoFillBackground(False)
        self.chat_container.setAttribute(Qt.WA_TranslucentBackground)
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(18, 14, 18, 14)
        self.chat_layout.setSpacing(10)
        self.chat_layout.addStretch()
        self.chat_scroll.setWidget(self.chat_container)
        layout.addWidget(self.chat_scroll, 1)

        # -- поле ввода ----------------------------------------------------
        bottom = QWidget()
        bottom.setStyleSheet("background: transparent;")
        b_layout = QHBoxLayout(bottom)
        b_layout.setContentsMargins(18, 10, 18, 14)
        b_layout.setSpacing(10)

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Спроси JARVIS…")
        self.entry.returnPressed.connect(self.send)
        b_layout.addWidget(self.entry, 1)

        # кнопка прикрепления файла
        self.attach_btn = QPushButton("+")
        self.attach_btn.setObjectName("attach")
        self.attach_btn.setFixedSize(44, 44)
        self.attach_btn.setToolTip("Прикрепить файл")
        self.attach_btn.clicked.connect(self.attach_file)
        b_layout.addWidget(self.attach_btn)

        self.send_btn = QPushButton("Отправить")
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(110, 44)
        self.send_btn.clicked.connect(self.send)
        b_layout.addWidget(self.send_btn)
        # принудительно обновить геометрию, чтобы кнопки точно отрисовались
        self.send_btn.show()
        self.attach_btn.show()
        layout.addWidget(bottom)

        # -- статус --------------------------------------------------------
        self.status_label = QLabel("Готов.")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

        self._append("assistant",
            "Привет. Я JARVIS — управляю мышью, клавиатурой, консолью и файлами.\n\n"
            "◱ Мини — компактный режим поверх экрана (Ctrl+Shift+J).\n"
            "F8 — сохранить точку под курсором, F12 — стоп.")

    # -- логотип-реактор ---------------------------------------------------
    def _draw_logo(self) -> None:
        self.logo.update()

    # -- режимы ------------------------------------------------------------
    def enter_mini_mode(self) -> None:
        self.hide()
        self.mini = MiniOverlay(
            self,
            on_send=self._mini_send,
            on_expand=self.exit_mini_mode,
            on_close=self.exit_mini_mode,
        )
        self.mini.show()

    def exit_mini_mode(self) -> None:
        if getattr(self, "mini", None) is not None:
            self.mini.close()
            self.mini = None
        self.show()
        self.raise_()
        self.entry.setFocus()

    def _mini_send(self, text: str) -> None:
        self.entry.setText(text)
        self.send()

    # -- чат ---------------------------------------------------------------
    def _append(self, tag: str, text: str) -> None:
        text = (text or "").strip()
        for prefix in ("Вы: ", "JARVIS: ", "⚙ ", "↳ ", "! "):
            if text.startswith(prefix):
                text = text[len(prefix):]
        if not text:
            return
        bubble = Bubble(tag, text)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        QTimer.singleShot(0, self._scroll_bottom)

    def _scroll_bottom(self) -> None:
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _refresh_model_label(self) -> None:
        model = str(self.settings.get("model", "")) or "модель не выбрана"
        self.model_label.setText(model)

    # -- диалоги -----------------------------------------------------------
    def open_settings(self) -> None:
        dialog = SettingsDialog(self, self.settings)
        if dialog.exec():
            self.settings = dialog.collect()
            self._save_settings()
            self._refresh_model_label()

    def open_points(self) -> None:
        dialog = PointsDialog(self, self.points)
        dialog.exec()

    def open_files(self) -> None:
        dialog = FilesDialog(self)
        dialog.exec()

    def open_history(self) -> None:
        dialog = HistoryDialog(self, self.store)
        dialog.exec()
        if dialog.result == "new":
            self.new_dialog()
        elif dialog.result:
            data = self.store.load(dialog.result)
            if not data:
                self.status_label.setText("Не удалось открыть чат.")
                return
            self._save_session()
            self.session_id = str(data.get("id") or dialog.result)
            self.session_title = str(data.get("title") or "")
            self.agent.messages = list(data.get("messages") or [])
            self._render_history(self.agent.messages)
            self.status_label.setText(
                f"Открыт чат: {self.session_title or 'без названия'}"
            )

    def open_github(self) -> None:
        self._append("assistant", "Запускаю вход в GitHub…")
        self._github_login()

    def _github_login(self) -> None:
        def run() -> None:
            try:
                result = github.login()
                self.events.put(("result", result))
            except Exception as exc:  # noqa: BLE001
                self.events.put(("error", f"GitHub: {exc}"))

        threading.Thread(target=run, daemon=True).start()

    def new_dialog(self) -> None:
        self._save_session()
        self.agent.reset()
        self.session_id = chatstore.ChatStore.new_id()
        self.session_title = ""
        self._clear_chat()
        self._append("assistant", "Новый диалог. Чем помочь?")
        self.status_label.setText("Новый диалог.")

    def _clear_chat(self) -> None:
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    # -- история -----------------------------------------------------------
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
        self.status_label.setText(
            f"Загружен чат: {self.session_title or 'без названия'}"
        )

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
        self._clear_chat()
        for message in messages:
            role = message.get("role")
            text = self._content_text(message.get("content"))
            if role == "user":
                self._append("user", text)
            elif role == "assistant":
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    args = str(function.get("arguments", ""))[:80]
                    self._append("tool", f"⚙ {function.get('name', '')}({args})")
                if text:
                    self._append("assistant", text)
            elif role == "tool":
                self._append("result", text[:400])

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

    # -- управление --------------------------------------------------------
    def stop(self) -> None:
        self.stop_event.set()
        actions.release_all()
        try:
            shell.close_all()
        except Exception:  # noqa: BLE001
            pass
        self.status_label.setText("Остановлено. Жду завершения текущего шага…")

    def attach_file(self) -> None:
        """Выбрать файл и прикрепить его к следующему сообщению."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Прикрепить файлы", "", "Все файлы (*.*)"
        )
        if not paths:
            return
        for path in paths:
            if path not in self.attachments:
                self.attachments.append(path)
        names = ", ".join(Path(p).name for p in self.attachments)
        self.status_label.setText(f"Прикреплено: {names}")
        self._append("tool", f"📎 Прикреплено: {names}")

    def send(self) -> None:
        text = self.entry.text().strip()
        if not text and not self.attachments:
            return
        if self.worker is not None and self.worker.is_alive():
            self.status_label.setText("Подождите: ассистент ещё работает (F12 — стоп).")
            return
        # если есть прикреплённые файлы — добавим их содержимое к запросу
        if self.attachments:
            parts = [text] if text else []
            for path in self.attachments:
                try:
                    content = Path(path).read_text(encoding="utf-8", errors="replace")
                    parts.append(f"\n\n[Файл: {Path(path).name}]\n{content[:8000]}")
                except Exception as exc:  # noqa: BLE001
                    parts.append(f"\n\n[Файл: {Path(path).name} — не прочитан: {exc}]")
            text = "\n".join(parts)
            self.attachments.clear()
        self.entry.clear()
        self._append("user", text)
        self.stop_event.clear()
        self._turn_started = time.time()
        self.send_btn.setEnabled(False)
        self.status_label.setText("Думаю…")
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
                        if getattr(self, "mini", None) is None:
                            self.enter_mini_mode()
                        else:
                            self.exit_mini_mode()
                    continue
                if kind == "idle":
                    self.send_btn.setEnabled(True)
                    self.status_label.setText("Готов.")
                    self._save_session()
                    continue
                if kind == "assistant":
                    self._append("assistant", text)
                elif kind == "user":
                    self._append("user", text)
                elif kind == "tool":
                    self._append("tool", text)
                elif kind == "result":
                    self._append("result", text)
                elif kind == "warn":
                    self._append("warn", text)
                elif kind == "error":
                    self._append("error", text)
                elif kind == "status":
                    self.status_label.setText(text)
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
            self.status_label.setText(f"курсор: X={x}, Y={y}   |   {state}")
        except Exception:  # noqa: BLE001
            pass

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
        self._hotkeys.start()

    def _quick_point(self) -> None:
        name = f"точка {len(self.points.names()) + 1}"
        x, y = self.points.add(name)
        self._append("result", f"сохранена точка «{name}» = ({x}, {y})")

    # -- подтверждение -----------------------------------------------------
    def _confirm(
        self,
        name: str,
        args: dict[str, Any],
        reason: str = "",
        force: bool = False,
    ) -> bool:
        if not force and not self.settings.get("confirm", False):
            return True
        text = f"Разрешить действие «{name}»?\n\n{reason}\n\n{json.dumps(args, ensure_ascii=False)[:400]}"
        box = QMessageBox(self)
        box.setWindowTitle("Подтверждение")
        box.setText(text)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        return box.exec() == QMessageBox.Yes

    # -- окно и фокус ------------------------------------------------------
    def _root_hwnd(self) -> int:
        try:
            return int(self.winId())
        except Exception:  # noqa: BLE001
            return 0

    def _target_window(self) -> dict[str, Any] | None:
        try:
            return screen.active_window()
        except Exception:  # noqa: BLE001
            return None

    def _ui_sync(self, func: Callable[[], None], timeout: float = 2.0) -> None:
        """Выполнить функцию в GUI-потоке и дождаться результата."""
        done = threading.Event()
        result: list[Any] = []

        def wrapper() -> None:
            try:
                result.append(func())
            except Exception as exc:  # noqa: BLE001
                result.append(exc)
            finally:
                done.set()

        QTimer.singleShot(0, wrapper)
        done.wait(timeout)

    def _with_minimize(self, func: Callable[[], None]) -> None:
        """Спрятать своё окно на время действия, чтобы не мешать."""
        hidden = False
        try:
            if self.isVisible():
                self._ui_sync(self.hide)
                hidden = True
                time.sleep(0.15)
            func()
        finally:
            if hidden:
                self._ui_sync(self.show)
                self._ui_sync(self.raise_)

    def _click_at(
        self,
        x: int,
        y: int,
        button: str = "left",
        count: int = 1,
        modifiers: str = "",
    ) -> None:
        if modifiers:
            self._with_minimize(
                lambda: actions.click(x, y, button, count, modifiers)
            )
            return
        actions.click(x, y, button, count)

    # -- инструменты -------------------------------------------------------
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
                    int(x), int(y),
                    str(args.get("button", "left")),
                    int(args.get("count", 1)),
                    modifiers,
                )
                return "клик выполнен"
            if modifiers:
                self._with_minimize(
                    lambda: actions.click(
                        None, None,
                        str(args.get("button", "left")),
                        int(args.get("count", 1)),
                        modifiers,
                    )
                )
                return f"клик с зажатым {modifiers} выполнен"
            actions.click(
                None, None,
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
                    self._ui_sync(self.hide)
                    hidden = True
                    time.sleep(0.2)
                target = screen.hwnd_under_point(x1, y1)
                screen.focus_point(x1, y1)
                actions.drag(x1, y1, x2, y2, float(args.get("seconds", 0.4)))
            finally:
                if hidden:
                    self._ui_sync(self.show)
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
                x, y,
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
            "read_file", "write_file", "edit_file", "list_dir",
            "delete_file", "move_file", "grep", "glob",
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
                cmd, cwd,
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

            hidden = False
            prev_active = screen.active_window()
            if self.isVisible():
                self._ui_sync(self.hide)
                hidden = True
                time.sleep(0.25)

            def restore() -> None:
                nonlocal hidden
                if not hidden:
                    return
                hidden = False
                self._ui_sync(self.show)
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
                        active["rect"][0], active["rect"][1],
                        active["rect"][2], active["rect"][3],
                    )
                cursor = actions.cursor_position()
                under = screen.window_under_point(cursor[0], cursor[1])
                width, height = screen.screen_size()
                taskbar = any(
                    word in question.lower()
                    for word in (
                        "панел", "трей", "час", "врем", "иконк",
                        "пуск", "задач",
                    )
                )
                wants_coords = bool(args.get("coords", False)) or any(
                    word in question.lower()
                    for word in (
                        "координат", "где находится", "где кнопка", "где поле",
                        "нажми", "клик", "кнопк", "ссылк", "меню", "элемент",
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
    def closeEvent(self, event) -> None:
        self.shutdown()
        event.accept()

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._save_session()
        if getattr(self, "mini", None) is not None:
            try:
                self.mini.close()
            except Exception:  # noqa: BLE001
                pass
            self.mini = None
        self.stop_event.set()
        actions.release_all()
        try:
            shell.close_all()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._hotkeys.stop()
        except Exception:  # noqa: BLE001
            pass


# --- логотип-реактор -------------------------------------------------------
class ReactorLogo(QWidget):
    """Анимированный реактор: ядро, орбиты, спутники."""

    def __init__(self, size: int = 44) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.12) % 6.283
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = self.width() / 2
        phase = self._phase
        pulse = 0.5 + 0.5 * __import__("math").sin(phase)

        # свечение
        for i in range(6, 0, -1):
            r = 12 + i * 2.2
            color = QColor(theme.ACCENT)
            color.setAlpha(int(30 * (7 - i) / 7))
            painter.setPen(color)
            painter.drawEllipse(int(c - r), int(c - r), int(r * 2), int(r * 2))

        # орбиты
        for tilt, color in ((0.55, theme.ACCENT), (0.30, theme.CYAN)):
            rx, ry = 19, 19 * tilt
            pen = QColor(color)
            pen.setAlpha(int(120 + 80 * pulse))
            painter.setPen(pen)
            painter.drawEllipse(int(c - rx), int(c - ry), int(rx * 2), int(ry * 2))

        # спутники
        for spd, tilt, color in ((1.0, 0.55, theme.CYAN), (-1.6, 0.30, theme.MAGENTA)):
            angle = phase * spd
            sx = c + 19 * __import__("math").cos(angle)
            sy = c + 19 * tilt * __import__("math").sin(angle)
            painter.setBrush(QColor(color))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(int(sx - 2), int(sy - 2), 4, 4)

        # ядро
        core_r = 6 + 1.2 * pulse
        painter.setBrush(QColor(theme.ACCENT))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(int(c - core_r), int(c - core_r), int(core_r * 2), int(core_r * 2))
        painter.setBrush(QColor(theme.CYAN))
        painter.drawEllipse(int(c - 2.5), int(c - 2.5), 5, 5)


# --- мини-панель -----------------------------------------------------------
class MiniOverlay(QWidget):
    """Плавающая мини-панель поверх экрана."""

    def __init__(self, master, on_send, on_expand, on_close) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.on_send = on_send
        self.on_expand = on_expand
        self.on_close = on_close
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(480, 68)

        outer = QFrame()
        outer.setStyleSheet(f"""
            QFrame {{
                background-color: {theme.PANEL};
                border: 1px solid {theme.ACCENT};
                border-radius: 20px;
            }}
        """)
        outer.setGraphicsEffect(self._shadow())
        lay = QHBoxLayout(outer)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)

        self.logo = ReactorLogo(44)
        lay.addWidget(self.logo)

        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Спроси JARVIS…")
        self.entry.returnPressed.connect(self._submit)
        lay.addWidget(self.entry, 1)

        expand = QPushButton("⤢")
        expand.setFixedSize(36, 36)
        expand.clicked.connect(self.on_expand)
        lay.addWidget(expand)

        close = QPushButton("✕")
        close.setFixedSize(36, 36)
        close.clicked.connect(self.on_close)
        lay.addWidget(close)

        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.addWidget(outer)

        # позиция внизу справа
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.width() - 480 - 32, screen.height() - 68 - 80)
        self.entry.setFocus()

    def _shadow(self):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        return shadow

    def _submit(self) -> None:
        text = self.entry.text().strip()
        if not text:
            return
        self.entry.clear()
        self.on_send(text)


# --- диалоги ---------------------------------------------------------------
class SettingsDialog(QDialog):
    def __init__(self, parent, settings: dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(460)
        self.setStyleSheet(theme.qss())
        self._settings = settings
        self._vars: dict[str, Any] = {}

        form = QFormLayout(self)
        form.setSpacing(10)

        self._add_entry(form, "api_url", "API URL")
        self._add_entry(form, "api_key", "API ключ")
        self._add_entry(form, "model", "Модель")
        self._add_entry(form, "vision_model", "Vision-модель")

        self._confirm = QCheckBox("Спрашивать перед действием")
        self._confirm.setChecked(bool(settings.get("confirm", False)))
        form.addRow(self._confirm)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _add_entry(self, form, key: str, label: str) -> None:
        edit = QLineEdit(str(self._settings.get(key, "")))
        self._vars[key] = edit
        form.addRow(label, edit)

    def collect(self) -> dict[str, Any]:
        data = dict(self._settings)
        for key, widget in self._vars.items():
            data[key] = widget.text().strip()
        data["confirm"] = self._confirm.isChecked()
        return data


class PointsDialog(QDialog):
    def __init__(self, parent, points) -> None:
        super().__init__(parent)
        self.setWindowTitle("Точки экрана")
        self.setMinimumSize(360, 400)
        self.setStyleSheet(theme.qss())
        self.points = points
        layout = QVBoxLayout(self)

        self.list = QListWidget()
        layout.addWidget(self.list)

        row = QHBoxLayout()
        add_btn = QPushButton("Добавить под курсором")
        add_btn.clicked.connect(self._add_current)
        row.addWidget(add_btn)
        go_btn = QPushButton("Перейти")
        go_btn.clicked.connect(self._click_selected)
        row.addWidget(go_btn)
        del_btn = QPushButton("Удалить")
        del_btn.clicked.connect(self._remove)
        row.addWidget(del_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for name, (x, y) in sorted(self.points.as_dict().items()):
            self.list.addItem(QListWidgetItem(f"{name} = ({x}, {y})"))

    def _add_current(self) -> None:
        name = f"точка {len(self.points.names()) + 1}"
        self.points.add(name)
        self.refresh()

    def _selected_name(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.text().split(" = ")[0]

    def _click_selected(self) -> None:
        name = self._selected_name()
        if name:
            point = self.points.get(name)
            if point:
                actions.click(point[0], point[1])

    def _remove(self) -> None:
        name = self._selected_name()
        if name:
            self.points.remove(name)
            self.refresh()


class HistoryDialog(QDialog):
    def __init__(self, parent, store) -> None:
        super().__init__(parent)
        self.setWindowTitle("История чатов")
        self.setMinimumSize(420, 400)
        self.setStyleSheet(theme.qss())
        self.store = store
        self.result: Any = None
        layout = QVBoxLayout(self)

        self.list = QListWidget()
        layout.addWidget(self.list)

        row = QHBoxLayout()
        open_btn = QPushButton("Открыть")
        open_btn.clicked.connect(self._open)
        row.addWidget(open_btn)
        del_btn = QPushButton("Удалить")
        del_btn.clicked.connect(self._delete)
        row.addWidget(del_btn)
        new_btn = QPushButton("Новый чат")
        new_btn.clicked.connect(self._new)
        row.addWidget(new_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for session in self.store.list_sessions():
            self.list.addItem(
                QListWidgetItem(f"{session.get('title', 'без названия')} — {session['id']}")
            )

    def _selected(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return item.text().split(" — ")[-1]

    def _open(self) -> None:
        sid = self._selected()
        if sid:
            self.result = sid
            self.accept()

    def _delete(self) -> None:
        sid = self._selected()
        if sid:
            self.store.delete(sid)
            self.refresh()

    def _new(self) -> None:
        self.result = "new"
        self.accept()


class FilesDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Рабочая папка")
        self.setMinimumSize(480, 400)
        self.setStyleSheet(theme.qss())
        layout = QVBoxLayout(self)

        path_label = QLabel(str(workspace.folder()))
        path_label.setStyleSheet(f"color: {theme.MUTED};")
        layout.addWidget(path_label)

        self.list = QListWidget()
        layout.addWidget(self.list)

        row = QHBoxLayout()
        refresh_btn = QPushButton("Обновить")
        refresh_btn.clicked.connect(self.refresh)
        row.addWidget(refresh_btn)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for item in workspace.list_files():
            name = item.get("name", "")
            size = item.get("size", 0)
            mtime = item.get("mtime", 0)
            when = "только что"
            if mtime:
                age = time.time() - float(mtime)
                when = f"{int(age // 60)} мин назад" if age > 60 else "только что"
            self.list.addItem(QListWidgetItem(f"{name} ({size} Б, {when})"))


# --- запуск ----------------------------------------------------------------
def _log_crash(exc_type, exc_value, exc_tb) -> None:
    import traceback

    try:
        with open(Path(__file__).with_name("crash.log"), "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()} ===\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    sys.excepthook = _log_crash
    threading.excepthook = lambda args: _log_crash(  # type: ignore[attr-defined]
        args.exc_type, args.exc_value, args.exc_traceback
    )
    app = QApplication(sys.argv)
    window = JarvisApp()
    window.show()
    if "--selftest" in sys.argv:
        QTimer.singleShot(1500, window.shutdown)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

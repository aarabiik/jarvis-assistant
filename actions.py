"""Действия мышью и клавиатурой + память именованных координат.

Модуль не зависит от GUI: им пользуется и ассистент, и интерфейс.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlsplit

import requests
from pynput.keyboard import Controller as KeyboardController
from pynput.keyboard import Key, KeyCode
from pynput.mouse import Button, Controller as MouseController

# --------------------------------------------------------------------------
# Ввод Unicode-текста (Windows SendInput, без буфера обмена)
# --------------------------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTunion)]


KEY_ALIASES: dict[str, Any] = {
    "ctrl": Key.ctrl,
    "control": Key.ctrl,
    "alt": Key.alt,
    "shift": Key.shift,
    "win": Key.cmd,
    "cmd": Key.cmd,
    "super": Key.cmd,
    "enter": Key.enter,
    "return": Key.enter,
    "tab": Key.tab,
    "esc": Key.esc,
    "escape": Key.esc,
    "space": Key.space,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "del": Key.delete,
    "insert": Key.insert,
    "home": Key.home,
    "end": Key.end,
    "pageup": Key.page_up,
    "pagedown": Key.page_down,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "printscreen": Key.print_screen,
    "capslock": Key.caps_lock,
}
for _i in range(1, 13):
    KEY_ALIASES[f"f{_i}"] = getattr(Key, f"f{_i}")

# Кириллица → латиница: модель иногда пишет «ctrl+а» русской буквой.
RU_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "q", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "c", "ш": "s", "щ": "s", "ъ": "]",
    "ы": "y", "ь": "m", "э": "e", "ю": "u", "я": "z",
}

PUNCT_VK = {
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA,
    "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0,
}


def _key_for(part: str) -> Any:
    """Клавиша для одного элемента комбинации.

    Одиночные символы переводятся в виртуальные коды (VK). Иначе pynput
    отправляет их как Unicode-пакет, а он игнорирует Ctrl/Alt — получается
    «ctrl+a» = просто «a».
    """
    alias = KEY_ALIASES.get(part.lower())
    if alias is not None:
        return alias
    if len(part) == 1:
        char = part
        if char.lower() in RU_TO_LATIN:
            char = RU_TO_LATIN[char.lower()]
        if char.isalpha() or char.isdigit():
            return KeyCode.from_vk(ord(char.upper()))
        if char in PUNCT_VK:
            return KeyCode.from_vk(PUNCT_VK[char])
        return KeyCode.from_char(char)
    raise ValueError(f"неизвестная клавиша: {part}")

_mouse = MouseController()
_keyboard = KeyboardController()

# Что сейчас реально зажато — чтобы гарантированно отпустить при стопе/выходе.
_HELD_KEYS: list[Any] = []
_HELD_BUTTONS: list[Any] = []
_stop_check: Callable[[], bool] | None = None


def set_stop_check(func: Callable[[], bool] | None) -> None:
    """Приложение сообщает, как узнать про F12/стоп."""
    global _stop_check
    _stop_check = func


def _stop_requested() -> bool:
    try:
        return bool(_stop_check and _stop_check())
    except Exception:  # noqa: BLE001
        return False


def release_all() -> None:
    """Отпускает всё зажатое: F12, закрытие окна, ошибка."""
    while _HELD_KEYS:
        key = _HELD_KEYS.pop()
        try:
            _keyboard.release(key)
        except Exception:  # noqa: BLE001
            pass
    while _HELD_BUTTONS:
        button = _HELD_BUTTONS.pop()
        try:
            _mouse.release(button)
        except Exception:  # noqa: BLE001
            pass


def cursor_position() -> tuple[int, int]:
    return tuple(_mouse.position)  # type: ignore[return-value]


def screen_size() -> tuple[int, int]:
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    return 1920, 1080


def move_mouse(x: int, y: int) -> None:
    _mouse.position = (int(x), int(y))


def click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    count: int = 1,
    modifiers: str = "",
) -> None:
    """Клик мышью. modifiers — клавиши, зажатые на время клика (shift, ctrl)."""
    held = resolve_combo(modifiers) if modifiers.strip() else []
    for key in held:
        _keyboard.press(key)
        time.sleep(0.05)
    try:
        if x is not None and y is not None:
            _mouse.position = (int(x), int(y))
            time.sleep(0.05)
        _mouse.click(_mouse_button(button), max(1, min(3, int(count))))
    finally:
        for key in reversed(held):
            _keyboard.release(key)
            time.sleep(0.02)


def scroll(amount: int) -> None:
    _mouse.scroll(0, int(amount))


def resolve_combo(combo: str) -> list[Any]:
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("пустая комбинация")
    if len(parts) == 1:
        # Одиночный символ без модификаторов печатаем как символ:
        # так раскладка работает привычно.
        part = parts[0]
        alias = KEY_ALIASES.get(part.lower())
        if alias is not None:
            return [alias]
        if len(part) == 1:
            return [KeyCode.from_char(part)]
        raise ValueError(f"неизвестная клавиша: {part}")
    return [_key_for(part) for part in parts]


def press_combo(combo: str, gap: float = 0.06) -> None:
    """Нажимает комбинацию с паузами.

    Без пауз Windows часто «проглатывает» Win: сочетание Win+R успевает
    пройти быстрее, чем система зарегистрирует модификатор.
    """
    keys = resolve_combo(combo)
    for key in keys:
        _keyboard.press(key)
        time.sleep(gap)
    for key in reversed(keys):
        _keyboard.release(key)
        time.sleep(gap)


_MOUSE_BUTTONS = {"left": Button.left, "right": Button.right, "middle": Button.middle}


def _mouse_button(button: str) -> Any:
    try:
        return _MOUSE_BUTTONS[str(button).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"неизвестная кнопка мыши: {button}") from exc


def hold_keys(combo: str, seconds: float = 1.0) -> None:
    """Зажимает клавиши (или комбинацию) на seconds и отпускает.

    Нужно для Shift+клик, удержания стрелок в играх, Ctrl+колесо и т. п.
    Отпускает всегда — в том числе при ошибке или по F12.
    """
    keys = resolve_combo(combo)
    seconds = max(0.05, min(30.0, float(seconds)))
    pressed: list[Any] = []
    try:
        for key in keys:
            _keyboard.press(key)
            pressed.append(key)
            _HELD_KEYS.append(key)
            time.sleep(0.05)
        deadline = time.time() + seconds
        while time.time() < deadline:
            if _stop_requested():
                break
            time.sleep(min(0.05, max(0.0, deadline - time.time())))
    finally:
        for key in reversed(pressed):
            try:
                _keyboard.release(key)
            except Exception:  # noqa: BLE001
                pass
            if key in _HELD_KEYS:
                _HELD_KEYS.remove(key)
            time.sleep(0.02)


def mouse_down(button: str = "left") -> None:
    """Зажимает кнопку мыши и не отпускает (пара к mouse_up)."""
    pressed = _mouse_button(button)
    _mouse.press(pressed)
    _HELD_BUTTONS.append(pressed)


def mouse_up(button: str = "left") -> None:
    """Отпускает кнопку мыши, зажатую mouse_down."""
    pressed = _mouse_button(button)
    _mouse.release(pressed)
    if pressed in _HELD_BUTTONS:
        _HELD_BUTTONS.remove(pressed)


def mouse_hold(button: str = "left", seconds: float = 1.0) -> None:
    """Держит кнопку мыши зажатой указанное время и всегда отпускает."""
    seconds = max(0.05, min(30.0, float(seconds)))
    pressed = _mouse_button(button)
    _mouse.press(pressed)
    _HELD_BUTTONS.append(pressed)
    try:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if _stop_requested():
                break
            time.sleep(min(0.05, max(0.0, deadline - time.time())))
    finally:
        _mouse.release(pressed)
        if pressed in _HELD_BUTTONS:
            _HELD_BUTTONS.remove(pressed)


def drag(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    seconds: float = 0.4,
    button: str = "left",
) -> None:
    """Перетаскивает: зажимает кнопку в (x1, y1) и ведёт курсор в (x2, y2)."""
    seconds = max(0.1, min(10.0, float(seconds)))
    _mouse.position = (int(x1), int(y1))
    time.sleep(0.08)
    _mouse.press(_mouse_button(button))
    steps = max(6, int(seconds * 60))
    try:
        for index in range(1, steps + 1):
            ratio = index / steps
            _mouse.position = (
                int(round(x1 + (x2 - x1) * ratio)),
                int(round(y1 + (y2 - y1) * ratio)),
            )
            time.sleep(seconds / steps)
    finally:
        _mouse.release(_mouse_button(button))


def _send_unicode_char(char: str) -> None:
    units = char.encode("utf-16-le")
    for offset in range(0, len(units), 2):
        code = int.from_bytes(units[offset : offset + 2], "little")
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            packet = _INPUT(
                type=INPUT_KEYBOARD,
                u=_INPUTunion(ki=_KEYBDINPUT(0, code, flags, 0, None)),
            )
            ctypes.windll.user32.SendInput(
                1, ctypes.byref(packet), ctypes.sizeof(_INPUT)
            )


def type_text(text: str, delay: float = 0.01) -> None:
    """Печатает текст в активное окно."""
    if sys.platform != "win32":
        for char in text:
            _keyboard.type(char)
            if delay:
                time.sleep(delay)
        return

    for char in text:
        if char == "\n":
            _keyboard.press(Key.enter)
            _keyboard.release(Key.enter)
        elif char == "\t":
            _keyboard.press(Key.tab)
            _keyboard.release(Key.tab)
        else:
            _send_unicode_char(char)
        if delay:
            time.sleep(delay)


# --------------------------------------------------------------------------
# Запуск программ и сайтов (без win+r и без поиска в меню «Пуск»)
# --------------------------------------------------------------------------
APP_ALIASES = {
    "хром": "chrome",
    "chrome": "chrome",
    "гугл хром": "chrome",
    "google chrome": "chrome",
    "эдж": "msedge",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "опен код": "opencode",
    "опенкод": "opencode",
    "opencode": "opencode",
    "open code": "opencode",
    "блокнот": "notepad",
    "notepad": "notepad",
    "калькулятор": "calc",
    "calc": "calc",
    "calculator": "calc",
    "проводник": "explorer",
    "explorer": "explorer",
    "терминал": "wt",
    "командная строка": "cmd",
    "cmd": "cmd",
    "paint": "mspaint",
    "пейнт": "mspaint",
    "word": "winword",
    "ворд": "winword",
    "excel": "excel",
    "эксель": "excel",
    "telegram": "telegram",
    "телеграм": "telegram",
    "настройки": "ms-settings:",
    "settings": "ms-settings:",
}

SITE_ALIASES = {
    "джимини": "https://gemini.google.com",
    "джемини": "https://gemini.google.com",
    "gemini": "https://gemini.google.com",
    "гугл": "https://www.google.com",
    "google": "https://www.google.com",
    "яндекс": "https://ya.ru",
    "yandex": "https://ya.ru",
    "ютуб": "https://www.youtube.com",
    "youtube": "https://www.youtube.com",
    "чатгпт": "https://chatgpt.com",
    "chatgpt": "https://chatgpt.com",
    "вк": "https://vk.com",
    "vk": "https://vk.com",
    "телеграм веб": "https://web.telegram.org",
}


def normalize_url(value: str) -> str:
    """Приводит строку к URL: 'gemini.google.com' → 'https://gemini.google.com'."""
    text = value.strip().strip('"').strip("'")
    if not text:
        raise ValueError("пустой адрес")
    alias = SITE_ALIASES.get(text.lower())
    if alias:
        return alias
    if "://" in text:
        return text
    if text.lower().startswith(("localhost", "127.0.0.1")):
        return "http://" + text
    return "https://" + text


def _start_menu_shortcut(name: str) -> Path | None:
    """Ищет ярлык программы в меню «Пуск» по части названия."""
    import os

    needle = name.strip().lower()
    if not needle:
        return None
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", ""))
        / "Microsoft/Windows/Start Menu/Programs",
    ]
    for root in roots:
        if not root.exists():
            continue
        try:
            for shortcut in root.rglob("*.lnk"):
                if needle in shortcut.stem.lower():
                    return shortcut
        except OSError:
            continue
    return None


def _shell_execute(target: str) -> int:
    return int(
        ctypes.windll.shell32.ShellExecuteW(None, "open", target, None, None, 1)
    )


def open_url(url: str) -> str:
    """Открывает сайт в браузере по умолчанию. Возвращает итоговый адрес."""
    address = normalize_url(url)
    code = _shell_execute(address)
    if code <= 32:
        raise RuntimeError(
            f"Windows не смогла открыть адрес {address} (код {code})."
        )
    return address


INSTALLER_EXT = (
    ".exe",
    ".msi",
    ".msix",
    ".zip",
    ".7z",
    ".rar",
    ".dmg",
    ".pkg",
    ".apk",
    ".deb",
    ".appimage",
)


def _downloads_dir() -> Path:
    folder = Path(os.environ.get("USERPROFILE") or str(Path.home())) / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _safe_filename(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "download.bin"


def _unique_path(folder: Path, name: str) -> Path:
    candidate = folder / name
    index = 1
    while candidate.exists():
        candidate = folder / f"{Path(name).stem} ({index}){Path(name).suffix}"
        index += 1
    return candidate


def _installer_link(html: str, page_url: str) -> str | None:
    """Ищет на странице ссылку на установщик, предпочитая Windows."""
    best: str | None = None
    best_score = -99
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I):
        href = href.strip()
        if not href or href.startswith(("javascript:", "mailto:", "#", "data:")):
            continue
        absolute = urljoin(page_url, href)
        path = urlsplit(absolute).path.lower()
        if not path.endswith(INSTALLER_EXT):
            continue
        score = 0
        if any(word in path for word in ("win", "setup", "install", "x64", "x86")):
            score += 3
        if any(word in path for word in ("mac", "linux", "android", "ios", "arm64")):
            score -= 4
        if "download" in path:
            score += 1
        if score > best_score:
            best_score = score
            best = absolute
    return best


def _filename_for(response: requests.Response, filename: str) -> str:
    if filename.strip():
        return _safe_filename(filename)
    disposition = response.headers.get("Content-Disposition") or ""
    match = re.search(r'filename\*?=(?:UTF-8\'\'|")?([^";]+)', disposition, flags=re.I)
    if match:
        return _safe_filename(unquote(match.group(1).strip()))
    return _safe_filename(unquote(Path(urlsplit(response.url).path).name))


def _save_to_downloads(response: requests.Response, filename: str) -> str:
    target = _unique_path(_downloads_dir(), _filename_for(response, filename))
    size = 0
    with open(target, "wb") as fh:
        for chunk in response.iter_content(262144):
            if chunk:
                fh.write(chunk)
                size += len(chunk)
    if size == 0:
        try:
            target.unlink()
        except OSError:
            pass
        raise RuntimeError("сервер вернул пустой файл")
    return f"{target} ({size / 1048576:.1f} МБ)"


def download_file(url: str, filename: str = "") -> str:
    """Скачивает файл в «Загрузки».

    Если дана обычная страница (HTML), ищет на ней ссылку на установщик для
    Windows и качает уже её. Это в разы быстрее, чем кликать «Скачать»
    в браузере через снимки экрана.
    """
    address = normalize_url(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    try:
        with requests.Session() as session:
            with session.get(
                address,
                headers=headers,
                timeout=(10, 30),
                stream=True,
                allow_redirects=True,
            ) as response:
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}: {address}")
                ctype = (response.headers.get("Content-Type") or "").lower()
                if "text/html" not in ctype:
                    return _save_to_downloads(response, filename)

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(131072):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > 3_000_000:
                        break
                html = b"".join(chunks).decode("utf-8", "replace")
                link = _installer_link(html, response.url)

            if not link:
                raise RuntimeError(
                    "на странице не нашёл прямую ссылку на установщик — "
                    "дай прямую ссылку на файл"
                )
            with session.get(
                link,
                headers=headers,
                timeout=(10, 120),
                stream=True,
                allow_redirects=True,
            ) as file_response:
                if file_response.status_code >= 400:
                    raise RuntimeError(f"HTTP {file_response.status_code}: {link}")
                return _save_to_downloads(file_response, filename)
    except requests.RequestException as exc:
        raise RuntimeError(f"не скачалось: {exc}") from exc


def open_app(name: str) -> str:
    """Запускает программу по имени, ярлыку или пути. Возвращает, что запущено."""
    text = name.strip().strip('"')
    if not text:
        raise ValueError("пустое имя программы")
    exe = APP_ALIASES.get(text.lower(), text)

    code = _shell_execute(exe)
    if code > 32:
        return exe

    shortcut = _start_menu_shortcut(text)
    if shortcut is not None:
        code = _shell_execute(str(shortcut))
        if code > 32:
            return f"{text} ({shortcut.name})"

    # Русское название могло превратиться в английское имя программы —
    # ищем ярлык и по нему тоже.
    if exe != text:
        shortcut = _start_menu_shortcut(exe)
        if shortcut is not None:
            code = _shell_execute(str(shortcut))
            if code > 32:
                return f"{text} ({shortcut.name})"

    raise RuntimeError(
        f"не нашёл программу «{text}». Попробуй open_url для сайта "
        f"или уточни название у пользователя."
    )


# --------------------------------------------------------------------------
# Именованные точки
# --------------------------------------------------------------------------
class PointStore:
    """Хранит координаты вида {"кнопка входа": [812, 455]} в JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._points: dict[str, tuple[int, int]] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._points = {}
            return
        points: dict[str, tuple[int, int]] = {}
        for name, value in raw.items():
            try:
                points[str(name)] = (int(value[0]), int(value[1]))
            except (TypeError, ValueError, IndexError):
                continue
        self._points = points

    def save(self) -> None:
        payload = {name: list(xy) for name, xy in self._points.items()}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def add(self, name: str, x: int | None = None, y: int | None = None) -> tuple[int, int]:
        if x is None or y is None:
            x, y = cursor_position()
        self._points[name.strip()] = (int(x), int(y))
        self.save()
        return self._points[name.strip()]

    def remove(self, name: str) -> bool:
        if name in self._points:
            del self._points[name]
            self.save()
            return True
        return False

    def get(self, name: str) -> tuple[int, int] | None:
        return self._points.get(name)

    def names(self) -> list[str]:
        return sorted(self._points)

    def as_dict(self) -> dict[str, tuple[int, int]]:
        return dict(self._points)

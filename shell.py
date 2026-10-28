"""Консоль без окон: разовые команды и интерактивные сессии.

`run_command` запускает команду скрыто (CREATE_NO_WINDOW / SW_HIDE), поэтому
консольные окна не выскакивают «перед лицом». `start_session` поднимает
настоящий псевдотерминал (pywinpty) и разбирает экран через pyte — так видны
вопросы y/n, ssh, REPL и полноэкранные TUI (nano, htop, mc).
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

CREATE_NO_WINDOW = 0x08000000
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0

MAX_OUTPUT = 12000
HEAD = 7000

try:  # настоящее окно терминала без окна на экране
    import winpty  # type: ignore
except Exception:  # noqa: BLE001
    winpty = None

try:
    import pyte  # type: ignore
except Exception:  # noqa: BLE001
    pyte = None


def gh_dir() -> Path | None:
    """Где лежит gh.exe, если он не попал в PATH текущего процесса."""
    found = shutil.which("gh")
    if found:
        return Path(found).parent
    for candidate in (
        Path(r"C:\Program Files\GitHub CLI\gh.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "GitHubCLI" / "bin" / "gh.exe",
    ):
        if candidate.exists():
            return candidate.parent
    return None


def git_dir() -> Path | None:
    found = shutil.which("git")
    if found:
        return Path(found).parent
    for candidate in (
        Path(r"C:\Program Files\Git\cmd\git.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "cmd" / "git.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "MinGit" / "cmd" / "git.exe",
    ):
        if candidate.exists():
            return candidate.parent
    return None


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    for folder in (gh_dir(), git_dir()):
        if folder is not None:
            env["PATH"] = str(folder) + os.pathsep + env.get("PATH", "")
    if extra:
        env.update({key: str(value) for key, value in extra.items()})
    return env


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp866", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def truncate(text: str, limit: int = MAX_OUTPUT, head: int = HEAD) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    tail = max(0, limit - head)
    return (
        text[:head]
        + f"\n… обрезано {len(text) - limit} символов …\n"
        + (text[-tail:] if tail else "")
    )


def run_command(
    cmd: str,
    cwd: str | Path | None = None,
    timeout: float = 60.0,
    *,
    shell: str = "powershell",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Выполняет команду скрыто. Возвращает код, stdout, stderr, время."""
    text = str(cmd or "").strip()
    if not text:
        raise ValueError("пустая команда")
    workdir = Path(cwd) if cwd else Path.cwd()
    timeout = max(1.0, min(600.0, float(timeout or 60)))

    if shell == "cmd":
        args = ["cmd.exe", "/d", "/c", text]
    else:
        script = (
            "$ErrorActionPreference='Continue';"
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "$ProgressPreference='SilentlyContinue';" + text
        )
        args = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ]

    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE

    started = time.time()
    process = subprocess.Popen(
        args,
        cwd=str(workdir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env(env),
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        startupinfo=startupinfo,
    )
    timed_out = False
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        out, err = process.communicate(timeout=10)
    elapsed = time.time() - started
    return {
        "code": process.returncode,
        "stdout": truncate(_decode(out or b"")),
        "stderr": truncate(_decode(err or b"")),
        "cwd": str(workdir),
        "timeout": timed_out,
        "elapsed": elapsed,
    }


def short_path(path: str) -> str:
    """Короткое имя 8.3 без пробелов — нужно для командной строки PTY."""
    if os.name != "nt":
        return str(path)
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        length = ctypes.windll.kernel32.GetShortPathNameW(
            ctypes.c_wchar_p(str(path)), buffer, 1024
        )
        return buffer.value if length else str(path)
    except Exception:  # noqa: BLE001
        return str(path)


def _normalize_session_cmd(cmd: str) -> str:
    """Убирает кавычки вокруг exe: pywinpty не находит \"C:\\...\\app.exe\"."""
    text = str(cmd).strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            program = text[1:end]
            rest = text[end + 1 :].lstrip()
            return short_path(program) + ((" " + rest) if rest else "")
    return text


def run_program(
    args: list[str],
    cwd: str | Path | None = None,
    timeout: float = 90.0,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Запускает программу напрямую, без shell и без окна."""
    workdir = Path(cwd) if cwd else Path.cwd()
    timeout = max(1.0, min(600.0, float(timeout or 90)))
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
    started = time.time()
    process = subprocess.Popen(
        [str(item) for item in args],
        cwd=str(workdir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=child_env(env),
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        startupinfo=startupinfo,
    )
    timed_out = False
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            process.kill()
        except OSError:
            pass
        out, err = process.communicate(timeout=10)
    return {
        "code": process.returncode,
        "stdout": truncate(_decode(out or b"")),
        "stderr": truncate(_decode(err or b"")),
        "cwd": str(workdir),
        "timeout": timed_out,
        "elapsed": time.time() - started,
    }


def format_result(result: dict[str, Any]) -> str:
    parts = [f"exit={result['code']} ({result['elapsed']:.1f} с)"]
    if result.get("timeout"):
        parts.append("ТАЙМАУТ: процесс убит")
    if result.get("stdout", "").strip():
        parts.append("stdout:\n" + result["stdout"].rstrip())
    if result.get("stderr", "").strip():
        parts.append("stderr:\n" + result["stderr"].rstrip())
    if len(parts) == 1:
        parts.append("(пустой вывод)")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Интерактивные сессии
# --------------------------------------------------------------------------
class Session:
    """Одна интерактивная программа: PTY + экран pyte."""

    def __init__(self, cmd: str, cwd: str | Path, cols: int = 120, rows: int = 40) -> None:
        self.id = uuid.uuid4().hex[:8]
        self.cmd = cmd
        self.cwd = str(cwd)
        self.cols = cols
        self.rows = rows
        self.created = time.time()
        self.alive = True
        self.error = ""
        self.lock = threading.Lock()
        self.raw = bytearray()
        self.screen = pyte.Screen(cols, rows) if pyte else None
        self.stream = pyte.ByteStream(self.screen) if pyte and self.screen else None
        self.process: Any = None
        self._read_thread: threading.Thread | None = None
        self._spawn()

    # -- запуск ---------------------------------------------------------
    def _spawn(self) -> None:
        if winpty is not None:
            self.process = winpty.PtyProcess.spawn(
                _normalize_session_cmd(self.cmd),
                cwd=self.cwd,
                dimensions=(self.rows, self.cols),
                env=child_env(),
            )
        else:
            self.process = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=child_env(),
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.screen = None
            self.stream = None
        self._read_thread = threading.Thread(target=self._reader, daemon=True)
        self._read_thread.start()

    def _reader(self) -> None:
        while True:
            try:
                data = self.process.read(8192) if winpty is not None else self.process.stdout.read(8192)
            except Exception:  # noqa: BLE001 — EOF, закрытие, ошибка PTY
                break
            if not data:
                break
            if isinstance(data, str):
                data = data.encode("utf-8", "replace")
            with self.lock:
                self.raw.extend(data)
                if len(self.raw) > 2_000_000:
                    del self.raw[: len(self.raw) - 2_000_000]
                if self.stream is not None:
                    try:
                        self.stream.feed(data)
                    except Exception:  # noqa: BLE001
                        pass
        self.alive = False

    # -- работа ---------------------------------------------------------
    def send(self, text: str, submit: bool = True) -> None:
        payload = str(text).replace("\r\n", "\n").replace("\n", "\r")
        if submit:
            payload += "\r"
        if winpty is not None:
            self.process.write(payload)
        else:
            self.process.stdin.write(payload.encode("utf-8", "replace"))
            self.process.stdin.flush()

    def screen_text(self) -> str:
        if self.screen is None:
            return ""
        with self.lock:
            lines = list(self.screen.display)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(line.rstrip() for line in lines)

    def tail(self, limit: int = 4000) -> str:
        with self.lock:
            data = bytes(self.raw[-limit:])
        return _decode(data)

    def read(self, max_chars: int = 6000) -> str:
        header = f"сессия {self.id}: {self.cmd} ({'жива' if self.alive else 'завершена'})"
        screen = self.screen_text()
        if screen:
            body = screen
            if len(body) > max_chars:
                body = "…\n" + body[-max_chars:]
            return f"{header}\nэкран {self.cols}x{self.rows}:\n{body}"
        raw = self.tail(max_chars)
        return f"{header}\nвывод:\n{raw or '(пока ничего)'}"

    def close(self) -> None:
        try:
            if winpty is not None:
                self.process.terminate(force=True)
            elif self.process.poll() is None:
                self.process.kill()
        except Exception:  # noqa: BLE001
            pass
        self.alive = False


_SESSIONS: dict[str, Session] = {}
_SESSIONS_LOCK = threading.Lock()


def start_session(cmd: str, cwd: str | Path, cols: int = 120, rows: int = 40) -> Session:
    text = str(cmd or "").strip()
    if not text:
        raise ValueError("пустая команда для сессии")
    session = Session(text, cwd, cols, rows)
    with _SESSIONS_LOCK:
        _SESSIONS[session.id] = session
    return session


def get_session(session_id: str) -> Session | None:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(str(session_id).strip())


def close_session(session_id: str) -> bool:
    with _SESSIONS_LOCK:
        session = _SESSIONS.pop(str(session_id).strip(), None)
    if session is None:
        return False
    session.close()
    return True


def close_all() -> int:
    with _SESSIONS_LOCK:
        items = list(_SESSIONS.values())
        _SESSIONS.clear()
    for session in items:
        session.close()
    return len(items)


def list_sessions() -> list[dict[str, Any]]:
    with _SESSIONS_LOCK:
        items = list(_SESSIONS.values())
    return [
        {
            "id": item.id,
            "cmd": item.cmd,
            "alive": item.alive,
            "age": round(time.time() - item.created, 1),
        }
        for item in items
    ]


def wait_for(session_id: str, pattern: str, timeout: float = 20.0) -> bool:
    """Ждёт, пока на экране сессии появится регулярка."""
    session = get_session(session_id)
    if session is None:
        return False
    deadline = time.time() + max(0.5, timeout)
    regexp = re.compile(pattern, re.IGNORECASE)
    while time.time() < deadline:
        if regexp.search(session.screen_text() or session.tail(8000)):
            return True
        if not session.alive:
            return bool(regexp.search(session.tail(8000)))
        time.sleep(0.2)
    return False

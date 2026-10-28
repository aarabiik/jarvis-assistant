"""GitHub через официальный CLI (gh): вход, статус, команды.

Токен не попадает в переписку: gh хранит его в своём хранилище, а вывод
инструментов маскируется. Если gh ещё не установлен — подскажем команду.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

import shell

DEVICE_CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b")


def gh_exe() -> str | None:
    found = shutil.which("gh")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\GitHub CLI\gh.exe"),
        Path(r"C:\Program Files (x86)\GitHub CLI\gh.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "GitHubCLI" / "bin" / "gh.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def git_exe() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for candidate in (
        Path(r"C:\Program Files\Git\cmd\git.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "MinGit" / "cmd" / "git.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def installed() -> bool:
    return gh_exe() is not None


def _quote(value: str) -> str:
    text = str(value)
    if not text:
        return '""'
    if re.search(r"[\s\"'`$]", text):
        return '"' + text.replace('"', '`"') + '"'
    return text


def run(
    args: list[str] | str,
    cwd: str | Path | None = None,
    timeout: float = 90.0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Выполняет `gh ...` скрыто. args — список или строка."""
    exe = gh_exe()
    if exe is None:
        raise RuntimeError(
            "gh не установлен. Один раз выполни: winget install --id GitHub.cli -e"
        )
    if isinstance(args, str):
        parts = shlex.split(args)
    else:
        parts = [str(item) for item in args]
    command = "& " + _quote(exe) + (" " + " ".join(_quote(p) for p in parts) if parts else "")
    return shell.run_program(
        [exe, *parts], cwd=cwd or Path.cwd(), timeout=timeout, env=env
    )


def auth_status() -> tuple[bool, str]:
    if not installed():
        return False, "gh не установлен"
    try:
        result = run(["auth", "status"], timeout=25)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    text = (result.get("stdout", "") + "\n" + result.get("stderr", "")).strip()
    return result.get("code") == 0, text


def current_user() -> str:
    try:
        result = run(["api", "user", "--jq", ".login"], timeout=25)
    except Exception:  # noqa: BLE001
        return ""
    return (result.get("stdout") or "").strip()


def start_login() -> str:
    """Запускает вход в PTY-сессии и возвращает её id."""
    exe = shell.short_path(gh_exe() or "gh")
    session = shell.start_session(
        f'{exe} auth login --hostname github.com --git-protocol https --web',
        cwd=Path.cwd(),
        cols=120,
        rows=40,
    )
    return session.id


def login_code(session_id: str) -> str:
    """Достаёт одноразовый код из экрана сессии."""
    session = shell.get_session(session_id)
    if session is None:
        return ""
    text = session.read(8000)
    match = DEVICE_CODE.search(text)
    return match.group(0) if match else ""


def answer_git_prompt(session_id: str) -> None:
    """gh спрашивает «Authenticate Git with your GitHub credentials?»."""
    session = shell.get_session(session_id)
    if session is None:
        return
    text = session.read(8000).lower()
    if "y/n" in text or "authenticate git" in text:
        session.send("Y", submit=True)


def setup_git() -> str:
    try:
        result = run(["auth", "setup-git"], timeout=40)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return (result.get("stdout") or result.get("stderr") or "").strip()


def status_text() -> str:
    if not installed():
        return (
            "gh не установлен. Установка: winget install --id GitHub.cli -e, "
            "потом нажми кнопку «GitHub» в окне."
        )
    ok, text = auth_status()
    if not ok:
        return "GitHub не подключён.\n" + text
    user = current_user()
    return f"GitHub подключён{f' как {user}' if user else ''}.\n{text}"

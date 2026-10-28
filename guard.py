"""Разрешения, журнал и маскировка секретов.

Каждый вызов инструмента получает уровень:
  safe       — чтение и просмотр, выполняется сразу;
  changing   — меняет состояние, выполняется и пишется в журнал;
  dangerous  — только после явного подтверждения пользователя;
  forbidden  — не выполняется никогда (список в настройках + встроенный).

Классификация команд — не только по регуляркам, но и по контексту:
`;`, `&&`, `|`, `>`, `$(`, обращение за пределы рабочей папки повышают уровень.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEVELS = ("safe", "changing", "dangerous", "forbidden")
_RANK = {name: index for index, name in enumerate(LEVELS)}

DEFAULT_FORBIDDEN = [
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\bcipher\s+/w\b",
    r"\bvssadmin\s+delete\b",
    r"\bwbadmin\s+delete\b",
    r"\breg\s+delete\s+hklm\b",
    r"remove-item\s+.*-recurse.*\b[a-z]:\\\\?\s*$",
    r"\b(rd|rmdir)\s+/s\s+/q\s+[a-z]:\\\\?\s*$",
    r"\bdel\s+/[fsq]\s+.*[a-z]:\\\\?\s*$",
    r"set-mppreference\s+.*-disable",
    r"netsh\s+advfirewall\s+set",
    r"\bwevtutil\s+cl\b",
]

DANGEROUS = [
    (r"(^|[;&|]\s*)(sudo|runas)\b", "запуск с повышенными правами"),
    (r"start-process\b.*-verb\s+runas", "запуск от администратора"),
    (r"\b(format|mkfs|diskpart|bcdedit)\b", "работа с дисками/загрузчиком"),
    (r"\b(shutdown|stop-computer|restart-computer)\b", "выключение/перезагрузка"),
    (r"\b(reg\s+delete|reg\s+add)\b.*hklm", "изменение системного реестра"),
    (r"\b(netsh\s+advfirewall|set-mppreference)\b", "настройки безопасности"),
    (r"\b(cipher\s+/w|vssadmin|wbadmin)\b", "уничтожение/резервные копии"),
    (r"(invoke-expression|\biex\b)", "выполнение скачанного кода"),
    (r"\|\s*(bash|sh|powershell|pwsh|iex)\b", "передача вывода в интерпретатор"),
    (r"\b(remove-item|rm|del|erase|rd|rmdir)\b[^|;&]*-r(f|ec|ecurse)?\b", "рекурсивное удаление"),
    (r"\bgit\s+push\b.*--force", "принудительная отправка в репозиторий"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", "потеря незакоммиченных изменений"),
    (r"\b(curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm)\b[^|]*(-x\s*post|-method\s+post|--data|\s-d\s)", "отправка данных наружу"),
    (r"\bgh\s+(repo\s+delete|release\s+delete|secret\s+(set|delete)|auth\s+logout|api\s+.*-x\s+delete)", "удаление/секреты GitHub"),
]

SAFE_COMMANDS = [
    r"^(dir|ls|gci|get-childitem|pwd|cd|type|cat|more|head|tail|where|which)\b",
    r"^(whoami|hostname|systeminfo|ipconfig|get-process|tasklist|ver|date|time)\b",
    r"^git\s+(status|log|diff|show|branch|remote|rev-parse|config\s+--get)\b",
    r"^gh\s+(auth\s+status|repo\s+view|repo\s+list|pr\s+(list|view|diff|checks)|issue\s+(list|view)|release\s+(list|view)|run\s+(list|view)|search)\b",
    r"^gh\s+api\b(?!.*(-x\s+post|-x\s+patch|-x\s+put|-x\s+delete|--method\s+(post|patch|put|delete)))",
    r"^(python|py)\s+-c\s+[\"']print",
]

CHANGING_COMMANDS = [
    r"^git\s+(add|commit|checkout|switch|merge|rebase|pull|push|tag|stash|fetch|clone|init)\b",
    r"^gh\s+(pr|issue|release|repo|workflow|run|gist)\b",
    r"^(pip|python\s+-m\s+pip|npm|pnpm|yarn|winget|choco|scoop)\b",
    r"^(mkdir|md|new-item|copy|cp|move|mv|rename|ren|touch)\b",
    r"^(start|start-process|explorer|notepad|code)\b",
    r"^(curl|wget|invoke-webrequest|iwr)\b",
    r"^(python|py|node|npm)\b",
]

_META = re.compile(r"(;|&&|\|\||\$\(|`|\||>)")


@dataclass
class Verdict:
    level: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.level != "forbidden"


def _bump(level: str, steps: int = 1) -> str:
    index = min(len(LEVELS) - 1, _RANK[level] + steps)
    return LEVELS[index]


def base_dir(settings: dict[str, Any]) -> Path:
    raw = str(settings.get("work_dir") or "").strip()
    path = Path(os.path.expandvars(os.path.expanduser(raw))) if raw else Path.cwd()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path.resolve()


def resolve_path(value: str, settings: dict[str, Any]) -> tuple[Path, bool]:
    """Путь и признак «внутри рабочей папки»."""
    base = base_dir(settings)
    text = str(value or "").strip().strip('"')
    candidate = Path(os.path.expandvars(os.path.expanduser(text or ".")))
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    try:
        resolved.relative_to(base)
        return resolved, True
    except ValueError:
        return resolved, False


def outside_level(settings: dict[str, Any]) -> str:
    """Уровень для путей вне рабочей папки.

    По умолчанию (allow_outside = True) работа с другими папками разрешена
    без предупреждения — это обычный «changing». Если пользователь выключил
    allow_outside, такие действия снова требуют подтверждения.
    """
    return "changing" if settings.get("allow_outside", True) else "dangerous"


def _forbidden_patterns(settings: dict[str, Any]) -> list[str]:
    extra = settings.get("forbidden_patterns") or []
    if isinstance(extra, str):
        extra = [part.strip() for part in extra.split(";") if part.strip()]
    return DEFAULT_FORBIDDEN + [str(item) for item in extra if str(item).strip()]


def _outside_paths(command: str, settings: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"[A-Za-z]:\\[^\s\"'|;&<>]+|(?:^|\s)(/[^\s\"'|;&<>]+)", command):
        token = match.group(0).strip()
        if token.startswith("/") and len(token) < 2:
            continue
        if token.lower().startswith(("http://", "https://")):
            continue
        _, inside = resolve_path(token, settings)
        if not inside:
            found.append(token)
    if re.search(r"(^|[\\/])\.\.([\\/]|$)", command):
        found.append("..")
    return found


def classify_command(command: str, settings: dict[str, Any]) -> Verdict:
    text = str(command or "").strip()
    if not text:
        return Verdict("changing", "пустая команда")
    lower = text.lower()

    for pattern in _forbidden_patterns(settings):
        if re.search(pattern, text, re.IGNORECASE):
            return Verdict("forbidden", f"команда подходит под запрет: {pattern}")

    for pattern, reason in DANGEROUS:
        if re.search(pattern, lower, re.IGNORECASE):
            return Verdict("dangerous", reason)

    level = "changing"
    for pattern in SAFE_COMMANDS:
        if re.search(pattern, lower, re.IGNORECASE):
            level = "safe"
            break
    else:
        for pattern in CHANGING_COMMANDS:
            if re.search(pattern, lower, re.IGNORECASE):
                level = "changing"
                break

    outside = _outside_paths(text, settings)
    if outside and level != "dangerous":
        return Verdict(
            outside_level(settings),
            "обращение за пределы рабочей папки: " + ", ".join(outside[:3]),
        )

    if _META.search(text) and level == "safe":
        return Verdict("changing", "составная команда (|, >, ;, &&) — не чистый просмотр")

    reason = "" if level == "safe" else "изменяет состояние"
    return Verdict(level, reason)


def classify_gh(args: list[str] | str, settings: dict[str, Any]) -> Verdict:
    text = "gh " + (" ".join(args) if isinstance(args, list) else str(args))
    verdict = classify_command(text, settings)
    if verdict.level == "forbidden":
        return verdict
    if verdict.level == "dangerous":
        return verdict
    low = text.lower()
    if re.search(r"\bgh\s+api\b", low) and re.search(
        r"(-x\s*(post|patch|put|delete)|--method\s+(post|patch|put|delete))", low
    ):
        return Verdict("changing", "запись через GitHub API")
    return verdict


# Инструменты, которые ничего не меняют.
SAFE_TOOLS = {
    "get_state",
    "list_points",
    "list_windows",
    "look_screen",
    "get_screen_state",
    "list_files",
    "list_sessions",
    "read_session",
    "read_file",
    "list_dir",
    "wait",
}

CHANGING_TOOLS = {
    "click",
    "click_point",
    "move_mouse",
    "scroll",
    "type_text",
    "press_keys",
    "hold_keys",
    "mouse_hold",
    "mouse_down",
    "mouse_up",
    "drag",
    "save_point",
    "save_file",
    "write_file",
    "move_file",
    "delete_file",
    "open_url",
    "open_app",
    "download_file",
    "switch_window",
    "start_session",
    "send_input",
    "close_session",
    "find_and_click",
    "run_plan",
}


def classify_tool(name: str, args: dict[str, Any], settings: dict[str, Any]) -> Verdict:
    """Уровень для вызова инструмента."""
    try:
        if name == "run_command":
            verdict = classify_command(str(args.get("cmd", "")), settings)
            cwd = str(args.get("cwd") or "").strip()
            if cwd:
                _, inside = resolve_path(cwd, settings)
                if not inside and verdict.level != "dangerous":
                    return Verdict(
                        outside_level(settings), "рабочий каталог вне рабочей папки"
                    )
            return verdict
        if name in {"start_session", "send_input"}:
            cwd = str(args.get("cwd") or "").strip()
            if cwd:
                _, inside = resolve_path(cwd, settings)
                if not inside:
                    return Verdict(
                        outside_level(settings), "рабочий каталог вне рабочей папки"
                    )
            return Verdict("changing", "интерактивная сессия")
        if name == "gh":
            return classify_gh(args.get("args", ""), settings)
        if name in {
            "read_file",
            "list_dir",
            "grep",
            "glob",
            "write_file",
            "edit_file",
            "delete_file",
            "move_file",
        }:
            path = str(
                args.get("path")
                or args.get("src")
                or args.get("name")
                or "."
            )
            _, inside = resolve_path(path, settings)
            if name == "move_file":
                _, inside_dst = resolve_path(str(args.get("dst", "")), settings)
                inside = inside and inside_dst
            if not inside:
                return Verdict(outside_level(settings), "путь вне рабочей папки")
            if name in {"read_file", "list_dir", "grep", "glob"}:
                return Verdict("safe", "")
            if name == "delete_file":
                return Verdict("changing", "удаление файла")
            return Verdict("changing", "запись файла")
        if name == "run_plan":
            steps = args.get("steps") or []
            worst = Verdict("safe", "")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    inner = str(step.get("tool") or "")
                    inner_args = step.get("args") or {}
                    if not isinstance(inner_args, dict):
                        inner_args = {}
                    verdict = classify_tool(inner, inner_args, settings)
                    if _RANK[verdict.level] > _RANK[worst.level]:
                        worst = verdict
            return worst
    except Exception:  # noqa: BLE001
        return Verdict("changing", "не удалось классифицировать — считаем изменяющим")

    if name in SAFE_TOOLS:
        return Verdict("safe", "")
    if name in CHANGING_TOOLS:
        return Verdict("changing", "")
    return Verdict("changing", "неизвестный инструмент — считаем изменяющим")


# --------------------------------------------------------------------------
# Секреты и журнал
# --------------------------------------------------------------------------
_SECRET_PATTERNS = [
    (re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,})"), "***"),
    (re.compile(r"(github_pat_[A-Za-z0-9_]{20,})"), "***"),
    (re.compile(r"(munitoai_[A-Za-z0-9_\-]{10,})"), "***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{10,}"), r"\1***"),
    (re.compile(r"(?i)\b(token|password|passwd|api[_-]?key|authorization)\b\s*[:=]\s*\S+"), r"\1=***"),
    (re.compile(r"(?i)(--token[= ]+)\S+"), r"\1***"),
]


def mask(text: str) -> str:
    result = str(text)
    for pattern, repl in _SECRET_PATTERNS:
        result = pattern.sub(repl, result)
    return result


def load_secrets(app_dir: Path) -> dict[str, str]:
    path = app_dir / "secrets.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def secret_env(secrets: dict[str, str]) -> dict[str, str]:
    """Переменные для дочерних процессов: gh/git берут токен отсюда."""
    env: dict[str, str] = {}
    token = secrets.get("github_token") or secrets.get("GITHUB_TOKEN") or ""
    if token:
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL"):
        if secrets.get(key.lower()):
            env[key] = secrets[key.lower()]
    return env


class Journal:
    """Журнал вызовов: время, инструмент, аргументы, решение, результат."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def write(
        self,
        tool: str,
        args: dict[str, Any],
        level: str,
        decision: str,
        result: str = "",
        duration: float = 0.0,
    ) -> None:
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tool": tool,
            "level": level,
            "decision": decision,
            "args": mask(json.dumps(args, ensure_ascii=False))[:2000],
            "result": mask(str(result))[:1000],
            "duration": round(duration, 2),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

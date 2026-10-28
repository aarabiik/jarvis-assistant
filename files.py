"""Файловые операции для кода: чтение по строкам, точечные правки, поиск.

Как в OpenCode: модель меняет только нужный фрагмент (`edit_file`), ищет по
содержимому (`grep`) и по именам (`glob`), а не перезаписывает файлы целиком.
Пути проверяет guard (рабочая папка / подтверждение снаружи).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
}
MAX_READ_LINES = 2000
MAX_FILE_BYTES = 2_000_000
MAX_RESULTS = 200


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(2048)
    except OSError:
        return True


def read_text(path: Path, offset: int = 1, limit: int = 400) -> str:
    """Текст файла с номерами строк: offset (с 1), limit (до 2000)."""
    if not path.exists():
        raise FileNotFoundError(f"файла нет: {path}")
    if path.is_dir():
        raise IsADirectoryError("это папка — используй list_dir")
    if _is_binary(path):
        raise ValueError(f"похоже на двоичный файл: {path.name}")
    text = _read_text(path)
    lines = text.splitlines()
    total = len(lines)
    offset = max(1, int(offset or 1))
    limit = max(1, min(MAX_READ_LINES, int(limit or 400)))
    chunk = lines[offset - 1 : offset - 1 + limit]
    numbered = "\n".join(
        f"{number:>5}| {line}" for number, line in enumerate(chunk, start=offset)
    )
    if not numbered:
        return f"(пустой файл или offset {offset} за концом: всего {total} строк)"
    if total > len(chunk) + offset - 1 or offset > 1:
        numbered += (
            f"\n… показаны строки {offset}–{offset + len(chunk) - 1} "
            f"из {total}. Читай дальше с offset={offset + len(chunk)}"
        )
    return numbered


def write_text(path: Path, content: str, append: bool = False) -> str:
    if path.is_dir():
        raise IsADirectoryError(f"это папка: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        fh.write(str(content))
    return (
        f"{'дописал в' if append else 'записал'} {path} "
        f"({path.stat().st_size} байт)"
    )


def edit_text(
    path: Path, old: str, new: str, replace_all: bool = False
) -> str:
    """Заменяет old на new. old обязан встречаться один раз (если не replace_all)."""
    if not path.exists():
        raise FileNotFoundError(f"файла нет: {path}")
    if path.is_dir():
        raise IsADirectoryError("это папка — правки текста не применимы")
    if not str(old):
        raise ValueError("пустой old: нечего заменять")
    raw = path.read_bytes().decode("utf-8", "replace")
    crlf = "\r\n" in raw
    target_old = old if old in raw else old.replace("\n", "\r\n")
    target_new = new if old in raw else new.replace("\n", "\r\n")
    count = raw.count(target_old)
    if count == 0:
        raise ValueError(
            "фрагмент не найден. Прочитай файл (read_file) и возьми точный "
            "текст, включая отступы."
        )
    if count > 1 and not replace_all:
        numbers = []
        start = 0
        while len(numbers) < 5:
            index = raw.find(target_old, start)
            if index < 0:
                break
            numbers.append(raw[:index].count("\n") + 1)
            start = index + len(target_old)
        raise ValueError(
            f"фрагмент встречается {count} раз (строки {', '.join(map(str, numbers))}). "
            f"Добавь контекст или replace_all=true."
        )
    updated = raw.replace(target_old, target_new) if replace_all else raw.replace(
        target_old, target_new, 1
    )
    path.write_bytes(updated.encode("utf-8"))
    line = raw[: raw.find(target_old)].count("\n") + 1
    return (
        f"изменил {path}: строка {line}, заменено {count if replace_all else 1} "
        f"фрагмент(ов), файл {path.stat().st_size} байт"
    )


def _walk_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    result: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [
            name
            for name in dirs
            if name not in SKIP_DIRS and not name.startswith(".git")
        ]
        for name in names:
            candidate = Path(current) / name
            try:
                if candidate.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            result.append(candidate)
            if len(result) >= 20000:
                return result
    return result


def grep(
    root: Path,
    pattern: str,
    path_glob: str = "",
    max_results: int = 100,
    case_sensitive: bool = False,
) -> str:
    """Ищет регулярку в файлах под root. Возвращает file:line: текст."""
    if not str(pattern):
        raise ValueError("пустой шаблон поиска")
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regexp = re.compile(pattern, flags)
    except re.error:
        regexp = re.compile(re.escape(pattern), flags)
    max_results = max(1, min(MAX_RESULTS, int(max_results or 100)))
    matches: list[str] = []
    scanned = 0
    for file in _walk_files(root):
        if path_glob:
            try:
                if not file.match(path_glob):
                    continue
            except ValueError:
                pass
        if _is_binary(file):
            continue
        scanned += 1
        try:
            text = _read_text(file)
        except OSError:
            continue
        try:
            rel = file.relative_to(root) if root.is_dir() else file
        except ValueError:
            rel = file
        for number, line in enumerate(text.splitlines(), 1):
            if regexp.search(line):
                matches.append(f"{rel}:{number}: {line.strip()[:200]}")
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break
    if not matches:
        return f"ничего не найдено (просмотрено файлов: {scanned})"
    header = f"найдено {len(matches)} (файлов просмотрено {scanned}):"
    return header + "\n" + "\n".join(matches)


def glob_paths(root: Path, pattern: str, limit: int = 200) -> str:
    """Список файлов по маске (например **/*.py)."""
    text = str(pattern or "*").strip()
    limit = max(1, min(MAX_RESULTS, int(limit or 200)))
    found: list[str] = []
    for file in _walk_files(root):
        try:
            rel = file.relative_to(root) if root.is_dir() else file
        except ValueError:
            rel = file
        rel_text = str(rel).replace("\\", "/")
        if file.match(text) or Path(rel_text).match(text):
            found.append(rel_text)
            if len(found) >= limit:
                break
    if not found:
        return f"по маске {text!r} ничего не найдено"
    return f"найдено {len(found)}:\n" + "\n".join(sorted(found))

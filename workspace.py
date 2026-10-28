"""Рабочая папка ассистента: файлы и заметки, которые он пишет по ходу дела.

Модель складывает сюда промежуточные результаты (планы, найденные ссылки,
куски кода, списки шагов), чтобы не держать всё в переписке и не терять
контекст между запросами. Пользователь видит те же файлы в окне «Файлы».
"""

from __future__ import annotations

import time
from pathlib import Path

ROOT = Path(__file__).with_name("workspace")
MAX_READ = 20000


def folder() -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT


def _path(name: str) -> Path:
    text = str(name or "").strip().replace("\\", "/").strip("/")
    if not text:
        raise ValueError("пустое имя файла")
    parts = [part for part in text.split("/") if part not in ("", ".", "..")]
    if not parts:
        raise ValueError("некорректное имя файла")
    target = folder().joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def write_file(name: str, content: str, append: bool = False) -> str:
    """Сохраняет текст в рабочую папку. append=True — дописать в конец."""
    target = _path(name)
    mode = "a" if append else "w"
    with open(target, mode, encoding="utf-8", newline="\n") as fh:
        fh.write(str(content))
    size = target.stat().st_size
    action = "дописал в" if append else "сохранил"
    return f"{action} {target.name} ({size} байт)"


def read_file(name: str) -> str:
    """Читает файл из рабочей папки."""
    target = _path(name)
    if not target.exists():
        names = ", ".join(item["name"] for item in list_files()) or "пусто"
        raise FileNotFoundError(f"файла {name!r} нет. Есть: {names}")
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ:
        return text[:MAX_READ] + f"\n… (обрезано, всего {len(text)} символов)"
    return text or "(файл пуст)"


def list_files() -> list[dict[str, object]]:
    """Список файлов рабочей папки: имя, размер, время изменения."""
    base = folder()
    items: list[dict[str, object]] = []
    try:
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            items.append(
                {
                    "name": path.relative_to(base).as_posix(),
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
    except OSError:
        pass
    items.sort(key=lambda item: float(item["mtime"]), reverse=True)
    return items


def describe_files() -> str:
    """Текст для модели: что уже лежит в рабочей папке."""
    items = list_files()
    if not items:
        return "рабочая папка пуста"
    now = time.time()
    parts = []
    for item in items[:30]:
        age = now - float(item["mtime"])
        when = f"{int(age // 60)} мин назад" if age > 60 else "только что"
        parts.append(f"{item['name']} ({item['size']} Б, {when})")
    return "; ".join(parts)


def delete_file(name: str) -> str:
    target = _path(name)
    if not target.exists():
        raise FileNotFoundError(f"файла {name!r} нет")
    target.unlink()
    return f"удалил {target.name}"

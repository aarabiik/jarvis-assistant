"""Дерево доступности Windows: текст экрана и клики по элементам.

UI Automation видит кнопки, поля, меню и их имена — это точнее и дешевле,
чем снимок экрана. Координаты остаются запасным вариантом.
"""

from __future__ import annotations

import ctypes
import threading
from typing import Any

try:
    import uiautomation as auto  # type: ignore
except Exception:  # noqa: BLE001
    auto = None

try:
    auto.SetGlobalSearchTimeout(1.5)  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

# Что показываем модели: интерактивные элементы и текст.
_WANTED = {
    "ButtonControl",
    "EditControl",
    "MenuItemControl",
    "TabItemControl",
    "HyperlinkControl",
    "CheckBoxControl",
    "RadioButtonControl",
    "ComboBoxControl",
    "ListItemControl",
    "TreeItemControl",
    "SliderControl",
    "SpinnerControl",
    "DocumentControl",
    "TextControl",
    "WindowControl",
    "PaneControl",
    "ToolBarControl",
    "MenuBarControl",
    "StatusBarControl",
    "SplitButtonControl",
    "ImageControl",
}

_INDEX: dict[str, Any] = {}
_LAST_WINDOW = {"handle": 0, "title": ""}
_LOCAL = threading.local()


def _ensure_thread() -> None:
    """UI Automation требует COM, инициализированный в каждом потоке."""
    if auto is None or getattr(_LOCAL, "ready", False):
        return
    try:
        auto.InitializeUIAutomationInCurrentThread()  # type: ignore[union-attr]
        _LOCAL.ready = True
    except Exception:  # noqa: BLE001
        try:
            auto.UIAutomationInitializerInThread()  # type: ignore[union-attr]
            _LOCAL.ready = True
        except Exception:  # noqa: BLE001
            pass


def available() -> bool:
    return auto is not None


def _rect(control: Any) -> tuple[int, int, int, int] | None:
    try:
        box = control.BoundingRectangle
        left, top = int(box.left), int(box.top)
        width, height = int(box.right) - left, int(box.bottom) - top
        if width <= 0 or height <= 0:
            return None
        return left, top, width, height
    except Exception:  # noqa: BLE001
        return None


def _foreground() -> Any:
    try:
        return auto.GetForegroundControl()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return None


def _walk(control: Any, depth: int, out: list[dict[str, Any]], limit: int) -> None:
    if depth < 0 or len(out) >= limit:
        return
    try:
        children = control.GetChildren()
    except Exception:  # noqa: BLE001
        return
    for child in children:
        if len(out) >= limit:
            return
        try:
            name = str(child.Name or "").strip()
            kind = str(child.ControlTypeName or "")
            if kind not in _WANTED:
                continue
            rect = _rect(child)
            if rect is None:
                continue
            if kind == "TextControl" and len(name) < 2:
                continue
            value = ""
            try:
                pattern = child.GetValuePattern()
                value = str(pattern.Value or "").strip()
            except Exception:  # noqa: BLE001
                value = ""
            automation_id = str(getattr(child, "AutomationId", "") or "").strip()
            if not name and automation_id:
                name = automation_id
            item = {
                "control": child,
                "name": name,
                "kind": kind.replace("Control", "").lower(),
                "rect": rect,
                "value": value[:120],
                "enabled": bool(getattr(child, "IsEnabled", True)),
            }
            out.append(item)
            _walk(child, depth - 1, out, limit)
        except Exception:  # noqa: BLE001
            continue


def snapshot(
    handle: int | None = None, max_items: int = 120, depth: int = 5
) -> dict[str, Any]:
    """Список видимых элементов активного окна (или окна по handle)."""
    _ensure_thread()
    if auto is None:
        return {"available": False, "items": [], "window": "", "handle": 0}
    root = None
    if handle:
        try:
            root = auto.ControlFromHandle(int(handle))  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            root = None
    if root is None:
        root = _foreground()
    if root is None:
        return {"available": True, "items": [], "window": "", "handle": 0}

    title = ""
    hwnd = 0
    try:
        title = str(root.Name or "")
        hwnd = int(getattr(root, "NativeWindowHandle", 0) or 0)
    except Exception:  # noqa: BLE001
        pass

    items: list[dict[str, Any]] = []
    _walk(root, depth, items, max_items)

    # Одно окно могли открыть заново — сбрасываем старые id.
    _INDEX.clear()
    lines: list[str] = []
    for number, item in enumerate(items, 1):
        element_id = f"e{number}"
        _INDEX[element_id] = item["control"]
        left, top, width, height = item["rect"]
        label = f"{element_id} {item['kind']}"
        if item["name"]:
            label += f' "{item["name"][:90]}"'
        if item["value"]:
            label += f' value="{item["value"][:60]}"'
        label += f" ({left},{top} {width}x{height})"
        if not item["enabled"]:
            label += " [недоступен]"
        lines.append(label)
    _LAST_WINDOW["handle"] = hwnd
    _LAST_WINDOW["title"] = title
    return {
        "available": True,
        "window": title,
        "handle": hwnd,
        "items": items,
        "text": "\n".join(lines),
    }


def describe(handle: int | None = None, max_items: int = 120) -> str:
    data = snapshot(handle, max_items=max_items)
    if not data["available"]:
        return (
            "UI Automation недоступен (нет пакета uiautomation). "
            "Используй look_screen или find_and_click."
        )
    if not data["items"]:
        return f"в окне «{data['window'] or '—'}» не нашлось доступных элементов"
    return (
        f"активное окно: «{data['window']}»\n"
        f"элементы (клик по имени или id, например click_element(\"Скачать\")):\n"
        + data["text"]
    )


def _find(name: str, kind: str = "") -> Any:
    """Ищет элемент по имени в текущем окне (точное, потом частичное)."""
    _ensure_thread()
    needle = str(name or "").strip().lower()
    if not needle:
        return None
    root = _foreground()
    if root is None:
        return None
    items: list[dict[str, Any]] = []
    _walk(root, 6, items, 400)
    wanted_kind = str(kind or "").strip().lower()

    def matches(item: dict[str, Any]) -> bool:
        if wanted_kind and item["kind"] != wanted_kind:
            return False
        return needle in item["name"].lower()

    exact = [item for item in items if matches(item) and item["name"].lower() == needle]
    if exact:
        return exact[0]["control"]
    partial = [item for item in items if matches(item)]
    if partial:
        clickable = [
            item
            for item in partial
            if item["kind"] in {"button", "menuitem", "hyperlink", "tabitem", "listitem"}
        ]
        return (clickable or partial)[0]["control"]
    return None


def click_element(
    element_id: str = "", name: str = "", kind: str = ""
) -> str:
    """Кликает элемент по id из последнего снимка или по имени."""
    _ensure_thread()
    if auto is None:
        return "UI Automation недоступен: кликай по координатам (click/find_and_click)"
    control = None
    label = ""
    if element_id:
        control = _INDEX.get(str(element_id).strip())
        if control is None:
            return (
                f"элемент {element_id} не найден: сначала вызови "
                f"get_screen_state и бери id оттуда"
            )
        label = str(element_id)
    if control is None:
        control = _find(name, kind)
        label = f'«{name}»'
    if control is None:
        return f"элемент {label or name} не найден в активном окне"

    try:
        name_text = str(getattr(control, "Name", "") or "")
        kind_text = str(getattr(control, "ControlTypeName", "") or "")
    except Exception:  # noqa: BLE001
        name_text, kind_text = "", ""
    try:
        control.SetFocus()
    except Exception:  # noqa: BLE001
        pass
    clicked = False
    try:
        control.Click(simulateMove=False)
        clicked = True
    except Exception:  # noqa: BLE001
        try:
            control.Click()
            clicked = True
        except Exception:  # noqa: BLE001
            clicked = False
    if not clicked:
        # Запасной путь: центр элемента мышью.
        rect = _rect(control)
        if rect is not None:
            import actions

            left, top, width, height = rect
            actions.click(left + width // 2, top + height // 2)
            clicked = True
    if not clicked:
        return f"не удалось нажать {label or name}"
    return f"нажал {label or name} ({kind_text or 'элемент'} «{name_text}»)"


def focused_window_handle() -> int:
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        return 0

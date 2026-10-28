"""Экран: снимки (с маркером курсора), описание через vision-модель, окна."""

from __future__ import annotations

import base64
import ctypes
import io
import time
from ctypes import wintypes
from typing import Any, Callable

import requests

user32 = ctypes.windll.user32
GA_ROOT = 2
SW_RESTORE = 9
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77

# Одно HTTP-соединение на все запросы к vision-модели.
_SESSION = requests.Session()

# Модель по умолчанию для look_screen. glm-5.3-flash на этом провайдере
# уходит в reasoning и возвращает пустой content, поэтому по умолчанию qwen.
DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _process_name(hwnd: int) -> str:
    """Имя exe-файла окна: msedge.exe, chrome.exe, notepad.exe…"""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return buffer.value.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def list_windows() -> list[dict[str, Any]]:
    """Видимые окна с заголовками и прямоугольниками."""
    found: list[dict[str, Any]] = []
    active = user32.GetForegroundWindow()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title.strip():
            return True
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 60 or height < 40:
            return True
        found.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "process": _process_name(hwnd),
                "rect": [rect.left, rect.top, width, height],
                "active": int(hwnd) == int(active),
                "minimized": bool(user32.IsIconic(hwnd)),
            }
        )
        return True

    user32.EnumWindows(callback, 0)
    found.sort(key=lambda item: (not item["active"], item["minimized"], item["title"]))
    return found


def active_window() -> dict[str, Any] | None:
    for window in list_windows():
        if window["active"]:
            return window
    return None


def window_under_point(x: int, y: int) -> str:
    """Заголовок окна, которое находится под точкой экрана."""
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not hwnd:
        return ""
    root = user32.GetAncestor(hwnd, GA_ROOT)
    return _window_title(root or hwnd)


_SHELL_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow",
    "MultitaskingViewFrame",
    "TaskListThumbnailWnd",
}


def _window_class(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def hwnd_under_point(x: int, y: int) -> int:
    """HWND верхнего окна под точкой экрана (0 — если окна нет)."""
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not hwnd:
        return 0
    return int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)


def focus_point(x: int, y: int) -> str:
    """Активирует окно под точкой перед кликом.

    Иначе первый клик в неактивное окно Windows тратит на активацию,
    и кнопка не нажимается. Рабочий стол и панель задач не трогаем —
    им активация не нужна и только мешает.
    """
    hwnd = user32.WindowFromPoint(POINT(int(x), int(y)))
    if not hwnd:
        return ""
    root = user32.GetAncestor(hwnd, GA_ROOT)
    target = int(root or hwnd)
    if _window_class(target) in _SHELL_CLASSES:
        return _window_title(target)
    if int(user32.GetForegroundWindow()) != target:
        activate_hwnd(target)
        time.sleep(0.15)
    return _window_title(target)


def _find_window(title_part: str) -> dict[str, Any] | None:
    needle = title_part.strip().lower()
    if not needle:
        return None
    windows = list_windows()
    for window in windows:
        if needle in window["title"].lower():
            return window
    # Второй проход: по имени программы (edge → msedge.exe, chrome → chrome.exe).
    needle_exe = needle if needle.endswith(".exe") else needle + ".exe"
    for window in windows:
        process = str(window.get("process", ""))
        if process and (needle in process or needle_exe == process):
            return window
    return None


def activate_hwnd(hwnd: int) -> None:
    """Выводит конкретное окно на передний план."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.25)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    if int(user32.GetForegroundWindow()) != int(hwnd):
        # Обход блокировки переднего плана: имитируем Alt, затем повторяем.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)


def activate_window(title_part: str) -> dict[str, Any] | None:
    """Выводит окно на передний план по части заголовка. Возвращает окно или None."""
    window = _find_window(title_part)
    if window is None:
        return None
    activate_hwnd(window["hwnd"])
    return window


def screen_size() -> tuple[int, int]:
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def virtual_origin() -> tuple[int, int]:
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
    )


def _draw_cursor(image, x: int, y: int, origin: tuple[int, int], scale: float) -> bool:
    """Рисует яркий маркер в точке курсора (сам курсор на снимок не попадает)."""
    from PIL import ImageDraw

    cx = int((x - origin[0]) * scale)
    cy = int((y - origin[1]) * scale)
    if not (0 <= cx < image.width and 0 <= cy < image.height):
        return False

    draw = ImageDraw.Draw(image)
    radius = 16
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=(255, 255, 255),
        width=6,
    )
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=(255, 0, 0),
        width=3,
    )
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        start = (cx + dx * (radius + 3), cy + dy * (radius + 3))
        end = (cx + dx * (radius + 15), cy + dy * (radius + 15))
        draw.line([start, end], fill=(255, 255, 255), width=5)
        draw.line([start, end], fill=(255, 0, 0), width=2)
    return True


def _grid_font(size: int = 13):
    from PIL import ImageFont

    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _grid_label(draw, font, text: str, x: int, y: int, anchor: str = "la") -> None:
    """Подпись с чёрной подложкой. anchor — как в PIL: 'ma', 'lm', 'md'."""
    try:
        box = draw.textbbox((x, y), text, font=font, anchor=anchor)
    except (ValueError, TypeError):
        box = draw.textbbox((x, y), text, font=font)
        anchor = "la"
    draw.rectangle(
        [box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 2], fill=(0, 0, 0, 210)
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 0, 255), anchor=anchor)


def _draw_grid(image, origin: tuple[int, int], scale: float, step: int = 100,
               band: bool = False, minor: int = 0):
    """Сетка с подписями в реальных координатах экрана (пикселях)."""
    from PIL import Image, ImageDraw

    if band:
        extended = Image.new("RGB", (image.width, image.height + 20), (0, 0, 0))
        extended.paste(image, (0, 0))
        image = extended
        band_top = image.height - 20
    else:
        band_top = image.height

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _grid_font()
    origin_x, origin_y = origin
    line_color = (255, 0, 255, 120)
    minor_color = (255, 0, 255, 55)

    if minor:
        x = ((origin_x + minor - 1) // minor) * minor
        while True:
            px = (x - origin_x) * scale
            if px >= image.width:
                break
            if px >= 0 and x % step:
                draw.line([(px, 0), (px, band_top)], fill=minor_color, width=1)
            x += minor

        y = ((origin_y + minor - 1) // minor) * minor
        while True:
            py = (y - origin_y) * scale
            if py >= band_top:
                break
            if py >= 0 and y % step:
                draw.line([(0, py), (image.width, py)], fill=minor_color, width=1)
            y += minor

    x = ((origin_x + step - 1) // step) * step
    while True:
        px = (x - origin_x) * scale
        if px >= image.width:
            break
        if px >= 0:
            draw.line([(px, 0), (px, band_top)], fill=line_color, width=1)
            if band:
                _grid_label(draw, font, str(x), int(px), band_top + 2, "ma")
            else:
                _grid_label(draw, font, str(x), int(px), 2, "ma")
                _grid_label(draw, font, str(x), int(px), band_top - 3, "md")
        x += step

    y = ((origin_y + step - 1) // step) * step
    while True:
        py = (y - origin_y) * scale
        if py >= band_top:
            break
        if py >= 0:
            draw.line([(0, py), (image.width, py)], fill=line_color, width=1)
            _grid_label(draw, font, str(y), 2, int(py), "lm")
        y += step

    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def capture_b64(
    max_width: int = 1024,
    quality: int = 65,
    region: tuple[int, int, int, int] | None = None,
    cursor: tuple[int, int] | None = None,
    grid: int = 0,
    grid_band: bool = False,
) -> tuple[str, bool]:
    """Снимок экрана (или области) в base64 JPEG.

    Возвращает (base64, попал_ли_курсор_в_кадр).
    PIL не рисует курсор, поэтому маркер добавляется вручную.
    grid — шаг сетки в пикселях экрана (0 — без сетки).
    """
    from PIL import ImageGrab

    if region is None:
        image = ImageGrab.grab(all_screens=True)
        origin = virtual_origin()
    else:
        left, top, width, height = region
        image = ImageGrab.grab(
            bbox=(left, top, left + width, top + height), all_screens=True
        )
        origin = (left, top)

    scale = 1.0
    if image.width > max_width:
        scale = max_width / image.width
        image = image.resize((max_width, max(1, int(image.height * scale))))
        image = image.convert("RGB")
    else:
        image = image.convert("RGB")

    if grid:
        image = _draw_grid(
            image, origin, scale, grid, band=grid_band, minor=max(10, grid // 2)
        )

    cursor_visible = False
    if cursor is not None:
        cursor_visible = _draw_cursor(image, cursor[0], cursor[1], origin, scale)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii"), cursor_visible


VISION_PROMPT = """Ты — зрение компьютерного ассистента. Тебе дают снимок экрана Windows.
Опиши, что происходит, по пунктам:
1) какие окна и программы открыты (заголовки, активное окно);
2) что находится в активном окне: панели, кнопки, поля ввода, меню, вкладки;
3) панель задач внизу: кнопки приложений, значки области уведомлений, часы;
4) весь значимый видимый текст — перечисли дословно;
5) где примерно расположены ключевые элементы (слева сверху, по центру, справа
   снизу) и в каком окне.
Если на снимке есть маркер курсора (яркий красный кружок с перекрестием) —
скажи, над чем он находится.
Отвечай по-русски, кратко и структурированно, без вступлений."""


FOCUS_PROMPT = """Ты — зрение компьютерного ассистента. Тебе дают снимок экрана Windows
и конкретный вопрос. Ответь СНАЧАЛА на вопрос — первым абзацем, коротко и по делу.
Не начинай с общего обзора окон, если вопрос не об этом.

Что учитывать:
- маркер курсора — яркий красный кружок с белой обводкой и перекрестием. Если он
  есть на снимке, точно опиши, над чем он: кнопка, поле, строка списка, край окна;
- панель задач Windows внизу — кнопки приложений, значки справа, часы: смотри туда,
  если вопрос про панель задач, время или «какие программы запущены»;
- если вопрос про конкретное окно — говори только про него;
- если на снимке чего-то не видно (перекрыто, обрезано, слишком мелко) — так и скажи:
  «на снимке не видно».
- не выдумывай элементы, которых нет на картинке.

После прямого ответа — не больше 3 строк контекста (что за окно и что рядом).
Отвечай по-русски."""


COORDS_BLOCK = """
На снимке нанесена координатная сетка. Тонкие линии — каждые 50 пикселей экрана,
подписанные жёлтым — каждые 100. Подписи сверху, снизу и слева — это РЕАЛЬНЫЕ
координаты экрана в пикселях. Определяй положение элемента между линиями и
называй центр в формате «название — (x, y)».
Не предполагай, что элементы стоят через равные промежутки: отсчитывай от
ближайших подписанных линий слева и справа от каждого элемента. Точность
±30 пикселей достаточна. Не выдумывай координаты: если элемент не видно,
так и скажи."""


def describe(
    settings: dict[str, Any],
    question: str = "",
    image_b64: str | None = None,
    region: tuple[int, int, int, int] | None = None,
    cursor: tuple[int, int] | None = None,
    context: str = "",
    taskbar: bool = False,
    grid: bool = False,
    after_capture: Callable[[], None] | None = None,
) -> str:
    """Отправляет снимок экрана vision-модели и возвращает текстовый ответ.

    after_capture вызывается сразу после снятия экрана и до запроса к сети:
    приложение успевает вернуть своё окно и не висит спрятанным, пока
    vision-модель думает.
    """
    model = str(settings.get("vision_model") or DEFAULT_VISION_MODEL).strip()
    # У зрения может быть свой провайдер: например, основная модель через
    # OpenRouter, а картинки — через Munito. Пустое поле = общий Base URL.
    base_url = str(
        settings.get("vision_base_url") or settings.get("base_url", "")
    ).strip().rstrip("/")
    if not base_url:
        raise RuntimeError("Не задан Base URL в настройках.")

    cursor_visible = False
    grid_scale = 1.0
    if image_b64:
        image = image_b64
    else:
        if grid:
            # Сетка должна совпадать с пикселями экрана один к одному:
            # иначе модель называет координаты картинки, а не экрана.
            full_width = region[2] if region else screen_size()[0]
            grid_width = max(1024, min(int(full_width), 2200))
            grid_scale = grid_width / float(max(1, full_width))
        else:
            grid_width = 1024
        image, cursor_visible = capture_b64(
            max_width=grid_width,
            quality=75 if grid else 65,
            region=region,
            cursor=cursor,
            grid=100 if grid else 0,
        )

    strip = ""
    if taskbar:
        width, height = screen_size()
        strip, _ = capture_b64(
            max_width=1600,
            quality=80,
            region=(0, height - 64, width, 64),
            grid=100,
            grid_band=True,
        )

    # Экран уже снят — приложение может вернуть своё окно, не дожидаясь сети.
    if after_capture is not None:
        try:
            after_capture()
        except Exception:  # noqa: BLE001
            pass

    if question.strip():
        prompt = FOCUS_PROMPT
        if context.strip():
            prompt += "\n\nТочные данные с машины:\n" + context.strip()
        prompt += f"\n\nВопрос пользователя: {question.strip()}"
        token_steps = (900, 1500)
    else:
        prompt = VISION_PROMPT
        if context.strip():
            prompt += "\n\nТочные данные с машины:\n" + context.strip()
        token_steps = (1200, 2000)

    if cursor is not None and not cursor_visible:
        prompt += (
            "\n\nПримечание: курсор находится за пределами снятой области, "
            "маркера на снимке нет."
        )
    if grid:
        prompt += "\n" + COORDS_BLOCK
        if grid_scale >= 0.995:
            prompt += (
                "\nИзображение передано один к одному: 1 пиксель картинки = "
                "1 пиксель экрана."
            )
        else:
            factor = 1.0 / grid_scale
            prompt += (
                f"\nИзображение уменьшено: 1 пиксель картинки = {factor:.2f} "
                f"пикселя экрана. Чтобы получить координаты экрана, умножь "
                f"координаты с картинки на {factor:.2f}."
            )
        token_steps = (1200, 2000)
    if taskbar:
        prompt += (
            "\n\nВторое изображение — увеличенная полоса панели задач (низ экрана) "
            "с подписями координат X. По ней читай значки, часы и подписи; "
            "координату иконки бери по подписи под ней."
        )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    content.append(
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image}"},
        }
    )
    if taskbar:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{strip}"},
            }
        )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": token_steps[0],
        "temperature": 0.1,
    }
    effort = str(settings.get("reasoning_effort", "")).strip()
    if effort:
        payload["reasoning_effort"] = effort
    headers = {"Content-Type": "application/json"}
    key = str(
        settings.get("vision_api_key") or settings.get("api_key", "")
    ).strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    last_note = ""
    for max_tokens in token_steps:
        payload["max_tokens"] = max_tokens
        response = None
        for attempt in range(4):
            try:
                response = _SESSION.post(
                    base_url + "/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=(10, 90),
                )
            except requests.RequestException as exc:
                if attempt < 3:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise RuntimeError(
                    f"vision-модель {model}: сеть недоступна ({exc})."
                ) from exc
            if response.status_code < 400:
                break
            # Перегрузка провайдера и «body must be valid JSON» — временные.
            transient = response.status_code in (400, 429, 500, 502, 503, 504)
            if transient and attempt < 3:
                time.sleep(min(1.5 * (attempt + 1), 6.0))
                continue
            raise RuntimeError(
                f"vision-модель {model} вернула HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        data = response.json()
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = str(message.get("content") or "").strip()
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                f"Неожиданный ответ vision-модели: {str(data)[:200]}"
            ) from exc
        if content:
            return content
        # Некоторые модели (glm, qwen) кладут весь ответ в reasoning,
        # а content оставляют пустым — это тоже валидный результат.
        reasoning = str(message.get("reasoning") or "").strip()
        if reasoning:
            return reasoning
        last_note = (
            "Модель израсходовала лимит на рассуждения и не вернула текст "
            f"(finish_reason={finish}). Попробуйте другую vision-модель "
            "или меньший размер экрана."
        )
    raise RuntimeError(last_note or "vision-модель вернула пустой ответ.")

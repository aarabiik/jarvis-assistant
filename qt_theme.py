"""Qt-тема JARVIS: космическая палитра и QSS-стили.

Qt Style Sheets дают настоящие скругления, градиенты, тени и плавные
эффекты — то, чего не умеет Tkinter.
"""

# --- палитра ---------------------------------------------------------------
BG = "#04050c"
PANEL = "#0a0d1a"
CARD = "#0f1424"
CHAT_BG = "#05070f"
INPUT_BG = "#0b1020"
BORDER = "#1a2138"
ACCENT = "#8b7bff"
ACCENT_HOVER = "#a99cff"
ACCENT_DIM = "#2b2a5e"
CYAN = "#4fd8ff"
MAGENTA = "#e879f9"
TEXT = "#eef1ff"
TEXT_DIM = "#a9b3d6"
MUTED = "#6a749b"
WARN = "#ffc857"
ERROR = "#ff6b8a"
USER_BUBBLE = "#3a2f7a"
USER_BUBBLE_TOP = "#4a3a9e"

FONT = "Segoe UI"
FONT_MONO = "Consolas"


def blend(color_a: str, color_b: str, t: float) -> str:
    """Смешивает два цвета #rrggbb: t=0 → a, t=1 → b."""
    t = max(0.0, min(1.0, t))
    a = [int(color_a[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def qss() -> str:
    """Глобальные стили приложения."""
    return f"""
    * {{
        font-family: "{FONT}";
        color: {TEXT};
    }}
    QMainWindow {{
        background-color: {BG};
    }}
    QWidget#header {{
        background-color: {PANEL};
        border-bottom: 1px solid {BORDER};
    }}
    QLabel#brand {{
        color: {TEXT};
        font-size: 20px;
        font-weight: 600;
    }}
    QLabel#model {{
        color: {MUTED};
        font-size: 11px;
    }}
    QLabel#status {{
        color: {MUTED};
        font-size: 11px;
        background-color: {PANEL};
        border-top: 1px solid {BORDER};
        padding: 6px 16px;
    }}
    QPushButton {{
        background-color: {CARD};
        color: {TEXT_DIM};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 7px 14px;
        font-size: 12px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {ACCENT_DIM};
        color: {ACCENT_HOVER};
        border-color: {ACCENT};
    }}
    QPushButton:pressed {{
        background-color: {ACCENT};
        color: #07080f;
    }}
    QPushButton#accent {{
        background-color: {ACCENT};
        color: #07080f;
        border: none;
        border-radius: 8px;
        font-weight: 600;
    }}
    QPushButton#accent:hover {{
        background-color: {ACCENT_HOVER};
    }}
    QPushButton#send {{
        background-color: {CYAN};
        color: #04050c;
        border: none;
        border-radius: 10px;
        font-size: 13px;
        font-weight: 600;
    }}
    QPushButton#send:hover {{
        background-color: #7fe6ff;
    }}
    QPushButton#send:hover {{
        background-color: {ACCENT_HOVER};
    }}
    QPushButton#send:disabled {{
        background-color: {ACCENT_DIM};
        color: {MUTED};
    }}
    QPushButton#attach {{
        background-color: {CARD};
        color: {TEXT_DIM};
        border: 1px solid {BORDER};
        border-radius: 22px;
        font-size: 16px;
    }}
    QPushButton#attach:hover {{
        background-color: {ACCENT_DIM};
        color: {ACCENT_HOVER};
        border-color: {ACCENT};
    }}
    QLineEdit {{
        background-color: {INPUT_BG};
        color: {TEXT};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 14px;
        font-size: 13px;
        selection-background-color: {ACCENT_DIM};
    }}
    QLineEdit:focus {{
        border-color: {ACCENT};
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {ACCENT_DIM};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {ACCENT};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QToolTip {{
        background-color: {CARD};
        color: {TEXT};
        border: 1px solid {ACCENT};
        padding: 4px;
    }}
    QCheckBox {{
        color: {TEXT};
        font-size: 13px;
        spacing: 8px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid {BORDER};
        border-radius: 4px;
        background-color: {INPUT_BG};
    }}
    QCheckBox::indicator:checked {{
        background-color: {ACCENT};
        border-color: {ACCENT};
    }}
    QLabel {{
        color: {TEXT};
    }}
    """


def bubble_user() -> str:
    """Стиль пузыря сообщения пользователя."""
    return f"""
    QFrame {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {USER_BUBBLE_TOP}, stop:1 {USER_BUBBLE});
        border-radius: 16px;
        border: 1px solid {ACCENT};
    }}
    QLabel {{
        background: transparent;
        color: #ffffff;
        font-size: 13px;
    }}
    """


def bubble_assistant() -> str:
    """Стиль пузыря сообщения ассистента."""
    return f"""
    QFrame {{
        background-color: {CARD};
        border-radius: 16px;
        border: 1px solid {BORDER};
    }}
    QLabel {{
        background: transparent;
        color: {TEXT};
        font-size: 13px;
    }}
    """


def bubble_tool() -> str:
    return f"""
    QFrame {{
        background-color: {CHAT_BG};
        border-radius: 10px;
        border-left: 3px solid {ACCENT_DIM};
    }}
    QLabel {{
        background: transparent;
        color: {TEXT_DIM};
        font-family: "{FONT_MONO}";
        font-size: 12px;
    }}
    """


def bubble_result() -> str:
    return f"""
    QFrame {{
        background-color: {CHAT_BG};
        border-radius: 10px;
        border-left: 3px solid {CYAN};
    }}
    QLabel {{
        background: transparent;
        color: {MUTED};
        font-family: "{FONT_MONO}";
        font-size: 12px;
    }}
    """


def bubble_warn() -> str:
    return f"""
    QFrame {{
        background-color: {CHAT_BG};
        border-radius: 10px;
        border-left: 3px solid {WARN};
    }}
    QLabel {{
        background: transparent;
        color: {WARN};
        font-size: 12px;
    }}
    """


def bubble_error() -> str:
    return f"""
    QFrame {{
        background-color: {CHAT_BG};
        border-radius: 10px;
        border-left: 3px solid {ERROR};
    }}
    QLabel {{
        background: transparent;
        color: {ERROR};
        font-size: 12px;
    }}
    """

"""Быстрый предпросмотр космического чата (не входит в приложение)."""

import tkinter as tk

import chatview
import theme

root = tk.Tk()
root.title("JARVIS preview")
root.geometry("900x700")
root.configure(bg=theme.BG)
theme.apply(root)

view = chatview.ChatView(root)
view.pack(fill="both", expand=True, padx=14, pady=14)

view.add("assistant", "Привет. Я JARVIS — управляю мышью, клавиатурой, консолью и файлами.")
view.add("user", "Открой блокнот и напиши привет")
view.add("tool", 'open_app({"name": "блокнот"})')
view.add("result", "открыл блокнот за 1.2 с")
view.add("assistant", "Готово. Блокнот открыт, текст напечатан. Что дальше?")
view.add("warn", "Работа вне рабочей папки разрешена — будь осторожен.")
view.add("error", "Ошибка API: таймаут соединения.")
view.set_busy(True)

root.mainloop()

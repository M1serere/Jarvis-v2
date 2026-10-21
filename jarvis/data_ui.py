from __future__ import annotations

from collections.abc import Callable
import logging
import sqlite3
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from .personal import LocalCommand, PersonalStore, display_time


class DataWindow:
    """Просмотр сохранённых записей и выбор программ через обычные диалоги."""
    def __init__(self, root: tk.Tk, store: PersonalStore, execute: Callable[[LocalCommand], bool], logger: logging.Logger):
        self.store = store
        self.execute = execute
        self.logger = logger
        self.window = tk.Toplevel(root)
        self.window.title("Мои данные · Джарвис")
        self.window.geometry("850x540")
        self.window.minsize(670, 420)
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=12)
        self.frames = {}
        self.trees = {}
        self.rows = {}
        for key, label, columns in (
            ("notes", "Заметки", ("№", "Создана", "Текст")),
            ("tasks", "Дела", ("№", "Состояние", "Текст")),
            ("alarms", "Таймеры и напоминания", ("№", "Тип", "Когда", "Состояние", "Текст")),
            ("programs", "Программы", ("Название", "Файл")),
        ):
            frame = ttk.Frame(self.notebook, padding=10)
            self.frames[key] = frame
            self.notebook.add(frame, text=label)
            tree_frame = ttk.Frame(frame)
            tree_frame.pack(fill="both", expand=True)
            tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse", height=10)
            scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            tree.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
            for column in columns:
                tree.heading(column, text=column)
                width = 45 if column == "№" else 155 if column in {"Когда", "Создана"} else 105
                tree.column(column, width=width, minwidth=40, stretch=column in {"Текст", "Файл", "Название"})
            tree.bind("<<TreeviewSelect>>", lambda event, selected=key: self._detail(selected))
            self.trees[key] = tree
            bar = ttk.Frame(frame)
            bar.pack(fill="x", pady=(8, 0))
            if key == "notes":
                ttk.Button(bar, text="Новая заметка", command=lambda: self._text_editor("Новая заметка", "add_note")).pack(side="left")
                ttk.Button(bar, text="Прочитать", command=lambda: self._selected_command("notes", "read_note")).pack(side="left", padx=6)
            elif key == "tasks":
                ttk.Button(bar, text="Добавить дело", command=lambda: self._text_editor("Новое дело", "add_task")).pack(side="left")
                ttk.Button(bar, text="Отметить выполненным", command=lambda: self._selected_command("tasks", "complete_task")).pack(side="left", padx=6)
            elif key == "alarms":
                ttk.Button(bar, text="Новый таймер", command=lambda: self._ask_alarm("add_timer", "Например: 5 минут чай")).pack(side="left")
                ttk.Button(bar, text="Напоминание", command=lambda: self._ask_alarm("add_reminder", "Например: завтра в 09:30 позвонить")).pack(side="left", padx=6)
                ttk.Button(bar, text="Отменить выбранное", command=lambda: self._selected_command("alarms", "cancel_alarm")).pack(side="left")
            else:
                ttk.Button(bar, text="Добавить программу", command=self.add_program).pack(side="left")
                ttk.Button(bar, text="Убрать из списка", command=self.remove_program).pack(side="left", padx=6)
        self.detail = ScrolledText(self.window, height=6, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.detail.pack(fill="x", padx=12, pady=(0, 12))
        self.notebook.bind("<<NotebookTabChanged>>", lambda event: self._detail(self.current_tab()))
        self.refresh()

    def current_tab(self) -> str:
        return next(key for key, frame in self.frames.items() if str(frame) == self.notebook.select())

    def select(self, tab: str) -> None:
        self.notebook.select(self.frames[tab])
        self.window.lift()

    def refresh(self) -> None:
        content = {"notes": self.store.notes(), "tasks": self.store.tasks(),
                   "alarms": self.store.alarms(), "programs": self.store.programs()}
        for key, rows in content.items():
            tree = self.trees[key]
            selection = tree.selection()
            tree.delete(*tree.get_children())
            self.rows[key] = {}
            for row in rows:
                identifier = row["alias"] if key == "programs" else str(row["id"])
                self.rows[key][identifier] = row
                preview = " ".join(row["text"].split())[:300] if key != "programs" else ""
                if key == "notes":
                    values = (identifier, display_time(row["created_at"]), preview)
                elif key == "tasks":
                    values = (identifier, "Выполнено" if row["done"] else "В плане", preview)
                elif key == "alarms":
                    values = (identifier, "Таймер" if row["kind"] == "timer" else "Напоминание",
                              display_time(row["due_at"]), {"pending": "Ожидает", "delivered": "Показано", "cancelled": "Отменено"}[row["status"]], preview)
                else:
                    values = (row["name"], row["path"])
                tree.insert("", "end", iid=identifier, values=values)
            if selection and tree.exists(selection[0]):
                tree.selection_set(selection)
        self._detail(self.current_tab())

    def _detail(self, key: str) -> None:
        selection = self.trees[key].selection()
        row = self.rows.get(key, {}).get(selection[0]) if selection else None
        text = row["path" if key == "programs" else "text"] if row else "Выбери строку, чтобы увидеть полный текст."
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def _selected_command(self, key: str, operation: str) -> None:
        selection = self.trees[key].selection()
        if selection:
            self.execute(LocalCommand(operation, selection[0]))

    def _text_editor(self, title: str, operation: str) -> None:
        window = tk.Toplevel(self.window)
        window.title(title)
        window.geometry("510x310")
        text = ScrolledText(window, wrap="word", font=("Segoe UI", 11))
        text.pack(fill="both", expand=True, padx=12, pady=12)

        def save() -> None:
            value = text.get("1.0", "end-1c").strip()
            if value:
                if self.execute(LocalCommand(operation, value)):
                    window.destroy()

        ttk.Button(window, text="Сохранить", command=save).pack(pady=(0, 12))
        text.focus_set()

    def _ask_alarm(self, operation: str, prompt: str) -> None:
        value = simpledialog.askstring("Таймер / напоминание", prompt, parent=self.window)
        if value:
            self.execute(LocalCommand(operation, value))

    def add_program(self) -> None:
        name = simpledialog.askstring("Название программы", "Как называть её в команде? Например: Блокнот", parent=self.window)
        if not name:
            return
        path = filedialog.askopenfilename(parent=self.window, title="Выбери исполняемый файл программы", filetypes=[("Приложения Windows", "*.exe")])
        if not path:
            return
        try:
            self.store.add_program(name, path)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.logger.error("Не удалось добавить программу: %s", exc)
            messagebox.showerror("Программа не добавлена", f"{exc}\nНазвание должно быть уникальным.", parent=self.window)
            return
        self.logger.info("Программа добавлена; имя=%s; файл=%s", name, path)
        self.refresh()

    def remove_program(self) -> None:
        selection = self.trees["programs"].selection()
        if selection:
            self.store.remove_program(selection[0])
            self.logger.info("Программа убрана из списка; имя=%s", selection[0])
            self.refresh()

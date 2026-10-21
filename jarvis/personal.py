"""Личные команды, локальное хранилище и однократные напоминания."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import time
from collections.abc import Callable

from .programs import launch_program, program_path
from .schedule import ScheduleError, duration, number, reminder_time


@dataclass(frozen=True)
class LocalCommand:
    operation: str
    argument: str = ""


QUESTIONS = {
    "add_note": "Что записать в заметку?",
    "read_note": "Назови номер заметки.",
    "add_task": "Какое дело добавить?",
    "complete_task": "Назови номер выполненного дела.",
    "add_timer": "На сколько поставить таймер? Например: пять минут.",
    "add_reminder": "Когда и о чём напомнить? Например: через 10 минут проверить духовку.",
    "cancel_alarm": "Назови номер таймера или напоминания.",
    "open_program": "Какую из выбранных программ открыть?",
}


def parse_local(text: str, *, generic_program: bool = False) -> LocalCommand | None:
    """Командные префиксы разбираются отдельно от исходного текста заметки."""
    text = text.strip()
    if generic_program:
        match = re.fullmatch(r"открой\s+(.+)", text, re.I | re.S)
        return LocalCommand("open_program", match[1].strip()) if match else None
    patterns = (
        ("add_note", r"(?:создай|добавь|сделай|запиши|сохрани)(?:\s+мне)?\s+заметку"),
        ("read_note", r"(?:прочитай|покажи|открой)\s+заметку"),
        ("add_task", r"(?:добавь|запиши)(?:\s+мне)?\s+(?:дело|задачу)(?:\s+в\s+список(?:\s+дел)?)?"),
        ("add_task", r"добавь\s+в\s+список\s+дел"),
        ("complete_task", r"(?:отметь|заверши|выполни)\s+(?:дело|задачу)(?:\s+(?:номер|выполненн(?:ым|ой)))?"),
        ("add_timer", r"(?:(?:поставь|запусти|установи)(?:\s+мне)?\s+)?таймер(?:\s+на)?"),
        ("add_reminder", r"напомни(?:\s+мне)?"),
        ("add_reminder", r"(?:создай|добавь|поставь)\s+напоминание"),
        ("cancel_alarm", r"(?:отмени|останови|удали)\s+(?:таймер|напоминание)(?:\s+номер)?"),
        ("open_program", r"(?:открой|запусти)\s+программу"),
        ("open_program", r"запусти"),
    )
    lists = (
        ("list_notes", r"(?:(?:покажи|прочитай)\s+)?(?:мои\s+)?(?:заметки|список\s+заметок)"),
        ("list_tasks", r"(?:(?:покажи|прочитай)\s+)?(?:мой\s+)?(?:список\s+дел|дела|задачи)"),
        ("list_timers", r"(?:покажи\s+)?(?:мои\s+)?таймеры"),
        ("list_reminders", r"(?:покажи\s+)?(?:мои\s+)?напоминания"),
        ("list_programs", r"(?:покажи\s+)?(?:мои\s+)?(?:программы|список\s+программ)"),
    )
    for operation, pattern in lists:
        if re.fullmatch(pattern + r"[.!?]*", text, re.I):
            return LocalCommand(operation)
    for operation, pattern in patterns:
        match = re.match(r"^(?:" + pattern + r")(?=$|[\s:,.!?])", text, re.I)
        if match:
            return LocalCommand(operation, text[match.end():].lstrip(" \t\r\n:,").strip())
    return None


def display_time(value: float) -> str:
    return datetime.fromtimestamp(value).strftime("%d.%m.%Y %H:%M:%S")


class PersonalStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=0.5)
        self.db.row_factory = sqlite3.Row
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Эта база данных создана более новой версией Джарвиса.")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS alarms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK(kind IN ('timer', 'reminder')),
                    text TEXT NOT NULL, due_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'delivered', 'cancelled')),
                    created_at REAL NOT NULL, delivered_at REAL);
                CREATE INDEX IF NOT EXISTS alarms_due ON alarms(status, due_at);
                CREATE TABLE IF NOT EXISTS programs (
                    alias TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL);
                PRAGMA user_version = 1;
            """)
        except Exception:
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def notes(self):
        return self.db.execute("SELECT * FROM notes ORDER BY id").fetchall()

    def tasks(self):
        return self.db.execute("SELECT * FROM tasks ORDER BY done, id").fetchall()

    def alarms(self):
        return self.db.execute("SELECT * FROM alarms ORDER BY due_at, id").fetchall()

    def programs(self):
        return self.db.execute("SELECT * FROM programs ORDER BY name").fetchall()

    def due(self, now: float):
        return self.db.execute("SELECT * FROM alarms WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id", (now,)).fetchall()

    def mark_delivered(self, alarm_id: int, now: float) -> None:
        with self.db:
            self.db.execute("UPDATE alarms SET status = 'delivered', delivered_at = ? WHERE id = ? AND status = 'pending'", (now, alarm_id))

    @staticmethod
    def alias(name: str) -> str:
        return " ".join(name.lower().replace("ё", "е").split())

    def add_program(self, name: str, path: str) -> None:
        name = name.strip()
        if not name or len(name) > 80 or not re.fullmatch(r"[\w -]+", name, re.UNICODE):
            raise ValueError("Название: от 1 до 80 букв, цифр, пробелов или дефисов.")
        resolved = program_path(path)
        with self.db:
            self.db.execute("INSERT INTO programs(alias, name, path) VALUES (?, ?, ?)", (self.alias(name), name, str(resolved)))

    def remove_program(self, alias: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM programs WHERE alias = ?", (alias,))


class PersonalService:
    def __init__(self, store: PersonalStore, *, demo: bool = False,
                 clock: Callable[[], float] = time.time, launcher: Callable[[str], None] = launch_program):
        self.store = store
        self.demo = demo
        self.clock = clock
        self.launcher = launcher

    def execute(self, command: LocalCommand) -> str:
        operation, argument = command.operation, command.argument.strip()
        now = self.clock()
        if operation in QUESTIONS and not argument:
            raise ValueError(QUESTIONS[operation])
        if operation in {"add_note", "add_task"}:
            table = "notes" if operation == "add_note" else "tasks"
            with self.store.db:
                cursor = self.store.db.execute(f"INSERT INTO {table}(text, created_at) VALUES (?, ?)", (argument, now))
            noun = "Заметка" if table == "notes" else "Дело"
            return f"{noun} №{cursor.lastrowid} сохранено: {argument}" if table == "tasks" else f"{noun} №{cursor.lastrowid} сохранена: {argument}"
        if operation == "list_notes":
            rows = self.store.notes()
            return "Заметки:\n" + "\n".join(f"№{r['id']}: {r['text']}" for r in rows) if rows else "Заметок пока нет. Скажи «Создай заметку …»."
        if operation == "list_tasks":
            rows = self.store.tasks()
            return "Список дел:\n" + "\n".join(f"{'✓' if r['done'] else '○'} №{r['id']}: {r['text']}" for r in rows) if rows else "Список дел пуст. Скажи «Добавь дело …»."
        if operation in {"read_note", "complete_task", "cancel_alarm"}:
            identifier = re.sub(r"^(?:номер\s+|№\s*)", "", argument, flags=re.I).rstrip(".!?")
            value = number(identifier)
            if value != int(value) or value < 1:
                raise ValueError("Назови целый положительный номер из списка.")
            item_id = int(value)
            if item_id > 2**63 - 1:
                raise ValueError("Такого номера нет. Посмотри список.")
            if operation == "read_note":
                row = self.store.db.execute("SELECT * FROM notes WHERE id = ?", (item_id,)).fetchone()
                if row is None:
                    raise ValueError(f"Заметка №{item_id} не найдена.")
                return f"Заметка №{item_id}:\n{row['text']}"
            with self.store.db:
                if operation == "complete_task":
                    cursor = self.store.db.execute("UPDATE tasks SET done = 1 WHERE id = ?", (item_id,))
                else:
                    cursor = self.store.db.execute("UPDATE alarms SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (item_id,))
            if not cursor.rowcount:
                raise ValueError("Номер не найден или напоминание уже завершено. Посмотри список.")
            return f"Дело №{item_id} выполнено." if operation == "complete_task" else f"Таймер / напоминание №{item_id} отменено."
        if operation in {"add_timer", "add_reminder"}:
            if operation == "add_timer":
                argument = re.sub(r"^на\s+", "", argument, flags=re.I)
                seconds, label = duration(argument)
                # Не принимать ошибочную вторую длительность за подпись таймера.
                if re.match(r"^(?:[+-]?\d|и\b)", label):
                    raise ScheduleError("Не удалось разобрать весь срок. Пример: «Таймер на 1 час 30 минут».")
                due, kind, label = now + seconds, "timer", label or "Таймер"
            else:
                due, label = reminder_time(argument, now)
                kind = "reminder"
                if not label:
                    raise ValueError("Добавь текст: «Напомни через 10 минут проверить духовку».")
            with self.store.db:
                cursor = self.store.db.execute("INSERT INTO alarms(kind, text, due_at, created_at) VALUES (?, ?, ?, ?)", (kind, label, due, now))
            return f"{'Таймер' if kind == 'timer' else 'Напоминание'} №{cursor.lastrowid}: {display_time(due)} — {label}."
        if operation in {"list_timers", "list_reminders"}:
            kind = "timer" if operation == "list_timers" else "reminder"
            rows = [r for r in self.store.alarms() if r["kind"] == kind and r["status"] == "pending"]
            return "\n".join(f"№{r['id']} · {display_time(r['due_at'])} · {r['text']}" for r in rows) if rows else "Нет ожидающих таймеров." if kind == "timer" else "Нет ожидающих напоминаний."
        if operation == "list_programs":
            rows = self.store.programs()
            return "Выбранные программы:\n" + "\n".join(r["name"] for r in rows) if rows else "Программы ещё не выбраны. Открой меню «Мои данные → Программы» и добавь .exe-файл."
        if operation == "open_program":
            alias = self.store.alias(argument.rstrip(".!?"))
            row = self.store.db.execute("SELECT * FROM programs WHERE alias = ?", (alias,)).fetchone()
            if row is None:
                raise ValueError("Эта программа не выбрана. Добавь её через «Мои данные → Программы».")
            if self.demo:
                return f"DEMO: была бы запущена программа «{row['name']}»."
            self.launcher(row["path"])
            return f"Команда запуска передана программе «{row['name']}»."
        raise ValueError("Эта команда пока не поддерживается.")

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import zipfile

from .browser import BrowserError, open_in_chrome
from .commands import Action, Assistant, Intent, Mode
from .config import CONFIG_PATH, ROOT, Settings, save_settings
from .data_ui import DataWindow
from .personal import LocalCommand, PersonalService, PersonalStore, display_time
from .llm import ModelError, ModelEvent, OllamaClient, build_messages
from .model_runtime import ModelRuntime
from .voice import VoiceEvent, VoiceWorker, list_microphones


class JarvisWindow:
    def __init__(self, root: tk.Tk, settings: Settings, logger: logging.Logger, demo: bool = False,
                 data_path: Path | str | None = None):
        self.root = root
        self.settings = settings
        self.logger = logger
        self.demo = demo
        self.assistant = Assistant(settings.city, settings.wake_words)
        # DEMO никогда не изменяет настоящие заметки и напоминания.
        self.store = PersonalStore(":memory:" if demo else data_path or ROOT / "data/jarvis.sqlite3")
        self.personal = PersonalService(self.store, demo=demo)
        self.events: queue.Queue[VoiceEvent | ModelEvent] = queue.Queue()
        self.voice: VoiceWorker | None = None
        self.epoch = 0
        self.ready = True  # Текстовые команды доступны независимо от микрофона.
        self.voice_error = ""
        self.closing = False
        self._log_window: tk.Toplevel | None = None
        self._settings_window: tk.Toplevel | None = None
        self._data_window: DataWindow | None = None
        self._notification_window: tk.Toplevel | None = None
        self._request = 0
        self._jobs: set[int] = set()
        self._cancelled_jobs: set[int] = set()
        self.model_runtime = ModelRuntime(ROOT)
        self._model_client: OllamaClient | None = None
        self._model_request: int | None = None
        self._model_text = ""
        self._conversation: list[dict[str, str]] = []
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._ui_exception
        self.logger.info("Запуск; режим=%s", "demo" if demo else "voice")
        if demo:
            self._show_mode("DEMO: данные временные, микрофон выключен, программы не запускаются.")
        else:
            self.voice = VoiceWorker(settings, self.events, logger)
            self.voice.start()
        self._poll_after = self.root.after(70, self._poll)
        self._alarm_after = self.root.after(0, lambda: self._check_alarms(startup=True))

    def _build(self) -> None:
        self.root.title("Джарвис · прототип" + (" · DEMO" if self.demo else ""))
        self.root.geometry("720x650")
        self.root.minsize(610, 540)
        self.root.configure(background="#101822")
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Activate.TButton", font=("Segoe UI", 12), padding=(22, 12))
        menu = tk.Menu(self.root)
        menu.add_command(label="Настройки", command=self.show_settings)
        data_menu = tk.Menu(menu, tearoff=False)
        for tab, label in (("notes", "Заметки"), ("tasks", "Список дел"), ("alarms", "Таймеры и напоминания"), ("programs", "Программы")):
            data_menu.add_command(label=label, command=lambda selected=tab: self.show_data(selected))
        menu.add_cascade(label="Мои данные", menu=data_menu)
        logs = tk.Menu(menu, tearoff=False)
        logs.add_command(label="Открыть журнал", command=self.show_logs)
        logs.add_command(label="Выгрузить ZIP", command=self.export_logs)
        menu.add_cascade(label="Логи", menu=logs)
        menu.add_command(label="Помощь", command=self.show_help)
        menu.add_command(label="Новый разговор", command=self.reset_conversation)
        menu.add_command(label="Выход", command=self.close)
        self.root.configure(menu=menu)

        frame = tk.Frame(self.root, background="#101822", padx=25, pady=16)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="ДЖАРВИС", font=("Segoe UI", 20, "bold"), fg="#e6eff9",
                 background="#101822").pack(pady=(0, 8))
        self.indicator = tk.Canvas(frame, width=22, height=22, background="#101822", highlightthickness=0)
        self.dot = self.indicator.create_oval(3, 3, 19, 19, fill="#e7b85e", outline="")
        self.indicator.pack()
        self.status = tk.StringVar(value="Загружаю распознавание…")
        tk.Label(frame, textvariable=self.status, font=("Segoe UI", 12), fg="#e6eff9",
                 background="#101822", wraplength=560).pack(pady=(5, 12))
        self.button = ttk.Button(frame, text="Активировать", style="Activate.TButton", command=self.toggle)
        self.button.pack()
        self.detail = tk.StringVar(value="После обращения сделай короткую паузу.")
        tk.Label(frame, textvariable=self.detail, font=("Segoe UI", 10), fg="#aab9cc",
                 background="#101822", wraplength=610, justify="center").pack(pady=(12, 6))
        self.last_phrase = tk.StringVar(value="Команда: —")
        tk.Label(frame, textvariable=self.last_phrase, font=("Segoe UI", 9), fg="#8395ab",
                 background="#101822", wraplength=610).pack()
        self.history = ScrolledText(frame, height=12, wrap="word", font=("Segoe UI", 10),
                                    background="#172332", foreground="#e6eff9", insertbackground="white", state="disabled")
        self.history.pack(fill="both", expand=True, pady=(12, 0))
        input_frame = tk.Frame(frame, background="#101822")
        input_frame.pack(fill="x", pady=(12, 0))
        self.text_input = ttk.Entry(input_frame)
        self.demo_input = self.text_input
        self.text_input.pack(side="left", fill="x", expand=True)
        self.text_input.bind("<Return>", lambda event: self._submit_demo())
        ttk.Button(input_frame, text="Отправить", command=self._submit_demo).pack(side="left", padx=(8, 0))
        self.text_input.focus_set()
        self._append_history("Джарвис", "Введи команду или скажи «Джарвис». Примеры команд — в меню «Помощь».")

    def _show_mode(self, message: str = "") -> None:
        active = self.assistant.mode == Mode.ACTIVE
        self.indicator.itemconfigure(self.dot, fill="#59d9a0" if active else "#8395ab")
        self.status.set("Слушаю команду" if active else "Жду обращения «Джарвис»")
        self.button.configure(text="В ожидание" if active else "Активировать")
        if self.voice_error:
            self.status.set(self.status.get() + " · только текст")
        if message:
            self.detail.set(message)

    def _new_epoch(self) -> None:
        self.epoch += 1
        if self.voice:
            self.voice.reset(self.epoch)

    def _cancel_jobs(self) -> None:
        self._cancelled_jobs.update(self._jobs)
        self._cancel_model()

    def _cancel_model(self) -> None:
        if self._model_client:
            self._model_client.cancel()
        self._model_client = None
        self._model_request = None

    def reset_conversation(self) -> None:
        self._cancel_model()
        self._conversation.clear()
        self.assistant.activate()
        self._new_epoch()
        self._show_mode("Начинаем новый разговор. Контекст модели очищен.")
        self._append_history("Джарвис", "Начинаем новый разговор. Контекст модели очищен.")

    def _remember(self, text: str, answer: str) -> None:
        self._conversation.extend([{"role": "user", "content": text[:4000]},
                                   {"role": "assistant", "content": answer[:4000]}])
        self._conversation = self._conversation[-12:]

    def toggle(self) -> None:
        if not self.ready or self.closing:
            return
        if self.assistant.mode == Mode.ACTIVE:
            action = self.assistant.sleep()
            self._cancel_jobs()
        else:
            action = self.assistant.activate()
            self._beep()
        self._new_epoch()
        self.logger.info("Кнопка; состояние=%s", self.assistant.mode.value)
        self._show_mode(action.message)

    def _beep(self) -> None:
        if self.settings.activation_sound:
            self.root.bell()

    def _submit_demo(self) -> None:
        text = self.text_input.get()
        self.text_input.delete(0, "end")
        if not text.strip() or self.closing:
            return
        if self.assistant.mode == Mode.WAITING:
            self.assistant.activate()
            self._new_epoch()
        self.handle_phrase(text)

    def _append_history(self, speaker: str, text: str) -> None:
        if not text:
            return
        self.history.configure(state="normal")
        self.history.insert("end", f"{speaker}: {text}\n\n")
        if int(self.history.index("end-1c").split(".")[0]) > 2000:
            self.history.delete("1.0", "800.0")
        self.history.configure(state="disabled")
        self.history.see("end")

    def handle_phrase(self, text: str) -> None:
        if self.closing or not self.ready:
            return
        before = self.assistant.mode
        action = self.assistant.handle(text)
        if action.intent == Intent.IGNORE:
            return  # Обычная речь в ожидании не сохраняется и не показывается.
        self._cancel_model()  # Новая команда отменяет ещё не принятый ответ модели.
        self.last_phrase.set("Команда: " + text[:220])
        self._append_history("Ты", text)
        self._request += 1
        request = self._request
        self.logger.info("Запрос #%s; текст=%r; действие=%s; состояние=%s", request, text,
                         action.intent.value, self.assistant.mode.value)
        if before != self.assistant.mode:
            self._new_epoch()
            if self.assistant.mode == Mode.ACTIVE:
                self._beep()
            else:
                self._cancel_jobs()
        elif action.intent == Intent.ACTIVATE:
            self._new_epoch()
        if action.intent == Intent.UNKNOWN:
            self._start_model(text)
            return
        self._show_mode(action.message)
        self._append_history("Джарвис", action.message)
        if action.command:
            self.run_local(action.command)
        elif action.intent not in {Intent.ACTIVATE, Intent.SLEEP}:
            self._remember(text, action.message)
        if action.intent in {Intent.ACTIVATE, Intent.SLEEP}:
            self._conversation.clear()
        if action.url:
            if self.demo:
                self.detail.set("DEMO: открылся бы Chrome. Адрес записан в логи.")
                self.logger.info("Запрос #%s; demo_url=%s", request, action.url)
            else:
                self._jobs.add(request)
                threading.Thread(target=self._open_page, args=(action, request),
                                 name=f"jarvis-browser-{request}", daemon=True).start()

    def _start_model(self, text: str) -> None:
        if self.demo or not self.settings.llm_enabled:
            message = ("DEMO: модель не вызывается. Для разговора запусти run.cmd. " if self.demo else "Локальная модель выключена в настройках. ")
            message += "Готовые команды доступны в меню «Помощь»."
            self._show_mode(message)
            self._append_history("Джарвис", message)
            return
        if len(text) > 4000:
            self._show_mode("Запрос слишком длинный. Используй до 4000 символов.")
            return
        client = OllamaClient(self.settings.llm_url, self.settings.llm_model, self.settings.llm_timeout)
        self._model_client = client
        self._model_request, self._model_text = self._request, text
        messages = build_messages(self.settings.personality, self._conversation, text,
                                  [row["name"] for row in self.store.programs()])
        request, runtime, events, logger = self._request, self.model_runtime, self.events, self.logger
        self._show_mode("Думаю… Готовые команды и «Отмена» доступны.")
        self.status.set("Думаю · " + self.settings.llm_model)

        def work() -> None:
            from time import monotonic
            started = monotonic()
            try:
                runtime.ensure(client)
                reply = client.reply(messages)
                logger.info("Модель; запрос #%s; тип=%s; время=%.2f с", request, reply.kind, monotonic() - started)
                events.put(ModelEvent("model_reply", "", request, reply))
            except ModelError as exc:
                logger.warning("Модель; запрос #%s; время=%.2f с; %s", request, monotonic() - started, exc)
                events.put(ModelEvent("model_error", str(exc), request))
            except Exception:
                logger.exception("Ошибка модели; запрос #%s", request)
                events.put(ModelEvent("model_error", "Ошибка локальной модели. Подробности в логах; готовые команды работают.", request))

        threading.Thread(target=work, name=f"jarvis-model-{request}", daemon=True).start()

    def _accept_model(self, event: ModelEvent) -> None:
        if event.epoch != self._model_request or self.assistant.mode != Mode.ACTIVE:
            return
        text = self._model_text
        self._model_client = None
        self._model_request = None
        if event.kind == "model_error":
            self._show_mode(event.text)
            self._append_history("Джарвис", event.text)
            return
        reply = event.reply
        if reply is None:
            return
        if reply.command:
            # Повторно применяем правила готовых команд, включая вопросы о параметрах.
            action = self.assistant._local(reply.command)
            if action.command:
                self.run_local(action.command)
                return
            message = action.message
        else:
            message = reply.message
        self.logger.info("Ответ модели; запрос #%s; тип=%s; текст=%r", event.epoch, reply.kind, message)
        self._remember(text, message)
        self._show_mode(message.splitlines()[0][:250])
        self._append_history("Джарвис", message)

    def run_local(self, command: LocalCommand) -> bool:
        if self.closing:
            return False
        self._cancel_model()
        try:
            message = self.personal.execute(command)
            self.logger.info("Личная команда=%s; результат=%s", command.operation, message)
        except (ValueError, OSError, sqlite3.Error) as exc:
            message = f"Не выполнено: {exc}"
            self.logger.error("Личная команда=%s; %s", command.operation, message)
            self._show_mode(message)
            self._append_history("Джарвис", message)
            self._remember(f"Команда {command.operation}: {command.argument}", message)
            return False
        self.detail.set(message.splitlines()[0][:250])
        self._append_history("Джарвис", message)
        self._remember(f"Команда {command.operation}: {command.argument}", message)
        self._show_mode(message.splitlines()[0][:250])
        if self._data_window and self._data_window.window.winfo_exists():
            self._data_window.refresh()
        return True

    def show_data(self, tab: str = "notes") -> None:
        if self._data_window is None or not self._data_window.window.winfo_exists():
            self._data_window = DataWindow(self.root, self.store, self.run_local, self.logger)
        self._data_window.refresh()
        self._data_window.select(tab)

    def show_help(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Команды Джарвиса")
        window.geometry("680x510")
        text = ScrolledText(window, wrap="word", font=("Segoe UI", 11), padx=16, pady=16)
        text.pack(fill="both", expand=True)
        text.insert("1.0", """Голосом сначала скажи «Джарвис». В текстовой строке можно сразу вводить команду.

Создай заметку: Купить корм, 2 пакета.
Покажи заметки / Прочитай заметку 1
Добавь дело записаться к врачу
Покажи список дел / Отметь дело 1
Таймер на пять минут чай
Поставь таймер на 1 час 30 минут
Напомни через десять минут проверить духовку
Напомни завтра в 09:30 позвонить
Напомни 25.10.2026 в 18:00 забрать заказ
Покажи таймеры / Покажи напоминания
Отмени таймер 1 / Отмени напоминание 2
Покажи программы / Открой программу Блокнот
Покажи погоду / Новости про музыку
Отмена — сбросить уточнение
Джарвис спасибо — ждать кодовое слово

Номера показаны в ответах и меню «Мои данные». Там же можно добавить программу, выбрав её .exe-файл.

Таймеры и напоминания показываются в отдельном окне со звуковым сигналом, в том числе в режиме ожидания имени. При закрытом Джарвисе они не срабатывают; просроченные записи появятся при следующем запуске. Время — местное время Windows.

Заметки, дела и напоминания хранятся в data/jarvis.sqlite3. DEMO использует временную базу, которая исчезает при закрытии. Озвучивания ответов пока нет.

Локальная модель — пункт 4:
Запусти setup-model.cmd один раз для загрузки Ollama и Qwen3.
После этого можно писать свободно: «Привет, как ты?», «Что тебе интересно?»,
«Я поставила пирог, пни меня минут через тридцать».
Если данных не хватает, Джарвис уточнит. Готовые команды работают без модели.
«Отмена», «Джарвис, стоп» и новая команда отменяют текущий ответ модели.
Меню «Новый разговор» очищает её контекст. Контекст хранится только до закрытия.
Модель и характер настраиваются в меню «Настройки». В DEMO модель отключена.""")
        text.configure(state="disabled")

    def _check_alarms(self, startup: bool = False) -> None:
        if self.closing:
            return
        self.root.after_cancel(self._alarm_after)
        try:
            now = self.personal.clock()
            due = self.store.due(now)
            if due:
                messages = []
                for row in due:
                    title = "Пропущено" if startup else "Таймер завершён" if row["kind"] == "timer" else "Напоминание"
                    messages.append(f"{title} №{row['id']} · {display_time(row['due_at'])}\n{row['text']}")
                self._show_notifications("\n\n".join(messages))
                for row in due:
                    self.store.mark_delivered(row["id"], now)
                    self.logger.info("Уведомление #%s; пропущено=%s; текст=%s", row["id"], startup, row["text"])
                if self._data_window and self._data_window.window.winfo_exists():
                    self._data_window.refresh()
        except (OSError, sqlite3.Error) as exc:
            self.logger.error("Проверка напоминаний: %s", exc)
            self.detail.set("Не удалось проверить напоминания. Подробности в логах.")
        self._alarm_after = self.root.after(1000, self._check_alarms)

    def _show_notifications(self, message: str) -> None:
        if self._notification_window is None or not self._notification_window.winfo_exists():
            window = self._notification_window = tk.Toplevel(self.root)
            window.title("Напоминания · Джарвис")
            window.geometry("580x340")
            self.notification_text = ScrolledText(window, wrap="word", font=("Segoe UI", 12), state="disabled")
            self.notification_text.pack(fill="both", expand=True, padx=14, pady=14)
            ttk.Button(window, text="Закрыть", command=window.destroy).pack(pady=(0, 12))
        self.notification_text.configure(state="normal")
        self.notification_text.insert("end", message + "\n\n")
        self.notification_text.configure(state="disabled")
        self.notification_text.see("end")
        self._notification_window.deiconify()
        self._notification_window.lift()
        self.root.bell()
        self._append_history("Уведомление", message)

    def _open_page(self, action: Action, request: int) -> None:
        try:
            if self.closing or request in self._cancelled_jobs:
                self.events.put(VoiceEvent("browser_cancelled", epoch=request))
                return
            open_in_chrome(action.url, self.settings.chrome_path)
            self.logger.info("Запрос #%s; адрес передан Chrome: %s", request, action.url)
            self.events.put(VoiceEvent("browser_ok", action.message, request))
        except BrowserError as exc:
            self.logger.error("Запрос #%s; %s", request, exc)
            self.events.put(VoiceEvent("browser_error", str(exc), request))
        except Exception:
            self.logger.exception("Запрос #%s; неожиданная ошибка браузера", request)
            self.events.put(VoiceEvent("browser_error", "Ошибка открытия страницы. Подробности в логах.", request))

    def _poll(self) -> None:
        if self.closing:
            return
        self.root.after_cancel(self._poll_after)
        for _ in range(40):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event.kind.startswith("model_"):
                self._accept_model(event)
            elif event.kind == "ready":
                self.ready = True
                self.voice_error = ""
                self.button.configure(state="normal")
                self._show_mode("Микрофон: " + event.text)
            elif event.kind == "phrase" and event.epoch == self.epoch:
                self.handle_phrase(event.text)
            elif event.kind == "error":
                self.voice_error = event.text
                self.indicator.itemconfigure(self.dot, fill="#f37e82")
                self.status.set("Голосовой ввод недоступен · текст работает")
                self.detail.set(event.text)
                self._append_history("Джарвис", event.text + " Можно вводить команды в текстовую строку.")
            elif event.kind == "warning":
                self.detail.set(event.text)
            elif event.kind.startswith("browser_"):
                self._jobs.discard(event.epoch)
                cancelled = event.epoch in self._cancelled_jobs
                self._cancelled_jobs.discard(event.epoch)
                if not cancelled and event.epoch == self._request and self.assistant.mode == Mode.ACTIVE:
                    self.detail.set(event.text)
                    self._append_history("Джарвис", event.text)
        self._poll_after = self.root.after(70, self._poll)

    def show_settings(self) -> None:
        if self._settings_window and self._settings_window.winfo_exists():
            self._settings_window.lift()
            return
        window = self._settings_window = tk.Toplevel(self.root)
        window.title("Настройки Джарвиса")
        window.resizable(False, False)
        window.transient(self.root)
        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        frame = ttk.Frame(notebook, padding=18)
        model_frame = ttk.Frame(notebook, padding=18)
        notebook.add(frame, text="Основное")
        notebook.add(model_frame, text="Модель и характер")
        city = tk.StringVar(value=self.settings.city)
        model = tk.StringVar(value=self.settings.model_path)
        chrome = tk.StringVar(value=self.settings.chrome_path)
        sound = tk.BooleanVar(value=self.settings.activation_sound)
        llm_enabled = tk.BooleanVar(value=self.settings.llm_enabled)
        llm_url = tk.StringVar(value=self.settings.llm_url)
        llm_model = tk.StringVar(value=self.settings.llm_model)
        llm_timeout = tk.StringVar(value=str(self.settings.llm_timeout))
        ttk.Checkbutton(model_frame, text="Использовать локальную модель для свободных запросов", variable=llm_enabled).pack(anchor="w")
        for label, variable in (("Адрес Ollama на этом компьютере", llm_url), ("Имя локальной модели", llm_model), ("Таймаут ответа, секунд (10–300)", llm_timeout)):
            ttk.Label(model_frame, text=label).pack(anchor="w", pady=(10, 2))
            ttk.Entry(model_frame, textvariable=variable, width=54).pack(fill="x")
        ttk.Label(model_frame, text="Характер Джарвиса (до 3000 символов)").pack(anchor="w", pady=(12, 2))
        personality = ScrolledText(model_frame, width=54, height=8, wrap="word", font=("Segoe UI", 10))
        personality.pack(fill="both", expand=True)
        personality.insert("1.0", self.settings.personality)
        ttk.Label(model_frame, text="Для установки запусти setup-model.cmd. Модель работает локально.\nИзменения применяются сразу. Контекст — только текущий сеанс.", wraplength=430).pack(anchor="w", pady=12)
        for row, (label, variable) in enumerate((("Город для погоды", city), ("Папка модели Vosk", model), ("Путь к chrome.exe", chrome))):
            ttk.Label(frame, text=label).grid(row=row * 2, column=0, columnspan=2, sticky="w", pady=(6, 2))
            ttk.Entry(frame, textvariable=variable, width=51).grid(row=row * 2 + 1, column=0, sticky="ew")

        def choose_model() -> None:
            selected = filedialog.askdirectory(parent=window, title="Папка модели с подпапками am и conf")
            if selected:
                model.set(selected)

        def choose_chrome() -> None:
            selected = filedialog.askopenfilename(parent=window, title="Выбери chrome.exe", filetypes=[("Приложение", "*.exe")])
            if selected:
                chrome.set(selected)

        ttk.Button(frame, text="…", width=3, command=choose_model).grid(row=3, column=1, padx=(5, 0))
        ttk.Button(frame, text="…", width=3, command=choose_chrome).grid(row=5, column=1, padx=(5, 0))
        ttk.Label(frame, text="Микрофон").grid(row=6, column=0, sticky="w", pady=(10, 2))
        devices: dict[str, int | str | None] = {"По умолчанию Windows": None}
        try:
            devices.update({f"{index}: {name}": index for index, name in list_microphones()})
        except Exception:
            self.logger.warning("Список микрофонов недоступен в настройках")
        selected_device = next((label for label, value in devices.items() if value == self.settings.microphone), None)
        if selected_device is None:
            selected_device = f"Настроенное устройство: {self.settings.microphone}"
            devices[selected_device] = self.settings.microphone
        microphone = tk.StringVar(value=selected_device)
        ttk.Combobox(frame, textvariable=microphone, values=list(devices), width=49, state="readonly").grid(row=7, column=0, columnspan=2, sticky="ew")
        ttk.Checkbutton(frame, text="Сигнал при активации", variable=sound).grid(row=8, column=0, sticky="w", pady=10)
        ttk.Label(frame, text="Модель и микрофон применяются после перезапуска.").grid(row=9, column=0, columnspan=2, pady=(0, 10))

        def save() -> None:
            try:
                updated = replace(self.settings, city=city.get(), model_path=model.get(), chrome_path=chrome.get(),
                                  microphone=devices[microphone.get()], activation_sound=sound.get(),
                                  llm_enabled=llm_enabled.get(), llm_url=llm_url.get().strip(),
                                  llm_model=llm_model.get().strip(), llm_timeout=int(llm_timeout.get()),
                                  personality=personality.get("1.0", "end").strip())
                save_settings(updated)
            except (ValueError, OSError) as exc:
                messagebox.showerror("Настройки не сохранены", str(exc), parent=window)
                return
            self.settings = updated
            self.assistant.city = updated.city
            self._cancel_model()
            self._conversation.clear()
            self.logger.info("Настройки сохранены в %s", CONFIG_PATH.name)
            window.destroy()
            self._show_mode("Настройки сохранены. Только смена Vosk и микрофона требует перезапуска.")

        ttk.Button(window, text="Сохранить", command=save).pack(pady=(0, 12))

    def show_logs(self) -> None:
        if self._log_window and self._log_window.winfo_exists():
            self._log_window.lift()
            return
        window = self._log_window = tk.Toplevel(self.root)
        window.title("Логи Джарвиса")
        window.geometry("880x480")
        text = ScrolledText(window, font=("Consolas", 10), wrap="word", state="disabled")
        text.pack(fill="both", expand=True, padx=10, pady=10)

        def refresh() -> None:
            if self.closing or not window.winfo_exists():
                return
            try:
                with (ROOT / "logs/jarvis.log").open("rb") as log:
                    log.seek(0, 2)
                    log.seek(max(0, log.tell() - 96 * 1024))
                    content = log.read().decode("utf-8", errors="replace")
            except OSError as exc:
                content = str(exc)
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", content)
            text.configure(state="disabled")
            text.see("end")
            window.after(1500, refresh)

        refresh()

    def export_logs(self) -> None:
        selected = filedialog.asksaveasfilename(parent=self.root, title="Выгрузить логи", defaultextension=".zip",
                                               initialfile=f"jarvis-logs-{datetime.now():%Y%m%d-%H%M%S}.zip",
                                               filetypes=[("ZIP-архив", "*.zip")])
        if not selected:
            return
        try:
            for handler in self.logger.handlers:
                handler.flush()
            with zipfile.ZipFile(selected, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted([*(ROOT / "logs").glob("jarvis.log*"), *(ROOT / "logs").glob("ollama.log*")]):
                    archive.write(path, arcname=path.name)
            self.detail.set("Логи выгружены. В них есть распознанные команды и поисковые запросы.")
        except OSError as exc:
            messagebox.showerror("Ошибка выгрузки", str(exc), parent=self.root)

    def _ui_exception(self, exc_type, exc_value, traceback) -> None:
        self.logger.error("Ошибка интерфейса", exc_info=(exc_type, exc_value, traceback))
        messagebox.showerror("Ошибка", "Подробности записаны в логи.", parent=self.root)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.root.after_cancel(self._poll_after)
        self.root.after_cancel(self._alarm_after)
        self.ready = False
        self.assistant.sleep()
        self._cancel_jobs()
        self.model_runtime.close()
        self._new_epoch()
        self.button.configure(state="disabled")
        self.status.set("Завершаю работу…")
        self.detail.set("Освобождаю микрофон")
        if self.voice:
            self.voice.stop()
        self._finish_close()

    def _finish_close(self) -> None:
        if self.voice and self.voice.is_alive():
            self.root.after(50, self._finish_close)
            return
        self.logger.info("Полное завершение приложения")
        self.store.close()
        self.root.destroy()

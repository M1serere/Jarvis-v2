from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from jarvis.commands import Assistant, Intent, Mode
from jarvis.personal import LocalCommand, PersonalService, PersonalStore
from jarvis.programs import launch_program, program_path
from jarvis.schedule import ScheduleError, duration, number, reminder_time


ROOT = Path(__file__).resolve().parent.parent


class LocalCommandTests(unittest.TestCase):
    def setUp(self):
        self.assistant = Assistant(city="Москва")

    def test_payload_preserves_case_punctuation_and_weather_words(self):
        text = 'Погода: дождь. Купить «Чай» — 2 пакета!\nНовости потом.'
        action = self.assistant.handle("Джарвис, создай заметку: " + text)
        self.assertEqual(action.intent, Intent.LOCAL)
        self.assertEqual(action.command, LocalCommand("add_note", text))
        self.assertFalse(action.url)

    def test_all_local_command_examples(self):
        examples = {
            "Добавь дело Проверить новости": ("add_task", "Проверить новости"),
            "Добавь в список дел купить молоко": ("add_task", "купить молоко"),
            "Покажи список дел": ("list_tasks", ""),
            "Отметь дело 2": ("complete_task", "2"),
            "Прочитай заметку номер один": ("read_note", "номер один"),
            "Покажи заметки": ("list_notes", ""),
            "Поставь таймер на пять минут чай": ("add_timer", "пять минут чай"),
            "Напомни мне через 10 минут проверить погоду": ("add_reminder", "через 10 минут проверить погоду"),
            "Напомни завтра в 09:30 Позвонить": ("add_reminder", "завтра в 09:30 Позвонить"),
            "Отмени напоминание 2": ("cancel_alarm", "2"),
            "Покажи программы": ("list_programs", ""),
            "Открой программу Блокнот": ("open_program", "Блокнот"),
            "Запусти Блокнот": ("open_program", "Блокнот"),
            "Открой Блокнот": ("open_program", "Блокнот"),
        }
        self.assistant.activate()
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(self.assistant.handle(text).command, LocalCommand(*expected))

    def test_missing_argument_can_be_supplied_in_next_phrase(self):
        self.assistant.handle("Джарвис создай заметку")
        self.assertEqual(self.assistant.pending, Intent.LOCAL)
        action = self.assistant.handle("Погода: дождь, взять зонт.")
        self.assertEqual(action.command, LocalCommand("add_note", "Погода: дождь, взять зонт."))
        self.assertIsNone(self.assistant.pending)

    def test_cancel_sleep_and_repeat_name_clear_local_question(self):
        for interrupt in ("Отмена", "Джарвис спасибо", "Джарвис"):
            with self.subTest(interrupt=interrupt):
                self.assistant.handle("Джарвис добавь дело")
                self.assistant.handle(interrupt)
                self.assertIsNone(self.assistant.pending)
                self.assertIsNone(self.assistant.handle("Купить молоко").command)

    def test_ambient_local_commands_are_ignored(self):
        for text in ("Создай заметку секрет", "Таймер на минуту", "Открой программу Блокнот"):
            self.assertEqual(self.assistant.handle(text).intent, Intent.IGNORE)
        self.assertEqual(self.assistant.mode, Mode.WAITING)

    def test_browser_command_priority_is_preserved(self):
        self.assistant.activate()
        self.assertEqual(self.assistant.handle("Открой прогноз").intent, Intent.WEATHER)
        self.assertEqual(self.assistant.handle("Открой новости про музыку").intent, Intent.NEWS)


class TimeParsingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 21, 12, 0).timestamp()

    def test_russian_and_numeric_durations(self):
        for text, seconds, label in (
            ("пять минут Чай", 300, "Чай"),
            ("1 час 30 минут", 5400, ""),
            ("один час и тридцать минут Обед", 5400, "Обед"),
            ("минуту", 60, ""),
            ("полчаса", 1800, ""),
            ("полторы минуты", 90, ""),
            ("2 дня", 172800, ""),
            ("1,5 часа", 5400, ""),
            ("двадцать пять секунд", 25, ""),
        ):
            with self.subTest(text=text):
                self.assertEqual(duration(text), (seconds, label))

    def test_invalid_duration_is_not_scheduled(self):
        for text in ("0 минут", "-5 минут", "потом", "999 дней", "0.001 секунды", "пять крокодилов"):
            with self.subTest(text=text), self.assertRaises(ScheduleError):
                duration(text)

    def test_relative_and_calendar_reminders(self):
        for text, expected, label in (
            ("через десять минут Проверить духовку", self.now + 600, "Проверить духовку"),
            ("завтра в 09:30 Позвонить", datetime(2026, 10, 22, 9, 30).timestamp(), "Позвонить"),
            ("сегодня в 18:00 Купить корм", datetime(2026, 10, 21, 18).timestamp(), "Купить корм"),
            ("25.10.2026 в 10:00 Заказ", datetime(2026, 10, 25, 10).timestamp(), "Заказ"),
            ("в 13:00 Обед", self.now + 3600, "Обед"),
        ):
            with self.subTest(text=text):
                self.assertEqual(reminder_time(text, self.now), (expected, label))

    def test_invalid_date_and_past_time_are_rejected(self):
        for text in ("сегодня в 09:00 Утро", "завтра в 25:00 Текст", "31.02.2027 в 10:00 Текст", "когда-нибудь Текст"):
            with self.subTest(text=text), self.assertRaises(ScheduleError):
                reminder_time(text, self.now)

    def test_number_rejects_unrecognized_words(self):
        self.assertEqual(number("сто двадцать три"), 123)
        with self.assertRaises(ScheduleError):
            number("два кота")
        with self.assertRaises(ScheduleError):
            number("двадцать двенадцать")


class PersonalWorkflowTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "data/jarvis.sqlite3"
        self.store = PersonalStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.now = 1000.0
        self.launcher = Mock()
        self.service = PersonalService(self.store, clock=lambda: self.now, launcher=self.launcher)

    def execute(self, operation, argument=""):
        return self.service.execute(LocalCommand(operation, argument))

    def test_notes_tasks_and_schedules_survive_restart(self):
        self.execute("add_note", "Текст: ' ; DROP TABLE notes; — Ёж\nСтрока 2")
        self.execute("add_task", "Купить чай")
        self.execute("complete_task", "один")
        self.execute("add_reminder", "через минуту Позвонить")
        self.execute("add_timer", "2 минуты")
        self.store.close()
        self.store = PersonalStore(self.path)
        self.assertIn("Ёж\nСтрока 2", self.store.notes()[0]["text"])
        self.assertEqual(self.store.tasks()[0]["done"], 1)
        self.assertEqual(len(self.store.alarms()), 2)
        self.assertEqual(self.store.alarms()[0]["due_at"], 1060)

    def test_missed_delivered_and_cancelled_alarms(self):
        self.execute("add_timer", "5 секунд Чай")
        self.execute("add_reminder", "через минуту Звонок")
        self.execute("cancel_alarm", "2")
        self.store.close()
        self.store = PersonalStore(self.path)
        self.assertEqual(self.store.due(1004), [])
        due = self.store.due(1100)
        self.assertEqual([r["id"] for r in due], [1])
        self.store.mark_delivered(1, 1100)
        self.store.close()
        self.store = PersonalStore(self.path)
        self.assertEqual(self.store.due(2000), [])
        self.assertEqual(self.store.alarms()[1]["status"], "cancelled")

    def test_missing_reminder_text_does_not_create_alarm(self):
        with self.assertRaisesRegex(ValueError, "текст"):
            self.execute("add_reminder", "через пять минут")
        self.assertEqual(self.store.alarms(), [])

    def test_invalid_ids_and_unknown_records_do_not_change_data(self):
        self.execute("add_task", "Чай")
        for value in ("0", "-1", "1.5", "кот", "999", str(2**64)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.execute("complete_task", value)
        self.assertEqual(self.store.tasks()[0]["done"], 0)

    def test_known_program_launch_and_alias_persistence(self):
        executable = Path(self.temporary.name) / "Программа с пробелами.exe"
        executable.write_bytes(b"test placeholder, never executed")
        self.store.add_program("Блокнот", str(executable))
        self.assertIn("Блокнот", self.execute("list_programs"))
        self.execute("open_program", "бЛоКнОт")
        self.launcher.assert_called_once_with(str(executable.resolve()))
        self.store.close()
        self.store = PersonalStore(self.path)
        self.assertEqual(self.store.programs()[0]["name"], "Блокнот")

    def test_unknown_program_or_shell_text_is_never_executed(self):
        for name in ("cmd.exe /c whoami", "C:/Windows/notepad.exe", "Блокнот & calc.exe"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.execute("open_program", name)
        self.launcher.assert_not_called()

    def test_demo_never_launches_program(self):
        executable = Path(self.temporary.name) / "test.exe"
        executable.write_bytes(b"test")
        self.store.add_program("Тест", str(executable))
        self.service.demo = True
        self.assertIn("DEMO", self.execute("open_program", "Тест"))
        self.launcher.assert_not_called()

    def test_program_launch_failure_is_not_success(self):
        executable = Path(self.temporary.name) / "test.exe"
        executable.write_bytes(b"test")
        self.store.add_program("Тест", str(executable))
        self.launcher.side_effect = OSError("запуск запрещён")
        with self.assertRaises(OSError):
            self.execute("open_program", "Тест")

    def test_future_database_version_is_preserved(self):
        with self.store.db:
            self.store.db.execute("PRAGMA user_version=99")
        self.store.close()
        with self.assertRaisesRegex(ValueError, "новой версией"):
            PersonalStore(self.path)
        self.store = PersonalStore(":memory:")


class ProgramLauncherTests(unittest.TestCase):
    def test_only_existing_absolute_exe_is_allowed(self):
        for path in ("relative.exe", "C:/not-present.exe", "C:/Windows/System32/cmd.exe /c calc", __file__):
            with self.subTest(path=path), self.assertRaises(ValueError):
                program_path(path)

    @patch("jarvis.programs.subprocess.Popen")
    def test_launch_uses_single_argument_and_no_shell(self, popen):
        cache = ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            executable = Path(directory) / "Программа с пробелами.exe"
            executable.write_bytes(b"test, never executed")
            launch_program(str(executable))
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [str(executable.resolve())])
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["cwd"], str(executable.parent.resolve()))


if __name__ == "__main__":
    unittest.main()

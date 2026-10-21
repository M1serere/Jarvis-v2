import gc
import logging
from pathlib import Path
import queue
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from jarvis.commands import Mode
from jarvis.personal import LocalCommand
from jarvis.config import Settings
from jarvis.ui import JarvisWindow
from jarvis.voice import VoiceEvent, VoiceWorker


class WindowScenarios(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.logger = Mock(spec=logging.Logger)
        self.app = JarvisWindow(self.root, Settings(city="Владивосток", activation_sound=False), self.logger, demo=True)

    def tearDown(self):
        if hasattr(self, "root"):
            try:
                if self.root.winfo_exists():
                    self.app.close()
            except tk.TclError:
                pass
        self.app = None
        self.root = None
        # Tkinter-объекты уничтожаются на главном потоке, прежде чем
        # последующие тесты запустят поток микрофона.
        gc.collect()

    @patch("jarvis.ui.open_in_chrome")
    def test_demo_never_opens_microphone_or_browser(self, browser):
        self.assertIsNone(self.app.voice)
        self.app.handle_phrase("Джарвис погода")
        browser.assert_not_called()
        self.assertIn("DEMO", self.app.detail.get())

    def test_button_and_thanks_update_indicator_and_state(self):
        self.app.toggle()
        self.assertEqual(self.app.assistant.mode, Mode.ACTIVE)
        self.assertEqual(self.app.button.cget("text"), "В ожидание")
        self.app.handle_phrase("Джарвис спасибо")
        self.assertEqual(self.app.assistant.mode, Mode.WAITING)
        self.assertEqual(self.app.button.cget("text"), "Активировать")

    def test_standby_speech_is_not_logged_or_displayed(self):
        self.logger.reset_mock()
        self.app.handle_phrase("Это личный разговор про погоду")
        self.logger.info.assert_not_called()
        self.assertEqual(self.app.last_phrase.get(), "Команда: —")

    def test_old_recognition_after_manual_deactivation_is_discarded(self):
        self.app.toggle()
        old_epoch = self.app.epoch
        self.app.toggle()
        self.app.events.put(VoiceEvent("phrase", "Джарвис покажи погоду", old_epoch))
        self.app._poll()
        self.assertEqual(self.app.assistant.mode, Mode.WAITING)
        self.assertEqual(self.app.last_phrase.get(), "Команда: —")

    def test_late_browser_result_does_not_overwrite_sleep_status(self):
        self.app.toggle()
        self.app._jobs.add(1)
        self.app.toggle()
        self.app.events.put(VoiceEvent("browser_ok", "Открываю погоду", 1))
        self.app._poll()
        self.assertIn("Жду", self.app.detail.get())
        self.assertNotIn(1, self.app._jobs)

    def test_microphone_failure_keeps_text_commands_available(self):
        self.app.toggle()
        self.app.events.put(VoiceEvent("error", "Микрофон отключён"))
        self.app._poll()
        self.assertTrue(self.app.ready)
        self.assertNotIn("disabled", self.app.button.state())
        self.assertIn("отключён", self.app.detail.get())
        self.app.text_input.insert(0, "Добавь дело Проверить микрофон")
        self.app._submit_demo()
        self.assertEqual(self.app.store.tasks()[0]["text"], "Проверить микрофон")

    def test_text_input_activates_without_wake_word(self):
        self.app.text_input.insert(0, "Создай заметку: Чай, 2 пакета!")
        self.app._submit_demo()
        self.assertEqual(self.app.store.notes()[0]["text"], "Чай, 2 пакета!")
        self.assertIn("сохранена", self.app.history.get("1.0", "end"))

    def test_timer_notifies_in_standby_once_and_keeps_mode(self):
        self.app.personal.clock = lambda: 1000.0
        self.app.run_local(LocalCommand("add_timer", "5 секунд Чай"))
        self.app.personal.clock = lambda: 1005.0
        self.app._check_alarms()
        self.assertEqual(self.app.assistant.mode, Mode.WAITING)
        self.assertIn("Чай", self.app.notification_text.get("1.0", "end"))
        self.assertEqual(self.app.store.alarms()[0]["status"], "delivered")
        before = self.app.notification_text.get("1.0", "end")
        self.app._check_alarms()
        self.assertEqual(before, self.app.notification_text.get("1.0", "end"))

    def test_startup_shows_missed_reminders(self):
        self.app.personal.clock = lambda: 1000.0
        self.app.run_local(LocalCommand("add_reminder", "через минуту Позвонить"))
        self.app.personal.clock = lambda: 2000.0
        self.app._check_alarms(startup=True)
        self.assertIn("Пропущено", self.app.notification_text.get("1.0", "end"))
        self.assertIn("Позвонить", self.app.notification_text.get("1.0", "end"))

    def test_cancelled_alarm_does_not_notify(self):
        self.app.personal.clock = lambda: 1000.0
        self.app.run_local(LocalCommand("add_timer", "секунду"))
        self.app.run_local(LocalCommand("cancel_alarm", "1"))
        self.app.personal.clock = lambda: 1002.0
        self.app._check_alarms()
        self.assertIsNone(self.app._notification_window)

    def test_all_data_tabs_display_records_and_full_note(self):
        self.app.run_local(LocalCommand("add_note", "Полный текст\nВторая строка"))
        self.app.run_local(LocalCommand("add_task", "Купить чай"))
        self.app.run_local(LocalCommand("add_timer", "10 минут"))
        self.app.show_data("notes")
        data = self.app._data_window
        for key in ("notes", "tasks", "alarms"):
            self.assertEqual(len(data.trees[key].get_children()), 1)
        data.trees["notes"].selection_set("1")
        data._detail("notes")
        self.assertIn("Вторая строка", data.detail.get("1.0", "end"))
        self.app.show_data("programs")
        self.assertEqual(data.current_tab(), "programs")

    def test_failed_save_does_not_report_success(self):
        with patch.object(self.app.personal, "execute", side_effect=ValueError("Ошибка записи")):
            self.assertFalse(self.app.run_local(LocalCommand("add_note", "Текст")))
        self.assertIn("Не выполнено", self.app.history.get("1.0", "end"))


class PersistentWindowScenarios(unittest.TestCase):
    @patch("jarvis.ui.VoiceWorker")
    def test_regular_mode_restart_and_demo_isolation(self, voice):
        voice.return_value.is_alive.return_value = False
        cache = Path(__file__).resolve().parent.parent / ".cache"
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            database = Path(directory) / "jarvis.sqlite3"

            def open_window(demo=False):
                try:
                    root = tk.Tk()
                except tk.TclError as exc:
                    self.skipTest(str(exc))
                root.withdraw()
                app = JarvisWindow(root, Settings(activation_sound=False), Mock(spec=logging.Logger),
                                   demo=demo, data_path=database)
                return root, app

            root, app = open_window()
            try:
                app.personal.clock = lambda: 1000.0
                app.text_input.insert(0, "Создай заметку: Сохранить после перезапуска!")
                app._submit_demo()
                app.run_local(LocalCommand("add_reminder", "через 5 секунд Чай"))
                app.show_data("notes")
                root.deiconify()
                root.update_idletasks()
                # Строка ввода остаётся видимой при первоначальном размере окна.
                entry_bottom = app.text_input.winfo_rooty() + app.text_input.winfo_height()
                self.assertLessEqual(entry_bottom, root.winfo_rooty() + root.winfo_height())
            finally:
                app.close()
            root, app = open_window()
            try:
                app.personal.clock = lambda: 1010.0
                root.update()  # Настоящий startup-callback и окно уведомления.
                self.assertEqual(app.store.notes()[0]["text"], "Сохранить после перезапуска!")
                self.assertIn("Пропущено", app.notification_text.get("1.0", "end"))
                self.assertIn("Чай", app.notification_text.get("1.0", "end"))
                self.assertEqual(app.store.alarms()[0]["status"], "delivered")
                app.show_data("alarms")
                root.update_idletasks()
                self.assertEqual(len(app._data_window.trees["alarms"].get_children()), 1)
            finally:
                app.close()
            root, app = open_window(demo=True)
            try:
                self.assertEqual(app.store.notes(), [])
                app.run_local(LocalCommand("add_note", "Только для демонстрации"))
            finally:
                root.update_idletasks()
                app.close()
            root, app = open_window()
            try:
                root.update()
                self.assertIsNone(app._notification_window)
                self.assertEqual(len(app.store.notes()), 1)
                self.assertEqual(app.store.notes()[0]["text"], "Сохранить после перезапуска!")
            finally:
                app.close()
        gc.collect()


class VoiceBufferTests(unittest.TestCase):
    def test_bounded_audio_queue_and_session_tag(self):
        worker = VoiceWorker(Settings(), queue.Queue(), Mock())
        worker.reset(12)
        for _ in range(40):
            worker._callback(b"\0\0", 1, None, False)
        self.assertEqual(worker._audio.qsize(), 25)
        self.assertTrue(worker._overflow.is_set())
        self.assertEqual(worker._audio.get_nowait(), (12, b"\0\0"))
        worker.stop()
        worker._clear_audio()
        worker._callback(b"\0\0", 1, None, False)
        self.assertTrue(worker._audio.empty())


if __name__ == "__main__":
    unittest.main()

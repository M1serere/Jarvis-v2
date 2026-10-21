import gc
import logging
import queue
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from jarvis.commands import Mode
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

    def test_microphone_failure_disables_button(self):
        self.app.toggle()
        self.app.events.put(VoiceEvent("error", "Микрофон отключён"))
        self.app._poll()
        self.assertFalse(self.app.ready)
        self.assertEqual(self.app.assistant.mode, Mode.WAITING)
        self.assertIn("disabled", self.app.button.state())
        self.assertIn("отключён", self.app.detail.get())


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

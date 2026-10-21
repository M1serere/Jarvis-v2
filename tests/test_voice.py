import json
from pathlib import Path
import queue
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from jarvis.config import Settings
from jarvis.voice import VoiceWorker


class VoiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        (directory / "am").mkdir()
        (directory / "am/final.mdl").touch()
        self.settings = Settings(model_path=str(directory))
        self.events = queue.Queue()
        self.worker = VoiceWorker(self.settings, self.events, Mock())
        self.addCleanup(self.stop_worker)
        self.stream = Mock()
        self.stream.active = True
        self.stream.__enter__ = Mock(return_value=self.stream)
        self.stream.__exit__ = Mock(return_value=False)
        self.recognizer = Mock()
        self.recognizer.AcceptWaveform.return_value = True
        self.recognizer.Result.return_value = json.dumps({"text": "джарвис покажи погоду"})
        model = Mock()
        model.vosk_model_find_word.return_value = 42
        self.vosk = types.SimpleNamespace(SetLogLevel=Mock(), Model=Mock(return_value=model),
                                          KaldiRecognizer=Mock(return_value=self.recognizer))
        self.sounddevice = types.SimpleNamespace(query_devices=Mock(return_value={"name": "Fake microphone", "default_samplerate": 48000}),
                                                 check_input_settings=Mock(), RawInputStream=Mock(return_value=self.stream),
                                                 PortAudioError=type("PortAudioError", (Exception,), {}))

    def stop_worker(self):
        self.worker.stop()
        if self.worker.ident is not None:
            self.worker.join(timeout=3)

    def next_event(self, kind):
        while True:
            event = self.events.get(timeout=3)
            if event.kind == kind:
                return event
            if event.kind == "error" and kind != "error":
                self.fail(event.text)

    def test_recognition_epoch_and_full_microphone_release(self):
        with patch.dict("sys.modules", {"sounddevice": self.sounddevice, "vosk": self.vosk}):
            self.worker.start()
            try:
                self.next_event("ready")
                self.worker.reset(7)
                self.worker._callback(b"fake audio", 1, None, False)
                event = self.next_event("phrase")
                self.assertEqual(event.text, "джарвис покажи погоду")
                self.assertEqual(event.epoch, 7)
            finally:
                self.worker.stop()
                self.worker.join(timeout=3)
            self.assertFalse(self.worker.is_alive())
            self.stream.__exit__.assert_called_once()
            self.assertTrue(self.worker._audio.empty())

    def test_decoder_failure_releases_device_and_reports_error(self):
        self.recognizer.AcceptWaveform.side_effect = RuntimeError("decoder failed")
        with patch.dict("sys.modules", {"sounddevice": self.sounddevice, "vosk": self.vosk}):
            self.worker.start()
            self.next_event("ready")
            self.worker._callback(b"fake audio", 1, None, False)
            error = self.next_event("error")
            self.worker.join(timeout=3)
            self.assertIn("decoder failed", error.text)
            self.assertFalse(self.worker.is_alive())
            self.stream.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()

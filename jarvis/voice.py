"""Микрофон и Vosk в отдельном потоке; на диск звук не записывается."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import queue
import threading
import time

from .config import Settings


@dataclass(frozen=True)
class VoiceEvent:
    kind: str
    text: str = ""
    epoch: int = 0


class VoiceWorker(threading.Thread):
    def __init__(self, settings: Settings, events: queue.Queue[VoiceEvent], logger: logging.Logger):
        super().__init__(name="jarvis-microphone", daemon=True)
        self.settings = settings
        self.events = events
        self.logger = logger
        self._stop_requested = threading.Event()
        self._overflow = threading.Event()
        self._audio: queue.Queue[tuple[int, bytes]] = queue.Queue(maxsize=25)
        self._requested_epoch = 0
        self._stream = None

    def reset(self, epoch: int) -> None:
        # Новые аудиоблоки помечаются сразу. Старый результат не может
        # исполниться после нажатия «В ожидание» или новой активации.
        self._requested_epoch = epoch

    def stop(self) -> None:
        self._stop_requested.set()

    def _callback(self, indata, frames, timing, status) -> None:
        if self._stop_requested.is_set():
            return
        if status:
            self._overflow.set()
        try:
            self._audio.put_nowait((self._requested_epoch, bytes(indata)))
        except queue.Full:
            self._overflow.set()

    def run(self) -> None:
        try:
            self._listen()
        except Exception as exc:
            if not self._stop_requested.is_set():
                self.logger.exception("Ошибка голосового ввода")
                self.events.put(VoiceEvent("error", str(exc)))
        finally:
            self._stream = None
            # Отбрасываем оставшиеся аудиоблоки после освобождения устройства.
            self._clear_audio()
            self.events.put(VoiceEvent("stopped"))

    def _clear_audio(self) -> None:
        while True:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                break

    def _listen(self) -> None:
        model_path = self.settings.model_directory
        if not (model_path / "am/final.mdl").is_file():
            raise RuntimeError("Не найдена модель Vosk. Запусти setup.cmd или выбери распакованную модель в настройках.")
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except (ImportError, OSError) as exc:
            raise RuntimeError("Не загрузились Vosk или sounddevice. Запусти setup.cmd и проверь ошибки установки.") from exc
        SetLogLevel(-1)
        model = Model(str(model_path))  # Явный локальный путь: без скачивания.
        missing = [word for word in self.settings.wake_words if model.vosk_model_find_word(word) < 0]
        if missing:
            raise RuntimeError("В словаре модели нет обращения: " + ", ".join(missing) + ". Нужна модель с этим словом.")
        if self._stop_requested.is_set():
            return
        try:
            device = sd.query_devices(self.settings.microphone, "input")
            sample_rate = 16000
            try:
                sd.check_input_settings(device=self.settings.microphone, channels=1,
                                        dtype="int16", samplerate=sample_rate)
            except sd.PortAudioError:
                sample_rate = int(device["default_samplerate"])
            recognizer = KaldiRecognizer(model, sample_rate)
            epoch = self._requested_epoch
            with sd.RawInputStream(device=self.settings.microphone, samplerate=sample_rate,
                                   blocksize=int(sample_rate * 0.1), channels=1,
                                   dtype="int16", callback=self._callback) as stream:
                self._stream = stream
                self.logger.info("Микрофон открыт: %s; %s Гц", device["name"], sample_rate)
                self.events.put(VoiceEvent("ready", str(device["name"])))
                last_audio = time.monotonic()
                last_warning = 0.0
                while not self._stop_requested.is_set():
                    if self._overflow.is_set():
                        self._overflow.clear()
                        self._clear_audio()
                        recognizer.Reset()
                        if time.monotonic() - last_warning > 5:
                            self.events.put(VoiceEvent("warning", "Пропуск звука. Повтори последнюю фразу."))
                            self.logger.warning("Переполнение или пропуск аудиобуфера")
                            last_warning = time.monotonic()
                    try:
                        block_epoch, data = self._audio.get(timeout=0.2)
                    except queue.Empty:
                        if not stream.active or time.monotonic() - last_audio > 5:
                            raise RuntimeError("Микрофон перестал передавать звук. Проверь подключение и перезапусти приложение.")
                        continue
                    last_audio = time.monotonic()
                    # Режим мог измениться, пока поток ждал queue.get().
                    # Обновляем epoch после получения блока, иначе первый
                    # звук новой команды будет ошибочно отброшен.
                    if epoch != self._requested_epoch:
                        epoch = self._requested_epoch
                        recognizer.Reset()
                    if block_epoch != epoch:
                        continue
                    if recognizer.AcceptWaveform(data):
                        text = json.loads(recognizer.Result()).get("text", "").strip()
                        if text and not self._stop_requested.is_set():
                            self.events.put(VoiceEvent("phrase", text, epoch))
                    # Промежуточные гипотезы не исполняются и не попадают
                    # в журнал: иначе «Джарвис, спасибо» может разбудить его.
        except sd.PortAudioError as exc:
            raise RuntimeError("Нет доступа к микрофону. Разреши доступ для классических приложений в Windows, "
                               "проверь выбранное устройство и перезапусти Джарвиса. " + str(exc)) from exc
        finally:
            self.logger.info("Микрофон освобождён")


def list_microphones() -> list[tuple[int, str]]:
    import sounddevice as sd

    return [(index, str(device["name"])) for index, device in enumerate(sd.query_devices())
            if device["max_input_channels"] > 0]

"""Переносимый Ollama запускается только вместе с запросом и завершается с окном."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time

from .llm import ModelError, OllamaClient


class ModelRuntime:
    def __init__(self, root: Path):
        self.root = root
        self.process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def ensure(self, client: OllamaClient) -> None:
        with self._lock:
            if self._closed.is_set() or client.cancelled.is_set():
                raise ModelError("Запрос отменён.")
            try:
                client._request("/api/version", timeout=2)
                return  # Чужой сервер не останавливаем.
            except ModelError:
                pass
            if self._closed.is_set() or client.cancelled.is_set():
                raise ModelError("Запрос отменён.")
            executable = self.root / "tools/ollama/ollama.exe"
            if not executable.is_file() or client.url != "http://127.0.0.1:11434":
                raise ModelError("Ollama недоступна. Запусти setup-model.cmd. Готовые команды работают без модели.")
            if self.process is None or self.process.poll() is not None:
                environment = os.environ.copy()
                environment.update(OLLAMA_HOST="127.0.0.1:11434", OLLAMA_NO_CLOUD="1",
                                   OLLAMA_MODELS=str(self.root / "models/ollama"), OLLAMA_MAX_LOADED_MODELS="1",
                                   OLLAMA_NUM_PARALLEL="1", OLLAMA_CONTEXT_LENGTH="8192",
                                   OLLAMA_FLASH_ATTENTION="0")
                # Ключи и служебные файлы переносимого сервера тоже остаются в проекте.
                environment["USERPROFILE"] = str(self.root / ".cache/ollama-home")
                (self.root / "logs").mkdir(exist_ok=True)
                (self.root / ".cache/ollama-home").mkdir(parents=True, exist_ok=True)
                log_path = self.root / "logs/ollama.log"
                if log_path.exists() and log_path.stat().st_size > 2 * 1024 * 1024:
                    log_path.replace(log_path.with_suffix(".log.1"))
                with (self.root / "logs/ollama.log").open("ab") as log:
                    self.process = subprocess.Popen([str(executable), "serve"], cwd=self.root, env=environment,
                                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self._closed.is_set() or client.cancelled.is_set():
                    raise ModelError("Запрос отменён.")
                if self.process.poll() is not None:
                    raise ModelError("Ollama не запустилась. Подробности в logs/ollama.log.")
                try:
                    client._request("/api/version", timeout=1)
                    return
                except ModelError:
                    client.cancelled.wait(0.15)
            raise ModelError("Ollama не успела запуститься. Подробности в logs/ollama.log.")

    def close(self) -> None:
        self._closed.set()
        # ensure может ещё запускать процесс. Дожидаемся выхода из него в фоне.
        def stop() -> None:
            with self._lock:
                process = self.process
                if process and process.poll() is None:
                    try:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
                        else:
                            process.terminate()
                    except (OSError, subprocess.TimeoutExpired):
                        process.terminate()
        threading.Thread(target=stop, name="jarvis-model-stop", daemon=False).start()

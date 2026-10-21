"""Установка переносимого Ollama без службы, автозапуска и прав администратора."""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.config import load_settings, save_settings
from jarvis.llm import ModelError, OllamaClient
from jarvis.model_runtime import ModelRuntime

VERSION = "v0.40.7"
ARCHIVE = f"ollama-windows-amd64-{VERSION}.zip"
URL = f"https://github.com/ollama/ollama/releases/download/{VERSION}/ollama-windows-amd64.zip"
SHA256 = "e6527768c53e85ec4596495fb0f035ef07f437b4b591ebc3581684699a4668f4"


def install_runtime() -> None:
    target = ROOT / "tools/ollama"
    marker = target / ".jarvis-installed"
    if (target / "ollama.exe").is_file() and marker.is_file() and marker.read_text() == SHA256:
        return
    archive = ROOT / ".cache/downloads" / ARCHIVE
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        print("Загрузка официального Ollama (~1,6 ГБ)…", flush=True)
        temporary = archive.with_suffix(".part")
        last = time.monotonic()
        with urlopen(URL, timeout=60) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                if time.monotonic() - last >= 5:
                    print(f"Ollama: {output.tell() / 2**20:.0f} МБ", flush=True)
                    last = time.monotonic()
        temporary.replace(archive)
    print("Проверяю SHA-256 архива Ollama…", flush=True)
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != SHA256:
        raise ValueError(f"Контрольная сумма неверна. Удали {archive} и повтори setup-model.cmd.")
    target.mkdir(parents=True, exist_ok=True)
    print("Распаковываю Ollama в tools/ollama…", flush=True)
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            destination = (target / entry.filename).resolve()
            if not destination.is_relative_to(target.resolve()):
                raise ValueError("В архиве обнаружен путь за пределами папки установки.")
        source.extractall(target)
    marker.write_text(SHA256, encoding="ascii")


def pull_model(client: OllamaClient) -> None:
    address = urlsplit(client.url)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=120)
    try:
        body = json.dumps({"model": client.model, "stream": True}).encode("utf-8")
        connection.request("POST", "/api/pull", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ModelError(f"Не удалось загрузить модель: HTTP {response.status}.")
        last, success = 0.0, False
        while line := response.readline(65536):
            item = json.loads(line)
            if "error" in item:
                raise ModelError("Загрузка модели: " + str(item["error"])[:400])
            status = item.get("status", "")
            if status == "success":
                success = True
            if time.monotonic() - last > 5 or status == "success":
                total, completed = item.get("total", 0), item.get("completed", 0)
                print(f"{status}: {completed / 2**20:.0f} / {total / 2**20:.0f} МБ" if total else status, flush=True)
                last = time.monotonic()
        if not success:
            raise ModelError("Загрузка модели прервалась. Повтори setup-model.cmd: Ollama продолжит загрузку.")
    finally:
        connection.close()


def main() -> int:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    runtime = ModelRuntime(ROOT)
    try:
        settings = load_settings()
        install_runtime()
        client = OllamaClient(settings.llm_url, settings.llm_model, settings.llm_timeout)
        runtime.ensure(client)
        print(f"Проверяю модель {settings.llm_model}…", flush=True)
        try:
            print(client.check())
        except ModelError:
            pull_model(client)
            print(client.check())
        settings.llm_enabled = True
        save_settings(settings)
        print("Готово. Запусти run.cmd. Интернет для разговора больше не нужен.", flush=True)
        return 0
    except (OSError, ValueError, zipfile.BadZipFile, ModelError, http.client.HTTPException) as exc:
        print(f"Установка модели не завершена: {exc}", file=sys.stderr)
        return 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())

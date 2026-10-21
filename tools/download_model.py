"""Устанавливает модель в проект из локального ZIP или с сайта Vosk.

Не вызывается самим приложением. Запускается пользователем или setup.cmd.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parent.parent
MODEL = "vosk-model-small-ru-0.22"
URL = f"https://alphacephei.com/vosk/models/{MODEL}.zip"
MIRROR_URL = "https://huggingface.co/rhasspy/vosk-models/resolve/e7ac2109d134b5f2404ba95389b2fb51916d4cab/ru/" + MODEL + ".zip"
# SHA-256 архива; также опубликован как LFS oid у закреплённого зеркала.
SHA256 = "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4"


def download(destination: Path) -> None:
    for url in (URL, MIRROR_URL):
        try:
            print(f"Источник: {url}", flush=True)
            request = Request(url, headers={"User-Agent": "JarvisPrototype/0.1"})
            with urlopen(request, timeout=30) as response, destination.open("wb") as output:
                if not response.geturl().startswith("https://"):
                    raise RuntimeError("Сервер перенаправил скачивание на незащищённый адрес.")
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > 128 * 1024 * 1024:
                        raise RuntimeError("Неожиданный размер скачиваемого файла.")
                    output.write(block)
            return
        except OSError as exc:
            print(f"Источник недоступен: {exc}", flush=True)
            if url == MIRROR_URL:
                raise


def install(archive: Path | None = None) -> Path:
    directory = ROOT / "models"
    directory.mkdir(exist_ok=True)
    destination = directory / MODEL
    if (destination / "am/final.mdl").is_file() and (destination / "conf/mfcc.conf").is_file():
        print(f"Модель уже установлена: {destination}")
        return destination
    if destination.exists():
        raise RuntimeError(f"Папка {destination} существует, но модель неполная. Переименуй её и повтори установку.")
    if archive is None:
        archive = next((path for path in (ROOT / f"{MODEL}.zip", ROOT / "models" / f"{MODEL}.zip",
                                          Path.home() / "Downloads" / f"{MODEL}.zip") if path.is_file()), None)
    with tempfile.TemporaryDirectory(prefix=".install-", dir=directory) as temporary:
        staging = Path(temporary)
        if archive is None:
            archive = staging / "model.zip"
            print("Скачиваю русскую модель Vosk (~45 МБ)...", flush=True)
            download(archive)
        else:
            archive = archive.resolve(strict=True)
            print(f"Распаковываю: {archive}", flush=True)
        digest = hashlib.sha256()
        with archive.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != SHA256:
            raise RuntimeError("Контрольная сумма ZIP не совпадает. Удали скачанный архив и повтори загрузку.")
        unpacked = staging / "unpacked"
        unpacked.mkdir()
        with ZipFile(archive) as zipped:
            members = zipped.infolist()
            if sum(member.file_size for member in members) > 512 * 1024 * 1024:
                raise RuntimeError("Неожиданно большой архив модели.")
            for member in members:
                target = (unpacked / member.filename).resolve()
                if not target.is_relative_to(unpacked.resolve()):
                    raise RuntimeError("В архиве есть путь за пределами папки модели.")
                if not member.filename.replace("\\", "/").startswith(MODEL + "/"):
                    raise RuntimeError("Архив не соответствует выбранной модели.")
            zipped.extractall(unpacked)
        model = unpacked / MODEL
        if not (model / "am/final.mdl").is_file() or not (model / "conf/mfcc.conf").is_file():
            raise RuntimeError("В ZIP не найдена полная модель Vosk.")
        model.rename(destination)
    print(f"Готово: {destination}")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Путь к уже скачанному ZIP")
    arguments = parser.parse_args()
    try:
        install(arguments.archive)
    except Exception as exc:
        print(f"Не удалось установить модель: {exc}", file=sys.stderr)
        sys.exit(1)

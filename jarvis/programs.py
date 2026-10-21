"""Запуск только заранее выбранного исполняемого файла."""
from __future__ import annotations

from pathlib import Path
import subprocess


def program_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Выбери существующий .exe-файл программы с полным путём.")
    path = path.resolve()
    if path.suffix.lower() != ".exe" or not path.is_file():
        raise ValueError("Выбери существующий .exe-файл программы с полным путём.")
    return path


def launch_program(value: str) -> None:
    path = program_path(value)
    subprocess.Popen([str(path)], cwd=str(path.parent), shell=False,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

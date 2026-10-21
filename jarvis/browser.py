from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit


class BrowserError(RuntimeError):
    pass


def find_chrome(configured: str = "") -> Path | None:
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if value := os.environ.get(variable):
            candidates.append(Path(value) / "Google/Chrome/Application/chrome.exe")
    if os.name == "nt":
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", 0, winreg.KEY_READ | view) as key:
                        candidates.append(Path(winreg.QueryValue(key, None).strip('"')))
                except OSError:
                    pass
    for name in ("chrome", "google-chrome", "google-chrome-stable", "chromium"):
        if value := shutil.which(name):
            candidates.append(Path(value))
    return next((path for path in candidates if path.is_file()), None)


def open_in_chrome(url: str, configured: str = "") -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "www.google.com" or parts.path != "/search":
        raise BrowserError("Этот прототип открывает только поисковые страницы Google по HTTPS.")
    chrome = find_chrome(configured)
    if chrome is None:
        raise BrowserError("Не найден Chrome. Укажи путь к chrome.exe в меню «Настройки».")
    try:
        # Список аргументов и shell=False: произнесённая тема не может
        # превратиться в команду оболочки или параметр запуска Chrome.
        subprocess.Popen([str(chrome), "--new-tab", url], shell=False,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise BrowserError(f"Не удалось запустить Chrome: {exc}") from exc

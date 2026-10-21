from __future__ import annotations

import argparse
import importlib.util
import json
import sys

from jarvis.config import ROOT, load_settings
from jarvis.diagnostics import configure_logging, install_thread_error_handler


def check_installation(settings) -> int:
    from jarvis.browser import find_chrome

    modules = {name: importlib.util.find_spec(name) is not None for name in ("tkinter", "vosk", "sounddevice")}
    model = settings.model_directory
    chrome = find_chrome(settings.chrome_path)
    report = {"python": sys.version.split()[0], "modules": modules,
              "model_found": (model / "am/final.mdl").is_file(),
              "model_path": str(model), "chrome": str(chrome) if chrome else None,
              "microphone_opened": False}
    if modules["vosk"] and report["model_found"]:
        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            loaded = Model(str(model))
            report["wake_words"] = {word: loaded.vosk_model_find_word(word) >= 0 for word in settings.wake_words}
        except Exception as exc:
            report["model_error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if (all(modules.values()) and report["model_found"] and chrome and
                 report.get("wake_words") and all(report["wake_words"].values()) and not report.get("model_error")) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Джарвис: локальное распознавание, погода и новости в Chrome")
    parser.add_argument("--demo", action="store_true", help="Ввод с клавиатуры, без микрофона и открытия браузера")
    parser.add_argument("--list-microphones", action="store_true", help="Показать доступные устройства ввода")
    parser.add_argument("--check", action="store_true", help="Проверить установку без открытия микрофона")
    args = parser.parse_args()
    logger = configure_logging(ROOT)
    install_thread_error_handler(logger)
    if args.list_microphones:
        try:
            from jarvis.voice import list_microphones
            for index, name in list_microphones():
                print(f"{index}: {name}")
            return 0
        except Exception as exc:
            print(f"Не удалось получить список микрофонов: {exc}", file=sys.stderr)
            return 1
    try:
        settings = load_settings()
        if args.check:
            return check_installation(settings)
        import tkinter as tk
        from jarvis.ui import JarvisWindow
        root = tk.Tk()
        JarvisWindow(root, settings, logger, demo=args.demo)
        root.mainloop()
        return 0
    except Exception as exc:
        logger.exception("Ошибка запуска")
        if sys.stderr:
            print(f"Не удалось запустить Джарвиса: {exc}", file=sys.stderr)
        if not args.check:
            try:
                import tkinter as tk
                from tkinter import messagebox
                error_root = tk.Tk()
                error_root.withdraw()
                messagebox.showerror("Джарвис: ошибка запуска", str(exc), parent=error_root)
                error_root.destroy()
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

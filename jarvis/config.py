from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path

from .llm import DEFAULT_PERSONALITY, validate_endpoint, validate_model


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


@dataclass
class Settings:
    city: str = ""
    model_path: str = "models/vosk-model-small-ru-0.22"
    chrome_path: str = ""
    microphone: int | str | None = None
    wake_words: list[str] = field(default_factory=lambda: ["джарвис"])
    activation_sound: bool = True
    llm_enabled: bool = True
    llm_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:1.7b"
    llm_timeout: int = 90
    personality: str = DEFAULT_PERSONALITY

    @property
    def model_directory(self) -> Path:
        path = Path(self.model_path).expanduser()
        return path if path.is_absolute() else ROOT / path

    def validate(self) -> None:
        for name in ("city", "model_path", "chrome_path"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"Настройка {name} должна быть строкой.")
            setattr(self, name, value.strip())
        if not self.model_path:
            raise ValueError("Не указан путь к модели Vosk.")
        if not isinstance(self.wake_words, list) or not self.wake_words:
            raise ValueError("wake_words должен содержать хотя бы одно обращение.")
        if any(not isinstance(word, str) or not word.strip() for word in self.wake_words):
            raise ValueError("Каждое обращение должно быть непустой строкой.")
        if any(not word.isalpha() for word in self.wake_words):
            raise ValueError("В этой версии обращение должно состоять из одного слова.")
        self.wake_words = list(dict.fromkeys(word.lower().replace("ё", "е") for word in self.wake_words))
        if isinstance(self.microphone, bool) or not isinstance(self.microphone, (int, str, type(None))):
            raise ValueError("microphone: номер, часть названия устройства или null.")
        if isinstance(self.microphone, int) and self.microphone < 0:
            raise ValueError("Номер микрофона не может быть отрицательным.")
        if not isinstance(self.activation_sound, bool):
            raise ValueError("activation_sound должен быть true или false.")
        if not isinstance(self.llm_enabled, bool):
            raise ValueError("llm_enabled должен быть true или false.")
        if not isinstance(self.llm_url, str):
            raise ValueError("llm_url должен быть строкой.")
        validate_endpoint(self.llm_url)
        validate_model(self.llm_model)
        if isinstance(self.llm_timeout, bool) or not isinstance(self.llm_timeout, int) or not 10 <= self.llm_timeout <= 300:
            raise ValueError("Таймаут модели: целое число от 10 до 300 секунд.")
        if not isinstance(self.personality, str) or not 1 <= len(self.personality.strip()) <= 3000:
            raise ValueError("Описание характера: от 1 до 3000 символов.")
        self.personality = self.personality.strip()


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    if not path.exists():
        settings = Settings()
        save_settings(settings, path)
        return settings
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("В файле настроек ожидается JSON-объект.")
        settings = Settings(**data)
        settings.validate()
        return settings
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Ошибка в {path.name}: {exc}") from exc


def save_settings(settings: Settings, path: Path = CONFIG_PATH) -> None:
    settings.validate()
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)

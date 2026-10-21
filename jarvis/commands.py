"""Разбор фраз и состояния диалога. Не обращается к микрофону или браузеру."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from urllib.parse import urlencode


class Mode(str, Enum):
    WAITING = "waiting"
    ACTIVE = "active"


class Intent(str, Enum):
    IGNORE = "ignore"
    ACTIVATE = "activate"
    SLEEP = "sleep"
    WEATHER = "weather"
    NEWS = "news"
    ASK_CITY = "ask_city"
    ASK_TOPIC = "ask_topic"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Action:
    intent: Intent
    message: str = ""
    url: str = ""


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+(?:[-+][^\W_]+)*", text, flags=re.UNICODE))


def weather_url(query: str) -> str:
    return "https://www.google.com/search?" + urlencode({"q": "погода " + query})


def news_url(topic: str) -> str:
    return "https://www.google.com/search?" + urlencode({"q": topic, "tbm": "nws", "hl": "ru"})


class Assistant:
    def __init__(self, city: str = "", wake_words: list[str] | None = None):
        self.city = city
        self.wake_words = set(wake_words or ["джарвис"])
        self.mode = Mode.WAITING
        self.pending: Intent | None = None
        self._pending_weather = ""

    def activate(self) -> Action:
        self.mode = Mode.ACTIVE
        self.pending = None
        self._pending_weather = ""
        return Action(Intent.ACTIVATE, "Слушаю команду")

    def sleep(self) -> Action:
        self.mode = Mode.WAITING
        self.pending = None
        self._pending_weather = ""
        return Action(Intent.SLEEP, "Жду обращения «Джарвис»")

    def handle(self, text: str) -> Action:
        phrase = normalize(text)
        if not phrase:
            return Action(Intent.IGNORE)
        words = phrase.split()
        addressed = words[0] in self.wake_words
        body = " ".join(words[1:]) if addressed else phrase
        # Проверяем всю законченную фразу до активации. «Джарвис, спасибо»
        # не должен повторно будить помощника или выполнять хвост команды.
        sleep_phrases = {"спасибо", "спасибо большое", "большое спасибо", "стоп", "остановись", "в ожидание"}
        if addressed and any(body == phrase or body.startswith(phrase + " ") for phrase in sleep_phrases):
            return self.sleep()
        if self.mode == Mode.WAITING and not addressed:
            return Action(Intent.IGNORE)
        if addressed and not body:
            # Повторное обращение также отменяет незавершённое уточнение.
            return self.activate()
        if self.mode == Mode.WAITING:
            self.activate()
        if body in {"отмена", "отмени"}:
            self.pending = None
            self._pending_weather = ""
            return Action(Intent.ACTIVATE, "Отменено. Слушаю команду")

        weather_match = re.search(r"\b(?:погода|погоду|погоде|погоды|прогноз)\b", body)
        news_match = re.search(r"\b(?:новости|новостей)\b", body)

        if self.pending == Intent.ASK_TOPIC and re.match(r"^(?:про|о|об|обо|по теме|на тему)\s+", body):
            self.pending = None
            return self._open_news(self._strip_topic_prefix(body))

        # Явная новая команда имеет приоритет перед ответом на уточнение.
        # У «Новости про погоду» действие — новости, погода здесь тема.
        if weather_match and (not news_match or weather_match.start() < news_match.start()):
            self.pending = None
            return self._weather(body)
        if news_match:
            self.pending = None
            return self._news(body, news_match)
        if self.pending == Intent.ASK_TOPIC:
            self.pending = None
            return self._open_news(self._strip_topic_prefix(body))
        if self.pending == Intent.ASK_CITY:
            self.pending = None
            city = re.sub(r"^(?:в городе|город|во|в)\s+", "", body)
            query = " ".join(part for part in (self._pending_weather, city) if part)
            self._pending_weather = ""
            return Action(Intent.WEATHER, f"Открываю прогноз: {query}", weather_url(query))
        return Action(Intent.UNKNOWN, "Не понял команду. Попробуй «Покажи погоду» или «Новости про музыку».")

    def _weather(self, body: str) -> Action:
        # Сохраняем произвольный город, дату и уточнения, удаляем только
        # служебные слова. «Погода в ...» работает и без города по умолчанию.
        details = re.sub(r"\b(?:покажи|показать|открой|открыть|найди|посмотри|скажи|расскажи|какая|какой|будет|сейчас|пожалуйста|мне|нам|погода|погоду|погоде|погоды|прогноз)\b", " ", body)
        details = " ".join(details.split())
        # Если после удаления дат осталось название места, не подставляем
        # второй город: «Погода Москва» — тоже допустимая формулировка.
        place = re.sub(r"\b(?:сегодня|завтра|послезавтра|вчера|утром|днем|вечером|ночью|выходные|выходных|неделю|неделе|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|на|в|во|для|к|и|с|до|через|число|часов|часа|дня|дней)\b|\b\d+\b", " ", details)
        has_city = bool(place.strip())
        if not has_city and not self.city:
            self.pending = Intent.ASK_CITY
            self._pending_weather = details
            return Action(Intent.ASK_CITY, "Для какого города показать погоду?")
        query = details if has_city else " ".join(part for part in (self.city, details) if part)
        return Action(Intent.WEATHER, f"Открываю прогноз: {query}", weather_url(query))

    def _news(self, body: str, match: re.Match[str]) -> Action:
        topic = body[match.end():].strip()
        # «Найди мне новости...» и «Покажи последние новости...» также
        # обрабатываются; слова перед «новости» относятся к самой команде.
        topic = self._strip_topic_prefix(topic)
        topic = re.sub(r"\bпожалуйста\b", "", topic).strip()
        if not topic or topic in {"сегодня", "за сегодня", "последние", "свежие"}:
            self.pending = Intent.ASK_TOPIC
            return Action(Intent.ASK_TOPIC, "По какой теме показать новости?")
        return self._open_news(topic)

    @staticmethod
    def _strip_topic_prefix(topic: str) -> str:
        return re.sub(r"^(?:(?:на тему|по теме|о|об|обо|про|по)\s+)+", "", topic).strip()

    @staticmethod
    def _open_news(topic: str) -> Action:
        if not topic:
            return Action(Intent.UNKNOWN, "Не расслышал тему. Повтори команду с темой новостей.")
        return Action(Intent.NEWS, f"Открываю новости: {topic}", news_url(topic))

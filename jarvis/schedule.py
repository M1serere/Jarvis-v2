"""Ограниченный, предсказуемый разбор времени без языковой модели."""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import re


class ScheduleError(ValueError):
    pass


_ONES = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "одну": 1,
    "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
    "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
}
_TEENS = dict(zip("десять одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split(), range(10, 20)))
_TENS = dict(zip("двадцать тридцать сорок пятьдесят шестьдесят семьдесят восемьдесят девяносто".split(), range(20, 100, 10)))
_HUNDREDS = dict(zip("сто двести триста четыреста пятьсот шестьсот семьсот восемьсот девятьсот".split(), range(100, 1000, 100)))


def number(text: str) -> float:
    text = text.strip().lower().replace("ё", "е")
    if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
        value = float(text.replace(",", "."))
        if math.isfinite(value):
            return value
    if text in {"пол", "половину", "половина"}:
        return 0.5
    if text in {"полтора", "полторы"}:
        return 1.5
    if text == "четверть":
        return 0.25
    words = text.split()
    value = 0
    if words and words[0] in _HUNDREDS:
        value += _HUNDREDS[words.pop(0)]
    if words and words[0] in _TEENS:
        value += _TEENS[words.pop(0)]
    else:
        if words and words[0] in _TENS:
            value += _TENS[words.pop(0)]
        if words and words[0] in _ONES:
            value += _ONES[words.pop(0)]
    if not words and value:
        return float(value)
    raise ScheduleError("Не удалось разобрать число. Можно использовать цифры: 5 минут.")


_UNIT = re.compile(r"(?<![а-яёa-z])(?P<unit>секунд(?:а|у|ы)?|сек|минут(?:а|у|ы)?|мин|час(?:а|ов)?|день|дня|дней|сутки|суток|[смчд])(?![а-яёa-z])", re.I)


def duration(text: str) -> tuple[float, str]:
    """Возвращает секунды и оставшийся текст, например подпись таймера."""
    rest = text.strip()
    total = 0.0
    special = re.match(r"^(полчаса|полминуты)\b", rest, re.I)
    if special:
        total = 1800 if special[1].lower() == "полчаса" else 30
        rest = rest[special.end():].strip()
    while rest:
        candidate = re.sub(r"^и\s+", "", rest, flags=re.I) if total else rest
        match = _UNIT.search(candidate)
        if match is None:
            break
        amount_text = candidate[:match.start()].strip()
        # «на минуту» и «через час» означают одну единицу.
        try:
            amount = number(amount_text) if amount_text else 1
        except ScheduleError:
            break
        unit = match["unit"].lower()
        multiplier = 1 if unit.startswith("с") and not unit.startswith("сут") else 60 if unit.startswith("м") else 3600 if unit.startswith("ч") else 86400
        total += amount * multiplier
        rest = candidate[match.end():].lstrip(" ,")
    if not math.isfinite(total) or not 0 < total <= 365 * 86400:
        raise ScheduleError("Укажи срок от 1 секунды до 365 дней, например «5 минут» или «час 30 минут».")
    if total < 1:
        raise ScheduleError("Минимальный срок — 1 секунда.")
    return total, rest


def reminder_time(text: str, now: float) -> tuple[float, str]:
    text = re.sub(r"^мне\s+", "", text.strip(), flags=re.I)
    relative = re.match(r"^через\s+", text, re.I)
    if relative:
        seconds, message = duration(text[relative.end():])
        return now + seconds, message
    match = re.match(r"^(?:(сегодня|завтра|послезавтра|\d{1,2}\.\d{1,2}\.\d{4})\s+)?(?:в\s+)?(\d{1,2}):(\d{2})(?:\s+|$)(.*)$", text, re.I | re.S)
    if not match:
        raise ScheduleError("Пример: «Напомни через 10 минут проверить духовку» или «Напомни завтра в 09:30 позвонить».")
    day, hour, minute, message = match.groups()
    current = datetime.fromtimestamp(now)
    try:
        if day and "." in day:
            date = datetime.strptime(day, "%d.%m.%Y").date()
        else:
            offset = {"завтра": 1, "послезавтра": 2}.get((day or "").lower(), 0)
            date = (current + timedelta(days=offset)).date()
        due = datetime.combine(date, datetime.min.time()).replace(hour=int(hour), minute=int(minute)).timestamp()
    except (ValueError, OverflowError, OSError) as exc:
        raise ScheduleError("Некорректная дата или время. Используй ДД.ММ.ГГГГ и ЧЧ:ММ.") from exc
    if due <= now:
        raise ScheduleError("Это время уже прошло. Укажи будущую дату, например «завтра в 09:30».")
    return due, message.strip()

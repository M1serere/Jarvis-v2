import unittest
from urllib.parse import parse_qs, urlsplit

from jarvis.commands import Assistant, Intent, Mode


def query(url):
    return parse_qs(urlsplit(url).query)


class CommandScenarios(unittest.TestCase):
    def setUp(self):
        self.assistant = Assistant("Владивосток")

    def test_ambient_speech_does_not_open_pages_or_change_mode(self):
        for text in ("Покажи погоду", "Новости про музыку", "Я говорю про Джарвиса", "", "спасибо"):
            with self.subTest(text=text):
                action = self.assistant.handle(text)
                self.assertEqual(action.intent, Intent.IGNORE)
                self.assertEqual(self.assistant.mode, Mode.WAITING)
                self.assertFalse(action.url)

    def test_wake_then_several_commands_then_sleep(self):
        self.assertEqual(self.assistant.handle("Джарвис!").intent, Intent.ACTIVATE)
        self.assertEqual(self.assistant.handle("Покажи погоду").intent, Intent.WEATHER)
        self.assertEqual(self.assistant.handle("Новости про музыку").intent, Intent.NEWS)
        self.assertEqual(self.assistant.mode, Mode.ACTIVE)
        self.assertEqual(self.assistant.handle("Джарвис, спасибо").intent, Intent.SLEEP)
        self.assertEqual(self.assistant.handle("Новости про музыку").intent, Intent.IGNORE)
        self.assertEqual(self.assistant.handle("Джарвис").intent, Intent.ACTIVATE)

    def test_thanks_does_not_activate_from_standby(self):
        for mode in Mode:
            for ending in ("спасибо", "спасибо большое", "стоп", "остановись", "спасибо покажи погоду"):
                with self.subTest(mode=mode, ending=ending):
                    self.assistant.mode = mode
                    action = self.assistant.handle("Джарвис, " + ending)
                    self.assertEqual(action.intent, Intent.SLEEP)
                    self.assertEqual(self.assistant.mode, Mode.WAITING)
                    self.assertFalse(action.url)

    def test_codeword_must_be_a_whole_word_at_start(self):
        for text in ("Джарвисов покажи погоду", "эй джарвис покажи погоду", "новости про джарвис"):
            self.assertEqual(self.assistant.handle(text).intent, Intent.IGNORE)

    def test_weather_with_codeword_in_same_utterance(self):
        action = self.assistant.handle("Джарвис, покажи погоду на завтра")
        self.assertEqual(action.intent, Intent.WEATHER)
        self.assertEqual(self.assistant.mode, Mode.ACTIVE)
        self.assertEqual(query(action.url)["q"], ["погода Владивосток на завтра"])

    def test_default_city_and_weather_variants(self):
        self.assistant.activate()
        for text in ("Покажи погоду", "Какая сегодня погода?", "Открой прогноз", "Прогноз погоды в выходные"):
            with self.subTest(text=text):
                action = self.assistant.handle(text)
                self.assertEqual(action.intent, Intent.WEATHER)
                self.assertIn("Владивосток", query(action.url)["q"][0])

    def test_explicit_city_replaces_default(self):
        self.assistant.activate()
        for text, location in (("Погода в Москве", "москве"), ("Покажи погоду Москва на завтра", "москва")):
            with self.subTest(text=text):
                action = self.assistant.handle(text)
                self.assertIn(location, query(action.url)["q"][0])
                self.assertNotIn("Владивосток", query(action.url)["q"][0])

    def test_missing_city_keeps_date_while_asking(self):
        assistant = Assistant()
        self.assertEqual(assistant.handle("Джарвис погода на завтра").intent, Intent.ASK_CITY)
        action = assistant.handle("в городе Владивосток")
        self.assertEqual(action.intent, Intent.WEATHER)
        self.assertEqual(query(action.url)["q"], ["погода на завтра владивосток"])

    def test_news_variants(self):
        self.assistant.activate()
        examples = (("Новости об искусстве", "искусстве"),
                    ("Покажи новости про нейросети", "нейросети"),
                    ("Найди новости на тему музыки", "музыки"))
        for text, topic in examples:
            with self.subTest(text=text):
                action = self.assistant.handle(text)
                self.assertEqual(action.intent, Intent.NEWS)
                self.assertEqual(query(action.url)["q"], [topic])
                self.assertEqual(query(action.url)["tbm"], ["nws"])

    def test_missing_topic_then_answer(self):
        self.assistant.activate()
        self.assertEqual(self.assistant.handle("Покажи новости").intent, Intent.ASK_TOPIC)
        action = self.assistant.handle("про искусственный интеллект")
        self.assertEqual(query(action.url)["q"], ["искусственный интеллект"])
        self.assertIsNone(self.assistant.pending)

    def test_weather_can_be_a_news_topic(self):
        self.assistant.activate()
        action = self.assistant.handle("Покажи новости про погоду")
        self.assertEqual(action.intent, Intent.NEWS)
        self.assertEqual(query(action.url)["q"], ["погоду"])
        self.assistant.handle("Покажи новости")
        action = self.assistant.handle("Про прогноз погоды")
        self.assertEqual(action.intent, Intent.NEWS)
        self.assertEqual(query(action.url)["q"], ["прогноз погоды"])

    def test_new_command_overrides_pending_question(self):
        self.assistant.activate()
        self.assistant.handle("Покажи новости")
        self.assertEqual(self.assistant.handle("Погода на завтра").intent, Intent.WEATHER)
        self.assertIsNone(self.assistant.pending)

    def test_sleep_and_manual_button_clear_pending_topic(self):
        self.assistant.activate()
        self.assistant.handle("Новости")
        self.assistant.sleep()
        self.assertIsNone(self.assistant.pending)
        self.assistant.activate()
        self.assertEqual(self.assistant.handle("нейросети").intent, Intent.UNKNOWN)

    def test_cancel_and_repeat_name_clear_pending(self):
        self.assistant.activate()
        self.assistant.handle("Новости")
        self.assistant.handle("Отмена")
        self.assertIsNone(self.assistant.pending)
        self.assistant.handle("Новости")
        self.assistant.handle("Джарвис")
        self.assertIsNone(self.assistant.pending)

    def test_unknown_command_does_not_produce_url(self):
        action = self.assistant.handle("Джарвис удали все файлы")
        self.assertEqual(action.intent, Intent.UNKNOWN)
        self.assertFalse(action.url)
        self.assertEqual(self.assistant.mode, Mode.ACTIVE)

    def test_topic_cannot_inject_additional_url_parameters(self):
        self.assistant.activate()
        action = self.assistant.handle("Новости про --new-window & q=другое #frag")
        self.assertEqual(urlsplit(action.url).netloc, "www.google.com")
        self.assertEqual(set(query(action.url)), {"q", "tbm", "hl"})


if __name__ == "__main__":
    unittest.main()

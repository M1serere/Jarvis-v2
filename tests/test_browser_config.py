from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jarvis.browser import BrowserError, open_in_chrome
from jarvis.config import Settings, load_settings, save_settings


class ConfigurationTests(unittest.TestCase):
    def test_utf8_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_settings(Settings(city="Владивосток"), path)
            loaded = load_settings(path)
            self.assertEqual(loaded.city, "Владивосток")
            self.assertEqual(loaded.wake_words, ["джарвис"])
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_invalid_settings_preserve_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"wake_words": []}', encoding="utf-8")
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                load_settings(path)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_types_rejected(self):
        for values in ({"city": 123}, {"microphone": True}, {"activation_sound": "false"}, {"wake_words": ["два слова"]}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings(**values).validate()


class ChromeTests(unittest.TestCase):
    @patch("jarvis.browser.subprocess.Popen")
    @patch("jarvis.browser.find_chrome", return_value=Path("C:/Chrome/chrome.exe"))
    def test_url_is_one_argument_without_shell(self, find, launch):
        url = "https://www.google.com/search?q=hello%26whoami"
        open_in_chrome(url)
        args, kwargs = launch.call_args
        self.assertEqual(args[0], [str(find.return_value), "--new-tab", url])
        self.assertIs(kwargs["shell"], False)

    def test_reject_unexpected_browser_targets(self):
        for url in ("file:///C:/Windows", "javascript:alert(1)", "https://other.example/search", "https://www.google.com.evil/search"):
            with self.subTest(url=url), self.assertRaises(BrowserError):
                open_in_chrome(url)

    @patch("jarvis.browser.find_chrome", return_value=None)
    def test_missing_chrome_is_an_explicit_error(self, find):
        with self.assertRaisesRegex(BrowserError, "Chrome"):
            open_in_chrome("https://www.google.com/search?q=weather")


if __name__ == "__main__":
    unittest.main()

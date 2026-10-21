"""Запуск настоящих CMD-файлов в изолированной папке без микрофона и сети."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import venv


ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.name == "nt", "Проверка запуска через Windows cmd.exe")
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        cache = ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="launchers-", dir=cache)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / "Проверка запуска с пробелами"
        self.directory.mkdir()
        for name in ("run.cmd", "setup.cmd", "demo.cmd"):
            (self.directory / name).write_bytes((ROOT / name).read_bytes())

    def make_runtime(self):
        venv.EnvBuilder(with_pip=False).create(self.directory / ".venv")
        (self.directory / "main.py").write_text(
            "import json, sys\n"
            "from pathlib import Path\n"
            "Path(__file__).with_name('launched.json').write_text(\n"
            "    json.dumps({'args': sys.argv[1:], 'cwd': str(Path.cwd())}), encoding='utf-8')\n",
            encoding="utf-8",
        )

    def execute(self, name):
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(self.directory / name)],
            cwd=ROOT, input="", capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        self.assertNotIn("is not recognized", result.stdout + result.stderr)
        return result

    def launched(self):
        marker = self.directory / "launched.json"
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(marker.exists(), "CMD-файл не запустил Python")
        return json.loads(marker.read_text(encoding="utf-8"))

    def test_run_without_environment_shows_complete_russian_message(self):
        result = self.execute("run.cmd")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Сначала запусти setup.cmd.", result.stdout)

    def test_run_starts_pythonw_from_project_directory(self):
        self.make_runtime()
        result = self.execute("run.cmd")
        self.assertEqual(result.returncode, 0)
        launched = self.launched()
        self.assertEqual(launched["args"], [])
        self.assertEqual(Path(launched["cwd"]), self.directory)

    def test_demo_passes_demo_argument(self):
        self.make_runtime()
        result = self.execute("demo.cmd")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.launched()["args"], ["--demo"])

    def test_setup_runs_both_installation_steps(self):
        self.make_runtime()
        # Подменяем только внешние операции установки, сам setup.cmd выполняется
        # целиком настоящим cmd.exe. Ни скачивания, ни установки пакетов в тесте нет.
        (self.directory / "pip.py").write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "Path('pip-args.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
            encoding="utf-8",
        )
        (self.directory / "tools").mkdir()
        (self.directory / "tools/download_model.py").write_text(
            "from pathlib import Path\nPath('model-installed.txt').write_text('ok', encoding='utf-8')\n",
            encoding="utf-8",
        )
        result = self.execute("setup.cmd")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.directory / "pip-args.json").read_text()), ["install", "-r", "requirements.txt"])
        self.assertTrue((self.directory / "model-installed.txt").exists())
        self.assertIn("Установка завершена.", result.stdout)


if __name__ == "__main__":
    unittest.main()

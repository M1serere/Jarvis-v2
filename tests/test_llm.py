import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import queue
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from jarvis.config import Settings
from jarvis.llm import (ModelError, ModelEvent, OllamaClient, build_messages, parse_reply,
                        validate_endpoint, validate_model)
from jarvis.personal import LocalCommand
from jarvis.ui import JarvisWindow


def answer(kind="chat", message="Привет. Рад тебя слышать.", operation="", argument=""):
    return json.dumps(dict(kind=kind, message=message, operation=operation, argument=argument), ensure_ascii=False)


class ReplyValidationTests(unittest.TestCase):
    def test_untrusted_actions_and_ambiguous_responses_are_rejected(self):
        for content in ("готово", "[]", answer("action", operation="shutdown"),
                        answer("action", operation="shell", argument="whoami"),
                        answer("chat", operation="add_note", argument="Не сохранять"),
                        answer("action", operation="list_notes", argument="DROP TABLE notes"),
                        '{"kind":"chat","message":false,"operation":"","argument":""}'):
            with self.subTest(content=content), self.assertRaises(ModelError):
                parse_reply(content)

    def test_model_success_message_is_not_used_for_actions(self):
        reply = parse_reply(answer("action", "Уже всё сохранил", "add_note", "Купить чай"))
        self.assertEqual(reply.command, LocalCommand("add_note", "Купить чай"))
        self.assertEqual(reply.message, "")

    def test_unsupported_request_uses_application_message(self):
        reply = parse_reply(answer("unsupported", "Я выключил ноутбук"))
        self.assertIn("Ничего не выполнено", reply.message)
        self.assertNotIn("выключил", reply.message)

    def test_incomplete_model_schedule_becomes_a_question_before_execution(self):
        for operation, argument in (("add_reminder", "пирог"), ("add_reminder", "через 30 минут"),
                                    ("add_timer", "когда-нибудь"), ("add_note", "")):
            with self.subTest(operation=operation, argument=argument):
                reply = parse_reply(answer("action", operation=operation, argument=argument))
                self.assertEqual(reply.kind, "clarify")
                self.assertIsNone(reply.command)
                self.assertIn("?", reply.message)

    def test_only_local_endpoints_and_local_model_names_allowed(self):
        for endpoint in ("https://ollama.com", "http://192.168.1.2:11434", "http://localhost.evil:11434",
                         "http://user:password@localhost:11434", "http://localhost:11434/path", "http://localhost:0"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                validate_endpoint(endpoint)
        for name in ("qwen:cloud", "qwen-cloud", "org/model", "https://server/model", "qwen;whoami", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_model(name)
        validate_endpoint("http://127.0.0.1:11434")
        validate_endpoint("http://[::1]:11434")
        validate_model("qwen3:1.7b")

    def test_history_is_bounded_and_personality_is_separate_from_request(self):
        history = [{"role": "user", "content": "x" * 3000} for _ in range(100)]
        messages = build_messages("Спокойный и ироничный", history, "Привет", ["Блокнот"])
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Спокойный и ироничный", messages[0]["content"])
        self.assertIn("Блокнот", messages[0]["content"])
        self.assertLessEqual(sum(len(m["content"]) for m in messages[1:-1]), 10000)
        self.assertEqual(messages[-1], {"role": "user", "content": "Привет"})

    def test_model_settings_reject_wrong_types_and_ranges(self):
        for values in ({"llm_enabled": "yes"}, {"llm_timeout": True}, {"llm_timeout": 0},
                       {"llm_url": 123}, {"llm_model": "qwen:cloud"}, {"personality": ""}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings(**values).validate()


class LocalAPIScenarios(unittest.TestCase):
    def setUp(self):
        self.responses = queue.Queue()
        self.requests = []
        self.show_data = {}
        self.hold_response = None
        self.request_started = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, body))
                status, payload = (200, owner.show_data) if self.path == "/api/show" else owner.responses.get(timeout=5)
                if self.path == "/api/chat" and owner.hold_response is not None:
                    owner.request_started.set()
                    owner.hold_response.wait(5)
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = OllamaClient(f"http://127.0.0.1:{self.server.server_port}", "qwen3:1.7b")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_real_http_serializes_russian_and_schema(self):
        self.responses.put((200, {"done": True, "message": {"content": answer("action", operation="add_reminder", argument="через 30 минут проверить пирог")}}))
        result = self.client.reply(build_messages("Джарвис", [], "Пни через полчаса", []))
        self.assertEqual(result.command, LocalCommand("add_reminder", "через 30 минут проверить пирог"))
        path, payload = self.requests[-1]
        self.assertEqual(path, "/api/chat")
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertIn("Пни через полчаса", payload["messages"][-1]["content"])
        self.assertEqual(payload["format"]["type"], "object")

    def test_http_failures_and_truncated_answers_are_explicit(self):
        for status, payload in ((404, {}), (503, {}), (302, {}),
                                (200, {"done": True, "done_reason": "length", "message": {"content": answer()}}),
                                (200, {"done": True, "message": {"content": "Не JSON"}})):
            self.responses.put((status, payload))
            with self.subTest(status=status, payload=payload), self.assertRaises(ModelError):
                self.client.reply([])
        self.assertEqual(len([r for r in self.requests if r[0] == "/api/chat"]), 5)

    def test_cloud_alias_is_rejected_before_sending_conversation(self):
        self.show_data = {"remote_host": "https://ollama.com", "remote_model": "cloud-model"}
        with self.assertRaisesRegex(ModelError, "облако"):
            self.client.reply([{"role": "user", "content": "Личный разговор"}])
        self.assertEqual([r[0] for r in self.requests], ["/api/show"])

    def test_cancelled_request_never_contacts_server(self):
        self.client.cancel()
        with self.assertRaises(ModelError):
            self.client.reply([])
        self.assertEqual(self.requests, [])

    def test_inflight_request_can_be_interrupted(self):
        self.hold_response = threading.Event()
        self.responses.put((200, {"done": True, "message": {"content": answer()}}))
        result = []
        def request():
            try:
                result.append(self.client.reply([]))
            except ModelError as exc:
                result.append(exc)
        worker = threading.Thread(target=request, daemon=True)
        worker.start()
        try:
            self.assertTrue(self.request_started.wait(2))
            self.client.cancel()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(len(result), 1)
            self.assertIsInstance(result[0], ModelError)
        finally:
            self.hold_response.set()
            worker.join(timeout=2)


class ModelWindowScenarios(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.root.withdraw()
        self.voice_patch = patch("jarvis.ui.VoiceWorker")
        voice = self.voice_patch.start()
        voice.return_value.is_alive.return_value = False
        self.app = JarvisWindow(self.root, Settings(activation_sound=False), Mock(spec=logging.Logger), data_path=":memory:")
        self.ensure_patch = patch.object(self.app.model_runtime, "ensure")
        self.ensure_patch.start()

    def tearDown(self):
        self.app.close()
        self.ensure_patch.stop()
        self.voice_patch.stop()
        self.app, self.root = None, None
        gc.collect()

    def submit(self, text):
        self.app.text_input.insert(0, text)
        self.app._submit_demo()

    def wait_reply(self):
        deadline = time.monotonic() + 3
        while self.app._model_request is not None and time.monotonic() < deadline:
            self.app._poll()
            time.sleep(0.005)
        self.assertIsNone(self.app._model_request)

    @patch("jarvis.ui.OllamaClient.reply")
    def test_clarification_and_free_form_action_reach_real_storage(self, reply):
        reply.side_effect = [parse_reply(answer("clarify", "Через сколько напомнить проверить пирог?")),
                             parse_reply(answer("action", "Всё готово", "add_reminder", "через 30 минут проверить пирог"))]
        self.app.personal.clock = lambda: 1000.0
        self.submit("Я поставила пирог, пни меня позже")
        self.wait_reply()
        self.assertEqual(self.app.store.alarms(), [])
        self.submit("Минут через тридцать")
        self.wait_reply()
        row = self.app.store.alarms()[0]
        self.assertEqual(row["due_at"], 2800)
        self.assertEqual(row["text"], "проверить пирог")
        self.assertIn("Через сколько", str(reply.call_args.args[0]))
        self.assertNotIn("Всё готово", self.app.history.get("1.0", "end"))

    @patch("jarvis.ui.OllamaClient.reply", side_effect=ModelError("Модель недоступна"))
    def test_model_outage_keeps_regular_commands_and_alarms_working(self, reply):
        self.submit("Привет, как дела?")
        self.wait_reply()
        self.assertIn("Модель недоступна", self.app.history.get("1.0", "end"))
        self.submit("Создай заметку: Работает без модели!")
        self.submit("Таймер на 5 секунд")
        self.assertEqual(self.app.store.notes()[0]["text"], "Работает без модели!")
        self.assertEqual(len(self.app.store.alarms()), 1)
        self.assertEqual(reply.call_count, 1)

    def test_late_model_action_is_ignored_after_cancel_or_new_command(self):
        late = parse_reply(answer("action", operation="add_note", argument="Не сохранять"))
        for phrase in ("Отмена", "Джарвис стоп", "Создай заметку: Настоящая"):
            self.app.assistant.activate()
            self.app._model_request = 100
            self.app._model_client = Mock()
            self.submit(phrase)
            self.app.events.put(ModelEvent("model_reply", "", 100, late))
            self.app._poll()
        self.assertEqual([r["text"] for r in self.app.store.notes()], ["Настоящая"])

    @patch("jarvis.ui.OllamaClient.reply")
    def test_unknown_program_cannot_execute_arbitrary_path_or_claim_success(self, reply):
        reply.return_value = parse_reply(answer("action", "Программа уже открыта", "open_program", "C:/Windows/notepad.exe"))
        with patch.object(self.app.personal, "launcher") as launch:
            self.submit("Мне нужен редактор текста")
            self.wait_reply()
            launch.assert_not_called()
        history = self.app.history.get("1.0", "end")
        self.assertIn("Не выполнено", history)
        self.assertNotIn("Программа уже открыта", history)

    @patch("jarvis.ui.OllamaClient.reply")
    def test_reset_conversation_and_disabled_model(self, reply):
        self.app._conversation.append({"role": "user", "content": "Старый разговор"})
        self.app.reset_conversation()
        self.assertEqual(self.app._conversation, [])
        self.app.settings.llm_enabled = False
        self.submit("Поговорим о музыке")
        reply.assert_not_called()
        self.assertIn("выключена", self.app.detail.get())


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from desktop_hud.api import HudAPIHandler


class ApiDispatchTests(unittest.TestCase):
    def _make_handler(self):
        handler = object.__new__(HudAPIHandler)
        handler.sent = []
        handler.main_thread_timeout_seconds = 5.0

        def send_json(payload, status=200):
            handler.sent.append((status, payload))

        handler._send_json = send_json
        return handler

    def test_dispatch_to_main_thread_uses_configured_timeout(self):
        handler = self._make_handler()
        handler.main_thread_timeout_seconds = 7.5

        class FakeEvent:
            def __init__(self):
                self.wait_timeout = None

            def wait(self, timeout=None):
                self.wait_timeout = timeout
                return False

            def set(self):
                return None

        fake_event = FakeEvent()

        with patch("desktop_hud.api.threading.Event", return_value=fake_event), patch(
            "desktop_hud.api.GLib.idle_add",
            return_value=1,
        ):
            result = HudAPIHandler._dispatch_to_main_thread(handler, lambda: True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "main_thread_timeout")
        self.assertEqual(fake_event.wait_timeout, 7.5)
        self.assertIn("7.5", result["message"])

    def test_dispatch_timeout_returns_504(self):
        handler = self._make_handler()
        handler._dispatch_to_main_thread = lambda func, *args: {
            "ok": False,
            "error": "main_thread_timeout",
            "message": "timed out",
        }

        result = handler._dispatch_value_or_error(lambda: True)
        self.assertIs(result, handler._DISPATCH_FAILED)
        self.assertEqual(handler.sent[0][0], 504)
        self.assertEqual(handler.sent[0][1]["error"], "main_thread_timeout")

    def test_dispatch_exception_returns_500(self):
        handler = self._make_handler()
        handler._dispatch_to_main_thread = lambda func, *args: {
            "ok": False,
            "error": "main_thread_exception",
            "message": "boom",
        }

        result = handler._dispatch_value_or_error(lambda: True)
        self.assertIs(result, handler._DISPATCH_FAILED)
        self.assertEqual(handler.sent[0][0], 500)
        self.assertEqual(handler.sent[0][1]["error"], "main_thread_exception")

    def test_ensure_dict_result_rejects_non_dict(self):
        handler = self._make_handler()
        payload = handler._ensure_dict_result(None, "/profiles/switch")
        self.assertIsNone(payload)
        self.assertEqual(handler.sent[0][0], 500)
        self.assertEqual(handler.sent[0][1]["error"], "invalid_response")


if __name__ == "__main__":
    unittest.main()

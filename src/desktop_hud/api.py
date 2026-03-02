"""HTTP API server for runtime control of HUD elements."""

from __future__ import annotations

import json
import logging
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer

from gi.repository import GLib

log = logging.getLogger(__name__)


class HudAPIHandler(BaseHTTPRequestHandler):
    """Handles REST API requests for the HUD."""

    _DISPATCH_FAILED = object()

    def __init__(
        self,
        hud_window,
        *args,
        main_thread_timeout_seconds: float = 5.0,
        **kwargs,
    ):
        self.hud_window = hud_window
        self.main_thread_timeout_seconds = max(0.1, float(main_thread_timeout_seconds))
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        log.debug(format, *args)

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}

        payload = self.rfile.read(length)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON") from exc

    def _dispatch_to_main_thread(self, func, *args):
        """Run a function on the GTK main thread and wait for result."""
        result = [None]
        error = [None]
        event = threading.Event()

        def on_main():
            try:
                result[0] = func(*args)
            except Exception as exc:  # pragma: no cover - defensive guard for API stability
                error[0] = exc
            finally:
                event.set()
            return False

        GLib.idle_add(on_main)
        timeout_seconds = self.main_thread_timeout_seconds
        if not event.wait(timeout=timeout_seconds):
            return {
                "ok": False,
                "error": "main_thread_timeout",
                "message": (
                    f"Main thread did not respond within "
                    f"{timeout_seconds:g} seconds"
                ),
            }

        if error[0] is not None:
            log.error(
                "Main-thread operation raised: %s",
                error[0],
                exc_info=(type(error[0]), error[0], error[0].__traceback__),
            )
            return {
                "ok": False,
                "error": "main_thread_exception",
                "message": str(error[0]),
            }

        return {"ok": True, "value": result[0]}

    def _dispatch_value_or_error(self, func, *args):
        outcome = self._dispatch_to_main_thread(func, *args)
        if outcome.get("ok"):
            return outcome.get("value")

        error_code = outcome.get("error", "main_thread_error")
        status = 504 if error_code == "main_thread_timeout" else 500
        self._send_json(
            {
                "error": error_code,
                "message": outcome.get("message", "Main-thread dispatch failed"),
            },
            status,
        )
        return self._DISPATCH_FAILED

    def _ensure_dict_result(self, result, endpoint: str) -> dict | None:
        if isinstance(result, dict):
            return result
        self._send_json(
            {
                "error": "invalid_response",
                "message": f"{endpoint} returned an invalid response",
            },
            500,
        )
        return None

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return

        if self.path == "/elements":
            elements = self._dispatch_value_or_error(self.hud_window.get_elements_info)
            if elements is self._DISPATCH_FAILED:
                return
            self._send_json({"elements": elements or []})
            return

        if self.path == "/mode":
            mode_info = self._dispatch_value_or_error(self.hud_window.get_mode_info)
            if mode_info is self._DISPATCH_FAILED:
                return
            self._send_json(mode_info)
            return

        if self.path == "/mode/diagnostics":
            diagnostics = self._dispatch_value_or_error(self.hud_window.get_mode_diagnostics)
            if diagnostics is self._DISPATCH_FAILED:
                return
            self._send_json(diagnostics)
            return

        if self.path == "/profiles":
            profiles = self._dispatch_value_or_error(self.hud_window.list_profiles)
            if profiles is self._DISPATCH_FAILED:
                return
            self._send_json(profiles)
            return

        if self.path == "/profiles/current":
            snapshot = self._dispatch_value_or_error(self.hud_window.get_current_layout_snapshot)
            if snapshot is self._DISPATCH_FAILED:
                return
            self._send_json(snapshot)
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            body = self._read_body()
        except ValueError as exc:
            self._send_json({"error": "invalid_json", "message": str(exc)}, 400)
            return

        if self.path == "/elements":
            ok = self._dispatch_value_or_error(self.hud_window._add_element, body)
            if ok is self._DISPATCH_FAILED:
                return
            if ok:
                self._send_json({"status": "added", "id": body.get("id")}, 201)
            else:
                self._send_json({"error": "failed to add element"}, 400)
            return

        if self.path == "/reload":
            from desktop_hud.config import load_config

            config = load_config()
            result = self._dispatch_value_or_error(self.hud_window.reload_config, config)
            if result is self._DISPATCH_FAILED:
                return
            self._send_json({"status": "reloaded"})
            return

        if self.path == "/mode":
            if "edit_mode" not in body:
                self._send_json({"error": "edit_mode is required"}, 400)
                return

            edit_mode = bool(body.get("edit_mode"))
            result = self._dispatch_value_or_error(self.hud_window.set_edit_mode, edit_mode)
            if result is self._DISPATCH_FAILED:
                return
            payload = self._ensure_dict_result(result, "/mode")
            if payload is None:
                return
            self._send_json(payload)
            return

        if self.path == "/profiles/switch":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return

            result = self._dispatch_value_or_error(self.hud_window.switch_profile, name)
            if result is self._DISPATCH_FAILED:
                return
            payload = self._ensure_dict_result(result, "/profiles/switch")
            if payload is None:
                return
            if payload.get("ok"):
                self._send_json(payload)
                return

            status = 404 if payload.get("error_code") == "profile_not_found" else 400
            self._send_json(payload, status)
            return

        if self.path == "/profiles/add":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return

            result = self._dispatch_value_or_error(self.hud_window.add_profile, name)
            if result is self._DISPATCH_FAILED:
                return
            payload = self._ensure_dict_result(result, "/profiles/add")
            if payload is None:
                return
            if payload.get("ok"):
                self._send_json(payload)
                return

            status = 404 if payload.get("error_code") == "profile_not_found" else 400
            self._send_json(payload, status)
            return

        if self.path == "/profiles/save":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return

            result = self._dispatch_value_or_error(self.hud_window.save_profile, name)
            if result is self._DISPATCH_FAILED:
                return
            payload = self._ensure_dict_result(result, "/profiles/save")
            if payload is None:
                return
            if payload.get("ok"):
                self._send_json(payload)
                return

            self._send_json(payload, 400)
            return

        if self.path == "/profiles/save-last-used":
            result = self._dispatch_value_or_error(self.hud_window.save_last_used_profile)
            if result is self._DISPATCH_FAILED:
                return
            payload = self._ensure_dict_result(result, "/profiles/save-last-used")
            if payload is None:
                return
            if payload.get("ok"):
                self._send_json(payload)
                return

            self._send_json(payload, 500)
            return

        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/elements/"):
            elem_id = self.path.split("/elements/", 1)[1]
            ok = self._dispatch_value_or_error(self.hud_window.remove_element, elem_id)
            if ok is self._DISPATCH_FAILED:
                return
            if ok:
                self._send_json({"status": "removed", "id": elem_id})
            else:
                self._send_json({"error": "element not found"}, 404)
            return

        self._send_json({"error": "not found"}, 404)

    def do_PATCH(self):
        if not self.path.startswith("/elements/"):
            self._send_json({"error": "not found"}, 404)
            return

        try:
            updates = self._read_body()
        except ValueError as exc:
            self._send_json({"error": "invalid_json", "message": str(exc)}, 400)
            return

        elem_id = self.path.split("/elements/", 1)[1]
        exists = self._dispatch_value_or_error(self.hud_window.has_element, elem_id)
        if exists is self._DISPATCH_FAILED:
            return
        if not exists:
            self._send_json({"error": "element not found"}, 404)
            return

        geometry_update = "position" in updates or "size" in updates

        if geometry_update:
            editable = self._dispatch_value_or_error(self.hud_window.is_element_editable, elem_id)
            if editable is self._DISPATCH_FAILED:
                return
            if not editable:
                self._send_json(
                    {
                        "error": "read_only_element",
                        "message": "Geometry changes are disabled for this element",
                    },
                    403,
                )
                return

        ok = self._dispatch_value_or_error(self.hud_window.update_element, elem_id, updates)
        if ok is self._DISPATCH_FAILED:
            return
        if ok:
            self._send_json({"status": "updated", "id": elem_id})
        else:
            self._send_json(
                {
                    "error": "update_failed",
                    "message": f"Element '{elem_id}' could not be updated",
                },
                500,
            )


def start_api_server(
    hud_window,
    port: int = 7820,
    main_thread_timeout_seconds: float = 5.0,
):
    """Start the API server (blocking — run in a thread)."""
    handler = partial(
        HudAPIHandler,
        hud_window,
        main_thread_timeout_seconds=main_thread_timeout_seconds,
    )
    server = HTTPServer(("127.0.0.1", port), handler)
    log.info(
        "API server listening on http://127.0.0.1:%d (main_thread_timeout=%ss)",
        port,
        f"{max(0.1, float(main_thread_timeout_seconds)):g}",
    )
    server.serve_forever()

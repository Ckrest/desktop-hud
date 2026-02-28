"""HTTP API server for runtime control of HUD elements."""

from __future__ import annotations

import json
import logging
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer

from gi.repository import GLib

log = logging.getLogger(__name__)


class HudAPIHandler(BaseHTTPRequestHandler):
    """Handles REST API requests for the HUD."""

    def __init__(self, hud_window, *args, **kwargs):
        self.hud_window = hud_window
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
        event = __import__("threading").Event()

        def on_main():
            result[0] = func(*args)
            event.set()
            return False

        GLib.idle_add(on_main)
        event.wait(timeout=5)
        return result[0]

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return

        if self.path == "/elements":
            elements = self._dispatch_to_main_thread(self.hud_window.get_elements_info)
            self._send_json({"elements": elements or []})
            return

        if self.path == "/mode":
            mode_info = self._dispatch_to_main_thread(self.hud_window.get_mode_info)
            self._send_json(mode_info)
            return

        if self.path == "/mode/diagnostics":
            diagnostics = self._dispatch_to_main_thread(self.hud_window.get_mode_diagnostics)
            self._send_json(diagnostics)
            return

        if self.path == "/profiles":
            profiles = self._dispatch_to_main_thread(self.hud_window.list_profiles)
            self._send_json(profiles)
            return

        if self.path == "/profiles/current":
            snapshot = self._dispatch_to_main_thread(self.hud_window.get_current_layout_snapshot)
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
            ok = self._dispatch_to_main_thread(self.hud_window._add_element, body)
            if ok:
                self._send_json({"status": "added", "id": body.get("id")}, 201)
            else:
                self._send_json({"error": "failed to add element"}, 400)
            return

        if self.path == "/reload":
            from desktop_hud.config import load_config

            config = load_config()
            self._dispatch_to_main_thread(self.hud_window.reload_config, config)
            self._send_json({"status": "reloaded"})
            return

        if self.path == "/mode":
            if "edit_mode" not in body:
                self._send_json({"error": "edit_mode is required"}, 400)
                return

            edit_mode = bool(body.get("edit_mode"))
            result = self._dispatch_to_main_thread(self.hud_window.set_edit_mode, edit_mode)
            self._send_json(result)
            return

        if self.path == "/profiles/switch":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return

            result = self._dispatch_to_main_thread(self.hud_window.switch_profile, name)
            if result.get("ok"):
                self._send_json(result)
                return

            status = 404 if result.get("error_code") == "profile_not_found" else 400
            self._send_json(result, status)
            return

        if self.path == "/profiles/add":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return

            result = self._dispatch_to_main_thread(self.hud_window.add_profile, name)
            if result.get("ok"):
                self._send_json(result)
                return

            status = 404 if result.get("error_code") == "profile_not_found" else 400
            self._send_json(result, status)
            return

        if self.path == "/profiles/save":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name is required"}, 400)
                return

            result = self._dispatch_to_main_thread(self.hud_window.save_profile, name)
            if result.get("ok"):
                self._send_json(result)
                return

            self._send_json(result, 400)
            return

        if self.path == "/profiles/save-last-used":
            result = self._dispatch_to_main_thread(self.hud_window.save_last_used_profile)
            if result.get("ok"):
                self._send_json(result)
                return

            self._send_json(result, 500)
            return

        self._send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/elements/"):
            elem_id = self.path.split("/elements/", 1)[1]
            ok = self._dispatch_to_main_thread(self.hud_window.remove_element, elem_id)
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
        exists = self._dispatch_to_main_thread(self.hud_window.has_element, elem_id)
        if not exists:
            self._send_json({"error": "element not found"}, 404)
            return

        geometry_update = "position" in updates or "size" in updates

        if geometry_update:
            editable = self._dispatch_to_main_thread(self.hud_window.is_element_editable, elem_id)
            if not editable:
                self._send_json(
                    {
                        "error": "read_only_element",
                        "message": "Geometry changes are disabled for this element",
                    },
                    403,
                )
                return

        ok = self._dispatch_to_main_thread(self.hud_window.update_element, elem_id, updates)
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


def start_api_server(hud_window, port: int = 7820):
    """Start the API server (blocking — run in a thread)."""
    handler = partial(HudAPIHandler, hud_window)
    server = HTTPServer(("127.0.0.1", port), handler)
    log.info("API server listening on http://127.0.0.1:%d", port)
    server.serve_forever()

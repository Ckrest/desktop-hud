import unittest

from desktop_hud.elements.base import ElementSkipRequested, HudElement
from desktop_hud.elements.image import ImageElement


class DummyElement(HudElement):
    def create_widget(self):
        return None


class ElementSourcePolicyTests(unittest.TestCase):
    def _make_element(self, **overrides):
        cfg = {
            "id": "dummy",
            "type": "image",
            "position": {"x": 0, "y": 0},
            "size": {"width": 10, "height": 10},
            "opacity": 1.0,
        }
        cfg.update(overrides)
        return DummyElement(cfg)

    def test_relative_source_paths_resolve_against_package_root(self):
        elem = self._make_element()
        resolved = elem.resolve_source_path("assets/showcase/botanical-illustration.svg")
        self.assertTrue(str(resolved).endswith("desktop-hud/assets/showcase/botanical-illustration.svg"))

    def test_invalid_missing_policy_falls_back_to_error(self):
        elem = self._make_element(on_missing_source="not-valid")
        self.assertEqual(elem.get_missing_source_policy(), "error")

    def test_skip_policy_raises_skip_exception(self):
        elem = self._make_element(on_missing_source="skip")
        with self.assertRaises(ElementSkipRequested):
            elem.handle_source_error("missing_source", "missing")

    def test_error_policy_raises_file_error_for_missing(self):
        elem = self._make_element(on_missing_source="error")
        with self.assertRaises(FileNotFoundError):
            elem.handle_source_error("missing_source", "missing")

    def test_placeholder_policy_uses_placeholder_builder(self):
        elem = self._make_element(on_missing_source="placeholder", placeholder_label="Custom")
        sentinel = object()
        elem._build_placeholder_widget = lambda title, detail: (sentinel, title, detail)
        result = elem.handle_source_error("missing_source", "missing file")
        self.assertEqual(result[0], sentinel)
        self.assertEqual(result[1], "Custom")

    def test_image_runtime_updates_require_recreate_for_source_changes(self):
        elem = ImageElement(
            {
                "id": "image-1",
                "type": "image",
                "source": "/tmp/example.png",
                "position": {"x": 0, "y": 0},
                "size": {"width": 100, "height": 100},
                "opacity": 1.0,
            },
        )
        self.assertTrue(elem.runtime_update_requires_recreate({"source": "/tmp/next.png"}))
        self.assertFalse(elem.runtime_update_requires_recreate({"opacity": 0.5}))


if __name__ == "__main__":
    unittest.main()

import unittest

from desktop_hud.elements.video import VideoElement


def _video_config(**overrides):
    cfg = {
        "id": "video-test",
        "type": "video",
        "source": "/tmp/example.mp4",
        "position": {"x": 0, "y": 0},
        "size": {"width": 100, "height": 100},
        "opacity": 1.0,
    }
    cfg.update(overrides)
    return cfg


class VideoElementRuntimeTests(unittest.TestCase):
    def test_backend_auto_selects_simple_when_alpha_false(self):
        elem = VideoElement(_video_config(alpha=False, backend="auto"))
        self.assertEqual(elem._select_backend(use_alpha=False), "simple")

    def test_backend_auto_selects_alpha_pipeline_when_alpha_true(self):
        elem = VideoElement(_video_config(alpha=True, backend="auto"))
        self.assertEqual(elem._select_backend(use_alpha=True), "alpha-pipeline")

    def test_backend_validation_rejects_unknown_value(self):
        elem = VideoElement(_video_config(backend="unsupported"))
        with self.assertRaises(ValueError):
            elem._select_backend(use_alpha=False)

    def test_runtime_update_requires_recreate_for_video_source_fields(self):
        elem = VideoElement(_video_config())
        self.assertTrue(elem.runtime_update_requires_recreate({"source": "/tmp/next.mp4"}))
        self.assertTrue(elem.runtime_update_requires_recreate({"backend": "simple"}))
        self.assertTrue(elem.runtime_update_requires_recreate({"loop": False}))

    def test_runtime_update_allows_geometry_without_recreate(self):
        elem = VideoElement(_video_config())
        self.assertFalse(
            elem.runtime_update_requires_recreate(
                {"position": {"x": 10, "y": 20}, "size": {"width": 200, "height": 120}},
            ),
        )

    def test_bool_parser_handles_string_flags(self):
        self.assertTrue(VideoElement._as_bool("true", default=False))
        self.assertFalse(VideoElement._as_bool("false", default=True))
        self.assertTrue(VideoElement._as_bool("yes", default=False))
        self.assertFalse(VideoElement._as_bool("off", default=True))

    def test_restart_media_stream_rewinds_and_plays(self):
        class FakeStream:
            def __init__(self):
                self.calls = []

            def seek(self, value):
                self.calls.append(("seek", value))

            def play(self):
                self.calls.append(("play", None))

        stream = FakeStream()
        self.assertTrue(VideoElement._restart_media_stream(stream))
        self.assertEqual(stream.calls, [("seek", 0), ("play", None)])

    def test_on_media_ended_restarts_when_loop_enabled(self):
        elem = VideoElement(_video_config(loop=True))
        elem._simple_loop_enabled = True

        class FakeStream:
            def __init__(self):
                self.restarted = 0

            def get_ended(self):
                return True

            def seek(self, value):
                if value == 0:
                    self.restarted += 1

            def play(self):
                return None

        stream = FakeStream()
        elem._on_media_ended(stream, None, "/tmp/example.mp4")
        self.assertEqual(stream.restarted, 1)

    def test_on_media_ended_ignores_when_loop_disabled(self):
        elem = VideoElement(_video_config(loop=False))
        elem._simple_loop_enabled = False

        class FakeStream:
            def __init__(self):
                self.restarted = 0

            def get_ended(self):
                return True

            def seek(self, value):
                if value == 0:
                    self.restarted += 1

            def play(self):
                return None

        stream = FakeStream()
        elem._on_media_ended(stream, None, "/tmp/example.mp4")
        self.assertEqual(stream.restarted, 0)


if __name__ == "__main__":
    unittest.main()

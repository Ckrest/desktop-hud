import tempfile
import unittest
from pathlib import Path

import yaml

from desktop_hud.layouts import LayoutProfileManager


class LayoutProfileSerializationTests(unittest.TestCase):
    def test_save_profile_preserves_element_source_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LayoutProfileManager(
                package_dir=Path(tmpdir),
                config={"layouts": {"directory": tmpdir}},
            )

            elements = [
                {
                    "id": "video-1",
                    "type": "video",
                    "source": "/tmp/fire.mp4",
                    "position": {"x": 100, "y": 200},
                    "size": {"width": 640, "height": 360},
                    "opacity": 0.9,
                    "editable": True,
                    "__source": "showcase",
                },
            ]

            saved_path = manager.save_profile("snapshot", elements)
            with open(saved_path, encoding="utf-8") as handle:
                payload = yaml.safe_load(handle)

            saved_element = payload["elements"][0]
            self.assertEqual(saved_element["source"], "/tmp/fire.mp4")
            self.assertNotIn("__source", saved_element)
            self.assertNotIn("editable", saved_element)


if __name__ == "__main__":
    unittest.main()

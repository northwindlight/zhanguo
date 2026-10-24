"""Shipped sprite frames must stay inside the atlas and contain a complete body."""
import hashlib
import json
from pathlib import Path
import unittest

try:
    from PIL import Image
except ImportError:
    Image = None

ASSETS = Path(__file__).resolve().parents[1] / "mods/ui/desktop/assets"


@unittest.skipIf(Image is None, "需要 Pillow 检查实际 PNG 帧")
class TestDesktopAssets(unittest.TestCase):
    def test_every_declared_character_frame_is_visible_and_in_bounds(self):
        sheets = json.loads((ASSETS / "actors/manifest.json").read_text())["sheets"]
        sources = json.loads((ASSETS / "actors/mini-world-characters.json").read_text())
        self.assertEqual(len(sources), 17)
        for source in sources:
            path = ASSETS / "actors" / source["file"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])
            sheet = sheets[path.stem]
            with Image.open(path) as image:
                alpha = image.getchannel("A")
                cw, ch = sheet["cell"]
                for action in ("idle", "walk", "attack"):
                    rows = sheet[action + "_rows"]
                    self.assertEqual(len(rows), 4)
                    for row in rows:
                        for frame in range(sheet[action + "_frames"]):
                            with self.subTest(file=source["file"], action=action, row=row, frame=frame):
                                bounds = (frame * cw, row * ch, (frame + 1) * cw, (row + 1) * ch)
                                self.assertLessEqual(bounds[2], image.width)
                                self.assertLessEqual(bounds[3], image.height)
                                bbox = alpha.crop(bounds).getbbox()
                                self.assertIsNotNone(bbox, "透明空帧会让士兵消失")
                                self.assertGreaterEqual(bbox[3] - bbox[1], 10, "不能把被切断的头或武器碎片当成人物帧")

    def test_user_portraits_keep_original_bytes_and_use_upper_body_regions(self):
        manifest = json.loads((ASSETS / "portraits/manifest.json").read_text())
        self.assertEqual(set(manifest["portraits"]), {"deepseek", "qwen", "kimi", "gemini", "gpt", "claude", "grok"})
        for key, entry in manifest["portraits"].items():
            path = ASSETS / "portraits" / entry["file"]
            with self.subTest(model=key), Image.open(path) as image:
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
                self.assertEqual(image.size, (entry["width"], entry["height"]))
                self.assertEqual(image.getchannel("A").getextrema(), (0, 255))
                x, y, width, height = entry["display_region"]
                self.assertEqual(y, 0, "上半身区域应保留头顶")
                self.assertGreater(height, 0)
                self.assertLess(height, image.height * .6)
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + width, image.width)


if __name__ == "__main__":
    unittest.main()

"""Distribution must contain its runtime sources, never user configs or saves."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.package_windows import ROOT, stage_runtime


class TestWindowsPackage(unittest.TestCase):
    def test_staging_does_not_copy_private_or_untracked_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "checkout"
            bundle = Path(tmp) / "bundle"
            names = ["host.py", "mods.toml", "ui.toml", "clients/provider.py", "mods/native/mod.toml",
                     "mp_config.json", "mp_config.example.json", "archive/session.json",
                     "userdata/desktop-settings.json", "mods/ui/desktop/assets/unified-v1/manifest.json"]
            for name in names + ["clients/not-tracked.json"]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("private-canary" if name.endswith(".json") else "runtime-source", encoding="utf-8")
            with patch("scripts.package_windows.tracked_files", return_value=[Path(p) for p in names]):
                stage_runtime(bundle, root)
            self.assertEqual({p.relative_to(bundle / "game").as_posix() for p in bundle.rglob("*") if p.is_file()},
                             {"host.py", "mods.toml", "ui.toml", "clients/provider.py", "mods/native/mod.toml"})
            self.assertFalse(any(b"private-canary" in p.read_bytes() for p in bundle.rglob("*") if p.is_file()))

    def test_curated_sources_boot_a_real_offline_world(self):
        with tempfile.TemporaryDirectory(prefix="zhanguo-portable-") as tmp:
            bundle = Path(tmp) / "中文游戏路径"
            stage_runtime(bundle)
            # Isolate sys.path from this checkout and from optional packages.
            code = """
import sys,json
sys.path.insert(0,'.')
from scenarios.eight_nations import build
h=build(seed=20261023)
f=h.ui.desktop_frame(h.observer_snapshot())
print(json.dumps({'tiles':len(f['tiles']),'armies':len(f['armies']),'clients':[c.version for c in h.clients.values()]}))
"""
            result = subprocess.run([sys.executable, "-I", "-S", "-c", code], cwd=bundle / "game",
                                    capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
            data = json.loads(result.stdout)
            self.assertEqual(data["tiles"], 576)
            self.assertEqual(data["armies"], 0)
            self.assertEqual(data["clients"], ["v13"] * 8)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Build the stock-only reviewer package (English-first, bilingual).

The package contains exactly what is needed to run the stock distribution offline and to run a
model evaluation: engine, stock mod, the tool/UI layers it imports, the bench harness, and an
English reviewer guide. It deliberately excludes the desktop UI assets (~51 MB), the research
archive, saves, journals and every other game distribution.

    python packaging/stock_release.py            # build + size check
    python packaging/stock_release.py --verify   # also run an offline episode from the package
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = Path(__file__).resolve().with_name("stock_release_files")
SIZE_LIMIT = 50 * 1024 * 1024

# 只收录「跑 stock 需要的最小球」：发行版清单、引擎、股票 mod、工具层、终端 UI、评测封装。
INCLUDE_DIRS = (
    "engine",
    "clients",
    "presentation",
    "ui",
    "mods/stock",
    "mods/native",
    "bench",
)
INCLUDE_FILES = (
    "host.py",
    "mp_run.py",
    "mods.stock.toml",
    "ui.toml",
    "mods/__init__.py",
    "mods/native/__init__.py",
    "VERSION",
    "LICENSE",
    "requirements-stock.txt",
)
# 发行版要用的 UI mod 后端在 mods/ui/*.py；desktop/ 是 Godot 界面资产，整目录排除。
UI_MOD_FILES = "mods/ui/*.py"
UI_MOD_EXTRA = ("mods/ui/mod.toml", "mods/ui/config.py")

EXCLUDE_NAMES = {"__pycache__", ".git", ".venv", "results", "reports", "*.pyc", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip", ".psd", ".pdb"}


def skip(path: Path) -> bool:
    if any(part in EXCLUDE_NAMES for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def copy_tree(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if item.is_dir() or skip(item):
            continue
        rel = item.relative_to(ROOT)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def version() -> str:
    path = ROOT / "VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "dev"


def build(out_dir: Path) -> Path:
    ver = version()
    name = f"stock-bench-{ver}"
    stage = out_dir / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for rel in INCLUDE_DIRS:
        src = ROOT / rel
        if not src.is_dir():
            raise SystemExit(f"missing required directory: {rel}")
        copy_tree(src, stage)
    for rel in INCLUDE_FILES:
        src = ROOT / rel
        if src.is_file():
            shutil.copy2(src, stage / rel)
    (stage / "mods" / "ui").mkdir(parents=True, exist_ok=True)
    for src in sorted(ROOT.glob(UI_MOD_FILES)):
        if src.name in {"desktop.py", "terminal.py"}:
            continue
        shutil.copy2(src, stage / "mods" / "ui" / src.name)
    for rel in UI_MOD_EXTRA:
        src = ROOT / rel
        if src.is_file():
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, stage / rel)

    # 评审材料（英文优先）
    for item in sorted(FILES.iterdir()):
        if item.is_file():
            shutil.copy2(item, stage / item.name)
    for extra in ("README.en.md",):
        src = ROOT / extra
        if src.is_file():
            shutil.copy2(src, stage / extra)

    archive = out_dir / f"{name}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for item in sorted(stage.rglob("*")):
            if item.is_file():
                zf.write(item, item.relative_to(stage.parent))
    return archive


def tree_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def verify(stage: Path) -> int:
    """Run one offline episode using the packaged files only."""
    python = sys.executable
    cmd = [python, "bench/run_eval.py", "run", "--spec", "bench/episodes/quick.json",
           "--name", "package-verify"]
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=stage)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the stock-only reviewer package")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output directory")
    ap.add_argument("--verify", action="store_true", help="run an offline episode from the package")
    args = ap.parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    archive = build(out_dir)
    stage = archive.with_suffix("")
    size = archive.stat().st_size
    print(f"built {archive}")
    print(f"uncompressed {tree_size(stage) / 1e6:.1f} MB · archive {size / 1e6:.1f} MB "
          f"(limit {SIZE_LIMIT / 1e6:.0f} MB)")
    if size > SIZE_LIMIT:
        print("ERROR: archive exceeds the 50 MB limit", file=sys.stderr)
        return 1
    if args.verify:
        code = verify(stage)
        if code != 0:
            print("ERROR: packaged self-test failed", file=sys.stderr)
            return code
        print("packaged self-test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

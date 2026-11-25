#!/usr/bin/env python3
"""Build the stock-only reviewer package (English-first, bilingual, double-blind clean).

The archive contains exactly what is needed to run the stock distribution offline and to run a
model evaluation: engine, stock mod, the tool/UI layers it imports, the bench harness and an
English reviewer guide. It excludes the desktop UI assets (~51 MB), the research archive, saves,
journals and every other game distribution.

Double-blind mode (default) additionally:

* drops repository-facing docs (English README, native dev docs) that carry project links;
* renames the searchable project name in code, catalogs and prose to a neutral one;
* strips authoring absolute paths and personal URLs from comments/strings;
* writes every archive entry with the same fixed timestamp (no authoring-time/timezone leak);
* runs `packaging/anonymity_check.py` on the staged tree *and* the finished archive and fails
  the build if anything identity-shaped survives.

    python packaging/stock_release.py                 # build + anonymity gate
    python packaging/stock_release.py --verify        # also run an offline episode from it
    python packaging/stock_release.py --no-anonymous  # internal build, keeps repository docs
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = Path(__file__).resolve().with_name("stock_release_files")
SIZE_LIMIT = 50 * 1024 * 1024
FIXED_STAMP = (2026, 1, 1, 0, 0, 0)

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
    "requirements/stock.txt",
)
# 发行版要用的 UI mod 后端在 mods/ui/*.py；desktop/ 是 Godot 界面资产，整目录排除。
UI_MOD_FILES = "mods/ui/*.py"
UI_MOD_EXTRA = ("mods/ui/mod.toml", "mods/ui/config.py")

# 双盲模式下不随包发布的文档：它们含仓库地址、开发史与个人链接。
ANONYMOUS_DROP_FILES = {"README.en.md", "mods/native/README.md", "mods/stock/THIRD_PARTY.md"}
# 仓库习惯文件与各子模块的开发说明：不影响运行，双盲包里一律不带。
ANONYMOUS_DROP_PATTERNS = ("*.gitignore", "mods/native/**/README.md")
ANONYMOUS_DROP_DIRS = {"mods/native/docs"}

EXCLUDE_NAMES = {"__pycache__", ".git", ".venv", "results", "reports", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".zip", ".psd", ".pdb", ".log", ".save"}
TEXT_SUFFIXES = {".md", ".py", ".json", ".toml", ".txt", ".sh", ".ps1", ".bat", ""}

RENAME = "stockbench"
ORIGINAL_PROJECT = "zhanguo"   # 门禁要确认的是「原名是否已消失」


def skip(path: Path, anonymous: bool) -> bool:
    if any(part in EXCLUDE_NAMES for part in path.parts):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    rel = path.relative_to(ROOT).as_posix()
    if anonymous:
        if rel in ANONYMOUS_DROP_FILES:
            return True
        if any(rel == d or rel.startswith(d + "/") for d in ANONYMOUS_DROP_DIRS):
            return True
        if any(path.relative_to(ROOT).match(pat) for pat in ANONYMOUS_DROP_PATTERNS):
            return True
    return False


def copy_tree(src: Path, dst: Path, anonymous: bool) -> None:
    for item in src.rglob("*"):
        if item.is_dir() or skip(item, anonymous):
            continue
        target = dst / item.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def version() -> str:
    path = ROOT / "VERSION"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "dev"


def stage_package(out_dir: Path, anonymous: bool) -> Path:
    name = f"stock-bench-{version()}"
    stage = out_dir / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for rel in INCLUDE_DIRS:
        src = ROOT / rel
        if not src.is_dir():
            raise SystemExit(f"missing required directory: {rel}")
        copy_tree(src, stage, anonymous)
    for rel in INCLUDE_FILES:
        src = ROOT / rel
        if src.is_file() and not skip(src, anonymous):
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, stage / rel)
    (stage / "mods" / "ui").mkdir(parents=True, exist_ok=True)
    for src in sorted(ROOT.glob(UI_MOD_FILES)):
        if src.name in {"desktop.py", "terminal.py"} or skip(src, anonymous):
            continue
        shutil.copy2(src, stage / "mods" / "ui" / src.name)
    for rel in UI_MOD_EXTRA:
        src = ROOT / rel
        if src.is_file() and not skip(src, anonymous):
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, stage / rel)

    # 评审材料（英文优先）与离线示例配置
    for item in sorted(FILES.iterdir()):
        if item.is_file():
            shutil.copy2(item, stage / item.name)
    return stage


def scrub(stage: Path) -> list:
    """Rewrite prose/code so the package is self-contained and non-identifying."""
    changed = []
    for path in sorted(stage.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        original = text
        # 可搜索的项目名 → 中性名（保留大小写风格）
        text = (text.replace("ZHANGUO", RENAME.upper())
                    .replace("Zhanguo", RENAME.capitalize())
                    .replace("zhanguo", RENAME))
        # 个人页面/仓库链接整段移除
        text = re.sub(r"https?://[^\s)\]<>]*northwind[^\s)\]<>]*", "https://example.invalid/removed", text)
        # 版权署名：双盲发布改为匿名持有者（保留许可证其余条款不变）
        text = re.sub(r"Copyright \(c\) \d{4}[^\n]*",
                      "Copyright (c) 2026 Anonymous Authors", text)
        # 行尾的本地绝对路径注释（D:\code\..., C:\Users\...）
        text = re.sub(r"[ \t]*#[ \t]*[A-Za-z]:\\[^\n]*", "", text)
        text = re.sub(r"[ \t]*#[ \t]*(?:/home/|/Users/)[^\n]*", "", text)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed.append(path.relative_to(stage).as_posix())
    return changed


def write_archive(stage: Path, out_dir: Path) -> Path:
    archive = out_dir / f"{stage.name}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for item in sorted(stage.rglob("*")):
            if not item.is_file():
                continue
            arcname = item.relative_to(stage.parent).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=FIXED_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            zf.writestr(info, item.read_bytes())
    return archive


def tree_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def clean_runtime_artifacts(stage: Path) -> None:
    """A self-test run creates caches and a result file; the staged tree must match the archive."""
    for name in ("bench/results", "bench/reports"):
        target = stage / name
        if target.exists():
            shutil.rmtree(target)
    for cache in stage.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def verify(stage: Path) -> int:
    """Run one offline episode using the packaged files only."""
    cmd = [sys.executable, "bench/run_eval.py", "run", "--spec", "bench/episodes/quick.json",
           "--name", "package-verify"]
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=stage).returncode


def run_gate(stage: Path, archive: Path, project: str) -> int:
    checker = Path(__file__).with_name("anonymity_check.py")
    code = 0
    for target in (stage, archive):
        proc = subprocess.run([sys.executable, str(checker), str(target),
                               "--project-name", project])
        code = code or proc.returncode
    return code


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the stock-only reviewer package")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output directory")
    ap.add_argument("--verify", action="store_true", help="run an offline episode from the package")
    ap.add_argument("--no-anonymous", action="store_true",
                    help="internal build: keep repository docs and skip the anonymity gate")
    args = ap.parse_args()
    anonymous = not args.no_anonymous
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    stage = stage_package(out_dir, anonymous)
    if anonymous:
        changed = scrub(stage)
        print(f"scrubbed {len(changed)} text file(s) for double-blind release")

    archive = write_archive(stage, out_dir)
    size = archive.stat().st_size
    print(f"built {archive}")
    print(f"uncompressed {tree_size(stage) / 1e6:.1f} MB · archive {size / 1e6:.1f} MB "
          f"(limit {SIZE_LIMIT / 1e6:.0f} MB)")
    if size > SIZE_LIMIT:
        print("ERROR: archive exceeds the 50 MB limit", file=sys.stderr)
        return 1
    if anonymous:
        code = run_gate(stage, archive, ORIGINAL_PROJECT)
        if code != 0:
            print("ERROR: double-blind gate failed (see findings above)", file=sys.stderr)
            return 1
    if args.verify:
        code = verify(stage)
        clean_runtime_artifacts(stage)
        if code != 0:
            print("ERROR: packaged self-test failed", file=sys.stderr)
            return code
        print("packaged self-test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

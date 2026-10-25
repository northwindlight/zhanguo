"""Build a self-contained Godot/.NET + embedded Python Windows review ZIP.

Run on Windows CI or a Windows build machine. Downloads are pinned and verified;
only tracked runtime sources and reviewed assets enter the output directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
GODOT_VERSION = "4.7.2"
PYTHON_VERSION = "3.13.17"
DOWNLOADS = {
    "godot": (f"Godot_v{GODOT_VERSION}-stable_mono_win64.zip", "a2a48473a7414c5f19fab690518caebb738c09ef9601f6bd2388676a7f53b3c0"),
    "templates": (f"Godot_v{GODOT_VERSION}-stable_mono_export_templates.tpz", "92f8681e349ef1f90891b792da95e3b2b0bd1ed610b78018c58feb2d87e15a9d"),
    "python": (f"python-{PYTHON_VERSION}-embed-amd64.zip", "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"),
}
RUNTIME_ROOTS = {"engine", "clients", "mods", "presentation", "ruleai", "scenarios", "ui"}
RUNTIME_SUFFIXES = {".py", ".json", ".toml", ".md", ".txt", ".c", ".h", ".cpp"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tracked_files(root=ROOT):
    return [Path(name) for name in subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0") if name]


def runtime_files(root=ROOT):
    """Allow-list of shipped engine data; never copy an existing working tree."""
    for path in tracked_files(root):
        if len(path.parts) == 1:
            if path.suffix == ".toml" or path.name in {"host.py", "mp_run.py", "settlement.py"}:
                yield path
        elif path.parts[0] in RUNTIME_ROOTS and path.suffix in RUNTIME_SUFFIXES:
            if path.parts[:3] != ("mods", "ui", "desktop"):
                yield path


def download(kind, cache):
    name, expected = DOWNLOADS[kind]
    path = cache / name
    if path.is_file() and digest(path) == expected:
        return path
    url = (f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{name}" if kind == "python" else
           f"https://github.com/godotengine/godot-builds/releases/download/{GODOT_VERSION}-stable/{name}")
    print(f"Download {kind}: {url}", flush=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as out:
        shutil.copyfileobj(response, out, 1024 * 1024)
    if digest(temporary) != expected:
        temporary.unlink()
        raise ValueError(f"{name}: download SHA-256 mismatch")
    temporary.replace(path)
    return path


def extract(archive, destination, selected=None):
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            path = Path(entry.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe archive member: " + entry.filename)
            if selected is None or selected(entry.filename):
                source.extract(entry, destination)


def tools(cache):
    editor = cache / "editor"
    archive = download("godot", cache)
    if not editor.is_dir():
        extract(archive, editor)
    godot = next(editor.rglob("*_console.exe"))
    templates = download("templates", cache)
    with zipfile.ZipFile(templates) as source:
        version = source.read("templates/version.txt").decode().strip()
    target = Path(os.environ["APPDATA"]) / "Godot/export_templates" / version
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(templates) as source:
        # Only Windows templates and their DLLs are needed, not every platform.
        for entry in source.infolist():
            name = Path(entry.filename)
            if entry.is_dir() or not (name.name.startswith("windows_") or name.suffix == ".dll" or name.name == "version.txt"):
                continue
            relative = Path(*name.parts[1:])
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            with source.open(entry) as stream, out.open("wb") as dest:
                shutil.copyfileobj(stream, dest)
    return godot


def stage_runtime(bundle, root=ROOT):
    for relative in runtime_files(root):
        path = bundle / "game" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, path)


def stage_sources(bundle, root=ROOT):
    stage_runtime(bundle, root)
    for file in (root / "packaging/windows").iterdir():
        if file.is_file():
            shutil.copy2(file, bundle / file.name)
    review = bundle / "美术预览"
    shutil.copytree(root / "docs/design/unified-v1", review)
    shutil.copytree(root / "mods/ui/desktop/assets/unified-v1", review / "素材",
                    ignore=shutil.ignore_patterns("*.import"))
    code = review / "CSharp"
    code.mkdir()
    for relative in tracked_files(root):
        if relative.parts[:4] == ("mods", "ui", "desktop", "Scripts") and relative.suffix == ".cs":
            shutil.copy2(root / relative, code / relative.name)
    shutil.copy2(root / "docs/design/gui-unified-tour-20261024.mp4", bundle / "演出参考.mp4")
    (bundle / "music").mkdir()
    (bundle / "music/README.txt").write_text("音乐交付材料放这里；本版不会自动播放。详见根目录给音乐和美术的说明。\n", encoding="utf-8")
    licenses = bundle / "LICENSES"
    licenses.mkdir()
    shutil.copy2(root / "LICENSE", licenses / "MPL-2.0.txt")
    for relative, name in [("assets/CREDITS.md", "素材来源.md"), ("assets/fonts/FUSION-PIXEL-OFL.txt", "Fusion-Pixel-OFL.txt"),
                           ("assets/portraits/manifest.json", "用户图片来源.json")]:
        shutil.copy2(root / "mods/ui/desktop" / relative, licenses / name)


def inspect(bundle):
    required = ["Zhanguo.exe", "Zhanguo.pck", "game/host.py", "game/ui/desktop.py", "game/mods.toml", "game/ui.toml",
                "runtime/python/python.exe", "runtime/python/python313.dll", "portable.json"]
    for name in required:
        if not (bundle / name).is_file():
            raise ValueError("Incomplete bundle: " + name)
    if not list(bundle.glob("data_*/coreclr.dll")) or not list(bundle.glob("data_*/GodotSharp.dll")):
        raise ValueError("Export did not include the .NET runtime and Godot API")
    forbidden = {".git", ".venv", "__pycache__", "archive", "userdata"}
    for path in bundle.rglob("*"):
        relative = path.relative_to(bundle)
        if forbidden.intersection(relative.parts) or path.name.startswith("mp_config") or path.suffix == ".pyc":
            raise ValueError("Unexpected private/development file: " + str(relative))


def build(output, cache):
    if os.name != "nt":
        raise ValueError("Build this package on Windows (the GitHub Windows workflow also runs it)")
    output.mkdir(parents=True, exist_ok=False)
    cache.mkdir(parents=True, exist_ok=True)
    bundle = output / "Zhanguo-Windows-x64"
    bundle.mkdir()
    stage_sources(bundle)
    extract(download("python", cache), bundle / "runtime/python")
    # Embedded Python ignores the registry, PATH, user site and PYTHONPATH.
    (bundle / "runtime/python/python313._pth").write_text("python313.zip\n.\n../../game\n", encoding="utf-8")
    godot = tools(cache)
    project = ROOT / "mods/ui/desktop"
    subprocess.run(["dotnet", "build", str(project / "Zhanguo.Desktop.csproj")], cwd=ROOT, check=True)
    subprocess.run([str(godot), "--headless", "--editor", "--path", str(project), "--import"], cwd=ROOT, check=True)
    subprocess.run([str(godot), "--headless", "--path", str(project), "--export-release", "Windows Portable", str(bundle / "Zhanguo.exe")], cwd=ROOT, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (bundle / "portable.json").write_text(json.dumps({"format": "zhanguo.portable/1", "commit": sha,
        "godot": GODOT_VERSION, "python": PYTHON_VERSION, "architecture": "windows-x64",
        "source": "https://github.com/northwindlight/zhanguo/tree/" + sha}, indent=2) + "\n", encoding="utf-8")
    with urllib.request.urlopen(f"https://raw.githubusercontent.com/godotengine/godot/{GODOT_VERSION}-stable/LICENSE.txt", timeout=30) as source:
        (bundle / "LICENSES/Godot-MIT.txt").write_bytes(source.read())
    inspect(bundle)
    manifest = {str(p.relative_to(bundle)).replace("\\", "/"): digest(p) for p in sorted(bundle.rglob("*")) if p.is_file()}
    (bundle / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = output / f"Zhanguo-Windows-x64-{sha[:7]}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dest:
        for file in sorted(bundle.rglob("*")):
            if file.is_file():
                dest.write(file, file.relative_to(output))
    archive.with_suffix(".zip.sha256").write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
    print("WINDOWS_PACKAGE_OK", archive, archive.stat().st_size, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    build(args.output.resolve(), args.cache.resolve())

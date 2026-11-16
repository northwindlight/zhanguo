#!/usr/bin/env python3
"""Double-blind gate: fail if a release tree or archive leaks identity information.

Checked for both a directory and a .zip:

* personal names / handles / e-mail addresses
* personal URLs (project pages, repository owners) — third-party attributions are allowed
* absolute home or authoring paths (`C:\\Users\\...`, `D:\\code\\...`, `/home/...`)
* the project's searchable name (configurable via --project-name)
* API keys and API-key-shaped strings
* files that must never ship: `.git`, saves, journals, reports, caches
* archive hazards: absolute entry names, non-uniform timestamps (timezone/authoring leak)

Exit code 0 = clean, 1 = findings, 2 = bad usage.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

TEXT_SUFFIXES = {".md", ".py", ".json", ".toml", ".txt", ".sh", ".ps1", ".bat",
                 ".cfg", ".csproj", ".sln", ".yml", ".yaml", ""}

# 名字/账号/个人链接：这些是真身份，必须为零。
IDENTITY = [
    (re.compile(r"张峰"), "author name"),
    (re.compile(r"northwind", re.I), "personal handle"),
    (re.compile(r"northwindlight", re.I), "personal handle"),
    (re.compile(r"\[[^\]]*\]\(mailto:"), "mail link"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "e-mail address"),
    # 只放行第三方归属链接（PAMS 等），其余 github 链接视为身份线索
    (re.compile(r"github\.com/(?!masanorihirano\b|northwindlight\b)[\w.-]+/"), "repository link"),
    (re.compile(r"github\.com/northwindlight", re.I), "repository link"),
    (re.compile(r"[A-Za-z]:\\\\(?:Users|code|Projects?|dev|src|home|work|tmp)\b", re.I),
     "authoring path (Windows)"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+"), "authoring path (POSIX)"),
    (re.compile(r"/Users/[A-Za-z0-9_.-]+"), "authoring path (macOS)"),
]

SECRETS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), "API key"),
    (re.compile(r'"api_key"\s*:\s*"(?!paste-your-api-key-here|<redacted>)[^"]{16,}"'), "API key"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"), "bearer token"),
]

FORBIDDEN_NAMES = {".git", ".venv", "__pycache__", "results", "reports",
                   "mp_config.json", "mp_save.json", "mp_journal.md"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".zip", ".save", ".log", ".psd"}
FORBIDDEN_PATTERNS = ("*.save.json", "*.journal.md", "*_save.json")


def text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def scan_text(name: str, text: str, project: str | None, findings: list) -> None:
    rules = list(IDENTITY) + list(SECRETS)
    if project:
        rules.append((re.compile(re.escape(project), re.I), f"project name '{project}'"))
    for pattern, label in rules:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            snippet = match.group(0)[:60]
            findings.append(f"{name}:{line}: {label}: {snippet!r}")


def check_dir(root: Path, project: str | None) -> list:
    findings = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in FORBIDDEN_NAMES for part in rel.parts):
            findings.append(f"{rel}: forbidden file/dir (must not ship)")
            continue
        if path.is_file() and (path.suffix.lower() in FORBIDDEN_SUFFIXES
                               or any(path.match(p) for p in FORBIDDEN_PATTERNS)):
            findings.append(f"{rel}: forbidden file type (must not ship)")
    for path in text_files(root):
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"{rel}: not UTF-8 (cannot verify -> treat as leak)")
            continue
        scan_text(str(rel), text, project, findings)
    return findings


def check_zip(path: Path, project: str | None) -> list:
    findings = []
    stamps = set()
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0]:
                findings.append(f"{name}: unsafe entry name")
            parts = Path(name).parts
            if any(part in FORBIDDEN_NAMES for part in parts):
                findings.append(f"{name}: forbidden entry")
            if info.filename.lower().endswith(tuple(FORBIDDEN_SUFFIXES)):
                findings.append(f"{name}: forbidden entry type")
            stamps.add(info.date_time)
            if info.is_dir():
                continue
            data = zf.read(info)
            if b"\x00" in data[:4096]:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            scan_text(name, text, project, findings)
    if len(stamps) > 1:
        findings.append(f"archive timestamps are not uniform: {sorted(stamps)[:3]}... "
                        f"({len(stamps)} distinct) -> authoring-time leak")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="Double-blind identity leak check")
    ap.add_argument("target", nargs="+", help="directory and/or .zip to check")
    ap.add_argument("--project-name", default="zhanguo",
                    help="searchable project name to scrub (default: zhanguo; pass '' to skip)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    project = args.project_name or None
    failures = 0
    for raw in args.target:
        target = Path(raw)
        if not target.exists():
            print(f"error: {target} does not exist", file=sys.stderr)
            return 2
        findings = check_zip(target, project) if target.suffix == ".zip" else check_dir(target, project)
        label = f"{target}"
        if findings:
            failures += len(findings)
            print(f"FAIL {label}: {len(findings)} finding(s)")
            if not args.quiet:
                for item in findings[:40]:
                    print("  -", item)
                if len(findings) > 40:
                    print(f"  ... {len(findings) - 40} more")
        else:
            print(f"OK   {label}: no identity leaks found")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

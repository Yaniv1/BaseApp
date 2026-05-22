#!/usr/bin/env python3
"""Pull base updates from a BaseApp source into an instantiated app.

Normal mode: copies only the files listed in BaseApp's config/base_files.json
manifest.  The manifest is fetched first and cached locally so that extending
BaseApp never requires editing this script.

Hard mode (--hard): copies every file from BaseApp, overwriting all local files
including app-specific placeholders (app/app.py, config/app.json, etc.).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

MANIFEST_REL = "config/base_files.json"

EXCLUDED_DIRS = {"__pycache__", ".git", ".venv"}
EXCLUDED_EXTENSIONS = {".pyc", ".pyo"}


def iter_source_files(source_root: Path):
    """Yield all non-excluded file paths relative to source_root."""
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        root_path = Path(root)
        for name in files:
            file_path = root_path / name
            if file_path.suffix.lower() not in EXCLUDED_EXTENSIONS:
                yield file_path.relative_to(source_root)


def hard_pull(local_root: Path, source_root: Path) -> tuple[int, list[str]]:
    """Copy every file from source_root into local_root, overwriting all."""
    copied = 0
    missing_sources: list[str] = []
    for rel_path in iter_source_files(source_root):
        src = source_root / rel_path
        dst = local_root / rel_path
        if not src.is_file():
            missing_sources.append(str(src))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return copied, missing_sources


def resolve_source(script_root: Path, source_arg: str) -> Path:
    """Resolve source path from argument, defaulting relative to script root."""
    candidate = Path(source_arg).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (script_root / candidate).resolve()


def load_manifest(source_root: Path, local_root: Path) -> list[tuple[str, str]]:
    """Fetch manifest from source, cache it locally, and return file map."""
    src_manifest = source_root / MANIFEST_REL
    dst_manifest = local_root / MANIFEST_REL

    if not src_manifest.is_file():
        raise FileNotFoundError(f"Manifest not found in source: {src_manifest}")

    dst_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_manifest, dst_manifest)

    with open(dst_manifest, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return [(entry[0], entry[1]) for entry in raw]


def pull_base_files(local_root: Path, source_root: Path, file_map: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """Copy base files listed in file_map from source into local root."""
    copied = 0
    missing_sources: list[str] = []

    for src_rel, dst_rel in file_map:
        src = source_root / src_rel
        dst = local_root / dst_rel

        if not src.exists() or not src.is_file():
            missing_sources.append(str(src))
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    return copied, missing_sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull and overwrite local base files from a BaseApp source.",
    )
    parser.add_argument(
        "--source",
        default="../BaseApp",
        help="Path to BaseApp source root. Default: ../BaseApp",
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="Override all files including app-specific placeholders.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    local_root = Path(__file__).resolve().parent
    source_root = resolve_source(local_root, args.source)

    if not source_root.exists() or not source_root.is_dir():
        print(f"Source not found: {source_root}")
        return 1

    if args.hard:
        copied, missing_sources = hard_pull(local_root, source_root)
    else:
        try:
            file_map = load_manifest(source_root, local_root)
        except FileNotFoundError as exc:
            print(str(exc))
            return 1
        copied, missing_sources = pull_base_files(local_root, source_root, file_map)

    print(f"Source: {source_root}")
    print(f"Local app: {local_root}")
    print(f"Base files updated: {copied}")

    if missing_sources:
        print("Missing source files:")
        for path in missing_sources:
            print(f"- {path}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

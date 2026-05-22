#!/usr/bin/env python3
"""Instantiate BaseApp into a target path.

Rules:
- Copy the BaseApp tree into the target path.
- Only core/base files are overwritten on repeated runs.
- App-dedicated placeholder files are created only if missing:
  - app/app.py
  - config/app.json
  - utils/apputils.py
  - docs/readme/app.md
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


APP_PLACEHOLDER_FILES = {
    Path("app/app.py"),
    Path("config/app.json"),
    Path("utils/apputils.py"),
    Path("docs/readme/app.md"),
}

EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
}

EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",
}


def iter_source_files(source_root: Path):
    """Yield source file paths relative to source_root."""
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

        root_path = Path(root)
        for name in files:
            file_path = root_path / name
            if file_path.suffix.lower() in EXCLUDED_EXTENSIONS:
                continue
            yield file_path.relative_to(source_root)


def copy_file(src_root: Path, dst_root: Path, rel_path: Path):
    """Copy one file from src_root to dst_root preserving metadata."""
    src = src_root / rel_path
    dst = dst_root / rel_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def populate_app_config(app_config_path: Path, target_value: str):
    """Populate app config by updating only COMMON.APP_NAME."""
    with open(app_config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    common = data.get("COMMON")
    if not isinstance(common, dict):
        common = {}
        data["COMMON"] = common

    common["APP_NAME"] = target_value

    with open(app_config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def instantiate(source_root: Path, target_root: Path, target_value: str):
    """Instantiate BaseApp tree into target_root with placeholder protections."""
    target_root.mkdir(parents=True, exist_ok=True)

    copied_core = 0
    created_placeholders = 0
    kept_placeholders = 0

    for rel_path in iter_source_files(source_root):
        if rel_path in APP_PLACEHOLDER_FILES:
            target_file = target_root / rel_path
            if target_file.exists():
                kept_placeholders += 1
                continue
            copy_file(source_root, target_root, rel_path)
            if rel_path == Path("config/app.json"):
                populate_app_config(target_file, target_value)
            created_placeholders += 1
            continue

        copy_file(source_root, target_root, rel_path)
        copied_core += 1

    return {
        "copied_core": copied_core,
        "created_placeholders": created_placeholders,
        "kept_placeholders": kept_placeholders,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Instantiate BaseApp into a target path.")
    parser.add_argument(
        "target",
        help="Target folder where BaseApp should be instantiated.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    source_root = Path(__file__).resolve().parent
    target_root = Path(args.target).expanduser().resolve()
    target_value = Path(args.target).name or target_root.name

    if source_root == target_root:
        raise ValueError("Target path must be different from BaseApp source path.")

    result = instantiate(source_root, target_root, target_value)

    print(f"Instantiated BaseApp at: {target_root}")
    print(f"Core files copied/updated: {result['copied_core']}")
    print(f"App placeholders created: {result['created_placeholders']}")
    print(f"App placeholders preserved: {result['kept_placeholders']}")


if __name__ == "__main__":
    main()

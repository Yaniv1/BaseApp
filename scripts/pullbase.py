#!/usr/bin/env python3
"""Pull base updates from a BaseApp source into an instantiated app.

Normal mode: syncs BaseApp docs/manifests folder locally, then applies entries
under the 'pull' behavior key from all manifest files in that folder.

Hard mode (--hard): copies every file from BaseApp, overwriting all local files
including app-specific placeholders (app/app.py, config/app.json, etc.).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.baseutils import Logger, Params, get_config, load_message_dict

MANIFESTS_DIR_REL = Path("docs/manifests")

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
    if source_arg == "auto":
        candidates = [
            script_root.parent.parent,
            script_root.parent.parent / "BaseApp",
            script_root.parent.parent.parent / "BaseApp",
        ]
        for candidate in candidates:
            resolved = candidate.resolve()
            manifests_dir = resolved / MANIFESTS_DIR_REL
            if manifests_dir.is_dir() and resolved != script_root.parent.resolve():
                return resolved
        return candidates[0].resolve()

    candidate = Path(source_arg).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (script_root / candidate).resolve()


def sync_manifests(source_root: Path, local_root: Path) -> list[Path]:
    """Copy all JSON manifests from source docs/manifests into local docs/manifests."""
    src_dir = source_root / MANIFESTS_DIR_REL
    dst_dir = local_root / MANIFESTS_DIR_REL

    if not src_dir.is_dir():
        raise FileNotFoundError(f"Manifests folder not found in source: {src_dir}")

    dst_dir.mkdir(parents=True, exist_ok=True)
    manifest_paths: list[Path] = []
    for src_manifest in sorted(src_dir.glob("*.json")):
        dst_manifest = dst_dir / src_manifest.name
        shutil.copy2(src_manifest, dst_manifest)
        manifest_paths.append(dst_manifest)

    return manifest_paths


def load_pull_entries(manifest_paths: list[Path]) -> list[tuple[str, str]]:
    """Load all pull entries from copied manifest files."""
    file_map: list[tuple[str, str]] = []

    for manifest_path in manifest_paths:
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid manifest format in {manifest_path}: expected object at root")

        entries = raw.get("pull")
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ValueError(f"Invalid manifest format in {manifest_path}: expected list at 'pull'")

        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid manifest item at {manifest_path}:pull[{index}] - expected object")

            source = item.get("source")
            target = item.get("target", source)
            if not isinstance(source, str) or not source:
                raise ValueError(f"Invalid manifest item at {manifest_path}:pull[{index}] - 'source' is required")
            if not isinstance(target, str) or not target:
                raise ValueError(f"Invalid manifest item at {manifest_path}:pull[{index}] - 'target' must be a string")

            file_map.append((source, target))

    return file_map


def pull_base_files(local_root: Path, source_root: Path, file_map: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """Copy base files listed in file_map from source into local root."""
    copied = 0
    missing_sources: list[str] = []

    for src_rel, dst_rel in file_map:
        src = source_root / src_rel

        if not src.exists():
            missing_sources.append(str(src))
            continue

        if src.is_file():
            dst = local_root / dst_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            continue

        if src.is_dir():
            dst_root = local_root / dst_rel
            for child in sorted(src.rglob("*")):
                if not child.is_file():
                    continue
                child_rel = child.relative_to(src)
                dst = dst_root / child_rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, dst)
                copied += 1
            continue

        missing_sources.append(str(src))

    return copied, missing_sources


def load_runtime_config(app_root: Path) -> Params:
    """Load and resolve base/app config values for runtime paths and logging."""
    base_config_path = app_root / "config" / "base.json"
    config = Params(get_config(config_path=str(base_config_path)))

    app_config_path = app_root / "config" / "app.json"
    if app_config_path.is_file():
        config.set(**get_config(config_path=str(app_config_path)))

    config.evaluate(["COMMON"])
    wrappers = getattr(getattr(config, "COMMON", Params()), "CONFIG_WRAPPERS", ["{$", "$}"])
    config.populate(["COMMON"], wrappers)
    return config


def resolve_template_path(app_root: Path, config: Params) -> str:
    """Resolve HTML template path from config COMMON.HTML_TEMPLATE."""
    template_value = getattr(getattr(config, "COMMON", Params()), "HTML_TEMPLATE", "../docs/templates/dataset_table.html")
    template_path = Path(str(template_value))
    if template_path.is_absolute():
        return str(template_path)
    return str((app_root / "config" / template_path).resolve())


def create_pullbase_logger(local_root: Path, local_app_name: str, timestamp: str):
    """Create pullbase logger under C:/data/{local_app}/{date}/basepulls."""
    local_config = load_runtime_config(local_root)
    output_prefix = Path(str(getattr(getattr(local_config, "COMMON", Params()), "OUTPUT_PREFIX", "C:/data")))
    output_date = str(getattr(getattr(local_config, "COMMON", Params()), "OUTPUT_VERSION", dt.datetime.utcnow().strftime("%Y%m%d")))
    data_root = output_prefix.parent if output_prefix.parent != Path("") else Path("C:/data")
    log_dir = data_root / local_app_name / output_date / "basepulls"
    log_file = log_dir / f"{local_app_name}_{timestamp}.csv"
    template = resolve_template_path(local_root, local_config)

    messages_dir = local_root / "docs" / "messages"
    message_dict = load_message_dict([
        messages_dir / "logger.csv",
        messages_dir / "base.csv",
    ])
    logger = Logger(
        log_path=str(log_file),
        start_time=dt.datetime.utcnow(),
        max_items=getattr(local_config.log, "max_items", None),
        verbose=getattr(local_config.log, "verbose", "INFO"),
        log_types=getattr(local_config.log, "types", None),
        type_colors=getattr(local_config.log, "colors", None),
        message_dict=message_dict,
    )
    return logger, template


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull and overwrite local base files from a BaseApp source.",
    )
    parser.add_argument(
        "--source",
        default="auto",
        help="Path to BaseApp source root. Default: auto-detect from script location",
    )
    parser.add_argument(
        "--hard",
        action="store_true",
        help="Override all files including app-specific placeholders.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    script_root = Path(__file__).resolve().parent
    local_root = script_root.parent
    source_root = resolve_source(script_root, args.source)
    timestamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    local_app_name = local_root.name

    logger = None
    template = None
    try:
        logger, template = create_pullbase_logger(local_root, local_app_name, timestamp)
        logger.log(
            message_code="PULL001",
            data={"source": str(source_root), "local": str(local_root), "hard": args.hard},
        )
    except Exception:
        logger = None
        template = None

    if not source_root.exists() or not source_root.is_dir():
        if logger:
            logger.log(message_code="PULLE02", message_type="ERROR", data={"source": str(source_root)})
            html_path = logger.save_html(
                title=f"Base Pull for {local_app_name} from {source_root}",
                template=template,
            )
            if html_path:
                print(f"Pullbase log html: {html_path}")
        print(f"Source not found: {source_root}")
        return 1

    if args.hard:
        copied, missing_sources = hard_pull(local_root, source_root)
        if logger:
            logger.log(message_code="PULL003", data={"copied": copied})
    else:
        try:
            manifest_paths = sync_manifests(source_root, local_root)
            file_map = load_pull_entries(manifest_paths)
            if logger:
                logger.log(
                    message_code="PULL004",
                    data={"entry_count": len(file_map), "manifest_count": len(manifest_paths)},
                )
        except (FileNotFoundError, ValueError) as exc:
            if logger:
                logger.log(message_code="PULLE05", message_type="ERROR", data={"error": str(exc)})
                html_path = logger.save_html(
                    title=f"Base Pull for {local_app_name} from {source_root}",
                    template=template,
                )
                if html_path:
                    print(f"Pullbase log html: {html_path}")
            print(str(exc))
            return 1
        copied, missing_sources = pull_base_files(local_root, source_root, file_map)
        if logger:
            logger.log(message_code="PULL006", data={"copied": copied})

    print(f"Source: {source_root}")
    print(f"Local app: {local_root}")
    print(f"Base files updated: {copied}")

    if missing_sources:
        if logger:
            logger.log(message_code="PULLE07", message_type="WARN", data={"count": len(missing_sources)})
        print("Missing source files:")
        for path in missing_sources:
            print(f"- {path}")
            if logger:
                logger.log(message_code="PULLW08", message_type="WARN", data={"path": path})
        if logger:
            html_path = logger.save_html(
                title=f"Base Pull for {local_app_name} from {source_root}",
                template=template,
            )
            if html_path:
                print(f"Pullbase log html: {html_path}")
        return 2

    if logger:
        logger.log(message_code="PULL999", message_type="GOOD")
        html_path = logger.save_html(
            title=f"Base Pull for {local_app_name} from {source_root}",
            template=template,
        )
        if html_path:
            print(f"Pullbase log html: {html_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
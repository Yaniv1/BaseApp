#!/usr/bin/env python3
"""Pull base updates from a BaseApp source into an instantiated app.

Normal mode: syncs BaseApp resources/manifests folder locally, then applies entries
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

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.baseutils import Logger, Params, get_config, load_message_lookup

MANIFESTS_DIR_REL = Path("resources/manifests")
SETUP_ENV_SCRIPT = Path("scripts/setup_env.ps1")

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


# Feature 3.2.1
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
    """Copy all JSON manifests from source resources/manifests into local resources/manifests."""
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

# Feature 3.2.12
def load_once_entries(manifest_paths: list[Path]) -> list[tuple[str, str]]:
    """Load all once entries from copied manifest files.

    Once entries are copied only when the destination file does not already exist in the local app.
    """
    file_map: list[tuple[str, str]] = []

    for manifest_path in manifest_paths:
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid manifest format in {manifest_path}: expected object at root")

        entries = raw.get("once")
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ValueError(f"Invalid manifest format in {manifest_path}: expected list at 'once'")

        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid manifest item at {manifest_path}:once[{index}] - expected object")

            source = item.get("source")
            target = item.get("destination", item.get("target", source))
            if not isinstance(source, str) or not source:
                raise ValueError(f"Invalid manifest item at {manifest_path}:once[{index}] - 'source' is required")
            if not isinstance(target, str) or not target:
                raise ValueError(f"Invalid manifest item at {manifest_path}:once[{index}] - 'target' must be a string")

            file_map.append((source, target))

    return file_map


# Feature 3.2.2
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
                if any(p in EXCLUDED_DIRS for p in child.parts) or child.suffix.lower() in EXCLUDED_EXTENSIONS:
                    continue
                child_rel = child.relative_to(src)
                dst = dst_root / child_rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, dst)
                copied += 1
            continue

        missing_sources.append(str(src))

    return copied, missing_sources


# Feature 3.2.13
def pull_once_files(
    local_root: Path, source_root: Path, file_map: list[tuple[str, str]]
) -> tuple[int, int, list[str]]:
    """Copy once files from source into local root only when destination does not already exist.

    Returns (copied, skipped, missing_sources) where skipped counts files already present locally.
    """
    copied = 0
    skipped = 0
    missing_sources: list[str] = []

    for src_rel, dst_rel in file_map:
        src = source_root / src_rel

        if not src.exists():
            missing_sources.append(str(src))
            continue

        if src.is_file():
            dst = local_root / dst_rel
            if dst.exists():
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            continue

        if src.is_dir():
            dst_root = local_root / dst_rel
            for child in sorted(src.rglob("*")):
                if not child.is_file():
                    continue
                if any(p in EXCLUDED_DIRS for p in child.parts) or child.suffix.lower() in EXCLUDED_EXTENSIONS:
                    continue
                child_rel = child.relative_to(src)
                dst = dst_root / child_rel
                if dst.exists():
                    skipped += 1
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(child, dst)
                copied += 1
            continue

        missing_sources.append(str(src))

    return copied, skipped, missing_sources


# Feature 3.2.15
def load_drop_entries(manifest_paths: list[Path]) -> list[tuple[str, str | None]]:
    """Load all drop entries from copied manifest files.

    Each entry is (source, target) where target may be None.
    - If target is present: source is moved/renamed to target.
    - If target is absent or null: source is deleted outright.
    """
    pairs: list[tuple[str, str | None]] = []

    for manifest_path in manifest_paths:
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid manifest format in {manifest_path}: expected object at root")

        entries = raw.get("drop")
        if entries is None:
            continue
        if not isinstance(entries, list):
            raise ValueError(f"Invalid manifest format in {manifest_path}: expected list at 'drop'")

        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                raise ValueError(f"Invalid manifest item at {manifest_path}:drop[{index}] - expected object")

            source = item.get("source")
            if not isinstance(source, str) or not source:
                raise ValueError(f"Invalid manifest item at {manifest_path}:drop[{index}] - 'source' is required")

            raw_target = item.get("target")
            target: str | None = raw_target if isinstance(raw_target, str) and raw_target else None

            pairs.append((source, target))

    return pairs


# Feature 3.2.16
def drop_deprecated_paths(local_root: Path, drop_pairs: list[tuple[str, str | None]]) -> tuple[int, int, int]:
    """Migrate or delete deprecated paths in the local app.

    This runs AFTER pull_base_files, so any file already at the target path
    was just placed there by the pull step and is the authoritative version.

    For each (source, target) pair:
    - If target is None: delete source outright (file or directory tree).
    - If target is present and target file is absent: move source to target.
    - If target is present and target file already exists: delete the stale
      old-location copy (the target is already authoritative from the pull).
    After processing all files, empty source directories are removed.

    Returns (moved, removed, skipped) where:
      moved   = files relocated to their new path
      removed = stale old-location copies deleted (target already had content, or delete-only entry)
      skipped = source paths that did not exist
    """
    moved = 0
    removed = 0
    skipped = 0

    for source_rel, target_rel in drop_pairs:
        source = local_root / source_rel
        if not source.exists():
            skipped += 1
            continue

        if source.is_file():
            if target_rel is None:
                source.unlink()
                removed += 1
            else:
                target = local_root / target_rel
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                    moved += 1
                else:
                    source.unlink()
                    removed += 1
            continue

        if source.is_dir():
            for child in sorted(source.rglob("*")):
                if not child.is_file():
                    continue
                if target_rel is None:
                    child.unlink()
                    removed += 1
                else:
                    child_rel = child.relative_to(source)
                    target_file = local_root / target_rel / child_rel
                    if not target_file.exists():
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(child), str(target_file))
                        moved += 1
                    else:
                        child.unlink()
                        removed += 1
            # Remove empty subdirectories bottom-up, then the root
            for dirpath in sorted(source.rglob("*"), reverse=True):
                if dirpath.is_dir():
                    try:
                        dirpath.rmdir()
                    except OSError:
                        pass
            try:
                source.rmdir()
            except OSError:
                pass

    return moved, removed, skipped


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
    template_value = getattr(getattr(config, "COMMON", Params()), "HTML_TEMPLATE", "../resources/templates/dataset_table.html")
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

    messages_dir_value = getattr(local_config.LOG, "messages_dir", "resources/message_codes")
    messages_dir_path = Path(str(messages_dir_value))
    if not messages_dir_path.is_absolute():
        messages_dir_path = (local_root / messages_dir_path).resolve()
    message_lookup = load_message_lookup([str(messages_dir_path)])
    logger = Logger(
        log_path=str(log_file),
        start_time=dt.datetime.utcnow(),
        max_items=getattr(local_config.LOG, "max_items", None),
        verbose=getattr(local_config.LOG, "verbose", "INFO"),
        log_types=getattr(local_config.LOG, "types", None),
        type_colors=getattr(local_config.LOG, "colors", None),
        message_lookup=message_lookup,
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


# Feature 3.2.3
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
    except Exception as exc:
        logger = None
        template = None
        print(f"Pullbase logging disabled: {exc}")

    if not source_root.exists() or not source_root.is_dir():
        if logger:
            logger.log(message_code="PULLE02", message_type="ERROR", data={"source": str(source_root)}, populate=True)
            html_path = logger.save_html(
                title=f"Base Pull for {local_app_name} from {source_root}",
                template=template,
            )
            if html_path:
                print(f"Pullbase log html: {html_path}")
        print(f"Source not found: {source_root}")
        return 1

    once_copied = once_skipped = 0

    if args.hard:
        copied, missing_sources = hard_pull(local_root, source_root)
        if logger:
            logger.log(message_code="PULL003", data={"copied": copied}, populate=True)
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
                logger.log(message_code="PULLE05", message_type="ERROR", data={"error": str(exc)}, populate=True)
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
            logger.log(message_code="PULL006", data={"copied": copied}, populate=True)

        # If this script was updated by the pull, re-exec with the fresh version
        # so that drop and once run under the newly pulled code, not the old in-memory version.
        this_script = Path(__file__).resolve()
        local_script = local_root / "scripts" / "pullbase.py"
        if local_script.resolve() != this_script and local_script.is_file():
            if local_script.read_bytes() != this_script.read_bytes():
                print("pullbase.py was updated — restarting with new version...")
                os.execv(sys.executable, [sys.executable, str(local_script)] + sys.argv[1:])

        drop_list = load_drop_entries(manifest_paths)
        if logger:
            logger.log(message_code="PULL009", data={"entry_count": len(drop_list)}, populate=True)
        drop_moved, drop_removed, drop_skipped = drop_deprecated_paths(local_root, drop_list)
        if logger:
            logger.log(message_code="PULL010", data={"moved": drop_moved, "removed": drop_removed, "skipped": drop_skipped}, populate=True)

        once_map = load_once_entries(manifest_paths)
        if logger:
            logger.log(
                message_code="PULL007",
                data={"entry_count": len(once_map)},
            )
        once_copied, once_skipped, once_missing = pull_once_files(local_root, source_root, once_map)
        if logger:
            logger.log(message_code="PULL008", data={"copied": once_copied, "skipped": once_skipped}, populate=True)
        missing_sources.extend(once_missing)

    print(f"Source: {source_root}")
    print(f"Local app: {local_root}")
    print(f"Base files updated: {copied}")
    if not args.hard:
        print(f"Once files synced: {once_copied} new (skipped {once_skipped} existing)")
        print(f"Deprecated paths: {drop_moved} moved, {drop_removed} removed (skipped {drop_skipped} already absent)")

    if missing_sources:
        if logger:
            logger.log(message_code="PULLW07", message_type="WARN", data={"count": len(missing_sources)}, populate=True)
        print("Missing source files:")
        for path in missing_sources:
            print(f"- {path}")
            if logger:
                logger.log(message_code="PULLW08", message_type="WARN", data={"path": path}, populate=True)
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

    run_setup_env(local_root)
    return 0


def run_setup_env(app_root: Path) -> None:
    """Run setup_env.ps1 for the given app root to create the venv and install deps.

    Locates setup_env.ps1 relative to this script's directory, then invokes it
    non-interactively via pwsh targeting the specified app root. Prints a warning
    if the script is not found or exits with a non-zero code; does not raise so
    that pullbase output is always shown.
    """
    import subprocess
    script_root = Path(__file__).resolve().parent
    setup_env = script_root / SETUP_ENV_SCRIPT.name
    if not setup_env.exists():
        print(f"[WARN] setup_env.ps1 not found at {setup_env}; skipping environment setup.")
        return
    result = subprocess.run(
        ["pwsh", "-NonInteractive", "-File", str(setup_env), "-AppRoot", str(app_root)],
        check=False,
    )
    if result.returncode != 0:
        print(f"[WARN] setup_env.ps1 exited with code {result.returncode} for {app_root}.")


def run_setup_env(app_root: Path) -> None:
    """Run setup_env.ps1 for the given app root to create the venv and install deps.

    Locates setup_env.ps1 relative to this script's directory, then invokes it
    non-interactively via pwsh targeting the specified app root. Prints a warning
    if the script is not found or exits with a non-zero code; does not raise so
    that pullbase output is always shown.
    """
    import subprocess
    script_root = Path(__file__).resolve().parent
    setup_env = script_root / SETUP_ENV_SCRIPT.name
    if not setup_env.exists():
        print(f"[WARN] setup_env.ps1 not found at {setup_env}; skipping environment setup.")
        return
    result = subprocess.run(
        ["pwsh", "-NonInteractive", "-File", str(setup_env), "-AppRoot", str(app_root)],
        check=False,
    )
    if result.returncode != 0:
        print(f"[WARN] setup_env.ps1 exited with code {result.returncode} for {app_root}.")


if __name__ == "__main__":
    raise SystemExit(main())
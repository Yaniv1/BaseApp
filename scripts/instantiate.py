#!/usr/bin/env python3
"""Instantiate BaseApp into a target path.

Rules:
- Read all manifest files from docs/manifests.
- Apply entries under 'pull' by copying on every instantiate run.
- Apply entries under 'once' by copying only when destination is missing.
- Create docs/instructions/{APP_NAME}.md only if missing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.baseutils import Logger, Params, get_config, load_message_lookup


MANIFESTS_DIR_REL = Path("docs/manifests")


def load_manifest_entries(source_root: Path, behaviors: set[str]) -> dict[str, list[tuple[Path, Path]]]:
    """Load entries from all manifests in docs/manifests grouped by behavior key."""
    manifests_dir = source_root / MANIFESTS_DIR_REL
    if not manifests_dir.is_dir():
        raise FileNotFoundError(f"Manifests folder not found: {manifests_dir}")

    grouped: dict[str, list[tuple[Path, Path]]] = {behavior: [] for behavior in behaviors}

    for manifest_path in sorted(manifests_dir.glob("*.json")):
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if not isinstance(raw, dict):
            raise ValueError(f"Invalid manifest format in {manifest_path}: expected object at root")

        for behavior in behaviors:
            entries = raw.get(behavior)
            if entries is None:
                continue
            if not isinstance(entries, list):
                raise ValueError(f"Invalid manifest format in {manifest_path}: expected list at '{behavior}'")

            for index, item in enumerate(entries):
                if not isinstance(item, dict):
                    raise ValueError(f"Invalid manifest item at {manifest_path}:{behavior}[{index}] - expected object")

                source = item.get("source")
                target = item.get("target", source)
                if not isinstance(source, str) or not source:
                    raise ValueError(
                        f"Invalid manifest item at {manifest_path}:{behavior}[{index}] - 'source' is required"
                    )
                if not isinstance(target, str) or not target:
                    raise ValueError(
                        f"Invalid manifest item at {manifest_path}:{behavior}[{index}] - 'target' must be a string"
                    )

                grouped[behavior].append((Path(source), Path(target)))

    return grouped


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


def ensure_app_instruction_file(target_root: Path, app_name: str) -> bool:
    """Create docs/instructions/{APP_NAME}.md for app-specific guidance if missing."""
    app_instructions_path = target_root / "docs" / "instructions" / f"{app_name}.md"
    if app_instructions_path.exists():
        return False

    app_instructions_path.parent.mkdir(parents=True, exist_ok=True)
    app_instructions_path.write_text(
        "\n".join(
            [
                f"# {app_name} Instructions",
                "## App specific instructions",
                "",
                "Use this file for instructions that apply only to this app variant.",
                "The app designer owns and maintains this file.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return True


def expand_manifest_entry(source_root: Path, source_rel: Path, target_rel: Path) -> list[tuple[Path, Path]]:
    """Expand a manifest entry to files; directories are expanded recursively."""
    source_path = source_root / source_rel

    if source_path.is_file():
        return [(source_rel, target_rel)]
    if source_path.is_dir():
        expanded: list[tuple[Path, Path]] = []
        for child in sorted(source_path.rglob("*")):
            if not child.is_file():
                continue
            child_rel = child.relative_to(source_path)
            expanded.append((source_rel / child_rel, target_rel / child_rel))
        return expanded

    raise FileNotFoundError(f"Manifest source not found: {source_path}")


def load_runtime_config(app_root: Path) -> Params:
    """Load and resolve base config values for instantiate runtime paths and logging."""
    base_config_path = app_root / "config" / "base.json"
    config = Params(get_config(config_path=str(base_config_path)))

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


def create_instantiate_logger(source_root: Path, target_app: str, timestamp: str):
    """Create instantiate logger under C:/data/BaseApp/instantiations/{date}."""
    config = load_runtime_config(source_root)
    output_prefix = Path(str(getattr(getattr(config, "COMMON", Params()), "OUTPUT_PREFIX", "C:/data/BaseApp")))
    output_date = str(getattr(getattr(config, "COMMON", Params()), "OUTPUT_VERSION", dt.datetime.utcnow().strftime("%Y%m%d")))
    log_dir = output_prefix / "instantiations" / output_date
    log_file = log_dir / f"{target_app}_{timestamp}.csv"
    template = resolve_template_path(source_root, config)

    messages_dir = os.path.abspath(getattr(config.log, "messages_dir", "../docs/messages"))
    message_lookup = load_message_lookup([messages_dir])
    logger = Logger(
        log_path=str(log_file),
        start_time=dt.datetime.utcnow(),
        max_items=getattr(config.log, "max_items", None),
        verbose=getattr(config.log, "verbose", "INFO"),
        log_types=getattr(config.log, "types", None),
        type_colors=getattr(config.log, "colors", None),
        message_lookup=message_lookup,
    )
    return logger, template


def instantiate(source_root: Path, target_root: Path, target_value: str, logger: Logger | None = None):
    """Instantiate BaseApp into target_root with manifest-driven file copying."""
    target_root.mkdir(parents=True, exist_ok=True)

    entries = load_manifest_entries(source_root, {"pull", "once"})
    pull_files = entries.get("pull", [])
    one_off_files = entries.get("once", [])

    if logger:
        logger.log(
            message_code="INST002",
            data={"pull_count": len(pull_files), "once_count": len(one_off_files)},
        )

    copied_core = 0
    created_placeholders = 0
    kept_placeholders = 0

    for src_rel, dst_rel in pull_files:
        for file_src_rel, file_dst_rel in expand_manifest_entry(source_root, src_rel, dst_rel):
            src = source_root / file_src_rel
            dst = target_root / file_dst_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_core += 1

    if logger:
        logger.log(message_code="INST003", data={"copied": copied_core})

    for src_rel, dst_rel in one_off_files:
        for file_src_rel, file_dst_rel in expand_manifest_entry(source_root, src_rel, dst_rel):
            src = source_root / file_src_rel
            dst = target_root / file_dst_rel
            if dst.exists():
                kept_placeholders += 1
                continue

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            if file_dst_rel == Path("config/app.json"):
                populate_app_config(dst, target_value)
            created_placeholders += 1

    if logger:
        logger.log(
            message_code="INST004",
            data={"created": created_placeholders, "preserved": kept_placeholders},
        )

    if ensure_app_instruction_file(target_root, target_value):
        created_placeholders += 1
        if logger:
            logger.log(message_code="INST005", data={"target_app": target_value})
    else:
        kept_placeholders += 1
        if logger:
            logger.log(message_code="INST006", data={"target_app": target_value})

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

    script_root = Path(__file__).resolve().parent
    source_root = script_root.parent
    target_root = Path(args.target).expanduser().resolve()
    target_value = Path(args.target).name or target_root.name
    timestamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    logger = None
    template = None
    try:
        logger, template = create_instantiate_logger(source_root, target_value, timestamp)
        logger.log(
            message_code="INST001",
            data={"source": str(source_root), "target": str(target_root)},
        )
    except Exception:
        logger = None
        template = None

    if source_root == target_root:
        error = ValueError("Target path must be different from BaseApp source path.")
        if logger:
            logger.log(message_code="INST007", message_type="ERROR", data={"error": str(error)})
        raise error

    try:
        result = instantiate(source_root, target_root, target_value, logger=logger)

        print(f"Instantiated BaseApp at: {target_root}")
        print(f"Core files copied/updated: {result['copied_core']}")
        print(f"App placeholders created: {result['created_placeholders']}")
        print(f"App placeholders preserved: {result['kept_placeholders']}")

        if logger:
            logger.log(message_code="INST999", message_type="GOOD", data={"target_app": target_value})
    except Exception as exc:
        if logger:
            logger.log(message_code="INSTE01", message_type="ERROR", data={"error": str(exc)})
        raise
    finally:
        if logger and template:
            html_path = logger.save_html(
                title=f"Instantiate {target_value}",
                template=template,
            )
            if html_path:
                print(f"Instantiate log html: {html_path}")


if __name__ == "__main__":
    main()
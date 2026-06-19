#!/usr/bin/env python3
"""Instantiate BaseApp into a target path.

Rules:
- Read all manifest files from resources/manifests.
- Apply entries under 'pull' by copying on every instantiate run.
- Apply entries under 'once' by copying only when destination is missing.
- Create build/instructions/{APP_NAME}.md only if missing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import os
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


# Feature 3.1.1
def load_manifest_entries(source_root: Path, behaviors: set[str]) -> dict[str, list[tuple[Path, Path]]]:
    """Load entries from all manifests in resources/manifests grouped by behavior key."""
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
                if behavior == "once":
                    target = item.get("destination", item.get("target", source))
                else:
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


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override values into base dict in-place and return base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_overrides(overrides_arg: str) -> dict:
    """Load config overrides from a JSON file path or an inline JSON string.

    Args:
        overrides_arg: A path to a JSON file or a JSON-encoded object string.

    Returns:
        Parsed override dict.

    Raises:
        ValueError: If overrides_arg is not a valid file path or JSON string.
    """
    # Feature 3.1.12
    candidate = Path(overrides_arg)
    if candidate.is_file():
        with open(candidate, "r", encoding="utf-8") as f:
            result = json.load(f)
    else:
        try:
            result = json.loads(overrides_arg)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Overrides argument is neither a valid file path nor valid JSON. "
                f"JSON error: {exc}"
            ) from exc

    if not isinstance(result, dict):
        raise ValueError("Overrides must be a JSON object (dict), not a list or scalar.")
    return result


def populate_app_config(app_config_path: Path, target_value: str, overrides: dict | None = None):
    """Populate app config by updating COMMON.APP_NAME and applying optional deep-merge overrides.

    Args:
        app_config_path: Path to the app config JSON file to update.
        target_value: Value to set for COMMON.APP_NAME.
        overrides: Optional dict of values to deep-merge into the config after APP_NAME is set.
    """
    with open(app_config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    common = data.get("COMMON")
    if not isinstance(common, dict):
        common = {}
        data["COMMON"] = common

    common["APP_NAME"] = target_value

    if overrides:
        _deep_merge(data, overrides)

    with open(app_config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def ensure_app_instruction_file(target_root: Path, app_name: str) -> bool:
    """Create build/instructions/{APP_NAME}.md for app-specific guidance if missing."""
    app_instructions_path = target_root / "build" / "instructions" / f"{app_name}.md"
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
            if any(p in EXCLUDED_DIRS for p in child.parts) or child.suffix.lower() in EXCLUDED_EXTENSIONS:
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
    """Resolve HTML template path from config COMMON.HTML_TEMPLATE relative to app_root."""
    template_value = getattr(getattr(config, "COMMON", Params()), "HTML_TEMPLATE", "resources/templates/dataset_table.html")
    template_path = Path(str(template_value))
    if template_path.is_absolute():
        return str(template_path)
    return str((app_root / template_path).resolve())


def create_instantiate_logger(source_root: Path, target_app: str, timestamp: str):
    """Create instantiate logger under C:/data/BaseApp/instantiations/{date}."""
    config = load_runtime_config(source_root)
    output_prefix = Path(str(getattr(getattr(config, "COMMON", Params()), "OUTPUT_PREFIX", "C:/data/BaseApp")))
    output_date = str(getattr(getattr(config, "COMMON", Params()), "OUTPUT_VERSION", dt.datetime.utcnow().strftime("%Y%m%d")))
    log_dir = output_prefix / "instantiations" / output_date
    log_file = log_dir / f"{target_app}_{timestamp}.csv"
    template = resolve_template_path(source_root, config)

    messages_dir_value = getattr(config.LOG, "messages_dir", "resources/message_codes")
    messages_dir_path = Path(str(messages_dir_value))
    if not messages_dir_path.is_absolute():
        messages_dir_path = (source_root / messages_dir_path).resolve()
    message_lookup = load_message_lookup([str(messages_dir_path)])
    logger = Logger(
        log_path=str(log_file),
        start_time=dt.datetime.utcnow(),
        max_items=getattr(config.LOG, "max_items", None),
        verbose=getattr(config.LOG, "verbose", "INFO"),
        log_types=getattr(config.LOG, "types", None),
        type_colors=getattr(config.LOG, "colors", None),
        message_lookup=message_lookup,
    )
    return logger, template


# Feature 3.1.2
def instantiate(source_root: Path, target_root: Path, target_value: str, logger: Logger | None = None, overrides: dict | None = None):
    """Instantiate BaseApp into target_root with manifest-driven file copying.

    Args:
        source_root: Path to the BaseApp source root.
        target_root: Destination path for the new app variant.
        target_value: APP_NAME value for the new app.
        logger: Optional Logger instance for structured output.
        overrides: Optional dict of config values to deep-merge into config/app.json
                   after APP_NAME is set.
    """
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
        logger.log(message_code="INST003", data={"copied": copied_core}, populate=True)

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
                populate_app_config(dst, target_value, overrides=overrides)
                if logger and overrides:
                    logger.log(message_code="INST010", data={"keys": list(overrides.keys())})
            created_placeholders += 1

    if logger:
        logger.log(
            message_code="INST004",
            data={"created": created_placeholders, "preserved": kept_placeholders},
        )

    if ensure_app_instruction_file(target_root, target_value):
        created_placeholders += 1
        if logger:
            logger.log(message_code="INST005", data={"target_app": target_value}, populate=True)
    else:
        kept_placeholders += 1
        if logger:
            logger.log(message_code="INST006", data={"target_app": target_value}, populate=True)

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
    parser.add_argument(
        "--source",
        default=None,
        help="Path to the BaseApp source root. Defaults to the parent of this script's directory.",
    )
    parser.add_argument(
        "--overrides",
        default=None,
        help=(
            "Config overrides to apply to config/app.json after instantiation. "
            "Accepts a path to a JSON file or an inline JSON object string. "
            "Keys follow the config file structure, e.g. "
            '{\"COMMON\": {\"APP_NAME\": \"MyApp\", \"VERSION\": \"1.0\"}}'
        ),
    )
    return parser.parse_args()


# Feature 3.1.3
def main():
    args = parse_args()

    script_root = Path(__file__).resolve().parent
    source_root = Path(args.source).expanduser().resolve() if args.source else script_root.parent
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
        error = ValueError(
            f"Target path must be different from the BaseApp source path.\n"
            f"  source: {source_root}\n"
            f"  target: {target_root}\n"
            f"If you are running this script from a copied location, use --source to point at BaseApp:\n"
            f"  python instantiate.py <target> --source <path/to/BaseApp>"
        )
        if logger:
            logger.log(message_code="INST007", message_type="ERROR", data={"error": str(error)})
        raise error

    # Load optional config overrides
    overrides = None
    if args.overrides:
        try:
            overrides = load_overrides(args.overrides)
        except ValueError as exc:
            if logger:
                logger.log(message_code="INSTW10", message_type="WARN", data={"error": str(exc)}, populate=True)
            print(f"[WARN] Could not load overrides: {exc}")

    try:
        result = instantiate(source_root, target_root, target_value, logger=logger, overrides=overrides)

        print(f"Instantiated BaseApp at: {target_root}")
        print(f"Core files copied/updated: {result['copied_core']}")
        print(f"App placeholders created: {result['created_placeholders']}")
        print(f"App placeholders preserved: {result['kept_placeholders']}")

        if logger:
            logger.log(message_code="INST999", message_type="GOOD", data={"target_app": target_value}, populate=True)
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

    run_setup_env(target_root)


def run_setup_env(app_root: Path) -> None:
    """Run setup_env.ps1 for the given app root to create the venv and install deps.

    Locates setup_env.ps1 relative to this script's parent (BaseApp root), then
    invokes it non-interactively via pwsh targeting the specified app root.
    Prints a warning if the script is not found or exits with a non-zero code;
    does not raise so that instantiate output is always shown.
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
    main()
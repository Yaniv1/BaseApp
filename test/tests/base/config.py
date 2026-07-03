"""Feature ID: 5.3.1.6. Pre-tests for config loading: local override and user-controlled overlay (feature 6.1.4, 2.2)."""

import json
import os
import tempfile
from pathlib import Path

from utils.baseutils import Config, create_logger


def _build_result(status, message, criteria, features, data=None):
    """Assemble a structured test result line consumed by TestManager."""
    payload = {
        "status": status,
        "message": message,
        "criteria": criteria,
        "features": features,
    }
    if data is not None:
        payload["data"] = data
    return payload


# Feature 5.3.1.6.1
def test_local_config_override(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.6.1. Pre-test for config/local.json override loading.

    Covers features:
      - 6.1.4  (Config: loads all JSON files in config/ sorted, local.json last)
      - 2.2    (config/local.json: gitignored local placeholder, deployed via once.json)

    Tests:
      Pre:  config/local.json exists in the workspace config directory.
      Live: Config merges local.json values on top of base+app with a temp config dir.
      Post: Local override values are present; non-overridden keys from app are preserved.
    """
    features = ["6.1.4", "2.2"]
    criteria = []

    try:
        # --- PRE: config/local.json exists in the workspace ---
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        local_json_path = os.path.join(base_dir, "config", "local.json")
        local_exists = os.path.isfile(local_json_path)
        criteria.append({
            "name": "local_json_file_exists",
            "operator": "eq",
            "actual": local_exists,
            "expected": True,
            "success": local_exists,
            "status": "PASS" if local_exists else "FAIL",
        })

        # --- LIVE: local.json overrides base+app config values ---
        workdir = tempfile.mkdtemp(prefix="base_local_cfg_")
        try:
            workdir_path = Path(workdir)
            config_dir = workdir_path / "config"
            config_dir.mkdir()

            base_cfg = {
                "COMMON": {
                    "CONFIG_WRAPPERS": ["{$", "$}"],
                    "APP_NAME": "TestApp",
                    "VERSION": "1.0",
                    "OUTPUT_PREFIX": "/tmp/out",
                    "OUTPUT_VERSION": "20260101",
                    "OUTPUT_PATH": "/tmp/out/20260101",
                    "OUTPUT_WORKERS": 1,
                    "DATE_FORMAT": "%Y%m%d",
                    "DATETIME_FORMAT": "%Y%m%dT%H%M%SZ",
                    "START_TIME": "20260101T000000Z",
                    "RUN_ID": "ABC123",
                    "HTML_TEMPLATE": "resources/templates/dataset_table.html",
                    "HTML_WRAPPERS": ["{$", "$}"]
                },
                "APP": {
                    "verbose": True,
                    "tier": "base"
                }
            }
            app_cfg = {
                "APP": {
                    "verbose": False,
                    "tier": "app"
                }
            }
            local_cfg = {
                "APP": {
                    "tier": "local"
                }
            }

            (config_dir / "base.json").write_text(json.dumps(base_cfg), encoding="utf-8")
            (config_dir / "app.json").write_text(json.dumps(app_cfg), encoding="utf-8")
            (config_dir / "local.json").write_text(json.dumps(local_cfg), encoding="utf-8")

            cfg = Config(base_config_path=str(config_dir / "base.json"))

            # local.json overrides app.json: tier should be "local"
            tier_val = getattr(cfg.config.APP, "tier", None)
            tier_ok = tier_val == "local"
            criteria.append({
                "name": "local_overrides_app_value",
                "operator": "eq",
                "actual": tier_val,
                "expected": "local",
                "success": tier_ok,
                "status": "PASS" if tier_ok else "FAIL",
            })

            # app.json set verbose=False; local.json did not touch verbose — should stay False
            verbose_val = getattr(cfg.config.APP, "verbose", None)
            verbose_ok = verbose_val is False
            criteria.append({
                "name": "non_overridden_key_preserved",
                "operator": "eq",
                "actual": verbose_val,
                "expected": False,
                "success": verbose_ok,
                "status": "PASS" if verbose_ok else "FAIL",
            })

            # base COMMON.APP_NAME should still be present
            app_name = getattr(cfg.config.COMMON, "APP_NAME", None)
            app_name_ok = app_name == "TestApp"
            criteria.append({
                "name": "common_app_name_preserved",
                "operator": "eq",
                "actual": app_name,
                "expected": "TestApp",
                "success": app_name_ok,
                "status": "PASS" if app_name_ok else "FAIL",
            })

        finally:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)

        # --- POST: local.json is absent → Config still loads fine ---
        workdir2 = tempfile.mkdtemp(prefix="base_no_local_cfg_")
        try:
            workdir2_path = Path(workdir2)
            config_dir2 = workdir2_path / "config"
            config_dir2.mkdir()
            (config_dir2 / "base.json").write_text(json.dumps(base_cfg), encoding="utf-8")

            cfg2 = Config(base_config_path=str(config_dir2 / "base.json"))
            app_name2 = getattr(cfg2.config.COMMON, "APP_NAME", None)
            no_local_ok = app_name2 == "TestApp"
            criteria.append({
                "name": "loads_without_local_json",
                "operator": "eq",
                "actual": app_name2,
                "expected": "TestApp",
                "success": no_local_ok,
                "status": "PASS" if no_local_ok else "FAIL",
            })
        finally:
            import shutil
            shutil.rmtree(workdir2, ignore_errors=True)

    except Exception as exc:
        criteria.append({
            "name": "no_exception",
            "operator": "eq",
            "actual": str(exc),
            "expected": "no exception",
            "success": False,
            "status": "FAIL",
        })

    all_pass = all(c["status"] == "PASS" for c in criteria)
    status = "PASS" if all_pass else "FAIL"
    return _build_result(status, message or "Validated config/local.json local override loading", criteria, features)


# Feature 5.3.1.6.2
def test_config_files_ordering(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.6.2. Pre-test for COMMON.CONFIG_FILES user-controlled overlay.

    Covers features:
      - 6.1.4  (Config: user-controlled overlay selection/order via COMMON.CONFIG_FILES)

    Tests:
      Pre:  config/base.json declares COMMON.CONFIG_FILES with app.json enabled.
      Live: A declared CONFIG_FILES REPLACES the default folder seed (unlisted files
            are not loaded), overlays apply in declared order (last wins), an overlaid
            file's own CONFIG_FILES extends the ramp-up, disabled (false) entries are
            skipped, and a missing referenced file is skipped with a warning (no error).
      Post: With no CONFIG_FILES declared, the default overlays the base folder
            alphabetically (base -> app -> local).
    """
    features = ["6.1.4"]
    criteria = []

    def _add(name, actual, expected, ok, operator="eq"):
        criteria.append({
            "name": name, "operator": operator, "actual": actual,
            "expected": expected, "success": ok, "status": "PASS" if ok else "FAIL",
        })

    try:
        # --- PRE: workspace config/base.json declares CONFIG_FILES with app.json ---
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        with open(os.path.join(base_dir, "config", "base.json"), "r", encoding="utf-8") as f:
            base_json = json.load(f)
        declared = base_json.get("COMMON", {}).get("CONFIG_FILES", {})
        _add("base_declares_config_files_app", declared, {"app.json": True},
             isinstance(declared, dict) and declared.get("app.json") is True)

        # --- LIVE: CONFIG_FILES drives selection/order, extension, skips, warnings ---
        workdir = tempfile.mkdtemp(prefix="base_cfg_files_")
        try:
            config_dir = Path(workdir) / "config"
            config_dir.mkdir()
            (config_dir / "folder").mkdir()

            base_cfg = {
                "COMMON": {
                    "CONFIG_WRAPPERS": ["{$", "$}"],
                    "APP_NAME": "TestApp",
                    "CONFIG_FILES": {
                        "app.json": True,
                        "missing.json": True,
                        "skip.json": False,
                        "folder": True,
                    },
                },
                "APP": {"order": "base", "tier": "base"},
            }
            app_cfg = {
                "APP": {"order": "app", "tier": "app"},
                "COMMON": {"CONFIG_FILES": {"child.json": True}},
            }
            child_cfg = {"APP": {"order": "child", "child_loaded": True}}
            skip_cfg = {"APP": {"order": "SKIPPED", "tier": "SKIPPED"}}
            other_cfg = {"APP": {"order": "OTHER", "other_loaded": True}}
            a_cfg = {"APP": {"order": "a"}}
            b_cfg = {"APP": {"order": "b"}}

            for name, obj in [
                ("base.json", base_cfg), ("app.json", app_cfg), ("child.json", child_cfg),
                ("skip.json", skip_cfg), ("other.json", other_cfg),
            ]:
                (config_dir / name).write_text(json.dumps(obj), encoding="utf-8")
            (config_dir / "folder" / "a.json").write_text(json.dumps(a_cfg), encoding="utf-8")
            (config_dir / "folder" / "b.json").write_text(json.dumps(b_cfg), encoding="utf-8")

            loader = Config(base_config_path=str(config_dir / "base.json"))
            cfg = loader.config
            _add("chain_applies_app_over_base", getattr(cfg.APP, "tier", None), "app",
                 getattr(cfg.APP, "tier", None) == "app")

            # declared list REPLACES default: other.json (unlisted) must NOT load
            other_loaded = getattr(cfg.APP, "other_loaded", None)
            _add("declared_list_replaces_default", other_loaded, None, other_loaded is None, operator="is")

            # app.json's own CONFIG_FILES extends processing to child.json
            _add("config_files_extends_worklist", getattr(cfg.APP, "child_loaded", None), True,
                 getattr(cfg.APP, "child_loaded", None) is True)

            # order patched app -> child -> folder/a -> folder/b => final "b"
            _add("config_files_order_last_wins", getattr(cfg.APP, "order", None), "b",
                 getattr(cfg.APP, "order", None) == "b")

            # skip.json (false) never loaded
            _add("disabled_entry_not_loaded", getattr(cfg.APP, "tier", None), "SKIPPED",
                 getattr(cfg.APP, "tier", None) != "SKIPPED", operator="ne")

            # missing.json referenced -> buffered as a load warning, no exception
            warned = any(
                "missing.json" in str(w.get("config_file", "")) for w in loader.load_warnings)
            _add("missing_file_warns_no_error", warned, True, warned)

            # buffered warnings are emitted as WARN log messages (code BASEW07)
            logger = create_logger({
                "messages_dir": os.path.join(base_dir, "resources", "message_codes"),
                "base_dir": base_dir,
            })
            loader.log_warnings(logger)
            warn_logged = any(
                e.get("message_code") == "BASEW07" and e.get("type") == "WARN"
                for e in logger.logs)
            _add("missing_file_logged_as_warn", warn_logged, True, warn_logged)

            # buffer is cleared after logging
            _add("warnings_buffer_cleared", loader.load_warnings, [], loader.load_warnings == [])
        finally:
            import shutil
            shutil.rmtree(workdir, ignore_errors=True)

        # --- POST: no CONFIG_FILES => default overlays base folder alphabetically ---
        workdir2 = tempfile.mkdtemp(prefix="base_cfg_default_")
        try:
            config_dir2 = Path(workdir2) / "config"
            config_dir2.mkdir()
            default_base = {
                "COMMON": {"CONFIG_WRAPPERS": ["{$", "$}"], "APP_NAME": "TestApp"},
                "APP": {"tier": "base"},
            }
            (config_dir2 / "base.json").write_text(json.dumps(default_base), encoding="utf-8")
            (config_dir2 / "app.json").write_text(json.dumps({"APP": {"tier": "app"}}), encoding="utf-8")
            (config_dir2 / "local.json").write_text(json.dumps({"APP": {"tier": "local"}}), encoding="utf-8")

            cfg2 = Config(base_config_path=str(config_dir2 / "base.json")).config
            # base -> app -> local alphabetically => local wins
            _add("default_folder_overlay_alphabetical", getattr(cfg2.APP, "tier", None), "local",
                 getattr(cfg2.APP, "tier", None) == "local")
        finally:
            import shutil
            shutil.rmtree(workdir2, ignore_errors=True)

    except Exception as exc:
        _add("no_exception", str(exc), "no exception", False)

    all_pass = all(c["status"] == "PASS" for c in criteria)
    status = "PASS" if all_pass else "FAIL"
    return _build_result(status, message or "Validated COMMON.CONFIG_FILES user-controlled config overlay", criteria, features)

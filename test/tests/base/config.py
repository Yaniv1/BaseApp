"""Feature ID: 5.3.1.6. Pre-tests for config/local.json local override support (feature 6.1.4, 2.2)."""

import json
import os
import tempfile
from pathlib import Path

from utils.baseutils import Config


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

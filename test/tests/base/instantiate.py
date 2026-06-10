"""Feature ID: 5.3.1.5. Pre-tests for instantiate config override (features 3.1.12, 3.1.6, 3.1.13)."""

import json
import tempfile
from pathlib import Path

from scripts.instantiate import load_overrides, populate_app_config, _deep_merge


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


# Feature 5.3.1.5.1
def test_instantiate_config_overrides(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.5.1. Pre-test for instantiate config override application.

    Covers features:
      - 3.1.12  (load_overrides: reads overrides from JSON file path or inline JSON string)
      - 3.1.6   (populate_app_config: applies overrides after setting APP_NAME)
      - 3.1.13  (_deep_merge: recursively merges nested override dicts without clobbering untouched keys)

    Tests:
      Pre:  load_overrides resolves a JSON file and an inline JSON string correctly.
      Live: populate_app_config sets APP_NAME and deep-merges overrides into config/app.json.
      Post: Non-overridden keys are preserved; override values are present; APP_NAME is set.
    """
    features = ["3.1.12", "3.1.6", "3.1.13"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="baseinst_ovr_")

    try:
        workdir_path = Path(workdir)

        # --- PRE: load_overrides from a JSON file ---
        overrides_dict = {"COMMON": {"VERSION": "test-1.0"}, "APP": {"verbose": False}}
        overrides_file = workdir_path / "overrides.json"
        overrides_file.write_text(json.dumps(overrides_dict), encoding="utf-8")

        loaded_from_file = load_overrides(str(overrides_file))
        criteria.append({
            "name": "load_overrides_from_file",
            "operator": "eq",
            "actual": loaded_from_file,
            "expected": overrides_dict,
            "success": loaded_from_file == overrides_dict,
            "status": "PASS" if loaded_from_file == overrides_dict else "FAIL",
        })

        # --- PRE: load_overrides from an inline JSON string ---
        inline_json = '{"COMMON": {"VERSION": "inline-2.0"}}'
        loaded_from_string = load_overrides(inline_json)
        expected_inline = {"COMMON": {"VERSION": "inline-2.0"}}
        criteria.append({
            "name": "load_overrides_from_string",
            "operator": "eq",
            "actual": loaded_from_string,
            "expected": expected_inline,
            "success": loaded_from_string == expected_inline,
            "status": "PASS" if loaded_from_string == expected_inline else "FAIL",
        })

        # --- PRE: load_overrides raises ValueError on invalid input ---
        invalid_raised = False
        try:
            load_overrides("not_a_file_or_json")
        except ValueError:
            invalid_raised = True
        criteria.append({
            "name": "load_overrides_invalid_raises",
            "operator": "eq",
            "actual": invalid_raised,
            "expected": True,
            "success": invalid_raised,
            "status": "PASS" if invalid_raised else "FAIL",
        })

        # --- LIVE: populate_app_config applies overrides and sets APP_NAME ---
        base_config = {
            "COMMON": {"APP_NAME": "OldName", "VERSION": "0.0"},
            "APP": {"verbose": True, "extra_key": "keep_me"},
        }
        config_path = workdir_path / "app.json"
        config_path.write_text(json.dumps(base_config, indent=4), encoding="utf-8")

        overrides_live = {"COMMON": {"VERSION": "26.01.01"}, "APP": {"verbose": False}}
        populate_app_config(config_path, "NewApp", overrides=overrides_live)

        result_config = json.loads(config_path.read_text(encoding="utf-8"))

        # APP_NAME is set to the target_value argument
        actual_name = result_config.get("COMMON", {}).get("APP_NAME")
        criteria.append({
            "name": "app_name_set",
            "operator": "eq",
            "actual": actual_name,
            "expected": "NewApp",
            "success": actual_name == "NewApp",
            "status": "PASS" if actual_name == "NewApp" else "FAIL",
        })

        # Override value applied in COMMON
        actual_version = result_config.get("COMMON", {}).get("VERSION")
        criteria.append({
            "name": "override_version_applied",
            "operator": "eq",
            "actual": actual_version,
            "expected": "26.01.01",
            "success": actual_version == "26.01.01",
            "status": "PASS" if actual_version == "26.01.01" else "FAIL",
        })

        # Override value applied in nested APP section
        actual_verbose = result_config.get("APP", {}).get("verbose")
        criteria.append({
            "name": "override_verbose_applied",
            "operator": "eq",
            "actual": actual_verbose,
            "expected": False,
            "success": actual_verbose is False,
            "status": "PASS" if actual_verbose is False else "FAIL",
        })

        # --- POST: Non-overridden key preserved ---
        actual_extra = result_config.get("APP", {}).get("extra_key")
        criteria.append({
            "name": "non_overridden_key_preserved",
            "operator": "eq",
            "actual": actual_extra,
            "expected": "keep_me",
            "success": actual_extra == "keep_me",
            "status": "PASS" if actual_extra == "keep_me" else "FAIL",
        })

        # --- POST: _deep_merge does not clobber sibling keys ---
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"x": 99}}
        _deep_merge(base, override)
        y_preserved = base["a"].get("y") == 2
        x_updated = base["a"].get("x") == 99
        b_preserved = base.get("b") == 3
        deep_merge_ok = y_preserved and x_updated and b_preserved
        criteria.append({
            "name": "deep_merge_preserves_siblings",
            "operator": "eq",
            "actual": {"x": base["a"].get("x"), "y": base["a"].get("y"), "b": base.get("b")},
            "expected": {"x": 99, "y": 2, "b": 3},
            "success": deep_merge_ok,
            "status": "PASS" if deep_merge_ok else "FAIL",
        })

    except Exception as exc:
        criteria.append({
            "name": "unexpected_error",
            "operator": "eq",
            "actual": str(exc),
            "expected": None,
            "success": False,
            "status": "FAIL",
        })

    overall = "PASS" if all(c["status"] == "PASS" for c in criteria) else "FAIL"
    return _build_result(
        status=overall,
        message=message or "Validated instantiate config overrides: load_overrides, populate_app_config, _deep_merge",
        criteria=criteria,
        features=features,
    )

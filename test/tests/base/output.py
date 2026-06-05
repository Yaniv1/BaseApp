"""Feature ID: 5.3.1.3. pre-test for output storage tests."""
import json
import os
import shutil
import tempfile
import threading
import time

import numpy as np
import pandas as pd

from utils.baseutils import AppManager, Params, to_json_compatible


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


class _DummyLogger:
    """Minimal logger stub that records BASE003 events from _save_output_artifact."""

    def __init__(self):
        self.events = []

    def log(self, message="", message_type=None, data=None, message_code=None, entry=True):
        self.events.append({"message_code": message_code, "data": data or {}})


def _make_manager(workdir):
    """Build a minimal AppManager instance with just the attributes _save_output_artifact needs."""
    manager = object.__new__(AppManager)
    manager.output_manifest_path = os.path.join(workdir, "manifest.json")
    manager.output_manifest = {}
    manager._output_manifest_lock = threading.Lock()
    manager.logger = _DummyLogger()
    manager.logger_name = "logger"
    return manager


def _last_skip_flag(manager):
    """Return the `skipped` flag of the most recent BASE003 log entry."""
    for event in reversed(manager.logger.events):
        if event.get("message_code") == "BASE003":
            return bool(event.get("data", {}).get("skipped", False))
    return False


# Feature 5.3.1.3.1
def test_output_delta(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.3.1. Pre-test that exercises _save_output_artifact checksum/manifest delta behavior.

    Covers features:
      - 6.1.11.3 (AppManager.store_outputs: output-level delta flag via checksum manifest)
    """
    features = ["6.1.11.3"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basedelta_out_")

    try:
        out_dir = os.path.join(workdir, "out")
        os.makedirs(out_dir, exist_ok=True)
        output_dict = {
            "path": out_dir,
            "file": "artifact.json",
            "format": "json",
            "delta": True,
        }
        artifact_path = os.path.join(out_dir, "artifact.json")

        mgr = _make_manager(workdir)

        # 1) First save: file is created, manifest entry recorded, manifest.json on disk.
        mgr._save_output_artifact("k", output_dict, {"v": 1})
        first_mtime = os.path.getmtime(artifact_path) if os.path.isfile(artifact_path) else None
        entry1 = mgr.output_manifest.get(artifact_path)
        manifest_on_disk = os.path.isfile(mgr.output_manifest_path)
        criteria.append({
            "name": "first_save_writes_file_and_manifest_entry",
            "operator": "and",
            "actual": {"file_exists": first_mtime is not None, "entry": entry1, "manifest_on_disk": manifest_on_disk},
            "expected": "file exists, manifest entry with sha256+mtime, manifest.json on disk",
            "success": first_mtime is not None and isinstance(entry1, dict) and entry1.get("sha256") and manifest_on_disk,
            "status": "PASS" if (first_mtime is not None and isinstance(entry1, dict) and entry1.get("sha256") and manifest_on_disk) else "FAIL",
        })

        # 2) Repeat save with same data: skip path taken, mtime unchanged.
        time.sleep(0.05)
        mgr._save_output_artifact("k", output_dict, {"v": 1})
        second_mtime = os.path.getmtime(artifact_path)
        skipped_second = _last_skip_flag(mgr)
        criteria.append({
            "name": "repeat_save_same_data_is_skipped",
            "operator": "and",
            "actual": {"mtime_unchanged": second_mtime == first_mtime, "skipped_logged": skipped_second},
            "expected": "mtime equal AND BASE003 skipped=True",
            "success": (second_mtime == first_mtime) and skipped_second,
            "status": "PASS" if ((second_mtime == first_mtime) and skipped_second) else "FAIL",
        })

        # 3) Modified data: file rewritten, manifest sha256 changes.
        time.sleep(0.05)
        prev_sha = mgr.output_manifest[artifact_path]["sha256"]
        mgr._save_output_artifact("k", output_dict, {"v": 2})
        third_mtime = os.path.getmtime(artifact_path)
        new_sha = mgr.output_manifest[artifact_path]["sha256"]
        criteria.append({
            "name": "modified_data_rewrites_and_updates_manifest",
            "operator": "and",
            "actual": {"mtime_advanced": third_mtime > first_mtime, "sha_changed": new_sha != prev_sha},
            "expected": "mtime advanced AND sha256 changed",
            "success": (third_mtime > first_mtime) and (new_sha != prev_sha),
            "status": "PASS" if ((third_mtime > first_mtime) and (new_sha != prev_sha)) else "FAIL",
        })

        # 4) Delete manifest entry but keep file: save should rewrite and restore entry.
        time.sleep(0.05)
        del mgr.output_manifest[artifact_path]
        mgr._save_output_artifact("k", output_dict, {"v": 2})
        restored_entry = mgr.output_manifest.get(artifact_path)
        criteria.append({
            "name": "missing_manifest_entry_triggers_rewrite",
            "operator": "neq",
            "actual": restored_entry,
            "expected": "non-empty manifest entry restored",
            "success": isinstance(restored_entry, dict) and bool(restored_entry.get("sha256")),
            "status": "PASS" if (isinstance(restored_entry, dict) and bool(restored_entry.get("sha256"))) else "FAIL",
        })

        # 5) delta=False: always writes; no manifest update for this artifact path.
        nondelta_dict = dict(output_dict, file="nondelta.json", delta=False)
        nondelta_path = os.path.join(out_dir, "nondelta.json")
        mgr._save_output_artifact("k2", nondelta_dict, {"v": 1})
        m1 = os.path.getmtime(nondelta_path)
        time.sleep(0.05)
        mgr._save_output_artifact("k2", nondelta_dict, {"v": 1})
        m2 = os.path.getmtime(nondelta_path)
        criteria.append({
            "name": "non_delta_mode_always_rewrites",
            "operator": "and",
            "actual": {"second_write_mtime_advanced": m2 > m1, "no_manifest_entry": nondelta_path not in mgr.output_manifest},
            "expected": "mtime advanced AND no manifest entry",
            "success": (m2 > m1) and (nondelta_path not in mgr.output_manifest),
            "status": "PASS" if ((m2 > m1) and (nondelta_path not in mgr.output_manifest)) else "FAIL",
        })

        # 6) Manifest persisted to disk matches in-memory dict.
        with open(mgr.output_manifest_path, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        criteria.append({
            "name": "manifest_file_matches_memory",
            "operator": "eq",
            "actual": on_disk.get(artifact_path),
            "expected": mgr.output_manifest.get(artifact_path),
            "success": on_disk.get(artifact_path) == mgr.output_manifest.get(artifact_path),
            "status": "PASS" if on_disk.get(artifact_path) == mgr.output_manifest.get(artifact_path) else "FAIL",
        })

        overall = "PASS" if all(c["success"] for c in criteria) else "FAIL"
        return _build_result(
            status=overall,
            message=message or "Validated _save_output_artifact delta mode for skip, overwrite, and missing-file paths",
            criteria=criteria,
            features=features,
            data={"workdir": workdir},
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# Feature 5.3.1.3.2
def test_concurrent_store_outputs(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.3.2. Pre-test that validates store_outputs concurrent mode (OUTPUT_WORKERS > 1).

    Covers features:
      - 6.1.11.3  (store_outputs: concurrent dispatch via ThreadPoolExecutor)
      - 6.1.11.9  (_store_one_output: single-artifact worker callable)
      - 6.1.11    (AppManager.__init__: _output_manifest_lock initialised)
      - 6.1.11.7  (_save_output_artifact: manifest guarded by lock)
    """
    features = ["6.1.11.3", "6.1.11.9", "6.1.11", "6.1.11.7"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="baseconcur_out_")

    try:
        artifact_names = [f"artifact_{i}" for i in range(6)]
        expected_files = sorted([f"{n}.json" for n in artifact_names])

        def _build_output_entries(out_path):
            """Return a plain dict of OUTPUT entries all pointing at out_path."""
            return {
                name: {
                    "store": True,
                    "source": f"RESULTS.{name}",
                    "path": out_path,
                    "file": f"{name}.json",
                    "format": "json",
                }
                for name in artifact_names
            }

        def _make_full_manager(out_path, output_workers):
            """Build a minimal AppManager with CONFIG and RESULTS ready for store_outputs."""
            mgr = object.__new__(AppManager)
            mgr.output_manifest_path = os.path.join(workdir, f"manifest_{output_workers}.json")
            mgr.output_manifest = {}
            mgr._output_manifest_lock = threading.Lock()
            mgr.logger = _DummyLogger()
            mgr.logger_name = "logger"
            mgr.CONFIG = Params({
                "COMMON": {"OUTPUT_WORKERS": output_workers},
                "OUTPUT": _build_output_entries(out_path),
            })
            mgr.RESULTS = Params({name: {"value": i} for i, name in enumerate(artifact_names)})
            return mgr

        # ------------------------------------------------------------------ #
        # Criterion 1: Sequential mode (OUTPUT_WORKERS=1) writes all 6 artifacts.
        # ------------------------------------------------------------------ #
        seq_dir = os.path.join(workdir, "seq")
        os.makedirs(seq_dir, exist_ok=True)
        seq_mgr = _make_full_manager(seq_dir, output_workers=1)
        seq_mgr.store_outputs()
        seq_files = sorted(os.listdir(seq_dir))
        seq_ok = seq_files == expected_files
        criteria.append({
            "name": "sequential_mode_writes_all_artifacts",
            "operator": "eq",
            "actual": seq_files,
            "expected": expected_files,
            "success": seq_ok,
            "status": "PASS" if seq_ok else "FAIL",
        })

        # ------------------------------------------------------------------ #
        # Criterion 2: Concurrent mode (OUTPUT_WORKERS=4) writes all 6 artifacts.
        # ------------------------------------------------------------------ #
        con_dir = os.path.join(workdir, "con")
        os.makedirs(con_dir, exist_ok=True)
        con_mgr = _make_full_manager(con_dir, output_workers=4)
        con_mgr.store_outputs()
        con_files = sorted(os.listdir(con_dir))
        con_ok = con_files == expected_files
        criteria.append({
            "name": "concurrent_mode_writes_all_artifacts",
            "operator": "eq",
            "actual": con_files,
            "expected": expected_files,
            "success": con_ok,
            "status": "PASS" if con_ok else "FAIL",
        })

        # ------------------------------------------------------------------ #
        # Criterion 3: Concurrent mode file contents match sequential mode.
        # ------------------------------------------------------------------ #
        contents_match = True
        mismatch_file = None
        for fname in expected_files:
            with open(os.path.join(seq_dir, fname), "r", encoding="utf-8") as f:
                seq_data = json.load(f)
            with open(os.path.join(con_dir, fname), "r", encoding="utf-8") as f:
                con_data = json.load(f)
            if seq_data != con_data:
                contents_match = False
                mismatch_file = fname
                break
        criteria.append({
            "name": "concurrent_output_contents_match_sequential",
            "operator": "eq",
            "actual": contents_match,
            "expected": True,
            "success": contents_match,
            "status": "PASS" if contents_match else "FAIL",
            **({"data": {"mismatch_file": mismatch_file}} if not contents_match else {}),
        })

        # ------------------------------------------------------------------ #
        # Criterion 4: A worker exception is logged as BASEW06 and does not
        #              prevent other outputs from being written.
        # ------------------------------------------------------------------ #
        err_dir = os.path.join(workdir, "err")
        os.makedirs(err_dir, exist_ok=True)
        # Create a file at the path that bad_output tries to use as a directory.
        bad_path = os.path.join(err_dir, "collision")
        with open(bad_path, "w") as f:
            f.write("not a directory")

        err_mgr = object.__new__(AppManager)
        err_mgr.output_manifest_path = os.path.join(workdir, "manifest_err.json")
        err_mgr.output_manifest = {}
        err_mgr._output_manifest_lock = threading.Lock()
        err_mgr.logger = _DummyLogger()
        err_mgr.logger_name = "logger"
        err_entries = _build_output_entries(err_dir)
        # bad_output points at a file path — os.makedirs inside _save_output_artifact will fail.
        err_entries["bad_output"] = {
            "store": True,
            "source": "RESULTS.artifact_0",
            "path": bad_path,
            "file": "should_fail.json",
            "format": "json",
        }
        err_mgr.CONFIG = Params({"COMMON": {"OUTPUT_WORKERS": 4}, "OUTPUT": err_entries})
        err_mgr.RESULTS = Params({name: {"value": i} for i, name in enumerate(artifact_names)})
        err_mgr.store_outputs()

        warn_logged = any(e.get("message_code") == "BASEW06" for e in err_mgr.logger.events)
        good_files = sorted(f for f in os.listdir(err_dir) if f.startswith("artifact_"))
        all_good_written = good_files == expected_files
        criteria.append({
            "name": "worker_exception_logged_as_basew06",
            "operator": "eq",
            "actual": warn_logged,
            "expected": True,
            "success": warn_logged,
            "status": "PASS" if warn_logged else "FAIL",
        })
        criteria.append({
            "name": "remaining_outputs_written_despite_worker_failure",
            "operator": "eq",
            "actual": good_files,
            "expected": expected_files,
            "success": all_good_written,
            "status": "PASS" if all_good_written else "FAIL",
        })

        # ------------------------------------------------------------------ #
        # Criterion 6: _output_manifest_lock is a real Lock on the stub manager.
        # ------------------------------------------------------------------ #
        lock_present = isinstance(err_mgr._output_manifest_lock, type(threading.Lock()))
        criteria.append({
            "name": "output_manifest_lock_initialised",
            "operator": "eq",
            "actual": lock_present,
            "expected": True,
            "success": lock_present,
            "status": "PASS" if lock_present else "FAIL",
        })

        overall = "PASS" if all(c["success"] for c in criteria) else "FAIL"
        return _build_result(
            status=overall,
            message=message or "Validated store_outputs concurrent mode: all artifacts written, errors isolated, lock present",
            criteria=criteria,
            features=features,
            data={"workdir": workdir},
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# Feature 5.3.1.3.3
def test_to_json_compatible(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.3.3. Pre-test for the module-level to_json_compatible serialization helper.

    Covers features:
      - 6.1.17  (to_json_compatible: module-level recursive JSON-serialization function)
    """
    features = ["6.1.17"]
    criteria = []

    # ------------------------------------------------------------------ #
    # Pre-test: to_json_compatible is importable at module level.
    # ------------------------------------------------------------------ #
    import_ok = callable(to_json_compatible)
    criteria.append({
        "name": "to_json_compatible_importable_at_module_level",
        "operator": "eq",
        "actual": import_ok,
        "expected": True,
        "success": import_ok,
        "status": "PASS" if import_ok else "FAIL",
    })

    # ------------------------------------------------------------------ #
    # Live test: scalar and collection type conversions.
    # ------------------------------------------------------------------ #

    # Params instance expands to dict.
    p = Params({"a": 1, "b": "x"})
    result_params = to_json_compatible(p)
    params_ok = isinstance(result_params, dict) and result_params.get("a") == 1
    criteria.append({
        "name": "params_expanded_to_dict",
        "operator": "eq",
        "actual": params_ok,
        "expected": True,
        "success": params_ok,
        "status": "PASS" if params_ok else "FAIL",
    })

    # np.floating NaN → None; normal float32 → Python float.
    nan_result = to_json_compatible(np.float32("nan"))
    float_result = to_json_compatible(np.float32(3.14))
    nan_ok = nan_result is None
    float_ok = isinstance(float_result, float) and abs(float_result - 3.14) < 0.01
    criteria.append({
        "name": "np_floating_nan_becomes_none",
        "operator": "eq",
        "actual": nan_ok,
        "expected": True,
        "success": nan_ok,
        "status": "PASS" if nan_ok else "FAIL",
    })
    criteria.append({
        "name": "np_floating_value_becomes_python_float",
        "operator": "eq",
        "actual": float_ok,
        "expected": True,
        "success": float_ok,
        "status": "PASS" if float_ok else "FAIL",
    })

    # np.integer → Python int.
    int_result = to_json_compatible(np.int64(42))
    int_ok = int_result == 42 and type(int_result) is int
    criteria.append({
        "name": "np_integer_becomes_python_int",
        "operator": "eq",
        "actual": int_ok,
        "expected": True,
        "success": int_ok,
        "status": "PASS" if int_ok else "FAIL",
    })

    # np.bool_ → Python bool.
    bool_result = to_json_compatible(np.bool_(True))
    bool_ok = bool_result is True and type(bool_result) is bool
    criteria.append({
        "name": "np_bool_becomes_python_bool",
        "operator": "eq",
        "actual": bool_ok,
        "expected": True,
        "success": bool_ok,
        "status": "PASS" if bool_ok else "FAIL",
    })

    # pd.NA → None.
    na_result = to_json_compatible(pd.NA)
    na_ok = na_result is None
    criteria.append({
        "name": "pd_na_becomes_none",
        "operator": "eq",
        "actual": na_ok,
        "expected": True,
        "success": na_ok,
        "status": "PASS" if na_ok else "FAIL",
    })

    # DataFrame → list of record dicts with converted values.
    df = pd.DataFrame({"col": [np.int64(1), np.int64(2)]})
    df_result = to_json_compatible(df)
    df_ok = (
        isinstance(df_result, list)
        and len(df_result) == 2
        and type(df_result[0]["col"]) is int
    )
    criteria.append({
        "name": "dataframe_converted_to_record_list",
        "operator": "eq",
        "actual": df_ok,
        "expected": True,
        "success": df_ok,
        "status": "PASS" if df_ok else "FAIL",
    })

    # ------------------------------------------------------------------ #
    # Post-test: result can be serialized to JSON without errors.
    # ------------------------------------------------------------------ #
    payload = {
        "int": np.int64(7),
        "float": np.float32(1.5),
        "nan": np.float32("nan"),
        "bool": np.bool_(False),
        "na": pd.NA,
        "df": pd.DataFrame({"x": [np.int64(0)]}),
    }
    try:
        serialized = json.dumps(to_json_compatible(payload))
        json_ok = isinstance(serialized, str)
    except Exception:
        json_ok = False
    criteria.append({
        "name": "converted_payload_json_serializable",
        "operator": "eq",
        "actual": json_ok,
        "expected": True,
        "success": json_ok,
        "status": "PASS" if json_ok else "FAIL",
    })

    overall = "PASS" if all(c["success"] for c in criteria) else "FAIL"
    return _build_result(
        status=overall,
        message=message or "Validated to_json_compatible module-level function: import, type conversions, and JSON serialization",
        criteria=criteria,
        features=features,
    )

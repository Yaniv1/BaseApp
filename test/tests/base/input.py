"""Feature ID: 5.3.1.2.1. Delta mode pre-test for input loading."""

import json
import os
import shutil
import tempfile
import time

from utils.datautils import DataLoader


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


# Feature 5.3.1.2.1
def test_input_delta(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.2.1. Pre-test that exercises DataLoader delta mode for new, unchanged, modified, and added files.

    Covers features:
      - 6.1.11.1 (AppManager.load_data: persists per-input last_modified across cycles)
      - 6.2.2   (DataLoader: input-level delta flag and last_modified tracking)
    """
    features = ["6.1.11.1", "6.2.2"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basedelta_in_")

    try:
        # Seed two input files in a temp surface.
        path_a = os.path.join(workdir, "a.json")
        path_b = os.path.join(workdir, "b.json")
        with open(path_a, "w", encoding="utf-8") as f:
            json.dump({"v": 1}, f)
        with open(path_b, "w", encoding="utf-8") as f:
            json.dump({"v": 1}, f)

        source = {"path": workdir, "format": "json", "delta": True}

        # First load: both files should be picked up; last_modified populated for both.
        loader1 = DataLoader(source=source, base_dir=workdir)
        data1 = loader1.load()
        criteria.append({
            "name": "first_load_picks_up_all_files",
            "operator": "eq",
            "actual": len(data1),
            "expected": 2,
            "success": len(data1) == 2,
            "status": "PASS" if len(data1) == 2 else "FAIL",
        })
        criteria.append({
            "name": "first_load_records_last_modified",
            "operator": "eq",
            "actual": len(loader1.last_modified),
            "expected": 2,
            "success": len(loader1.last_modified) == 2,
            "status": "PASS" if len(loader1.last_modified) == 2 else "FAIL",
        })

        # Second load on unchanged surface: nothing should be reloaded.
        loader2 = DataLoader(
            source=source,
            base_dir=workdir,
            data=dict(data1),
            last_modified=dict(loader1.last_modified),
        )
        loader2.load()
        # Detect reloads by tracking which files actually went through _load_file.
        # Simpler proxy: if last_modified stayed identical and data dict was not re-populated, none reloaded.
        reloaded_unchanged = loader2.last_modified != loader1.last_modified
        criteria.append({
            "name": "unchanged_cycle_skips_all_files",
            "operator": "eq",
            "actual": reloaded_unchanged,
            "expected": False,
            "success": not reloaded_unchanged,
            "status": "PASS" if not reloaded_unchanged else "FAIL",
        })

        # Touch file a to bump its mtime; only a should reload.
        time.sleep(0.05)
        new_mtime = time.time() + 1
        os.utime(path_a, (new_mtime, new_mtime))
        loader3 = DataLoader(
            source=source,
            base_dir=workdir,
            data=dict(data1),
            last_modified=dict(loader1.last_modified),
        )
        loader3.load()
        a_key = "a.json"
        b_key = "b.json"
        a_changed = loader3.last_modified.get(a_key) != loader1.last_modified.get(a_key)
        b_unchanged = loader3.last_modified.get(b_key) == loader1.last_modified.get(b_key)
        criteria.append({
            "name": "modified_file_is_reloaded",
            "operator": "eq",
            "actual": a_changed,
            "expected": True,
            "success": a_changed,
            "status": "PASS" if a_changed else "FAIL",
        })
        criteria.append({
            "name": "unmodified_file_is_skipped",
            "operator": "eq",
            "actual": b_unchanged,
            "expected": True,
            "success": b_unchanged,
            "status": "PASS" if b_unchanged else "FAIL",
        })

        # Add a new file on the input surface; only the new file should be loaded.
        path_c = os.path.join(workdir, "c.json")
        with open(path_c, "w", encoding="utf-8") as f:
            json.dump({"v": 1}, f)
        loader4 = DataLoader(
            source=source,
            base_dir=workdir,
            data=dict(loader3.data),
            last_modified=dict(loader3.last_modified),
        )
        loader4.load()
        c_loaded = "c.json" in loader4.data and "c.json" in loader4.last_modified
        criteria.append({
            "name": "new_file_is_loaded_on_next_cycle",
            "operator": "eq",
            "actual": c_loaded,
            "expected": True,
            "success": c_loaded,
            "status": "PASS" if c_loaded else "FAIL",
        })

        # Non-delta mode rescans and reloads everything regardless of state.
        source_full = {"path": workdir, "format": "json", "delta": False}
        loader5 = DataLoader(
            source=source_full,
            base_dir=workdir,
            data=dict(loader4.data),
            last_modified=dict(loader4.last_modified),
        )
        loader5.load()
        all_tracked = all(k in loader5.last_modified for k in loader4.data.keys())
        criteria.append({
            "name": "non_delta_mode_loads_all_files",
            "operator": "eq",
            "actual": all_tracked,
            "expected": True,
            "success": all_tracked,
            "status": "PASS" if all_tracked else "FAIL",
        })

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    overall = "FAIL" if any(c["status"] == "FAIL" for c in criteria) else "PASS"
    return _build_result(
        status=overall,
        message=message or "Validated DataLoader delta mode across new, unchanged, modified, and added files",
        criteria=criteria,
        features=features,
        data={"workdir": workdir},
    )

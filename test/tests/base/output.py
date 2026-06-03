"""Feature ID: 5.3.1.2.2. Delta mode pre-test for output storage."""

import json
import os
import shutil
import tempfile
import time

from utils.baseutils import AppManager


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
    manager.logger = _DummyLogger()
    manager.logger_name = "logger"
    return manager


def _last_skip_flag(manager):
    """Return the `skipped` flag of the most recent BASE003 log entry."""
    for event in reversed(manager.logger.events):
        if event.get("message_code") == "BASE003":
            return bool(event.get("data", {}).get("skipped", False))
    return False


# Feature 5.3.1.2.2
def test_output_delta(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.2.2. Pre-test that exercises _save_output_artifact checksum/manifest delta behavior.

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

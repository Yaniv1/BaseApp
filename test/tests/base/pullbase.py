"""Feature ID: 5.3.1.4. Pre-tests for pullbase once-pull sync (features 3.2.12 and 3.2.13)."""

import json
import shutil
import tempfile
from pathlib import Path

from scripts.pullbase import load_once_entries, pull_once_files


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


def _write_manifest(directory: Path, once_entries: list) -> Path:
    """Write a minimal manifest JSON file with the given once entries."""
    manifest = {"once": once_entries}
    path = directory / "once.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


# Feature 5.3.1.4.1
def test_pullbase_once_sync(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.4.1. Pre-test for pullbase once-pull sync.

    Covers features:
      - 3.2.12  (load_once_entries: reads 'once' entries from manifest files)
      - 3.2.13  (pull_once_files: copies missing files; skips existing files)

    Tests:
      Pre:  load_once_entries returns correct entries from a manifest file.
      Live: pull_once_files copies missing files and skips existing ones.
      Post: Missing source paths are reported in missing_sources.
    """
    features = ["3.2.12", "3.2.13"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basepullonce_")

    try:
        source_root = Path(workdir) / "source"
        local_root = Path(workdir) / "local"
        manifests_dir = local_root / "resources" / "manifests"
        source_root.mkdir(parents=True)
        local_root.mkdir(parents=True)
        manifests_dir.mkdir(parents=True)

        # Create source files
        (source_root / "build").mkdir(parents=True)
        (source_root / "build" / "requirements").mkdir(parents=True)
        req_src = source_root / "build" / "requirements" / "app.json"
        req_src.write_text('{"REQUIREMENTS":[]}', encoding="utf-8")
        task_src = source_root / "build" / "tasks" / "app.json"
        task_src.parent.mkdir(parents=True)
        task_src.write_text('{"TASKS":[]}', encoding="utf-8")

        # Write manifest into local manifests dir (as pullbase would after sync_manifests)
        _write_manifest(
            manifests_dir,
            [
                {"source": "build/requirements/app.json"},
                {"source": "build/tasks/app.json"},
            ],
        )

        # --- PRE: load_once_entries reads entries correctly ---
        manifest_paths = list(manifests_dir.glob("*.json"))
        entries = load_once_entries(manifest_paths)

        criteria.append({
            "name": "load_once_entries_count",
            "operator": "eq",
            "actual": len(entries),
            "expected": 2,
            "success": len(entries) == 2,
            "status": "PASS" if len(entries) == 2 else "FAIL",
        })
        sources = [e[0] for e in entries]
        criteria.append({
            "name": "load_once_entries_sources",
            "operator": "eq",
            "actual": sorted(sources),
            "expected": sorted(["build/requirements/app.json", "build/tasks/app.json"]),
            "success": sorted(sources) == sorted(["build/requirements/app.json", "build/tasks/app.json"]),
            "status": "PASS" if sorted(sources) == sorted(["build/requirements/app.json", "build/tasks/app.json"]) else "FAIL",
        })

        # --- LIVE: pull_once_files copies missing, skips existing ---
        # First run: both destinations are absent -> both should be copied
        copied1, skipped1, missing1 = pull_once_files(local_root, source_root, entries)
        criteria.append({
            "name": "pull_once_copies_missing_files",
            "operator": "eq",
            "actual": copied1,
            "expected": 2,
            "success": copied1 == 2,
            "status": "PASS" if copied1 == 2 else "FAIL",
        })
        criteria.append({
            "name": "pull_once_skips_none_on_first_run",
            "operator": "eq",
            "actual": skipped1,
            "expected": 0,
            "success": skipped1 == 0,
            "status": "PASS" if skipped1 == 0 else "FAIL",
        })
        criteria.append({
            "name": "pull_once_no_missing_sources_on_first_run",
            "operator": "eq",
            "actual": len(missing1),
            "expected": 0,
            "success": len(missing1) == 0,
            "status": "PASS" if len(missing1) == 0 else "FAIL",
        })

        # Verify files actually landed at destinations
        req_dst = local_root / "build" / "requirements" / "app.json"
        task_dst = local_root / "build" / "tasks" / "app.json"
        criteria.append({
            "name": "pull_once_destinations_exist",
            "operator": "eq",
            "actual": req_dst.is_file() and task_dst.is_file(),
            "expected": True,
            "success": req_dst.is_file() and task_dst.is_file(),
            "status": "PASS" if req_dst.is_file() and task_dst.is_file() else "FAIL",
        })

        # Second run: both destinations now exist -> both should be skipped
        copied2, skipped2, missing2 = pull_once_files(local_root, source_root, entries)
        criteria.append({
            "name": "pull_once_skips_existing_files",
            "operator": "eq",
            "actual": skipped2,
            "expected": 2,
            "success": skipped2 == 2,
            "status": "PASS" if skipped2 == 2 else "FAIL",
        })
        criteria.append({
            "name": "pull_once_copies_none_on_second_run",
            "operator": "eq",
            "actual": copied2,
            "expected": 0,
            "success": copied2 == 0,
            "status": "PASS" if copied2 == 0 else "FAIL",
        })

        # --- POST: missing source is reported in missing_sources ---
        bad_entries = [("docs/nonexistent/ghost.json", "docs/nonexistent/ghost.json")]
        copied3, skipped3, missing3 = pull_once_files(local_root, source_root, bad_entries)
        criteria.append({
            "name": "pull_once_reports_missing_source",
            "operator": "eq",
            "actual": len(missing3),
            "expected": 1,
            "success": len(missing3) == 1,
            "status": "PASS" if len(missing3) == 1 else "FAIL",
        })

        overall = all(c["status"] == "PASS" for c in criteria)
        return _build_result(
            status="PASS" if overall else "FAIL",
            message=message or "Validated pullbase once-pull sync: load_once_entries and pull_once_files",
            criteria=criteria,
            features=features,
        )

    except Exception as exc:  # noqa: BLE001
        return _build_result(
            status="FAIL",
            message=f"pullbase once-pull test raised exception: {exc}",
            criteria=criteria,
            features=features,
            data={"error": str(exc)},
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

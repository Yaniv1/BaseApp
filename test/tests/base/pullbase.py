"""Feature ID: 5.3.1.4. Pre-tests for pullbase once-pull sync (features 3.2.12 and 3.2.13)."""

import json
import shutil
import tempfile
from pathlib import Path

from scripts.pullbase import is_self_pull, load_once_entries, pull_once_files, resolve_baseapp_source


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


def _crit(name, actual, expected, success, operator="eq"):
    """Build one criterion line with a derived PASS/FAIL status."""
    return {
        "name": name,
        "operator": operator,
        "actual": actual,
        "expected": expected,
        "success": bool(success),
        "status": "PASS" if success else "FAIL",
    }


# Feature 5.3.1.4.2
def test_pullbase_baseapp_resolution_with_main_fallback(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.4.2. Pre-test for BaseApp source resolution.

    Covers feature 3.2.19 (resolve_baseapp_source): a task-branch worktree without
    its own config/local.json falls back to the app's main worktree local.json to
    discover COMMON.BASEAPP; --branch selects the BaseApp worktree by swapping the
    BASEAPP leaf; and when no BASEAPP is recorded the resolver falls back to the
    existing --baseSource behavior.
    """
    features = ["3.2.19"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basepull_resolve_")

    try:
        root = Path(workdir)

        # BaseApp source with main + dev branch worktrees.
        baseapps = root / "BaseApp"
        baseapp_main = baseapps / "main"
        baseapp_dev = baseapps / "dev"
        baseapp_main.mkdir(parents=True)
        baseapp_dev.mkdir(parents=True)

        # App container with a main worktree (carrying local.json) and a task
        # worktree (NO local.json yet).
        app = root / "MyApp"
        app_main = app / "main"
        app_task = app / "BASE-TASK-1"
        (app_main / "config").mkdir(parents=True)
        (app_task / "config").mkdir(parents=True)
        (app_main / "config" / "local.json").write_text(
            json.dumps({"COMMON": {"BASEAPP": str(baseapp_main)}}), encoding="utf-8"
        )

        script_root = app_task / "scripts"
        script_root.mkdir(parents=True)

        # 1. From the task worktree (no local.json) -> falls back to main's BASEAPP.
        resolved = resolve_baseapp_source(script_root, app_task, "main", "auto")
        criteria.append(_crit("task_falls_back_to_main_baseapp", str(resolved), str(baseapp_main.resolve()),
                              resolved == baseapp_main.resolve()))

        # 2. --branch swaps the BASEAPP leaf ({parent}/{branch}).
        resolved_dev = resolve_baseapp_source(script_root, app_task, "dev", "auto")
        criteria.append(_crit("branch_leaf_swap", str(resolved_dev), str(baseapp_dev.resolve()),
                              resolved_dev == baseapp_dev.resolve()))

        # 3. From the main worktree itself, reads its own local.json.
        resolved_main = resolve_baseapp_source(app_main / "scripts", app_main, "main", "auto")
        criteria.append(_crit("main_reads_own_local_config", str(resolved_main), str(baseapp_main.resolve()),
                              resolved_main == baseapp_main.resolve()))

        # 4. No BASEAPP anywhere -> falls back to explicit --baseSource.
        bare_app = root / "NoConfigApp"
        bare_branch = bare_app / "main"
        explicit_source = root / "explicit_src"
        explicit_source.mkdir(parents=True)
        (bare_branch / "scripts").mkdir(parents=True)
        resolved_fallback = resolve_baseapp_source(
            bare_branch / "scripts", bare_branch, "main", str(explicit_source)
        )
        criteria.append(_crit("falls_back_to_explicit_source", str(resolved_fallback), str(explicit_source.resolve()),
                              resolved_fallback == explicit_source.resolve()))

        # 5. Branch folder absent -> uses recorded BASEAPP path as-is.
        resolved_absent = resolve_baseapp_source(script_root, app_task, "nonexistent-branch", "auto")
        criteria.append(_crit("absent_branch_uses_recorded_path", str(resolved_absent), str(baseapp_main.resolve()),
                              resolved_absent == baseapp_main.resolve()))

        overall = all(c["status"] == "PASS" for c in criteria)
        return _build_result(
            status="PASS" if overall else "FAIL",
            message=message or "Validated resolve_baseapp_source: worktree/main fallback, branch leaf-swap, --baseSource fallback",
            criteria=criteria,
            features=features,
        )

    except Exception as exc:  # noqa: BLE001
        return _build_result(
            status="FAIL",
            message=f"pullbase resolution test raised exception: {exc}",
            criteria=criteria,
            features=features,
            data={"error": str(exc)},
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# Feature 5.3.1.4.3
def test_pullbase_refuses_self_pull(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.4.3. Pre-test for the pullbase self-pull guard.

    Covers feature 3.2.20 (is_self_pull): pullbase must refuse to copy when the
    resolved source and the local app belong to the same deployment/repository,
    so running it inside a BaseApp (or any app) worktree cannot overwrite the
    running branch's changes with a sibling branch worktree's files. A genuine
    variant app pulling from a separate BaseApp container is NOT flagged.
    """
    features = ["3.2.20"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basepull_selfpull_")

    try:
        root = Path(workdir)

        # A bare/worktree container: {container}/.bare + sibling branch worktrees.
        container = root / "BaseApp"
        (container / ".bare").mkdir(parents=True)
        main_wt = container / "main"
        task_wt = container / "BASE-TASK-1"
        main_wt.mkdir()
        task_wt.mkdir()

        # 1. Sibling worktrees of the same container -> self-pull.
        criteria.append(_crit("siblings_flagged", is_self_pull(task_wt, main_wt), True,
                              is_self_pull(task_wt, main_wt) is True))

        # 2. Source resolved to the shared container (ancestor of local) -> self-pull.
        criteria.append(_crit("container_ancestor_flagged", is_self_pull(task_wt, container), True,
                              is_self_pull(task_wt, container) is True))

        # 3. Exact same folder -> self-pull.
        criteria.append(_crit("same_folder_flagged", is_self_pull(task_wt, task_wt), True,
                              is_self_pull(task_wt, task_wt) is True))

        # 4. Genuine variant app in a separate container pulling from BaseApp main
        #    -> NOT a self-pull.
        variant = root / "MyVariant" / "main"
        variant.mkdir(parents=True)
        criteria.append(_crit("distinct_container_not_flagged", is_self_pull(variant, main_wt), False,
                              is_self_pull(variant, main_wt) is False))

        overall = all(c["status"] == "PASS" for c in criteria)
        return _build_result(
            status="PASS" if overall else "FAIL",
            message=message or "Validated is_self_pull: same-repo/sibling/ancestor flagged, distinct container allowed",
            criteria=criteria,
            features=features,
        )

    except Exception as exc:  # noqa: BLE001
        return _build_result(
            status="FAIL",
            message=f"pullbase self-pull guard test raised exception: {exc}",
            criteria=criteria,
            features=features,
            data={"error": str(exc)},
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

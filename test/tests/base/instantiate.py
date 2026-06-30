"""Feature ID: 5.3.1.5. Pre-tests for instantiate config override (features 3.1.12, 3.1.6, 3.1.13)."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from scripts.instantiate import (
    load_overrides,
    populate_app_config,
    _deep_merge,
    resolve_app_root,
    populate_local_config,
    instantiate,
)


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


def _make_min_source(root: Path) -> Path:
    """Create a minimal BaseApp-like source tree (manifests + a few files) under root."""
    manifests = root / "resources" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "pull.json").write_text(
        json.dumps({"pull": [{"source": "app/app.py"}, {"source": "scripts/pullbase.py"}]}),
        encoding="utf-8",
    )
    (manifests / "once.json").write_text(
        json.dumps({"once": [{"source": "config/app.json", "destination": "config/app.json"}]}),
        encoding="utf-8",
    )
    (root / "app").mkdir(parents=True)
    (root / "app" / "app.py").write_text("print('app')\n", encoding="utf-8")
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "pullbase.py").write_text("# pullbase\n", encoding="utf-8")
    (root / "config").mkdir(parents=True)
    (root / "config" / "app.json").write_text(
        json.dumps({"COMMON": {"APP_NAME": "TEMPLATE"}}, indent=4), encoding="utf-8"
    )
    return root


# Feature 5.3.1.5.2
def test_instantiate_branch_aware_layout(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.5.2. Pre-test for the branch-aware deployment layout.

    Covers features:
      - 3.1.14  (resolve_app_root: derives {abspath(container)}/{app-name}/{branch})
      - 3.1.15  (populate_local_config: records COMMON.BASEAPP in config/local.json,
                 preserving other keys)
    """
    features = ["3.1.14", "3.1.15"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="baseinst_layout_")

    try:
        workdir_path = Path(workdir)

        # --- resolve_app_root: absolute container ---
        abs_container = workdir_path / "code"
        abs_container.mkdir(parents=True)
        root_abs = resolve_app_root(str(abs_container), "MyApp", "main", workdir_path / "scripts")
        expected_abs = (abs_container / "MyApp" / "main").resolve()
        criteria.append(_crit("resolve_app_root_absolute", str(root_abs), str(expected_abs), root_abs == expected_abs))

        # --- resolve_app_root: relative container resolved against script dir ---
        script_dir = workdir_path / "BaseApp" / "main" / "scripts"
        script_dir.mkdir(parents=True)
        root_rel = resolve_app_root("../../../", "MyApp", "main", script_dir)
        expected_rel = (script_dir / ".." / ".." / ".." / "MyApp" / "main").resolve()
        criteria.append(_crit("resolve_app_root_relative", str(root_rel), str(expected_rel), root_rel == expected_rel))
        # the leaf is the branch, the parent leaf is the app-name
        criteria.append(_crit("resolve_app_root_branch_leaf", root_rel.name, "main", root_rel.name == "main"))
        criteria.append(_crit("resolve_app_root_appname_segment", root_rel.parent.name, "MyApp", root_rel.parent.name == "MyApp"))

        # --- resolve_app_root: legacy worktree=off -> flat {root}/{app-name} ---
        root_flat = resolve_app_root(str(abs_container), "MyApp", "main", workdir_path / "scripts", worktree=False)
        expected_flat = (abs_container / "MyApp").resolve()
        criteria.append(_crit("resolve_app_root_worktree_off_flat", str(root_flat), str(expected_flat), root_flat == expected_flat))
        criteria.append(_crit("resolve_app_root_worktree_off_no_branch_leaf", root_flat.name, "MyApp", root_flat.name == "MyApp"))

        # --- populate_local_config: writes BASEAPP, preserves existing keys ---
        app_root = workdir_path / "app_root"
        (app_root / "config").mkdir(parents=True)
        local_path = app_root / "config" / "local.json"
        local_path.write_text(
            json.dumps({"COMMON": {"EXISTING": "keep"}, "APP": {"x": 1}}), encoding="utf-8"
        )
        baseapp = workdir_path / "BaseApp" / "main"
        written = populate_local_config(app_root, baseapp)
        data = json.loads(local_path.read_text(encoding="utf-8"))
        criteria.append(_crit("local_config_path", str(written), str(local_path), written == local_path))
        criteria.append(_crit("local_config_baseapp", data.get("COMMON", {}).get("BASEAPP"), str(baseapp),
                              data.get("COMMON", {}).get("BASEAPP") == str(baseapp)))
        criteria.append(_crit("local_config_preserves_common", data.get("COMMON", {}).get("EXISTING"), "keep",
                              data.get("COMMON", {}).get("EXISTING") == "keep"))
        criteria.append(_crit("local_config_preserves_app", data.get("APP", {}).get("x"), 1,
                              data.get("APP", {}).get("x") == 1))

        # --- populate_local_config: creates file when missing ---
        app_root2 = workdir_path / "app_root2"
        app_root2.mkdir(parents=True)
        populate_local_config(app_root2, baseapp)
        data2 = json.loads((app_root2 / "config" / "local.json").read_text(encoding="utf-8"))
        criteria.append(_crit("local_config_created_when_missing", data2.get("COMMON", {}).get("BASEAPP"),
                              str(baseapp), data2.get("COMMON", {}).get("BASEAPP") == str(baseapp)))

        # --- ensure_local_gitignore: local.json + regenerable paths are gitignored ---
        gitignore2 = app_root2 / ".gitignore"
        gi_lines = set(gitignore2.read_text(encoding="utf-8").splitlines()) if gitignore2.exists() else set()
        for pat in ("config/local.json", ".venv/", "__pycache__/"):
            criteria.append(_crit(f"gitignore_has_{pat}", pat in gi_lines, True, pat in gi_lines))

    except Exception as exc:  # noqa: BLE001
        criteria.append(_crit("unexpected_error", str(exc), None, False))

    overall = "PASS" if all(c["status"] == "PASS" for c in criteria) else "FAIL"
    return _build_result(
        status=overall,
        message=message or "Validated branch-aware layout: resolve_app_root and populate_local_config",
        criteria=criteria,
        features=features,
    )


# Feature 5.3.1.5.3
def test_instantiate_layout_survives_git_init(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.5.3. instantiate writes no VCS metadata and its layout is
    safe to place under a post-hoc ``git init``.

    Covers feature 3.1.2 (instantiate) in the multi-branch deployment context:
    instantiate must not create any ``.git`` metadata of its own, and a later
    ``git init`` over the instantiated tree must leave the template files intact
    and trackable (so converting a deployment into the bare/worktree layout never
    destroys the template structure).
    """
    features = ["3.1.2"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="baseinst_gitinit_")

    try:
        workdir_path = Path(workdir)
        source_root = _make_min_source(workdir_path / "source")
        target_root = workdir_path / "deploy" / "MyApp" / "main"

        instantiate(source_root, target_root, "MyApp", logger=None)

        app_py = target_root / "app" / "app.py"
        app_cfg = target_root / "config" / "app.json"
        criteria.append(_crit("instantiate_copied_pull_file", app_py.is_file(), True, app_py.is_file()))
        criteria.append(_crit("instantiate_copied_once_file", app_cfg.is_file(), True, app_cfg.is_file()))

        # APP_NAME applied to the once-copied config.
        cfg = json.loads(app_cfg.read_text(encoding="utf-8"))
        criteria.append(_crit("instantiate_set_app_name", cfg.get("COMMON", {}).get("APP_NAME"), "MyApp",
                              cfg.get("COMMON", {}).get("APP_NAME") == "MyApp"))

        # instantiate must NOT write any git metadata.
        no_git = not (target_root / ".git").exists()
        criteria.append(_crit("instantiate_writes_no_git_metadata", no_git, True, no_git))

        # Post-hoc git init must preserve the template structure.
        import shutil as _shutil
        if _shutil.which("git"):
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")

            def _git(*args):
                return subprocess.run(
                    ["git", "-c", "commit.gpgsign=false", "-C", str(target_root), *args],
                    capture_output=True, text=True, env=env,
                )

            _git("init", "-b", "main")
            _git("add", "-A")
            _git("commit", "-m", "adopt instantiated tree")
            tracked = _git("ls-files").stdout.replace("\\", "/")
            criteria.append(_crit("git_init_tracks_app_file", "app/app.py" in tracked, True, "app/app.py" in tracked))
            criteria.append(_crit("git_init_tracks_config_file", "config/app.json" in tracked, True, "config/app.json" in tracked))
            # files still physically present after init/add/commit
            criteria.append(_crit("files_intact_after_git_init", app_py.is_file() and app_cfg.is_file(), True,
                                  app_py.is_file() and app_cfg.is_file()))
        else:
            criteria.append(_crit("git_init_skipped_git_unavailable", "skipped", "skipped", True))

    except Exception as exc:  # noqa: BLE001
        criteria.append(_crit("unexpected_error", str(exc), None, False))

    overall = "PASS" if all(c["status"] == "PASS" for c in criteria) else "FAIL"
    return _build_result(
        status=overall,
        message=message or "Validated instantiate writes no git metadata and survives post-hoc git init",
        criteria=criteria,
        features=features,
    )

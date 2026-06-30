"""Feature ID: 5.3.1.8. init_worktree.ps1 layout test module."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_init_bare_worktree_repo_builds_bare_and_worktrees(tmp_path):
    """Feature 3.8 / BASE-REQ-014.17 regression test.

    ``scripts/init_worktree.ps1`` must clone a repository into the
    multi-branch bare/worktree layout rather than a plain single-tree clone.
    Cloning from a purely local source repository (no network), this asserts the
    script (1) creates the shared bare object store at ``{APP}/.bare``, (2) writes
    the rename-safe relative pointer ``{APP}/.git`` -> ``gitdir: ./.bare``, (3)
    checks the default branch out as the ``{APP}/<default-branch>`` worktree with
    the repo content present, (4) adds a second worktree for a brand-new
    ``-taskBranch`` created off the default branch, and (5) is idempotent (a
    re-run against the existing container succeeds without error).
    """
    import subprocess as sp
    import shutil as _shutil

    pwsh = _shutil.which("pwsh")
    if not pwsh:
        import pytest
        pytest.skip("pwsh is not available")
    if not _shutil.which("git"):
        import pytest
        pytest.skip("git is not available")

    # --- build a small local source repository (the clone "remote") ----------
    source = tmp_path / "source"
    source.mkdir()

    def git(*args, cwd, check=True):
        return sp.run(
            ["git", "-c", "safe.bareRepository=all", "-C", str(cwd), *args],
            capture_output=True, text=True, check=check,
        )

    git("init", cwd=source)
    git("config", "user.email", "tester@example.com", cwd=source)
    git("config", "user.name", "Tester", cwd=source)
    git("config", "core.autocrlf", "false", cwd=source)
    git("config", "commit.gpgsign", "false", cwd=source)
    git("checkout", "-B", "main", cwd=source)
    (source / "README.md").write_text("hello base\n", encoding="utf-8")
    git("add", "-A", cwd=source)
    git("commit", "-m", "init", cwd=source)

    # --- run the initializer -------------------------------------------------
    container_root = tmp_path / "containers"
    container_root.mkdir()
    script = PROJECT_ROOT / "scripts" / "init_worktree.ps1"
    name = "MyApp"

    def run_init(*extra):
        return sp.run(
            [
                pwsh, "-NoProfile", "-File", str(script),
                "-baseRepo", str(source),
                "-root", str(container_root),
                "-appName", name,
                "-branch", "main",
                *extra,
            ],
            capture_output=True, text=True, check=False,
        )

    result = run_init()
    assert result.returncode == 0, (result.stdout + result.stderr)

    container = container_root / name

    # (1) shared bare object store
    assert (container / ".bare").is_dir(), (result.stdout + result.stderr)

    # (2) rename-safe relative .git pointer
    git_pointer = (container / ".git").read_text(encoding="ascii")
    assert git_pointer == "gitdir: ./.bare", repr(git_pointer)

    # (3) default-branch worktree with repo content
    main_tree = container / "main"
    assert main_tree.is_dir()
    assert (main_tree / "README.md").read_text(encoding="utf-8") == "hello base\n"
    worktrees = git("worktree", "list", "--porcelain", cwd=container).stdout
    assert str(main_tree).replace("\\", "/") in worktrees.replace("\\", "/")
    main_branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=main_tree).stdout.strip()
    assert main_branch == "main"

    # (4) a brand-new -taskBranch is created off main and checked out as its own
    #     worktree (folder named from the branch's last segment).
    result2 = run_init("-taskBranch", "task/TEST-1")
    assert result2.returncode == 0, (result2.stdout + result2.stderr)
    task_tree = container / "TEST-1"
    assert task_tree.is_dir(), (result2.stdout + result2.stderr)
    task_branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=task_tree).stdout.strip()
    assert task_branch == "task/TEST-1"
    # the new branch is based off main, so it carries main's content
    assert (task_tree / "README.md").read_text(encoding="utf-8") == "hello base\n"

    # (5) idempotency: re-running against the existing container still succeeds.
    result3 = run_init("-taskBranch", "task/TEST-1")
    assert result3.returncode == 0, (result3.stdout + result3.stderr)


def test_init_bare_worktree_repo_migrates_existing_deployment(tmp_path):
    """Feature 3.8.2 / BASE-REQ-014.20 regression test.

    ``scripts/init_worktree.ps1`` must be able to migrate an existing
    branch-agnostic single-tree deployment (app files directly inside ``{APP}``,
    no ``.bare``) into the canonical bare/worktree layout *in place* and without a
    clone URL. Migration is auto-detected when ``{APP}`` has content but no
    ``.bare``. This asserts the script (1) creates ``{APP}/.bare`` and the
    rename-safe ``{APP}/.git`` pointer, (2) checks the content out as the
    ``{APP}/{branch}`` worktree, (3) preserves gitignored/untracked files such as
    ``config/local.json`` (carrying ``COMMON.BASEAPP``) into the worktree while
    excluding volatile dirs like ``.venv``, (4) leaves a local-only repo with no
    dangling ``origin`` remote, and (5) is idempotent.
    """
    import subprocess as sp
    import shutil as _shutil

    pwsh = _shutil.which("pwsh")
    if not pwsh:
        import pytest
        pytest.skip("pwsh is not available")
    if not _shutil.which("git"):
        import pytest
        pytest.skip("git is not available")

    def git(*args, cwd):
        return sp.run(
            ["git", "-c", "safe.bareRepository=all", "-C", str(cwd), *args],
            capture_output=True, text=True, check=False,
        )

    # --- build an existing single-tree deployment (no .bare, no git) ---------
    container_root = tmp_path / "containers"
    container_root.mkdir()
    app = container_root / "MyApp"
    (app / "config").mkdir(parents=True)
    (app / "app.py").write_text("print('app')\n", encoding="utf-8")
    (app / ".gitignore").write_text("config/local.json\n.venv/\n", encoding="utf-8")
    (app / "config" / "local.json").write_text(
        '{"COMMON":{"BASEAPP":"C:/code/BaseApp/main"}}', encoding="utf-8"
    )
    (app / ".venv").mkdir()
    (app / ".venv" / "junk.txt").write_text("x", encoding="utf-8")

    script = PROJECT_ROOT / "scripts" / "init_worktree.ps1"

    def run_init():
        # No -baseRepo: migration mode is auto-detected from the existing content.
        return sp.run(
            [pwsh, "-NoProfile", "-File", str(script),
             "-root", str(container_root), "-appName", "MyApp"],
            capture_output=True, text=True, check=False,
        )

    result = run_init()
    assert result.returncode == 0, (result.stdout + result.stderr)

    # (1) bare object store + rename-safe pointer
    assert (app / ".bare").is_dir(), (result.stdout + result.stderr)
    assert (app / ".git").read_text(encoding="ascii") == "gitdir: ./.bare"

    # (2) default-branch worktree with the original content
    main_tree = app / "main"
    assert main_tree.is_dir(), (result.stdout + result.stderr)
    assert (main_tree / "app.py").read_text(encoding="utf-8") == "print('app')\n"

    # (3) gitignored local.json preserved; .venv excluded from the worktree
    local_json = main_tree / "config" / "local.json"
    assert local_json.is_file(), "config/local.json must be preserved into the worktree"
    assert "BASEAPP" in local_json.read_text(encoding="utf-8")
    assert not (main_tree / ".venv").exists(), ".venv must not be carried into the worktree"

    # (4) local-only repo: no dangling origin remote
    remotes = git("remote", cwd=app).stdout.strip()
    assert remotes == "", f"expected no remotes after migration, got: {remotes!r}"

    # (5) idempotent re-run
    result2 = run_init()
    assert result2.returncode == 0, (result2.stdout + result2.stderr)
    assert (app / ".bare").is_dir()
    assert (app / "main" / "app.py").is_file()

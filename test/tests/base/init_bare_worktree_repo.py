"""Feature ID: 5.3.1.8. init_bare_worktree_repo.ps1 layout test module."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_init_bare_worktree_repo_builds_bare_and_worktrees(tmp_path):
    """Feature 3.8 / BASE-REQ-014.13 regression test.

    ``scripts/init_bare_worktree_repo.ps1`` must clone a repository into the
    multi-branch bare/worktree layout rather than a plain single-tree clone.
    Cloning from a purely local source repository (no network), this asserts the
    script (1) creates the shared bare object store at ``{APP}/.bare``, (2) writes
    the rename-safe relative pointer ``{APP}/.git`` -> ``gitdir: ./.bare``, (3)
    checks the default branch out as the ``{APP}/<default-branch>`` worktree with
    the repo content present, (4) adds a second worktree for a brand-new
    ``-TaskBranch`` created off the default branch, and (5) is idempotent (a
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
    script = PROJECT_ROOT / "scripts" / "init_bare_worktree_repo.ps1"
    name = "MyApp"

    def run_init(*extra):
        return sp.run(
            [
                pwsh, "-NoProfile", "-File", str(script),
                "-Url", str(source),
                "-Root", str(container_root),
                "-Name", name,
                "-DefaultBranch", "main",
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

    # (4) a brand-new -TaskBranch is created off main and checked out as its own
    #     worktree (folder named from the branch's last segment).
    result2 = run_init("-TaskBranch", "task/TEST-1")
    assert result2.returncode == 0, (result2.stdout + result2.stderr)
    task_tree = container / "TEST-1"
    assert task_tree.is_dir(), (result2.stdout + result2.stderr)
    task_branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=task_tree).stdout.strip()
    assert task_branch == "task/TEST-1"
    # the new branch is based off main, so it carries main's content
    assert (task_tree / "README.md").read_text(encoding="utf-8") == "hello base\n"

    # (5) idempotency: re-running against the existing container still succeeds.
    result3 = run_init("-TaskBranch", "task/TEST-1")
    assert result3.returncode == 0, (result3.stdout + result3.stderr)

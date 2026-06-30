<#
.SYNOPSIS
    Feature 3.8. Initialize a repository in the bare "{APP}/{branch}" worktree layout,
    where the shared object store lives in .bare and every branch is a peer worktree
    subfolder.

    Resulting layout for a container named MyRepo:

        MyRepo\
          .bare\                 <- shared bare object store (the real clone)
          .git                   <- file: "gitdir: ./.bare" (relative, rename-safe)
          main\                  <- worktree for the default branch
          <task-id>\             <- (optional) worktree for an extra branch

.DESCRIPTION
    Machine-agnostic and idempotent. Nothing here is hard-coded to a particular
    user, drive, or absolute path: you pass the clone URL and (optionally) where to
    put it. Re-running against an existing container is safe -- it skips work that is
    already done and just (re)verifies. Designed so any machine adopting this
    framework can run it locally.

    This is the multi-branch-aware way to clone the repository: a plain `git clone`
    produces a single working tree that can only have one branch checked out at a
    time, which prevents the per-task worktree workflow the framework relies on (see
    launch_task_agent.ps1, which adds task worktrees as siblings of main under the
    {APP} container). The same script works for BaseApp and for any variant app --
    just pass that app's clone URL -- so variant apps developed on top of BaseApp
    benefit from the identical bare/worktree layout. Because the whole scripts/
    folder is propagated to variant apps via resources/manifests/pull.json, every
    variant app receives this script automatically on its next base-update pull.

.PARAMETER baseRepo
    Clone URL of the remote repository (https or ssh). Required for CLONE mode.
    Optional in MIGRATION mode (see below): when omitted the existing local
    deployment is converted in place as a LOCAL-ONLY repo and no remote is
    contacted; when supplied it is attached as 'origin' on the migrated repo (the
    standard fetch refspec is configured) so you can `git push -u origin <branch>`
    later. No push is performed by this script.

.PARAMETER root
    Parent directory that will CONTAIN the {APP} container folder. Defaults to the
    parent of the {APP} this script is deployed under: this script lives at
    {APP}/{branch}/scripts, so the default root is that {APP}'s parent directory
    (NOT the current working directory).

.PARAMETER appName
    Container folder name ({APP}). Defaults to the repo name inferred from -baseRepo
    when -baseRepo is given (e.g. https://github.com/Yaniv1/BaseApp.git -> "BaseApp");
    otherwise defaults to the {APP} folder name this script is deployed under
    (its grandparent's parent), so a no-argument run targets the script's own app.

.PARAMETER branch
    The primary worktree branch. In CLONE mode it is the branch checked out as the
    first worktree, defaulting to the remote's HEAD (falling back to "main"). In
    MIGRATION mode it is the branch for the first worktree created from the existing
    deployment's history, defaulting to the deployment's current branch (or "main"
    when the deployment has no git history yet).

.PARAMETER taskBranch
    Optional. Also create a second worktree for this branch.
      * If the branch exists on the remote it is checked out.
      * Otherwise it is created (branched off the primary branch).
    The worktree folder name is derived from the branch (e.g. "task/BASE-TASK-1"
    -> folder "BASE-TASK-1"), or pass -taskFolder to override.

.PARAMETER taskFolder
    Folder name for the -taskBranch worktree. Defaults to the branch's last segment.

.EXAMPLE
    # Clone BaseApp into the current directory, container auto-named from the URL:
    pwsh -File .\scripts\init_worktree.ps1 -baseRepo https://github.com/Yaniv1/BaseApp.git

.EXAMPLE
    # Clone a variant app elsewhere, custom container name, and spin up a task worktree:
    pwsh -File .\scripts\init_worktree.ps1 `
        -baseRepo https://github.com/Yaniv1/MyVariantApp.git `
        -root     D:\work `
        -appName  MyVariantApp `
        -taskBranch task/APP-TASK-260624-0001

.EXAMPLE
    # MIGRATION: convert an existing branch-agnostic single-tree deployment
    # (D:\work\MyApp with app files directly inside) into the bare/worktree layout.
    # Auto-detected because MyApp has content but no .bare; no -baseRepo needed:
    pwsh -File .\scripts\init_worktree.ps1 -root D:\work -appName MyApp

.EXAMPLE
    # MIGRATION + attach a remote: convert in place AND wire up 'origin' so the
    # migrated repo can be pushed later (no push is performed here):
    pwsh -File .\scripts\init_worktree.ps1 `
        -root D:\work -appName MyApp `
        -baseRepo https://github.com/Yaniv1/MyApp.git
#>

# Feature 3.8
[CmdletBinding()]
param(
    [string]$baseRepo,

    [string]$root,
    [string]$appName,
    [string]$branch,
    [string]$taskBranch,
    [string]$taskFolder
)

$ErrorActionPreference = "Stop"

function Info  { param([string]$m) Write-Host "[init] $m" -ForegroundColor Cyan }
function Ok    { param([string]$m) Write-Host "[ ok ] $m" -ForegroundColor Green }
function Warn  { param([string]$m) Write-Host "[warn] $m" -ForegroundColor Yellow }
function Die   { param([string]$m) Write-Host "[fail] $m" -ForegroundColor Red; exit 1 }

function Add-MigrationExcludes {
    # Append regenerable / local-only patterns to the repo's LOCAL exclude file
    # (.git/info/exclude) so a migrated app never commits its virtualenv, bytecode
    # caches, or the machine-specific config/local.json into history -- even when the
    # deployment carries no .gitignore of its own. Local-only (not a tracked
    # .gitignore) and idempotent; it only affects still-untracked files.
    param([string]$RepoPath)
    $excludeFile = Join-Path $RepoPath ".git\info\exclude"
    $excludeDir  = Split-Path -Parent $excludeFile
    if (-not (Test-Path -LiteralPath $excludeDir)) {
        New-Item -ItemType Directory -Force -Path $excludeDir | Out-Null
    }
    $existing = if (Test-Path -LiteralPath $excludeFile) { @(Get-Content -LiteralPath $excludeFile) } else { @() }
    foreach ($pat in @('.venv/', '__pycache__/', 'config/local.json')) {
        if ($existing -notcontains $pat) { Add-Content -LiteralPath $excludeFile -Value $pat }
    }
}

# --- sanity: git present ------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is not on PATH."
}

# --- derive container name + paths -------------------------------------------
# This script is deployed at {APP}/{branch}/scripts, so its grandparent's parent
# is the {APP} folder and the level above that is the default Root that CONTAINS
# {APP}. These provide the script-location defaults for -root / -appName.
$scriptDir   = $PSScriptRoot
$myBranchDir = if ($scriptDir)   { Split-Path -Parent $scriptDir }   else { $null }
$myAppDir    = if ($myBranchDir) { Split-Path -Parent $myBranchDir } else { $null }
$myAppName   = if ($myAppDir)    { Split-Path -Leaf   $myAppDir }    else { $null }
$myRoot      = if ($myAppDir)    { Split-Path -Parent $myAppDir }    else { (Get-Location).Path }

if (-not $appName) {
    if ($baseRepo) {
        # last path segment of the URL, minus a trailing .git
        $leaf = ($baseRepo.TrimEnd('/').Split('/')[-1])
        $appName = $leaf -replace '\.git$', ''
    } elseif ($myAppName) {
        # no -baseRepo and no -appName -> target the {APP} this script is deployed under
        $appName = $myAppName
    }
}
if (-not $appName) { Die "Could not determine a container name; pass -appName." }

if (-not $root) { $root = $myRoot }
$root = (Resolve-Path -LiteralPath $root).Path
$app  = Join-Path $root $appName
$bare = Join-Path $app ".bare"

Info "Container : $app"
if ($baseRepo) { Info "Remote    : $baseRepo" }

# --- migration helper: convert an existing single-tree deployment -------------
function Convert-ExistingDeployment {
    <#
        Feature 3.8.2. Convert an existing branch-agnostic single-tree deployment
        at $AppPath into the canonical "{APP}/.bare" + "{APP}/{branch}" layout,
        non-destructively. Preserves git history (or seeds one when absent) and
        restores untracked/gitignored files (e.g. config/local.json carrying
        COMMON.BASEAPP) into the new worktree. Idempotent: no-op if .bare exists.
        Returns the branch name of the worktree it created.
    #>
    param(
        [string]$AppPath,
        [string]$branchName,
        [string]$RemoteUrl
    )

    $bareLocal = Join-Path $AppPath ".bare"
    if (Test-Path -LiteralPath $bareLocal) {
        Info "migration: .bare already present -> skipping conversion"
        if (-not $branchName) { $branchName = "main" }
        return $branchName
    }

    # 1. ensure history
    $hasRepo = Test-Path -LiteralPath (Join-Path $AppPath ".git")
    if (-not $hasRepo) {
        if (-not $branchName) { $branchName = "main" }
        git -C $AppPath init -b $branchName | Out-Null
        if ($LASTEXITCODE -ne 0) { Die "migration: git init failed." }
        Add-MigrationExcludes -RepoPath $AppPath
        git -C $AppPath add -A | Out-Null
        git -C $AppPath -c user.email=baseapp@local -c user.name=baseapp commit -m "Initial commit (migrated to bare/worktree layout)" --quiet | Out-Null
        if ($LASTEXITCODE -ne 0) { Die "migration: initial commit failed." }
        Ok "migration: initialized history on '$branchName'"
    } else {
        $current = (git -C $AppPath rev-parse --abbrev-ref HEAD 2>$null)
        if (-not $branchName) {
            $branchName = if ($current -and $current -ne "HEAD") { $current } else { "main" }
        }
        Add-MigrationExcludes -RepoPath $AppPath
        git -C $AppPath add -A 2>$null | Out-Null
        $pending = git -C $AppPath status --porcelain
        if ($pending) {
            git -C $AppPath -c user.email=baseapp@local -c user.name=baseapp commit -m "Snapshot before migration to bare/worktree layout" --quiet | Out-Null
        }
        Ok "migration: using existing history on '$branchName'"
    }

    # 2. record untracked + ignored files to preserve (e.g. config/local.json)
    $preserve = @()
    $statusLines = git -C $AppPath status --ignored --porcelain
    foreach ($line in $statusLines) {
        if ($line -match '^(\?\?|!!)\s+(.*)$') {
            $rel = $Matches[2].Trim('"')
            if ($rel -match '(^|/)(\.venv|__pycache__|\.git)(/|$)') { continue }
            $preserve += $rel
        }
    }

    # 3. rename {APP} -> temp sibling
    $parent = Split-Path -Parent $AppPath
    $leaf   = Split-Path -Leaf   $AppPath
    $tempLeaf = "{0}.migrate-{1}" -f $leaf, (Get-Date -Format "yyyyMMddHHmmss")
    $temp   = Join-Path $parent $tempLeaf
    Rename-Item -LiteralPath $AppPath -NewName $tempLeaf
    Ok "migration: staged existing tree at $temp"

    try {
        # recreate empty container
        New-Item -ItemType Directory -Force -Path $AppPath | Out-Null

        # 4. bare clone from the staged working tree
        $bareTarget = Join-Path $AppPath ".bare"
        git clone --bare -- $temp $bareTarget 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Die "migration: bare clone from existing tree failed." }

        # 5. relative .git pointer (written before any further git -C $AppPath call so
        #    that git resolves via the pointer, honouring safe.bareRepository=explicit)
        Set-Content -LiteralPath (Join-Path $AppPath ".git") -Value "gitdir: ./.bare" -NoNewline -Encoding ascii

        # The cloned bare's 'origin' points at the temporary staged tree (removed
        # below), so drop it -- a migrated repo is local-only until a real remote
        # is added. This keeps subsequent re-runs from trying to fetch a dead path.
        git -C $AppPath remote remove origin 2>$null | Out-Null

        # If a real remote URL was supplied, attach it as 'origin' and configure
        # the standard fetch refspec so all branches track as origin/*. No fetch or
        # push is performed here (the remote may be empty / unreachable); the user
        # can `git -C <app> push -u origin <branch>` when ready.
        if ($RemoteUrl) {
            git -C $AppPath remote add origin $RemoteUrl 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { Die "migration: failed to add origin remote '$RemoteUrl'." }
            git -C $AppPath config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" | Out-Null
            Ok "migration: attached remote origin -> $RemoteUrl"
        }

        # 6. worktree add into the empty {branch} slot
        $wt = Join-Path $AppPath $branchName
        git -C $AppPath worktree add -- $wt $branchName 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Die "migration: git worktree add failed." }
        Ok "migration: worktree '$branchName' created"

        # 7. restore preserved untracked/ignored files into the worktree
        $restored = 0
        foreach ($rel in $preserve) {
            $relClean = $rel -replace '/','\'
            $src = Join-Path $temp $relClean
            if (-not (Test-Path -LiteralPath $src)) { continue }
            $dst = Join-Path $wt $relClean
            $dstDir = Split-Path -Parent $dst
            if ($dstDir) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
            if ((Get-Item -LiteralPath $src).PSIsContainer) {
                Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
            } else {
                Copy-Item -LiteralPath $src -Destination $dst -Force
            }
            $restored++
        }
        Ok "migration: restored $restored untracked/ignored item(s)"
    }
    finally {
        # 8. remove the staged temp tree
        if (Test-Path -LiteralPath $temp) {
            Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    return $branchName
}

# --- mode detection: MIGRATION (existing tree, no .bare) vs CLONE -------------
$bareExists = Test-Path -LiteralPath $bare
$appHasContent = $false
if (Test-Path -LiteralPath $app) {
    $children = Get-ChildItem -LiteralPath $app -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne '.bare' -and $_.Name -ne '.git' }
    $appHasContent = [bool]$children
}

$migrated = $false
if (-not $bareExists -and $appHasContent) {
    Info "existing deployment detected at $app with no .bare -> MIGRATION mode"
    $branch = Convert-ExistingDeployment -AppPath $app -BranchName $branch -RemoteUrl $baseRepo
    $migrated = $true
}

if (-not $migrated) {
    if (-not $bareExists -and -not $baseRepo) {
        Die "No existing deployment to migrate and -Url not provided; pass -Url to clone."
    }

    # --- 1. create container --------------------------------------------------
    if (-not (Test-Path -LiteralPath $app)) {
        New-Item -ItemType Directory -Force -Path $app | Out-Null
        Ok "created container folder"
    } else {
        Info "container folder already exists -> reusing"
    }

    # --- 2. bare clone into .bare ---------------------------------------------
    if (-not (Test-Path -LiteralPath $bare)) {
        Info "cloning (bare) into .bare ..."
        git clone --bare -- $baseRepo $bare
        if ($LASTEXITCODE -ne 0) { Die "git clone --bare failed." }
        Ok "bare clone created"
    } else {
        Info ".bare already present -> skipping clone"
    }

    # --- 3. relative .git pointer (rename-safe) -------------------------------
    $gitFile = Join-Path $app ".git"
    Set-Content -LiteralPath $gitFile -Value "gitdir: ./.bare" -NoNewline -Encoding ascii
    Ok ".git -> gitdir: ./.bare"

    # --- 4. normal fetch refspec + fetch --------------------------------------
    # Only fetch when an 'origin' remote exists. A migrated, local-only repo has no
    # remote; and a re-run against an existing .bare should not fail hard if the
    # remote is unreachable.
    $hasOrigin = @(git -C $app remote 2>$null) -contains 'origin'
    # Allow attaching a remote to an already-migrated local-only repo on a later
    # re-run: if no origin yet but a -Url was supplied, add it before fetching.
    if (-not $hasOrigin -and $baseRepo) {
        git -C $app remote add origin $baseRepo 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Die "failed to add origin remote '$baseRepo'." }
        Ok "attached remote origin -> $baseRepo"
        $hasOrigin = $true
    }
    if ($hasOrigin) {
        # A --bare clone defaults to a single-branch / mirror-ish refspec; normalize it
        # so all remote branches are tracked as origin/*.
        git -C $app config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" | Out-Null
        Info "fetching all branches ..."
        git -C $app fetch --prune origin 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            if ($bareExists) {
                Warn "fetch failed (offline or stale remote) -> continuing with local refs"
            } else {
                Die "git fetch failed."
            }
        } else {
            Ok "fetched"
        }
    } else {
        Info "no 'origin' remote configured -> skipping fetch (local-only repo)"
    }

    # --- 5. resolve default branch --------------------------------------------
    if (-not $branch) {
        $headRef = (git -C $app symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>$null)
        if ($headRef) {
            $branch = $headRef -replace '^origin/', ''
        } else {
            # try to set it, then re-read; finally fall back to the bare repo's local
            # HEAD branch (local-only repos), then to 'main'.
            git -C $app remote set-head origin -a 2>$null | Out-Null
            $headRef = (git -C $app symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>$null)
            if ($headRef) {
                $branch = $headRef -replace '^origin/', ''
            } else {
                $localHead = (git -C $app symbolic-ref --quiet --short HEAD 2>$null)
                $branch = if ($localHead) { $localHead } else { "main" }
            }
        }
    }
}
Info "default branch: $branch"

# --- helper: add a worktree for a branch (idempotent) -------------------------
function Add-Worktree {
    param(
        [string]$Folder,           # folder name under the container
        [string]$WorktreeBranch,   # local branch name to be on
        [switch]$CreateIfMissing
    )
    $path = Join-Path $app $Folder

    # already a registered worktree at this path?
    $listed = git -C $app worktree list --porcelain 2>$null
    $norm       = ($path -replace '\\','/').ToLower()
    $listedNorm = (($listed -join "`n") -replace '\\','/').ToLower()
    if ($listedNorm -match [regex]::Escape($norm)) {
        Ok "worktree '$Folder' already present"
        return
    }
    if (Test-Path -LiteralPath $path) {
        Warn "'$path' exists but is not a registered worktree; leaving it untouched."
        return
    }

    $localExists  = $false
    git -C $app rev-parse --verify --quiet "refs/heads/$WorktreeBranch" *> $null
    if ($LASTEXITCODE -eq 0) { $localExists = $true }

    $remoteExists = $false
    git -C $app rev-parse --verify --quiet "refs/remotes/origin/$WorktreeBranch" *> $null
    if ($LASTEXITCODE -eq 0) { $remoteExists = $true }

    if ($localExists) {
        git -C $app worktree add -- $path $WorktreeBranch
    } elseif ($remoteExists) {
        # new local branch tracking the remote one
        git -C $app worktree add --track -b $WorktreeBranch -- $path "origin/$WorktreeBranch"
    } elseif ($CreateIfMissing) {
        # Prefer origin/<primary> when a remote exists (CLONE mode); otherwise base
        # off the local primary branch (MIGRATION mode has no origin).
        $base = "origin/$branch"
        git -C $app rev-parse --verify --quiet "refs/remotes/origin/$branch" *> $null
        if ($LASTEXITCODE -ne 0) { $base = $branch }
        git -C $app worktree add -b $WorktreeBranch -- $path $base
    } else {
        Die "branch '$WorktreeBranch' not found locally or on origin (and -CreateIfMissing not set)."
    }
    if ($LASTEXITCODE -ne 0) { Die "git worktree add for '$Folder' failed." }
    Ok "worktree '$Folder' -> branch '$WorktreeBranch'"
}

# --- 6. default-branch worktree ----------------------------------------------
# In MIGRATION mode the default-branch worktree was already created by
# Convert-ExistingDeployment, so skip the redundant (and noisy) re-add here.
if (-not $migrated) {
    Add-Worktree -Folder $branch -WorktreeBranch $branch
}

# --- 7. optional task worktree -----------------------------------------------
if ($taskBranch) {
    if (-not $taskFolder) { $taskFolder = $taskBranch.Split('/')[-1] }
    Add-Worktree -Folder $taskFolder -WorktreeBranch $taskBranch -CreateIfMissing
}

# --- 8. verify ----------------------------------------------------------------
Info "worktree layout:"
git -C $app worktree list

Ok "DONE. Container ready at: $app"
Write-Host ""
Write-Host "Open a specific worktree (not the container) in your editor, e.g.:" -ForegroundColor Gray
Write-Host "    $app\$branch" -ForegroundColor Gray
Write-Host ""
Write-Host "Add more branches later with:" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree add <folder> <existing-branch>" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree add <folder> -b <new-branch> origin/$branch" -ForegroundColor Gray
Write-Host "Remove one cleanly with:" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree remove <folder>;  git -C `"$app`" worktree prune" -ForegroundColor Gray
Write-Host "After ANY rename of the container, repair the (absolute) pointers:" -ForegroundColor Gray
Write-Host "    Set-Content `"$app\.git`" 'gitdir: ./.bare' -NoNewline" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree repair <each-worktree-path>" -ForegroundColor Gray

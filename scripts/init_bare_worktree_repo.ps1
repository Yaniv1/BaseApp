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

.PARAMETER Url
    Clone URL of the remote (https or ssh). Required.

.PARAMETER Root
    Parent directory that will CONTAIN the container folder.
    Defaults to the current directory.

.PARAMETER Name
    Container folder name ({APP}). Defaults to the repo name inferred from -Url
    (e.g. https://github.com/Yaniv1/BaseApp.git -> "BaseApp").

.PARAMETER DefaultBranch
    Branch to check out as the first worktree. Defaults to the remote's HEAD
    (falls back to "main").

.PARAMETER TaskBranch
    Optional. Also create a second worktree for this branch.
      * If the branch exists on the remote it is checked out.
      * Otherwise it is created (branched off the default branch).
    The worktree folder name is derived from the branch (e.g. "task/BASE-TASK-1"
    -> folder "BASE-TASK-1"), or pass -TaskFolder to override.

.PARAMETER TaskFolder
    Folder name for the -TaskBranch worktree. Defaults to the branch's last segment.

.EXAMPLE
    # Clone BaseApp into the current directory, container auto-named from the URL:
    pwsh -File .\scripts\init_bare_worktree_repo.ps1 -Url https://github.com/Yaniv1/BaseApp.git

.EXAMPLE
    # Clone a variant app elsewhere, custom container name, and spin up a task worktree:
    pwsh -File .\scripts\init_bare_worktree_repo.ps1 `
        -Url   https://github.com/Yaniv1/MyVariantApp.git `
        -Root  D:\work `
        -Name  MyVariantApp `
        -TaskBranch task/APP-TASK-260624-0001
#>

# Feature 3.8
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Url,

    [string]$Root = (Get-Location).Path,
    [string]$Name,
    [string]$DefaultBranch,
    [string]$TaskBranch,
    [string]$TaskFolder
)

$ErrorActionPreference = "Stop"

function Info  { param([string]$m) Write-Host "[init] $m" -ForegroundColor Cyan }
function Ok    { param([string]$m) Write-Host "[ ok ] $m" -ForegroundColor Green }
function Warn  { param([string]$m) Write-Host "[warn] $m" -ForegroundColor Yellow }
function Die   { param([string]$m) Write-Host "[fail] $m" -ForegroundColor Red; exit 1 }

# --- sanity: git present ------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Die "git is not on PATH."
}

# --- derive container name + paths -------------------------------------------
if (-not $Name) {
    # last path segment of the URL, minus a trailing .git
    $leaf = ($Url.TrimEnd('/').Split('/')[-1])
    $Name = $leaf -replace '\.git$', ''
}
if (-not $Name) { Die "Could not infer a container name; pass -Name." }

$Root = (Resolve-Path -LiteralPath $Root).Path
$app  = Join-Path $Root $Name
$bare = Join-Path $app ".bare"

Info "Container : $app"
Info "Remote    : $Url"

# --- 1. create container ------------------------------------------------------
if (-not (Test-Path -LiteralPath $app)) {
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    Ok "created container folder"
} else {
    Info "container folder already exists -> reusing"
}

# --- 2. bare clone into .bare -------------------------------------------------
if (-not (Test-Path -LiteralPath $bare)) {
    Info "cloning (bare) into .bare ..."
    git clone --bare -- $Url $bare
    if ($LASTEXITCODE -ne 0) { Die "git clone --bare failed." }
    Ok "bare clone created"
} else {
    Info ".bare already present -> skipping clone"
}

# --- 3. relative .git pointer (rename-safe) -----------------------------------
$gitFile = Join-Path $app ".git"
Set-Content -LiteralPath $gitFile -Value "gitdir: ./.bare" -NoNewline -Encoding ascii
Ok ".git -> gitdir: ./.bare"

# --- 4. normal fetch refspec + fetch -----------------------------------------
# A --bare clone defaults to a single-branch / mirror-ish refspec; normalize it so
# all remote branches are tracked as origin/*.
git -C $app config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*" | Out-Null
Info "fetching all branches ..."
git -C $app fetch --prune origin
if ($LASTEXITCODE -ne 0) { Die "git fetch failed." }
Ok "fetched"

# --- 5. resolve default branch ------------------------------------------------
if (-not $DefaultBranch) {
    $headRef = (git -C $app symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>$null)
    if ($headRef) {
        $DefaultBranch = $headRef -replace '^origin/', ''
    } else {
        # try to set it, then re-read; fall back to 'main'
        git -C $app remote set-head origin -a 2>$null | Out-Null
        $headRef = (git -C $app symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>$null)
        $DefaultBranch = if ($headRef) { $headRef -replace '^origin/', '' } else { "main" }
    }
}
Info "default branch: $DefaultBranch"

# --- helper: add a worktree for a branch (idempotent) -------------------------
function Add-Worktree {
    param(
        [string]$Folder,      # folder name under the container
        [string]$Branch,      # local branch name to be on
        [switch]$CreateIfMissing
    )
    $path = Join-Path $app $Folder

    # already a registered worktree at this path?
    $listed = git -C $app worktree list --porcelain 2>$null
    $norm   = ($path -replace '\\','/')
    if ($listed -match [regex]::Escape($norm)) {
        Ok "worktree '$Folder' already present"
        return
    }
    if (Test-Path -LiteralPath $path) {
        Warn "'$path' exists but is not a registered worktree; leaving it untouched."
        return
    }

    $localExists  = $false
    git -C $app rev-parse --verify --quiet "refs/heads/$Branch" *> $null
    if ($LASTEXITCODE -eq 0) { $localExists = $true }

    $remoteExists = $false
    git -C $app rev-parse --verify --quiet "refs/remotes/origin/$Branch" *> $null
    if ($LASTEXITCODE -eq 0) { $remoteExists = $true }

    if ($localExists) {
        git -C $app worktree add -- $path $Branch
    } elseif ($remoteExists) {
        # new local branch tracking the remote one
        git -C $app worktree add --track -b $Branch -- $path "origin/$Branch"
    } elseif ($CreateIfMissing) {
        $base = "origin/$DefaultBranch"
        git -C $app worktree add -b $Branch -- $path $base
    } else {
        Die "branch '$Branch' not found locally or on origin (and -CreateIfMissing not set)."
    }
    if ($LASTEXITCODE -ne 0) { Die "git worktree add for '$Folder' failed." }
    Ok "worktree '$Folder' -> branch '$Branch'"
}

# --- 6. default-branch worktree ----------------------------------------------
Add-Worktree -Folder $DefaultBranch -Branch $DefaultBranch

# --- 7. optional task worktree -----------------------------------------------
if ($TaskBranch) {
    if (-not $TaskFolder) { $TaskFolder = $TaskBranch.Split('/')[-1] }
    Add-Worktree -Folder $TaskFolder -Branch $TaskBranch -CreateIfMissing
}

# --- 8. verify ----------------------------------------------------------------
Info "worktree layout:"
git -C $app worktree list

Ok "DONE. Container ready at: $app"
Write-Host ""
Write-Host "Open a specific worktree (not the container) in your editor, e.g.:" -ForegroundColor Gray
Write-Host "    $app\$DefaultBranch" -ForegroundColor Gray
Write-Host ""
Write-Host "Add more branches later with:" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree add <folder> <existing-branch>" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree add <folder> -b <new-branch> origin/$DefaultBranch" -ForegroundColor Gray
Write-Host "Remove one cleanly with:" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree remove <folder>;  git -C `"$app`" worktree prune" -ForegroundColor Gray
Write-Host "After ANY rename of the container, repair the (absolute) pointers:" -ForegroundColor Gray
Write-Host "    Set-Content `"$app\.git`" 'gitdir: ./.bare' -NoNewline" -ForegroundColor Gray
Write-Host "    git -C `"$app`" worktree repair <each-worktree-path>" -ForegroundColor Gray

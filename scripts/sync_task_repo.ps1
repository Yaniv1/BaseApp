param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [Parameter(Mandatory = $true)]
    [string]$TaskFile,
    [Parameter(Mandatory = $true)]
    [string]$CommitMessage
)

$ErrorActionPreference = 'Stop'

$repoPath = Resolve-Path -Path $RepoRoot
$taskRel = $TaskFile -replace '\\', '/'

Push-Location $repoPath
try {
    $statusOutput = git status --short -- $taskRel
    if (-not $statusOutput) {
        Write-Output "No local changes detected for $taskRel"
    }
    else {
        git add -- $taskRel
        git commit -m $CommitMessage
        if ($LASTEXITCODE -ne 0) {
            Write-Output "No commit created for $taskRel"
        }
    }
    # --autostash lets the rebase proceed even when unrelated files in the
    # working tree have unstaged changes; they are restored afterwards.
    git pull --rebase --autostash origin HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "git pull --rebase failed for $taskRel. Resolve the conflict manually, then re-run the sync."
    }
    git push origin HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "git push failed for $taskRel. The branch may still be behind the remote; pull and retry."
    }
    Write-Output "Synced $taskRel to git"
}
finally {
    Pop-Location
}

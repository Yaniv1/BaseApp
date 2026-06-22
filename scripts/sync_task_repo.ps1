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
        try {
            git commit -m $CommitMessage
        }
        catch {
            Write-Output "No commit created for $taskRel"
        }
    }
    git pull --rebase origin HEAD
    git push origin HEAD
    Write-Output "Synced $taskRel to git"
}
finally {
    Pop-Location
}

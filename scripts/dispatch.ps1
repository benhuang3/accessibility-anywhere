<#
Hand a task to Codex or Cursor, running headless in its own git worktree.

  .\scripts\dispatch.ps1 -Agent codex  -Task "Add a 'sepia' color filter in ally/tools"
  .\scripts\dispatch.ps1 -Agent cursor -Task "Restyle the dashboard header" -Model sonnet-4
  .\scripts\dispatch.ps1 -Agent cursor -Task "Review agent/codex for bugs" -ReadOnly

The worktree is ..\ally-<agent> on branch agent/<agent>, created from main the first time.
Output goes to logs\agents\<agent>-<time>.log, the agent's final message to <same>.final.md.
When it finishes, the script prints the new commits and changed files on that branch.
#>
param(
    [Parameter(Mandatory)][ValidateSet('codex', 'cursor')][string]$Agent,
    [Parameter(Mandatory)][string]$Task,
    [string]$Model,
    [switch]$ReadOnly
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$worktree = Join-Path (Split-Path -Parent $repo) "ally-$Agent"
$branch = "agent/$Agent"

if (-not (Test-Path $worktree)) {
    git -C $repo show-ref --verify --quiet "refs/heads/$branch"
    if ($LASTEXITCODE -eq 0) { git -C $repo worktree add $worktree $branch }
    else { git -C $repo worktree add -b $branch $worktree main }
    if ($LASTEXITCODE -ne 0) { throw "Could not create worktree $worktree" }
}
$worktree = (Resolve-Path $worktree).Path
$startCommit = git -C $worktree rev-parse HEAD

# Until AGENTS.md is committed, the worktree doesn't have it, so pass the rules in the prompt.
$prompt = $Task
if (-not (Test-Path (Join-Path $worktree 'AGENTS.md'))) {
    $rules = Get-Content -Raw (Join-Path $repo 'AGENTS.md')
    $prompt = "$rules`n---`n`nYour branch is $branch. Task:`n$Task"
}

$logDir = Join-Path $repo 'logs\agents'
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $logDir "$Agent-$stamp.log"
$final = Join-Path $logDir "$Agent-$stamp.final.md"
"[$stamp] $Agent in $worktree`n$Task`n" | Out-File -Encoding utf8 $log

# Both CLIs write progress to stderr, which Windows PowerShell turns into errors.
$ErrorActionPreference = 'Continue'

if ($Agent -eq 'codex') {
    # A worktree's git data lives in the main repo's .git, so the sandbox must be allowed to write there to commit.
    $gitDir = Join-Path $repo '.git'
    $codexArgs = @('exec', '-C', $worktree, '-o', $final, '--color', 'never')
    if ($ReadOnly) { $codexArgs += @('-s', 'read-only') }
    else { $codexArgs += @('-s', 'workspace-write', '--add-dir', $gitDir) }
    if ($Model) { $codexArgs += @('-m', $Model) }
    $prompt | codex @codexArgs - *>&1 | Tee-Object -FilePath $log -Append
} else {
    $cursorArgs = @('-p', '--trust', '--workspace', $worktree, '--output-format', 'text')
    if ($ReadOnly) { $cursorArgs += @('--mode', 'ask') } else { $cursorArgs += '--force' }
    if ($Model) { $cursorArgs += @('--model', $Model) }
    $out = & cursor-agent @cursorArgs $prompt *>&1
    $out | Tee-Object -FilePath $log -Append
    $out | Out-File -Encoding utf8 $final
}
$exit = $LASTEXITCODE

"`n=== $Agent finished (exit $exit). Log: $log"
"=== New commits on ${branch}:"
git -C $worktree log --oneline "$startCommit..HEAD"
"=== Uncommitted changes:"
git -C $worktree status --short
exit $exit

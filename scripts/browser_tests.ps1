# Run the page-step browser tests (tests/browser) on Windows.
#
#   powershell -ExecutionPolicy Bypass -File scripts\browser_tests.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\browser_tests.ps1 -Headed
#   powershell -ExecutionPolicy Bypass -File scripts\browser_tests.ps1 -Repo D:\ally
#
# Uses the local repo copy made by scripts\win_setup.ps1 (C:\ally) and its .venv. Installs Playwright and
# its Chromium into that .venv if they are missing. Never uses port 8765.

param(
    [string]$Repo = "C:\ally",
    [switch]$Headed
)

$ErrorActionPreference = "Stop"

$py = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Can't find $py. Run scripts\win_setup.ps1 first (it copies the repo and makes the .venv)." }

Write-Host "== Playwright"
& $py -c "import playwright" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $py -m pip install --quiet playwright
    if ($LASTEXITCODE -ne 0) { throw "pip install playwright failed" }
}
# Safe to repeat: it only downloads Chromium when it isn't there yet.
& $py -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install chromium failed" }

Write-Host "== Browser tests"
if ($Headed) { $env:ALLY_HEADED = "1" } else { Remove-Item Env:ALLY_HEADED -ErrorAction SilentlyContinue }
Push-Location (Join-Path $Repo "tests")
try {
    & $py -m unittest browser.test_page_steps -v
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ""
if ($code -eq 0) { Write-Host "== Browser tests passed (skips are steps not written yet)" }
else { Write-Host "== Browser tests FAILED (exit $code)" }
Write-Host "Screenshots: $(Join-Path $Repo 'logs\browser')"
exit $code

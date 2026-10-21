param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$AppDirectory,
    [string]$OutputDirectory,
    [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$taskSource = $PSScriptRoot
$taskRoot = Split-Path -Parent $taskSource
if (-not $PythonPath) {
    $PythonPath = Join-Path $taskRoot 'work\venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonPath)) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
$taskArguments = @((Join-Path $taskSource 'release_package.py'), '--version', $Version, '--project-root', $taskRoot)
if ($AppDirectory) { $taskArguments += @('--app-directory', $AppDirectory) }
if ($OutputDirectory) { $taskArguments += @('--output-directory', $OutputDirectory) }
& $PythonPath @taskArguments
if ($LASTEXITCODE -ne 0) { throw 'ClickTrack release packaging failed.' }

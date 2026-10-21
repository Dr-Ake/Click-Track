param([string]$FfmpegPath, [string]$PythonPath)
$ErrorActionPreference = 'Stop'
$taskSource = $PSScriptRoot
$taskRoot = Split-Path -Parent $taskSource
$taskBuild = Join-Path $taskRoot 'work\build'
if (-not $PythonPath) {
    $PythonPath = Join-Path $taskRoot 'work\venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonPath)) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
if (-not $FfmpegPath) {
    $FfmpegPath = Join-Path $taskRoot 'app\_internal\ffmpeg.exe'
    if (-not (Test-Path -LiteralPath $FfmpegPath)) { $FfmpegPath = (Get-Command ffmpeg -ErrorAction Stop).Source }
}
$env:CLICKTRACK_FFMPEG = $FfmpegPath
& $PythonPath -m PyInstaller --noconfirm `
    --distpath (Join-Path $taskRoot 'work\dist') `
    --workpath $taskBuild (Join-Path $taskSource 'ClickTrack.spec')
if ($LASTEXITCODE -ne 0) { throw 'ClickTrack build failed.' }
Write-Host ('Built app: ' + (Join-Path $taskRoot 'work\dist\ClickTrack\ClickTrack.exe'))

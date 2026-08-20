[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\AfricanVillas",
    [switch]$StatusOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runtimeRoot = Join-Path $InstallRoot "runtime"
$releasePointer = Join-Path $runtimeRoot "current-release.txt"
if (-not (Test-Path -LiteralPath $releasePointer -PathType Leaf)) {
    throw "Missing release pointer: $releasePointer"
}
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $InstallRoot "releases")).TrimEnd('\') + '\'
$currentRelease = [IO.Path]::GetFullPath((Get-Content -LiteralPath $releasePointer -Raw).Trim())
if (-not $currentRelease.StartsWith($releaseRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Release pointer is outside $releaseRoot"
}
$pythonPath = Join-Path $currentRelease "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Missing release Python: $pythonPath"
}

$env:CODEX_HOME = Join-Path $runtimeRoot "codex-home"
$env:PYTHONUTF8 = "1"
$command = if ($StatusOnly) { "status" } else { "login" }
& $pythonPath -m african_villas.codex_auth_cli $command
exit $LASTEXITCODE

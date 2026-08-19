[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\AfricanVillas"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runtimeRoot = Join-Path $InstallRoot "runtime"
$configPath = Join-Path $runtimeRoot "config.production.json"
$releasePointer = Join-Path $runtimeRoot "current-release.txt"
$logRoot = Join-Path $runtimeRoot "logs"

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Missing production config: $configPath"
}
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

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($config.WebPassword) -or $config.WebPassword -like "REPLACE_*") {
    throw "WebPassword must be configured"
}
if ([string]::IsNullOrWhiteSpace($config.SessionSecret) -or $config.SessionSecret -like "REPLACE_*") {
    throw "SessionSecret must be configured"
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $runtimeRoot "codex-home") | Out-Null

$env:AFRICAN_VILLAS_DATA_DIR = Join-Path $runtimeRoot "data"
$env:AFRICAN_VILLAS_WEB_HOST = "127.0.0.1"
$env:AFRICAN_VILLAS_WEB_PORT = "8092"
$env:AFRICAN_VILLAS_WEB_USERNAME = [string]$config.WebUsername
$env:AFRICAN_VILLAS_WEB_PASSWORD = [string]$config.WebPassword
$env:AFRICAN_VILLAS_SESSION_SECRET = [string]$config.SessionSecret
$env:AFRICAN_VILLAS_ALLOWED_HOSTS = [string]$config.AllowedHosts
$env:AFRICAN_VILLAS_PUBLIC_ORIGIN = [string]$config.PublicOrigin
$env:AFRICAN_VILLAS_MAX_UPLOAD_BYTES = [string]$config.MaxUploadBytes
$env:CODEX_HOME = Join-Path $runtimeRoot "codex-home"
$env:PYTHONUTF8 = "1"
if (-not [string]::IsNullOrWhiteSpace($config.OpenAIApiKey)) {
    $env:OPENAI_API_KEY = [string]$config.OpenAIApiKey
}

$logPath = Join-Path $logRoot ("web-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
& $pythonPath -m african_villas.web_cli *>> $logPath
exit $LASTEXITCODE

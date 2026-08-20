[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\AfricanVillas",
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$ReleaseId = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runtimeRoot = Join-Path $InstallRoot "runtime"
$releasesRoot = Join-Path $InstallRoot "releases"
$configPath = Join-Path $runtimeRoot "config.production.json"
$releasePointer = Join-Path $runtimeRoot "current-release.txt"
$webScript = Join-Path $runtimeRoot "start-web.ps1"
$backupScript = Join-Path $runtimeRoot "backup-data.ps1"
$loginScript = Join-Path $runtimeRoot "login-codex.ps1"
$deployScript = Join-Path $runtimeRoot "deploy-artifact.ps1"
$codexHome = Join-Path $runtimeRoot "codex-home"

foreach ($directory in @(
    $runtimeRoot,
    $releasesRoot,
    (Join-Path $runtimeRoot "data"),
    (Join-Path $runtimeRoot "logs"),
    (Join-Path $runtimeRoot "backups"),
    (Join-Path $runtimeRoot "codex-home")
)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "config.production.example.json") -Destination $configPath
    Write-Warning "Created $configPath. Fill secrets and run this script again. No task was registered."
    exit 2
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($config.WebPassword) -or $config.WebPassword -like "REPLACE_*") {
    throw "Configure WebPassword in $configPath"
}
if ([string]::IsNullOrWhiteSpace($config.SessionSecret) -or $config.SessionSecret -like "REPLACE_*") {
    throw "Configure SessionSecret in $configPath"
}

& icacls.exe $configPath "/inheritance:r" "/grant:r" "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to protect $configPath" }
& icacls.exe $codexHome "/inheritance:r" "/grant:r" "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to protect $codexHome" }
$authModeProperty = $config.PSObject.Properties["CodexAuthMode"]
$authMode = if ($null -eq $authModeProperty) { "chatgpt" } else { [string]$authModeProperty.Value }
if ($authMode -notin @("chatgpt", "api")) { throw "CodexAuthMode must be chatgpt or api" }
$codexConfig = @(
    'cli_auth_credentials_store = "file"',
    ('forced_login_method = "{0}"' -f $authMode)
) -join [Environment]::NewLine
[IO.File]::WriteAllText((Join-Path $codexHome "config.toml"), $codexConfig, [Text.UTF8Encoding]::new($false))

if ([string]::IsNullOrWhiteSpace($ReleaseId)) {
    $releaseId = Get-Date -Format "yyyyMMdd-HHmmss"
} else {
    if ($ReleaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$') {
        throw "ReleaseId may contain only letters, digits, dots, underscores, and hyphens"
    }
    $releaseId = $ReleaseId
}
$releasePath = [IO.Path]::GetFullPath((Join-Path $releasesRoot $releaseId))
$releaseBoundary = [IO.Path]::GetFullPath($releasesRoot).TrimEnd('\') + '\'
if (-not $releasePath.StartsWith($releaseBoundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved release is outside $releaseBoundary"
}
$venvRoot = Join-Path $releasePath "venv"
New-Item -ItemType Directory -Force -Path $releasePath | Out-Null
& py -3.12 -m venv $venvRoot
if ($LASTEXITCODE -ne 0) { throw "Unable to create Python 3.12 virtual environment" }
$pythonPath = Join-Path $venvRoot "Scripts\python.exe"
& $pythonPath -m pip install --disable-pip-version-check --upgrade $SourceRoot
if ($LASTEXITCODE -ne 0) { throw "Package installation failed" }

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "start-web.ps1") -Destination $webScript -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "backup-data.ps1") -Destination $backupScript -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "login-codex.ps1") -Destination $loginScript -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "deploy-artifact.ps1") -Destination $deployScript -Force

$databasePath = Join-Path $runtimeRoot "data\african_villas.db"
if (Test-Path -LiteralPath $databasePath -PathType Leaf) {
    & $pythonPath -m african_villas.backup_cli `
        --data-dir (Join-Path $runtimeRoot "data") `
        --backup-dir (Join-Path $runtimeRoot "backups") `
        --retention-days 30
    if ($LASTEXITCODE -ne 0) { throw "Pre-deployment backup failed" }
}

$previousRelease = $null
if (Test-Path -LiteralPath $releasePointer -PathType Leaf) {
    $previousRelease = (Get-Content -LiteralPath $releasePointer -Raw).Trim()
}
$existingTask = Get-ScheduledTask -TaskName "AfricanVillasWeb" -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    Stop-ScheduledTask -TaskName "AfricanVillasWeb" -ErrorAction SilentlyContinue
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        if ((Get-ScheduledTask -TaskName "AfricanVillasWeb").State -ne "Running") { break }
        Start-Sleep -Seconds 1
    }
}

$pointerTemp = "$releasePointer.new"
[IO.File]::WriteAllText($pointerTemp, $releasePath, [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $pointerTemp -Destination $releasePointer -Force

$taskUser = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$webAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -InstallRoot "{1}"' -f $webScript, $InstallRoot
)
$webTrigger = New-ScheduledTaskTrigger -AtStartup
$webSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "AfricanVillasWeb" -Action $webAction -Trigger $webTrigger -Principal $taskUser -Settings $webSettings -Force | Out-Null

$backupAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -InstallRoot "{1}"' -f $backupScript, $InstallRoot
)
$backupTrigger = New-ScheduledTaskTrigger -Daily -At "03:30"
Register-ScheduledTask -TaskName "AfricanVillasBackup" -Action $backupAction -Trigger $backupTrigger -Principal $taskUser -Force | Out-Null

Start-ScheduledTask -TaskName "AfricanVillasWeb"
$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8092/health" -TimeoutSec 2
        if ($health.ok -and $health.release -eq $releaseId) { $healthy = $true; break }
    } catch {
        # The new process may still be starting.
    }
}
if (-not $healthy) {
    Stop-ScheduledTask -TaskName "AfricanVillasWeb" -ErrorAction SilentlyContinue
    $rollbackMessage = "no previous release was available"
    if (-not [string]::IsNullOrWhiteSpace($previousRelease)) {
        [IO.File]::WriteAllText($pointerTemp, $previousRelease, [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $pointerTemp -Destination $releasePointer -Force
        Start-ScheduledTask -TaskName "AfricanVillasWeb"
        $rollbackMessage = "the previous release pointer was restored"
    }
    throw "New release failed health check; $rollbackMessage"
}
Write-Host "AfricanVillasWeb is healthy on 127.0.0.1:8092. Caddy was not changed."

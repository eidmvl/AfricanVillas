[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BundlePath,

    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Fa-f0-9]{64}$')]
    [string]$ExpectedSha256,

    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Fa-f0-9]{40}$')]
    [string]$CommitSha,

    [string]$InstallRoot = "C:\AfricanVillas",
    [string]$StagingRoot = "C:\AfricanVillasDeploy"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$stagingRootFull = [IO.Path]::GetFullPath($StagingRoot).TrimEnd('\')
$stagingBoundary = $stagingRootFull + '\'
$bundleFull = [IO.Path]::GetFullPath($BundlePath)
if (-not $bundleFull.StartsWith($stagingBoundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Bundle must be located below $stagingBoundary"
}
if (-not (Test-Path -LiteralPath $bundleFull -PathType Leaf)) {
    throw "Deployment bundle does not exist: $bundleFull"
}

$actualSha256 = (Get-FileHash -LiteralPath $bundleFull -Algorithm SHA256).Hash
if (-not $actualSha256.Equals($ExpectedSha256, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Deployment bundle SHA-256 does not match"
}

New-Item -ItemType Directory -Force -Path $stagingRootFull | Out-Null
$shortSha = $CommitSha.Substring(0, 12).ToLowerInvariant()
$timestamp = Get-Date -Format "yyyyMMdd-HHmmssfff"
$stagePath = [IO.Path]::GetFullPath((Join-Path $stagingRootFull "$timestamp-$shortSha"))
if (-not $stagePath.StartsWith($stagingBoundary, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved staging directory is outside $stagingBoundary"
}
New-Item -ItemType Directory -Path $stagePath | Out-Null
Expand-Archive -LiteralPath $bundleFull -DestinationPath $stagePath

$payloadRoot = Join-Path $stagePath "payload"
$windowsRoot = Join-Path $payloadRoot "windows"
$installerPath = Join-Path $windowsRoot "install-service.ps1"
$manifestPath = Join-Path $payloadRoot "manifest.json"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Deployment bundle is missing windows/install-service.ps1"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Deployment bundle is missing manifest.json"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ([string]$manifest.commit -ne $CommitSha) {
    throw "Deployment manifest commit does not match the requested commit"
}
$wheels = @(Get-ChildItem -LiteralPath $payloadRoot -Filter "*.whl" -File)
if ($wheels.Count -ne 1) {
    throw "Deployment bundle must contain exactly one wheel"
}

$releaseId = "$timestamp-$shortSha"
& $installerPath -InstallRoot $InstallRoot -SourceRoot $wheels[0].FullName -ReleaseId $releaseId
if (-not $?) {
    throw "Production installer failed"
}

$health = Invoke-RestMethod -Uri "http://127.0.0.1:8092/health" -TimeoutSec 10
if (-not $health.ok -or $health.release -ne $releaseId) {
    throw "Post-deployment health check returned an unexpected release"
}

Write-Host "Deployed commit $CommitSha as release $releaseId"

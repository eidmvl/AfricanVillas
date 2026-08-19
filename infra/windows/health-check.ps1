[CmdletBinding()]
param(
    [int]$Port = 8092
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 10
if (-not $response.ok) {
    throw "African Villas health check failed"
}
$response | ConvertTo-Json -Depth 5

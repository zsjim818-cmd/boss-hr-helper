param(
    [int]$Port = 9222,
    [string]$ProfileDir = "",
    [string]$EdgePath = "",
    [switch]$Headless,
    [switch]$Check
)

$ErrorActionPreference = "Stop"

if (-not $ProfileDir) {
    $ProfileDir = Join-Path $env:LOCALAPPDATA "boss-hr-helper\edge-profile\port-$Port"
}
$ProfileDir = [System.IO.Path]::GetFullPath($ProfileDir)
$DailyProfile = [System.IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "Microsoft\Edge\User Data"))
if ($ProfileDir.TrimEnd('\') -ieq $DailyProfile.TrimEnd('\')) {
    throw "Refusing to use the daily Edge profile. Choose a dedicated profile directory."
}

if (-not $EdgePath) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    $EdgePath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $EdgePath) { throw "Microsoft Edge was not found; pass -EdgePath." }

function Get-CdpHealth {
    param([int]$HealthPort)
    $version = Invoke-RestMethod -Uri "http://127.0.0.1:$HealthPort/json/version" -TimeoutSec 3
    $pages = Invoke-RestMethod -Uri "http://127.0.0.1:$HealthPort/json/list" -TimeoutSec 3
    [pscustomobject]@{
        endpoint = "http://127.0.0.1:$HealthPort"
        browser = $version.Browser
        webSocketDebuggerUrl = $version.webSocketDebuggerUrl
        pages = @($pages | Select-Object -First 10 | ForEach-Object { $_.url })
    } | ConvertTo-Json -Depth 4
}

if ($Check) {
    Get-CdpHealth -HealthPort $Port
    exit 0
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
$arguments = @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=$ProfileDir",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--enable-automation",
    "about:blank"
)
if ($Headless) { $arguments = @("--headless=new", "--disable-gpu") + $arguments }

$process = Start-Process -FilePath $EdgePath -ArgumentList $arguments -PassThru
Write-Output "Started Edge PID $($process.Id) on port $Port with profile $ProfileDir"

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    try {
        Get-CdpHealth -HealthPort $Port
        exit 0
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
throw "Edge did not expose the CDP endpoint on port $Port within 30 seconds."

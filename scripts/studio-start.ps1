<#
.SYNOPSIS
Starts the Studio API for an event: the firewall rule, then studio serve, detached.

.DESCRIPTION
Run on the Studio in an administrator PowerShell, by hand or over SSH. It creates
the inbound firewall rule karaoke-studio (TCP on -Port, Tailscale only) when it is
missing, then starts this clone's studio serve detached from the session, so it
keeps running after an SSH session ends. Its output goes to -LogFile, whose end is
shown when the server stops within its first seconds. Does nothing
when a studio serve of this clone is already running: a second one would remove
the first one's builds in flight. scripts/studio-stop.ps1 stops it.

.PARAMETER TokenFile
A file holding the Studio token. studio serve reads it as STUDIO_TOKEN.

.PARAMETER Library
The library folder, passed as --library (studio serve's default: library, in the clone).

.PARAMETER ModelDir
The model folder, passed as --model-dir (studio serve's default otherwise).

.PARAMETER Port
The port of studio serve and of the firewall rule (default: 8790).

.PARAMETER LogFile
Where studio serve's output is appended (default: studio-serve.log, in the clone).
#>
# Without elevation, the command lines of elevated processes read as empty, and a
# running server would go unseen.
#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory = $true)]
    [string]$TokenFile,
    [string]$Library,
    [string]$ModelDir,
    [int]$Port = 8790,
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'

function Get-FullPath([string]$path) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path)
}

function Format-Literal([string]$value) {
    "'" + $value.Replace("'", "''") + "'"
}

$clone = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$studioExe = Join-Path $clone '.venv\Scripts\studio.exe'

$serverPattern = [regex]::Escape($studioExe) + '[''"]?\s+serve(\s|$)'
$running = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $serverPattern })
if ($running) {
    Write-Host "studio serve of this clone is already running (PID $($running.ProcessId -join ', ')); nothing started."
    Write-Host 'Stop it first with scripts\studio-stop.ps1 to start it again.'
    exit 0
}

$TokenFile = (Resolve-Path -LiteralPath $TokenFile).Path
if ($LogFile) {
    $LogFile = Get-FullPath $LogFile
} else {
    $LogFile = Join-Path $clone 'studio-serve.log'
}

if (-not (Get-NetFirewallRule -Name 'karaoke-studio' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'karaoke-studio' -DisplayName 'karaoke-studio' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -InterfaceAlias 'Tailscale' -RemoteAddress '100.64.0.0/10' | Out-Null
    Write-Host "Created the firewall rule karaoke-studio (TCP $Port, Tailscale only)"
}

$serveArguments = "serve --port $Port"
if ($Library) {
    $serveArguments += " --library $(Format-Literal (Get-FullPath $Library))"
}
if ($ModelDir) {
    $serveArguments += " --model-dir $(Format-Literal (Get-FullPath $ModelDir))"
}
# Win32_Process.Create passes no environment of this session, so the wrapper reads
# the token itself; the token never reaches a command line or the log. Python and
# the wrapper agree on UTF-8, or accented song titles come out garbled in the log.
# The command holds no double quotes, since it sits inside one pair of them.
$wrapper = '$env:STUDIO_TOKEN = (Get-Content -Raw -LiteralPath ' + (Format-Literal $TokenFile) + ').Trim(); ' +
    '$env:PYTHONIOENCODING = ''utf-8''; [Console]::OutputEncoding = [Text.Encoding]::UTF8; ' +
    '& ' + (Format-Literal $studioExe) + " $serveArguments 2>&1 | " +
    'ForEach-Object { [IO.File]::AppendAllText(' + (Format-Literal $LogFile) + ', [string]$_ + [Environment]::NewLine) }'
$commandLine = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "' + $wrapper + '"'

$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $commandLine
    CurrentDirectory = $clone
}
if ($result.ReturnValue -ne 0) {
    Write-Host "Could not start studio serve (Win32_Process.Create returned $($result.ReturnValue))."
    exit 1
}

# A server that fails at start (no token, the separation extra missing, the port in
# use) ends within seconds, and only its log says why.
Start-Sleep -Seconds 5
if (-not (Get-Process -Id $result.ProcessId -ErrorAction SilentlyContinue)) {
    Write-Host "studio serve stopped right after starting. The end of its log ($LogFile):"
    if (Test-Path -LiteralPath $LogFile) {
        Get-Content -LiteralPath $LogFile -Tail 20 -Encoding UTF8 | ForEach-Object { Write-Host "  $_" }
    }
    exit 1
}
Write-Host "Started studio serve on port $Port (PID $($result.ProcessId)), detached."
Write-Host "Log: $LogFile"

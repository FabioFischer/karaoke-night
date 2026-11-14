<#
.SYNOPSIS
Opens a karaoke night on the Stage: REAPER, stage serve and the full screen browser.

.DESCRIPTION
The target of the desktop shortcut on the Stage (docs/NIGHT.md). It opens REAPER
with the session unless REAPER is already running, starts stage serve in its own
console window (closing that window stops the server), waits until the Stage API
answers, then opens Edge in full screen at /karaoke, with a profile of its own.

.PARAMETER Project
The REAPER session (.RPP) to open.

.PARAMETER Studio
The Studio API URL, passed to stage serve as --studio. Without it, only songs in
the library can be added.

.PARAMETER TokenFile
A file holding the Studio token. stage serve gets it as STUDIO_TOKEN.

.PARAMETER Library
The library folder (default: library, in the clone).

.PARAMETER Port
The port of stage serve (default: 8791).
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Project,
    [string]$Studio,
    [string]$TokenFile,
    [string]$Library,
    [int]$Port = 8791,
    [string]$ReaperPath = 'C:\Program Files\REAPER (x64)\reaper.exe',
    [string]$EdgePath = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
)

$ErrorActionPreference = 'Stop'

trap {
    Write-Host $_
    # The shortcut's window closes when the script ends and would take the message with it.
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}

function Get-FullPath([string]$path) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path)
}

function Format-Argument([string]$value) {
    # A trailing backslash would escape the closing quote.
    '"' + $value.TrimEnd('\') + '"'
}

$clone = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if ($Library) {
    $Library = Get-FullPath $Library
} else {
    $Library = Join-Path $clone 'library'
}
$stageUrl = "http://127.0.0.1:$Port"

if (Get-Process -Name 'reaper' -ErrorAction SilentlyContinue) {
    Write-Host 'REAPER is already running: left as it is, with the session it has open.'
} else {
    $Project = Get-FullPath $Project
    Write-Host "Opening REAPER with $Project"
    Start-Process -FilePath $ReaperPath -ArgumentList (Format-Argument $Project)
}

$stageExe = Join-Path $clone '.venv\Scripts\stage.exe'
$serveArguments = "serve --port $Port --library $(Format-Argument $Library) " +
    "--web $(Format-Argument (Join-Path $clone 'web\dist'))"
if ($Studio) {
    $serveArguments += " --studio $(Format-Argument $Studio)"
}
if ($TokenFile) {
    # Set only while stage serve starts, so the browser opened next does not inherit it.
    $env:STUDIO_TOKEN = (Get-Content -Raw -LiteralPath $TokenFile).Trim()
}
Write-Host "Starting stage serve on port $Port in its own window (close it to stop the server)"
$server = Start-Process -FilePath $stageExe -ArgumentList $serveArguments -WorkingDirectory $clone -PassThru
if ($TokenFile) {
    Remove-Item Env:STUDIO_TOKEN
}

Write-Host "Waiting for the Stage at $stageUrl"
$deadline = (Get-Date).AddSeconds(30)
while ($true) {
    try {
        Invoke-WebRequest -Uri "$stageUrl/api/state" -UseBasicParsing -TimeoutSec 2 | Out-Null
        break
    } catch {
        if ($server.HasExited) {
            throw "stage serve stopped before answering, and its window closed with it.`n" +
                "To see why, run it in a terminal, in ${clone}:`n" +
                "  .venv\Scripts\stage.exe $serveArguments"
        }
        if ((Get-Date) -gt $deadline) {
            throw "stage serve did not answer at $stageUrl within 30 s; its window shows why, and is left open."
        }
        Start-Sleep -Milliseconds 500
    }
}

$edgeProfile = Join-Path $env:LOCALAPPDATA 'karaoke-night\edge'
Write-Host "Opening the browser at $stageUrl/karaoke (F11 leaves full screen)"
Start-Process -FilePath $EdgePath -ArgumentList (
    "--user-data-dir=$(Format-Argument $edgeProfile) --app=$stageUrl/karaoke --start-fullscreen"
)

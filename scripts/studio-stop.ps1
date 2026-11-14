<#
.SYNOPSIS
Stops the Studio API of this clone and removes its firewall rule.

.DESCRIPTION
Run on the Studio in an administrator PowerShell, by hand or over SSH, after an
event. It stops every process of this clone's studio serve (the PowerShell
wrapper scripts\studio-start.ps1 started, studio.exe and its Python processes),
found by this clone's own studio.exe path on their command lines, then removes
the firewall rule karaoke-studio. Prints nothing when neither is there.
#>

$ErrorActionPreference = 'Stop'

$clone = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$studioExe = Join-Path $clone '.venv\Scripts\studio.exe'

$server = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine.IndexOf($studioExe, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
    $_.CommandLine.Contains(' serve')
})
foreach ($process in $server) {
    # A child may already be gone with its parent.
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped $($process.Name) (PID $($process.ProcessId))"
}

if (Get-NetFirewallRule -Name 'karaoke-studio' -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -Name 'karaoke-studio'
    Write-Host 'Removed the firewall rule karaoke-studio'
}

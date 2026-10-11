[CmdletBinding()]
param([ValidateSet('Start','Stop','Status','Report')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$prototype = $PSScriptRoot
$project = (Resolve-Path (Join-Path $prototype '..\..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$runtime = Join-Path $prototype 'runtime-chatgpt-plus'
$report = Join-Path $runtime 'reports\index.html'
$endpoint = Join-Path $runtime 'reports\dashboard.json'
function Get-DashboardUrl {
    if (Test-Path -LiteralPath $endpoint) {
        try {
            $saved = Get-Content -LiteralPath $endpoint -Raw | ConvertFrom-Json
            $url = "http://127.0.0.1:$($saved.port)/"
            Invoke-RestMethod -Uri ($url + 'api/status') -TimeoutSec 2 | Out-Null
            return $url
        } catch { }
    }
    $logs = Join-Path $runtime 'logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $tag = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $dashOut = Join-Path $logs "$tag.dashboard.stdout.log"
    $dashErr = Join-Path $logs "$tag.dashboard.stderr.log"
    $dashArgs = '-u "' + (Join-Path $prototype 'dashboard.py') + '" --runtime "' + $runtime + '"'
    Start-Process -FilePath $python -ArgumentList $dashArgs -WorkingDirectory $prototype -WindowStyle Hidden -RedirectStandardOutput $dashOut -RedirectStandardError $dashErr | Out-Null
    $until = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 250
        if (Test-Path -LiteralPath $endpoint) {
            try {
                $saved = Get-Content -LiteralPath $endpoint -Raw | ConvertFrom-Json
                $url = "http://127.0.0.1:$($saved.port)/"
                Invoke-RestMethod -Uri ($url + 'api/status') -TimeoutSec 2 | Out-Null
                return $url
            } catch { }
        }
    } until ((Get-Date) -ge $until)
    throw "Le tableau de bord local n'a pas démarré. Consulter $dashErr"
}
if ($Action -eq 'Report') { Start-Process (Get-DashboardUrl); exit 0 }
if ($Action -eq 'Status') {
    $url = Get-DashboardUrl
    Invoke-RestMethod -Uri ($url + 'api/status') | ConvertTo-Json -Depth 20
    exit 0
}
if ($Action -eq 'Stop') {
    Push-Location -LiteralPath $prototype
    try { & $python -m supervisor --runtime $runtime stop }
    finally { Pop-Location }
    exit $LASTEXITCODE
}
$dashboardUrl = Get-DashboardUrl
# The Python launcher holds an OS lock for the entire lifetime of the loop.
$handle = [IO.File]::Open((Join-Path $runtime 'launcher.lock'), 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
try {
    try { $handle.Lock(0,1) }
    catch {
        Start-Process $dashboardUrl
        Write-Output 'Ralph est déjà lancé. Consultez le rapport pour son état.'
        exit 0
    }
    $handle.Unlock(0,1)
} finally { $handle.Dispose() }
$schedule = Join-Path $project '.local\codex-auto\schedule.json'
if (Test-Path -LiteralPath $schedule) {
    if ((Get-Content -LiteralPath $schedule -Raw | ConvertFrom-Json).enabled) {
        throw 'Ancienne automatisation encore active : ne pas lancer deux superviseurs.'
    }
}
$logs = Join-Path $runtime 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$tag = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$stdout = Join-Path $logs "$tag.stdout.log"
$stderr = Join-Path $logs "$tag.stderr.log"
$arguments = '-u "' + (Join-Path $prototype 'launch_project.py') + '"'
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
$watchOut = Join-Path $logs "$tag.report.stdout.log"
$watchErr = Join-Path $logs "$tag.report.stderr.log"
$watchArgs = '-u "' + (Join-Path $prototype 'watch_live.py') + '" --runtime "' + $runtime + '"'
Start-Process -FilePath $python -ArgumentList $watchArgs -WorkingDirectory $prototype -WindowStyle Hidden -RedirectStandardOutput $watchOut -RedirectStandardError $watchErr | Out-Null
$deadline = (Get-Date).AddSeconds(45)
$started = $false
do {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    if (Test-Path -LiteralPath $stdout) {
        foreach ($line in Get-Content -LiteralPath $stdout) {
            try { if (($line | ConvertFrom-Json).status -eq 'started') { $started = $true } }
            catch { }
        }
    }
    if ($process.HasExited) {
        Get-Content -LiteralPath $stdout,$stderr -ErrorAction SilentlyContinue
        throw 'Ralph a quitté : consulter le motif ci-dessus.'
    }
} until ($started -or (Get-Date) -ge $deadline)
$result = [ordered]@{status=$(if ($started) {'ralph_started'} else {'preflight_pending'});pid=$process.Id;stdout=$stdout;stderr=$stderr;report=$report}
$result.dashboard = $dashboardUrl
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'launch.json') -Encoding UTF8
$result | ConvertTo-Json
Start-Process $dashboardUrl

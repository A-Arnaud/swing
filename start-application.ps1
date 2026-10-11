$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$pythonPath = Join-Path $projectPath '.venv/Scripts/python.exe'
$logPath = Join-Path $projectPath ('.local/logs/application-manager.' + [guid]::NewGuid().ToString('N'))
Start-Process -FilePath $pythonPath -ArgumentList @((Join-Path $PSScriptRoot 'application_runtime.py')) -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput "$logPath.log" -RedirectStandardError "$logPath.err.log" | Out-Null
Write-Output 'Application startup requested. State: .local/application-state.json; URL: http://127.0.0.1:8000/'

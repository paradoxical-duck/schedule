#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"

$npmDir = Join-Path $env:APPDATA "npm"
$sourceDir = Join-Path $npmDir "schedule-src"

New-Item -ItemType Directory -Path $npmDir -Force | Out-Null
New-Item -ItemType Directory -Path $sourceDir -Force | Out-Null

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "schedule.py") -Destination (Join-Path $sourceDir "schedule.py") -Force

@'
#!/usr/bin/env pwsh
python "$env:APPDATA\npm\schedule-src\schedule.py" @args
exit $LASTEXITCODE
'@ | Set-Content -LiteralPath (Join-Path $npmDir "schedule.ps1") -Encoding ASCII

@'
@echo off
python "%APPDATA%\npm\schedule-src\schedule.py" %*
exit /b %ERRORLEVEL%
'@ | Set-Content -LiteralPath (Join-Path $npmDir "schedule.cmd") -Encoding ASCII

Write-Host "Installed schedule to $npmDir"
Write-Host "Try: schedule -Chat `"chat name`" -Prompt `"hello`""

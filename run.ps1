# Launch Nexa: .\run.ps1 [doctor] [--ask "..."] [--model ...]   (works from any directory)
# Python: $env:NEXA_PYTHON, else the signed python.org 3.14 install (Smart App Control
# blocks the unsigned venv interpreter under OneDrive), else whatever `python` is on PATH.
$signed = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"
$py = if ($env:NEXA_PYTHON) { $env:NEXA_PYTHON } elseif (Test-Path $signed) { $signed } else { "python" }

if (-not $env:PYTHONIOENCODING) { $env:PYTHONIOENCODING = "utf-8" }

Set-Location $PSScriptRoot
& $py -m nexa @args
exit $LASTEXITCODE

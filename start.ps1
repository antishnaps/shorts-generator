$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& $env:ComSpec /d /c start.bat
exit $LASTEXITCODE

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$Cli = Join-Path $Root '.tools\node_modules\.bin\playwright-cli.cmd'
if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw "Playwright CLI ausente. Execute scripts\install.ps1."
}
& $Cli @args
exit $LASTEXITCODE

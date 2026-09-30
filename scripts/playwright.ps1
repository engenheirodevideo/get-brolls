$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$Gb = Join-Path $Root 'scripts\gb.py'
# `setup --where tools` diz qual .tools está em uso ($GB_HOME\runtime ou GB_RUNTIME_DIR).
# Um GB_PROFILE herdado do getbrolls vale; rodado à mão, nenhum perfil do workspace vale.
$WhereArgs = @('setup', '--where', 'tools')
if (-not $env:GB_PROFILE) { $WhereArgs = @('--profile', 'off') + $WhereArgs }
# A CLI escreve UTF-8; sem isto o Windows PowerShell 5.1 decodificaria na página OEM.
$ConsoleEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $WhereOutput = (& python $Gb @WhereArgs | Out-String)
} finally {
    [Console]::OutputEncoding = $ConsoleEncoding
}
if (-not $WhereOutput.Trim()) {
    throw 'getbrolls setup --where tools não devolveu JSON; confira a instalação com o doctor.'
}
$Where = $WhereOutput | ConvertFrom-Json
$Cli = $null
if ($Where.PSObject.Properties['in_use'] -and $Where.in_use.executable) {
    $Cli = [string]$Where.in_use.executable
}
# O npm ci deixa o playwright-cli.cmd em node_modules\.bin; é ele que o Windows executa.
if (-not $Cli -or -not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw "Playwright CLI (playwright-cli.cmd) ausente. Rode python `"$Gb`" setup."
}
& $Cli @args
exit $LASTEXITCODE

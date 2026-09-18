# Mesma bateria de qualidade que o CI roda no job `quality`, para rodar antes do commit.
# Instale as ferramentas uma vez: python -m pip install -r requirements-dev.txt
#requires -Version 5.1
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')

function Invoke-Step {
    param([string]$Name, [string]$Command, [string[]]$CommandArgs)
    Write-Host "==> $Name"
    & $Command @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "$Name falhou (exit $LASTEXITCODE)" }
}

Invoke-Step 'ruff check' 'ruff' @('check', '.')
Invoke-Step 'ruff format --check' 'ruff' @('format', '--check', '.')
Invoke-Step 'pyright' 'pyright' @()
Invoke-Step 'unittest' 'python' @('-m', 'unittest', 'discover', '-s', 'tests')

Write-Host 'OK'

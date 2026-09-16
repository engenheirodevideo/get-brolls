param(
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Native {
    param(
        [string]$Label,
        [string]$File,
        [string[]]$Arguments
    )
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label falhou com código $LASTEXITCODE."
    }
}

$Root = Split-Path -Parent $PSScriptRoot
$Missing = $false
foreach ($Tool in @('python', 'ffmpeg', 'ffprobe', 'curl', 'node', 'npm', 'npx')) {
    if (-not (Get-Command $Tool -CommandType Application -ErrorAction SilentlyContinue)) {
        Write-Host "MISSING: $Tool"
        $Missing = $true
    }
}
if ($Missing) {
    throw 'Instale os executáveis conforme GUIDE.md e repita.'
}

Invoke-Native -Label 'Verificação do Python' -File 'python' -Arguments @('-c', 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ obrigatório"')

$NodeVersion = (& node --version).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Não foi possível consultar a versão do Node.' }
$NodeMajor = [int](($NodeVersion -replace '^v', '').Split('.')[0])
if ($NodeMajor -lt 22) { throw "Node 22+ obrigatório (encontrado $NodeVersion)." }

if ($Check) {
    Write-Host 'Pré-requisitos do instalador encontrados; check não instala nem testa rede/login.'
    exit 0
}

Invoke-Native -Label 'Criação da venv' -File 'python' -Arguments @('-m', 'venv', (Join-Path $Root '.venv'))
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
Invoke-Native -Label 'Instalação Python' -File $VenvPython -Arguments @('-m', 'pip', 'install', '-r', (Join-Path $Root 'requirements.txt'))
Invoke-Native -Label 'Importação yt-dlp/EJS' -File $VenvPython -Arguments @('-c', 'import yt_dlp, yt_dlp_ejs; print("yt-dlp e EJS importados")')
Invoke-Native -Label 'Instalação Playwright CLI' -File 'npm' -Arguments @('--cache', (Join-Path $Root '.tools\npm-cache'), 'install', '--prefix', (Join-Path $Root '.tools'), '--no-audit', '--no-fund', '--save-exact', '@playwright/cli@0.1.20')
$Playwright = Join-Path $Root '.tools\node_modules\.bin\playwright-cli.cmd'
Invoke-Native -Label 'Verificação Playwright CLI' -File $Playwright -Arguments @('--version')
$env:PATH = "$(Join-Path $Root '.venv\Scripts');$env:PATH"
Invoke-Native -Label 'Doctor' -File $VenvPython -Arguments @((Join-Path $Root 'scripts\gb.py'), 'doctor')

Write-Host "`nDependências instaladas em .venv\ e .tools\; não fazem parte do repositório."
Write-Host 'Navegador existente: siga GUIDE.md para reutilizar a sessão autorizada.'

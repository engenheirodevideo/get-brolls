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
    throw 'Instale os executáveis conforme docs/GUIDE.md e repita.'
}

Invoke-Native -Label 'Verificação do Python' -File 'python' -Arguments @('-c', 'import sys; assert sys.version_info >= (3,11), ''Python 3.11+ obrigatório''')

$Filters = (& ffmpeg -hide_banner -filters 2>$null | Out-String)
if ($Filters -notmatch ' drawtext ') {
    Write-Host 'AVISO: FFmpeg sem o filtro drawtext (libfreetype): o contact sheet sai sem número e timecode nas células. Instale um build com freetype (gyan.dev ou BtbN) e coloque no PATH.'
}

$NodeVersion = (& node --version).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Não foi possível consultar a versão do Node.' }
$NodeMajor = [int](($NodeVersion -replace '^v', '').Split('.')[0])
if ($NodeMajor -lt 22) { throw "Node 22+ obrigatório (encontrado $NodeVersion)." }

if ($Check) {
    Write-Host 'Pré-requisitos do instalador encontrados; check não instala nem testa rede/login.'
    exit 0
}

$Gb = Join-Path $Root 'scripts\gb.py'
# A CLI escreve UTF-8; sem isto o Windows PowerShell 5.1 decodificaria na página OEM.
$ConsoleEncoding = [Console]::OutputEncoding
# setup instala em $GB_HOME\runtime (ou GB_RUNTIME_DIR); sai 4 quando falta item do
# sistema (FFmpeg, Node...), e o runtime sai do mesmo jeito.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    & python $Gb 'setup'
    $SetupStatus = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $ConsoleEncoding
}
if ($SetupStatus -ne 0 -and $SetupStatus -ne 4) {
    throw "setup falhou (código $SetupStatus); veja a mensagem acima."
}
if ($SetupStatus -eq 0) {
    & (Join-Path $PSScriptRoot 'playwright.ps1') '--version'
    if ($LASTEXITCODE -ne 0) { throw "Verificação Playwright CLI falhou com código $LASTEXITCODE." }
}
# doctor sai 4 quando summary.missing não está vazio (faltam itens), com o JSON em stdout.
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $DoctorOutput = & python $Gb 'doctor'
    $DoctorStatus = $LASTEXITCODE
} finally {
    [Console]::OutputEncoding = $ConsoleEncoding
}
$DoctorOutput | Write-Output
if ($DoctorStatus -eq 4) {
    Write-Host ''
    Write-Host 'doctor encontrou pendências (código 4: faltam itens). O que falta e como resolver, de summary.missing:'
    $Doctor = ($DoctorOutput | Out-String) | ConvertFrom-Json
    foreach ($Item in $Doctor.summary.missing) {
        Write-Host "- $($Item.item): $($Item.fix)"
    }
    exit 4
}
if ($DoctorStatus -ne 0) {
    throw "Doctor falhou com código $DoctorStatus."
}

Write-Host "`nDependências em `$GB_HOME\runtime\ (ou GB_RUNTIME_DIR), compartilhadas entre instalações; fora do repositório."
Write-Host 'Navegador existente: siga docs/GUIDE.md para reutilizar a sessão autorizada.'

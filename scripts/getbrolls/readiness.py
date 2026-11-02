"""O que o `doctor` e o `setup --check` sondam: executáveis obrigatórios e opcionais, pins, dados da instalação
e o veredito humano. A sondagem é uma só (`readiness_probe`), para o `ready` dos dois bater."""

# pylint: disable=fixme
# `fixme` é o comentário "Todo executável..." (não é um TODO de verdade, é o nome da variável).

import os
import shutil
from pathlib import Path

from . import _paths, venv_state
from .errors import DataRootError

# Raiz real da skill/plugin: o comando sugerido não pode depender da pasta atual.
INSTALLER = _paths.installer_hint()
# O que fazer quando os arquivos de dados existem mas não abrem: nenhum reinstalar resolve.
DATA_PERMISSION_FIX = "Confira as permissões da pasta de dados do getbrolls (`install.data_root`) e repita."
SYSTEM_TOOLS = "instale pelo gerenciador do sistema; veja docs/GUIDE.md#instalação"

# Executável obrigatório → (comando que resolve, impacto real da ausência).
REQUIRED_EXECUTABLES = {
    "ffmpeg": (SYSTEM_TOOLS, "Prévia, corte e verificação ficam indisponíveis"),
    "ffprobe": (SYSTEM_TOOLS, "Prévia, corte e verificação ficam indisponíveis"),
    "curl": (SYSTEM_TOOLS, "Download dos pares CDN do Instagram fica indisponível"),
    "node": (SYSTEM_TOOLS, "Playwright CLI e runtime EJS do yt-dlp ficam indisponíveis"),
    "npx": (SYSTEM_TOOLS, "Instalação e execução do Playwright CLI ficam indisponíveis"),
    "yt-dlp": (INSTALLER, "YouTube e TikTok ficam indisponíveis sem ele"),
    "playwright-cli": (INSTALLER, "Instagram indisponível sem ele"),
}

# Ausência esperada em parte dos ambientes: não bloqueia o fluxo principal.
OPTIONAL_EXECUTABLES = {
    "deno": "Alternativa ao Node apenas para o runtime EJS",
    "bash": "Somente os helpers opcionais de YouTube; a CLI não depende dele",
}

OPTIONAL_KEYS = {
    "PEXELS_API_KEY": "Busca no Pexels desativada; defina a chave no ambiente ou no .env",
    "PIXABAY_API_KEY": "Busca no Pixabay desativada; defina a chave no ambiente ou no .env",
}


# Todo executável que o doctor sonda: obrigatórios mais opcionais, sem duplicar.
PROBED_EXECUTABLES = tuple(sorted(set(REQUIRED_EXECUTABLES) | set(OPTIONAL_EXECUTABLES)))


def doctor_overrides():
    """Pins válidos e pins inválidos: um pin quebrado não derruba o diagnóstico."""
    from getbrolls.config import PATH_KEYS, pin_override

    active = {}
    problems = []
    for key in PATH_KEYS:
        try:
            value = pin_override(key)
        except ValueError as exc:
            problems.append({"item": key, "fix": INSTALLER, "note": str(exc)})
            continue
        if value:
            active[key] = str(value)
    return active, problems


def doctor_resolved(overrides):
    """Executável absoluto realmente usado por ferramenta, ou None quando ausente."""
    from getbrolls.config import TOOL_PATH_KEYS

    resolved = {}
    for name in PROBED_EXECUTABLES:
        if name == "yt-dlp":
            # O que `social.command()` usaria (pin, venv, PATH), nunca o PATH na frente da venv.
            from .social import ytdlp_in_use

            try:
                found = ytdlp_in_use()[0]
            except ValueError:
                found = None
            resolved[name] = str(Path(found).resolve()) if found else None
            continue
        found = overrides.get(TOOL_PATH_KEYS.get(name) or "") or shutil.which(name)
        if not found and name == "playwright-cli":
            found = _local_playwright()
        resolved[name] = str(Path(found).resolve()) if found else None
    return resolved


def data_fix():
    """Como repor os arquivos de dados: o instalador no checkout, reinstalar no pacote."""
    return _paths.installer_hint() if _paths.origin() == "checkout" else _paths.REINSTALL_COMMAND


def data_problems():
    """(faltam, sem permissão de leitura) dos arquivos de dados; todos faltam sem a pasta."""
    try:
        return _paths.data_inventory()
    except DataRootError:
        return list(_paths.REQUIRED_DATA), []


def data_problem_notes(missing, unreadable):
    """As frases do `doctor` e do `setup --check` para os dados: o que falta e o que não abre."""
    notes = ["Faltam: " + ", ".join(missing)] if missing else []
    return notes + (["Sem permissão de leitura: " + ", ".join(unreadable)] if unreadable else [])


def readiness_probe():
    """A sondagem que decide o `ready` do `doctor` e do `setup --check`: pins, engine social
    e a presença de cada executável sondado, pela mesma regra nos dois."""
    from .social import doctor as social_doctor

    # Pin inválido vira item de `missing`, não morte do diagnóstico.
    overrides, pin_problems = doctor_overrides()
    try:
        social = social_doctor()
    except ValueError as exc:
        social = {"engine": "yt-dlp", "installed": False, "error": str(exc)}
    _judge_ytdlp(social)
    return overrides, pin_problems, social, _doctor_executables(overrides, social)


def _judge_ytdlp(social):
    """O yt-dlp em uso só conta se funciona: a venv dele importa o `yt_dlp` e, sem venv
    em uso, nenhuma venv gerenciada ficou pela metade (`venv_state.ytdlp_problem`)."""
    problem = venv_state.ytdlp_problem(social.get("source"), social.get("executable"))
    if problem:
        social["installed"] = False
        social["problem"] = problem


def doctor_summary(executables, pins=(), data_missing=(), data_unreadable=()):
    """Veredito humano do doctor: o que funciona, o que falta e o que é opcional."""
    ok = sorted(name for name, present in executables.items() if present)
    missing = [
        {"item": name, "fix": REQUIRED_EXECUTABLES[name][0], "note": REQUIRED_EXECUTABLES[name][1]}
        for name in sorted(REQUIRED_EXECUTABLES)
        if not executables.get(name)
    ]
    missing += list(pins)
    # Um `item` por linha: o arquivo que falta e o que não abre têm conserto diferente.
    rows = {
        "Faltam": ("dados da instalação", data_fix()),
        "Sem permissão de leitura": ("dados da instalação (sem permissão)", DATA_PERMISSION_FIX),
    }
    for note in data_problem_notes(data_missing, data_unreadable):
        item, fix = rows[note.partition(":")[0]]
        missing.append({"item": item, "fix": fix, "note": note})
    optional = [
        {"item": name, "note": note} for name, note in sorted(OPTIONAL_EXECUTABLES.items()) if not executables.get(name)
    ]
    optional += [{"item": key, "note": note} for key, note in sorted(OPTIONAL_KEYS.items()) if not os.environ.get(key)]
    return {"ok": ok, "missing": missing, "optional": optional}


PLUGIN_PROBLEM_STATUSES = ("failed", "suspended", "invalid", "incompatible")


def doctor_plugin_problems(rows):
    """Uma linha para o `summary` quando algum plugin não carregou: quem lê
    só o veredito vê a falha sem abrir `plugins[]`. `None` quando está tudo certo."""
    broken = [row for row in rows if row.get("status") in PLUGIN_PROBLEM_STATUSES]
    if not broken:
        return None
    names = ", ".join(f"{row['id']} ({row['status']})" for row in broken)
    label = "plugin com problema" if len(broken) == 1 else "plugins com problema"
    return (
        f"{len(broken)} {label}: {names}. O motivo de cada um está em plugins[] deste doctor; "
        "conserte a pasta do plugin (ou reinstale) e habilite de novo com plugins --action enable."
    )


def doctor_contact_sheet(ffmpeg_present):
    """Whether the contact sheet can be numbered by ffmpeg (drawtext + TrueType font)."""
    from .media import drawtext_available, find_font

    status = {"drawtext": False, "font": None, "labels": False}
    optional = []
    if not ffmpeg_present:
        return {"status": status, "optional": optional}
    status["drawtext"] = drawtext_available()
    try:
        status["font"] = find_font()
    except ValueError as exc:
        optional.append({"item": "GB_FONT_FILE", "note": str(exc)})
    status["labels"] = bool(status["drawtext"] and status["font"])
    if not status["drawtext"]:
        optional.append(
            {
                "item": "drawtext",
                "note": (
                    "FFmpeg sem o filtro drawtext (libfreetype): o contact sheet sai sem número "
                    "e timecode nas células; o Storyboard imprime a legenda de tempos. "
                    "Reinstale o FFmpeg com freetype (Homebrew: brew reinstall ffmpeg; "
                    "Windows: build gyan.dev/BtbN; Ubuntu: apt install ffmpeg)."
                ),
            }
        )
    elif not status["font"]:
        optional.append(
            {
                "item": "font",
                "note": (
                    "Nenhuma fonte TrueType encontrada para rotular o contact sheet; instale "
                    "DejaVu/Liberation/Arial ou defina GB_FONT_FILE."
                ),
            }
        )
    return {"status": status, "optional": optional}


def _local_playwright(root=None):
    """Playwright CLI instalado em `.tools`, ou None quando não há um."""
    if root is not None:
        bin_dir = Path(root) / ".tools/node_modules/.bin"
    else:
        bin_dir = _paths.tools_dir().path / "node_modules/.bin"
    for name in ("playwright-cli.cmd", "playwright-cli"):
        # lenient: tool discovery: try the next candidate name
        if (bin_dir / name).is_file():
            return bin_dir / name
    return None


def _doctor_executables(overrides, social):
    """Presença de cada executável sondado: pin, `PATH` ou o runtime da instalação."""
    from getbrolls.config import TOOL_PATH_KEYS

    executables = {
        name: bool(overrides.get(TOOL_PATH_KEYS.get(name) or "") or shutil.which(name)) for name in PROBED_EXECUTABLES
    }
    executables["yt-dlp"] = social["installed"]
    executables["playwright-cli"] = bool(_local_playwright() or shutil.which("playwright-cli"))
    return executables

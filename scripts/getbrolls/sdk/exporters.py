"""Rodar um exportador de plugin e conferir o que ele devolveu (experimental).

O exportador é uma função pura: recebe cópias do plano e das opções e devolve texto
(`files`) e pedidos de mídia (`media`). Nada aqui escreve em disco nem resolve id de
mídia — isso é do comando que exporta. O que sai daqui só tem tipos do core.
"""

import json
import logging
import re
from pathlib import Path
from typing import NamedTuple

from .. import __version__, logs
from . import guard
from .contracts import CORE, NAME_RE, ExportResult, MediaRequest
from .files import bad_file_name, walk_leaves

_log = logs.get("sdk")

FILE_EXTENSIONS = (".html", ".json", ".css", ".js", ".md", ".txt")
PATH_MAX_CHARS = 240
PATH_MAX_SEGMENTS = 6
FILES_MAX = 200
FILE_MAX_BYTES = 2 * 1024 * 1024
TOTAL_MAX_BYTES = 8 * 1024 * 1024
MEDIA_MAX = 500
MEDIA_ID_MAX_CHARS = 200
NOTES_MAX = 50
ASSETS_DIR = "assets"

# Plano de exemplo que `sdk.testing.check_exporter` entrega ao exportador: cenas de
# vários layouts, mídia de clipe, A-ROLL e componente, legenda e camadas, válido pelo
# `schemas/export_plan.schema.json`. É o mesmo arquivo que a doc mostra a quem escreve plugin.
SAMPLE_PLAN = Path(__file__).resolve().parents[3] / "examples" / "plans" / "reels.plan.json"


def sample_plan():
    """Cópia nova do plano de exemplo, com a versão instalada em `getbrolls_version`."""
    try:
        plan = json.loads(SAMPLE_PLAN.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        root = SAMPLE_PLAN.parents[2]
        shown = SAMPLE_PLAN.relative_to(root).as_posix() if SAMPLE_PLAN.is_relative_to(root) else SAMPLE_PLAN.name
        raise ValueError(f"Não consegui ler o plano de exemplo {shown}: reinstale o get-brolls.") from None
    return {**plan, "getbrolls_version": __version__}


class ValidatedExport(NamedTuple):
    """Resultado do exportador já conferido, só com tipos do core."""

    files: dict[str, str]
    media: tuple[tuple[str, str], ...]  # (media_id, dest)
    notes: tuple[str, ...]


class ExportValidationError(ValueError):
    """O `ExportResult` quebra uma regra; o texto é do core e diz qual."""


def _shown(text):
    return repr(guard.plain_line(text, limit=120))


def _check_path(path, where):
    """Caminho POSIX relativo, curto, com cada pedaço sendo um nome de arquivo simples."""
    if type(path) is not str or not path:
        raise ExportValidationError(f"{where}: o caminho tem que ser texto não vazio.")
    if len(path) > PATH_MAX_CHARS:
        raise ExportValidationError(f"{where}: caminho com mais de {PATH_MAX_CHARS} caracteres.")
    if "\\" in path or path.startswith("/"):
        raise ExportValidationError(f"{where}: {_shown(path)} tem que ser relativo, com / como separador.")
    segments = path.split("/")
    if len(segments) > PATH_MAX_SEGMENTS:
        raise ExportValidationError(f"{where}: {_shown(path)} tem mais de {PATH_MAX_SEGMENTS} níveis.")
    if any(bad_file_name(segment) for segment in segments):
        raise ExportValidationError(
            f"{where}: {_shown(path)} tem um nome inválido; cada parte usa letras, números, '.', '_' ou '-', "
            "sem começar por '.', sem '..', sem terminar em '.' e sem ser um nome reservado do Windows."
        )
    return segments


def _check_text(text, where):
    if "\x00" in text:
        raise ExportValidationError(f"{where}: o texto tem o caractere NUL.")
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ExportValidationError(f"{where}: o texto não é UTF-8 válido.") from None


def _snapshot(value, copy):
    """Cópia rasa (`dict(...)`/`list(...)`) antes de conferir: o que for conferido é o
    que segue adiante, mesmo que o plugin ainda mexa no objeto dele."""
    try:
        return copy(value)
    except (RuntimeError, TypeError):
        raise ExportValidationError("o resultado mudou enquanto era conferido; devolva valores prontos.") from None


def _check_files(files):
    if type(files) is not dict:
        raise ExportValidationError("files tem que ser um dict de caminho para texto.")
    files = _snapshot(files, dict)
    if len(files) > FILES_MAX:
        raise ExportValidationError(f"files passa de {FILES_MAX} arquivos.")
    total = 0
    checked = {}
    for path, text in files.items():
        segments = _check_path(path, "files")
        if segments[0].casefold() == ASSETS_DIR:
            raise ExportValidationError(f"files: {_shown(path)} está em assets/, reservado para a mídia.")
        suffix = Path(segments[-1]).suffix
        if suffix not in FILE_EXTENSIONS:
            raise ExportValidationError(
                f"files: {_shown(path)} tem extensão fora de {', '.join(FILE_EXTENSIONS)} (em minúsculas)."
            )
        if type(text) is not str:
            raise ExportValidationError(f"files: o conteúdo de {_shown(path)} tem que ser texto.")
        size = _check_text(text, f"files: {_shown(path)}")
        if size > FILE_MAX_BYTES:
            raise ExportValidationError(f"files: {_shown(path)} passa de {FILE_MAX_BYTES // (1024 * 1024)} MB.")
        total += size
        if total > TOTAL_MAX_BYTES:
            raise ExportValidationError(f"files passa de {TOTAL_MAX_BYTES // (1024 * 1024)} MB no total.")
        checked[path] = text
    return checked


def _check_media(media):
    if type(media) is not list:
        raise ExportValidationError("media tem que ser uma lista de MediaRequest.")
    media = _snapshot(media, list)
    if len(media) > MEDIA_MAX:
        raise ExportValidationError(f"media passa de {MEDIA_MAX} pedidos.")
    checked = []
    for request in media:
        if type(request) is not MediaRequest:
            raise ExportValidationError("media: cada item tem que ser MediaRequest(media_id, dest).")
        media_id, dest = request.media_id, request.dest
        if type(media_id) is not str or not media_id or len(media_id) > MEDIA_ID_MAX_CHARS:
            raise ExportValidationError(f"media: media_id tem que ser texto de 1 a {MEDIA_ID_MAX_CHARS} caracteres.")
        _check_text(media_id, "media: media_id")
        segments = _check_path(dest, "media")
        if segments[0] != ASSETS_DIR or len(segments) < 2:  # noqa: PLR2004 - "assets/<arquivo>" no mínimo
            raise ExportValidationError(f"media: {_shown(dest)} tem que ficar dentro de assets/.")
        checked.append((media_id, dest))
    return tuple(checked)


def _check_notes(notes, owner):
    if type(notes) is not list:
        raise ExportValidationError("notes tem que ser uma lista de texto.")
    notes = _snapshot(notes, list)
    if len(notes) > NOTES_MAX:
        raise ExportValidationError(f"notes passa de {NOTES_MAX} itens.")
    if any(type(note) is not str for note in notes):
        raise ExportValidationError("notes: cada item tem que ser texto.")
    return tuple(guard.sanitize_text(owner, note) for note in notes)


def _check_collisions(paths):
    """Sem dois caminhos iguais ignorando caixa, e nenhum arquivo servindo de pasta
    para outro (`a.html` e `a.html/b.css`) — em `files`, em `media` e entre os dois."""
    folded = {}
    for path in paths:
        key = path.casefold()
        if key in folded:
            raise ExportValidationError(
                f"{_shown(path)} e {_shown(folded[key])} são o mesmo caminho num disco que não diferencia caixa."
            )
        folded[key] = path
    for path in paths:
        parts = path.casefold().split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in folded:
                raise ExportValidationError(
                    f"{_shown(folded[parent])} é arquivo e pasta de {_shown(path)} ao mesmo tempo."
                )


def validate_export_result(result, owner=CORE):
    """`ValidatedExport` com só tipos do core, ou `ExportValidationError` com a regra quebrada.

    Tipos exatos (`type(...) is`), cada atributo lido uma vez: conferir nunca roda
    código do plugin. O conteúdo dos arquivos não é saneado — escapar o texto do
    plano no formato de saída é trabalho do exportador."""
    if type(result) is not ExportResult:
        raise ExportValidationError("o exportador tem que devolver ExportResult(files, media, notes).")
    files, media, notes = result.files, result.media, result.notes
    checked_files = _check_files(files)
    checked_media = _check_media(media)
    checked_notes = _check_notes(notes, owner)
    _check_collisions([*checked_files, *(dest for _, dest in checked_media)])
    return ValidatedExport(checked_files, checked_media, checked_notes)


NOTE_LABEL = "Nota do plugin"


def note_line(owner, text):
    """Uma nota do exportador pronta para mostrar: uma linha, sem marcação ativa
    (link, imagem e ênfase saem escapados) e com o prefixo que diz de qual plugin veio.
    `ValidatedExport.notes` já vem numa linha, mas com a marcação como o plugin escreveu."""
    from ..delivery import inert

    clean = guard.sanitize_text(owner, str(text))
    return f"{NOTE_LABEL} {owner}: {inert(clean, plugin=owner)}"


def _json_copy(value, what):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise ValueError(f"{what} do export não é JSON válido.") from None


def _unavailable(registry, name):
    """Mensagem de exportador que não pode rodar agora: inexistente, ou de um plugin
    instalado que não está carregado (com a dica certa, inclusive fora de GB_PLUGINS)."""
    from . import loader

    row = next(
        (row for row in registry.plugins.values() if name in (row.get("contributes") or {}).get("exporters", [])),
        None,
    ) or loader.declared_by(name, "exporters")
    if row is None:
        return f"Exportador {name} não existe. Rode plugins --action list para ver os plugins instalados."
    hint = loader.status_hint(row, "Rode plugins --action list / doctor.")
    return f"Exportador {name} é do plugin {row['id']}, {loader.status_phrase(row)}. {hint}"


def run_exporter(registry, name, plan, options):
    """Roda o exportador `name` sobre cópias de `plan`/`options` e devolve o resultado conferido.

    Qualquer falha — exceção do plugin (inclusive `SystemExit`) ou resultado fora das
    regras — vira `ValueError` com `Plugin <id>: …`: o texto exato de um `PluginError`,
    só o tipo de qualquer outra exceção."""
    if type(name) is not str or not NAME_RE.fullmatch(name):
        raise ValueError("Nome de exportador inválido; use 2–32 caracteres a-z, 0-9 e _.")
    spec = registry.exporter(name)
    owner = registry.owner("exporter", name)
    if spec is None or owner is None:
        raise ValueError(_unavailable(registry, name))
    if owner != CORE and (registry.plugins.get(owner) or {}).get("status") != "enabled":
        raise ValueError(_unavailable(registry, name))
    plan_copy = _json_copy(plan, "O plano")
    options_copy = _json_copy(options, "As opções")
    result = guard.isolated(
        owner,
        spec.export,
        plan_copy,
        options_copy,
        log_fields={"exporter": name},
        on_failure=lambda failure: ValueError(
            guard.prefixed(owner, failure.text)
            if failure.text
            else f"Plugin {owner}: o exportador {name} falhou ({failure.type_name})."
        ),
    )
    try:
        validated = validate_export_result(result, owner)
    except ExportValidationError as exc:
        logs.event(_log, logging.WARNING, "plugin_export_refused", plugin=owner, exporter=name)
        raise ValueError(f"Plugin {owner}: o exportador {name} devolveu um resultado fora das regras: {exc}") from None
    logs.event(
        _log,
        logging.INFO,
        "plugin_export",
        plugin=owner,
        exporter=name,
        files=len(validated.files),
        media=len(validated.media),
    )
    return validated


# Marcas de caminho local: pasta pessoal de macOS/Linux, pastas temporárias, letra de
# unidade do Windows e variáveis que viram a pasta pessoal.
_LOCAL_PATH_RE = re.compile(
    r"(?<![\w.~-])/(?:Users|home|root|private|var/folders|tmp)/"
    r"|(?<![A-Za-z])[A-Za-z]:[\\/]"
    r"|\$HOME\b|%USERPROFILE%"
    r"|(?<![\w.])~[\\/]"
)

# Separador de pasta em qualquer sistema: a pasta pessoal casa escrita com `/` ou com `\`.
_SEPARATORS = re.compile(r"[\\/]")


def find_local_paths(value, where="$"):
    """Onde, dentro de `value` (dict/list/texto, como um plano ou os `files` de um
    export), aparece algo com cara de caminho local absoluto. Devolve a lista de
    posições (`$.files['index.html']`); vazia quando não há nenhum. Export é para
    compartilhar: caminho local vaza nome de usuário e pastas."""
    home_re = None
    try:
        home = str(Path.home()).rstrip("/\\")
    except RuntimeError:
        home = ""
    if len(home) > 1:
        # A pasta pessoal inteira, sem caixa: mesma regra de `_path_re` em export.py (não casa dentro
        # de um nome maior, como `/root` em "rootless"; o ponto final de uma frase logo depois ainda
        # casa). `/` e `\` valem igual: no Windows a pasta vem com `\` e o texto costuma ter `/`.
        body = r"[\\/]".join(re.escape(part) for part in _SEPARATORS.split(home))
        home_re = re.compile(r"(?<![\w~-])" + body + r"(?![\w-]|\.\w)", re.IGNORECASE)
    found = [
        position
        for position, item in walk_leaves(value, where)
        if type(item) is str and (_LOCAL_PATH_RE.search(item) or (home_re and home_re.search(item)))
    ]
    return sorted(found)

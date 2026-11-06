"""Exemplo do SDK do getbrolls: o exportador `exporter_example`, uma lista das cenas do roteiro revisado.

O plano de export do core (um dict, `export_version` 2) vira dois arquivos de texto: `cortes.csv`, uma
linha por cena (início, duração, layout, título e fala; UTF-8 com BOM e `;` como separador, o que o
Excel em português abre direito), e `EXPORT.md`, com o tema, as cenas e as
pendências do plano. É uma função pura: não abre arquivo, não usa rede, não lê relógio e não pede mídia;
quem grava a pasta em exports/<exporter>/<NNN>/ é o core.

Todo texto do plano é dado não confiável. No CSV, cada célula fica numa linha só e, se começar por `=`,
`+`, `-` ou `@`, ganha um apóstrofo na frente, para a planilha não a ler como fórmula. No Markdown, os
caracteres de marcação saem escapados. Sequências de espaço, tab e quebra de linha viram um espaço só.
"""

import csv
import io

from getbrolls.sdk import ExportResult, PluginError

EXPORT_VERSION = 2
COLUMNS = ("cena", "inicio_s", "duracao_s", "layout", "titulo", "fala")
FORMULA_START = ("=", "+", "-", "@")
BOM = "\ufeff"
DELIMITER = ";"
MARKDOWN_SPECIAL = frozenset("\\`*_{}[]()#+-.!|<>")


def _one_line(value):
    """O texto numa linha só: quebras e tabs viram um espaço."""
    return " ".join(str("" if value is None else value).split())


def _cell(value):
    """Uma célula de CSV que nunca é fórmula nem quebra a linha."""
    text = _one_line(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def _md(value):
    """Texto do plano dentro do Markdown, com a marcação escapada."""
    return "".join("\\" + char if char in MARKDOWN_SPECIAL else char for char in _one_line(value))


def _seconds(value):
    return f"{float(value or 0):.2f}"


def _csv(scenes):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=DELIMITER, lineterminator="\n")
    writer.writerow(COLUMNS)
    for scene in scenes:
        writer.writerow(
            (
                _cell(scene["id"]),
                _seconds(scene["start_s"]),
                _seconds(scene["duration_s"]),
                _cell(scene["layout"]["kind"]),
                _cell(scene["title"]),
                _cell(scene["speech_clean"]),
            )
        )
    return BOM + buffer.getvalue()


def _export_md(plan):
    scenes = plan["scenes"]
    lines = [
        f"# Cortes: {_md(plan['meta']['tema'])}",
        "",
        f"{len(scenes)} cena(s), {_seconds(plan['total_s'])} s no total. A lista está em `cortes.csv`.",
        "",
        "## Cenas",
        "",
        *(f"- {_md(scene['id'])} · {_md(scene['title'])} · {_seconds(scene['duration_s'])} s" for scene in scenes),
    ]
    if plan["warnings"]:
        lines += ["", "## Pendências", "", *(f"- {_md(warning)}" for warning in plan["warnings"])]
    return "\n".join(lines) + "\n"


def cortes(plan, options):
    """`cortes.csv` e `EXPORT.md` a partir do plano; `PluginError` para um `export_version` desconhecido."""
    del options  # reservado ao contrato ({"args": {}}); este exemplo não tem opção
    if plan.get("export_version") != EXPORT_VERSION:
        raise PluginError(f"export_version {plan.get('export_version')} não é suportado pelo exemplo cortes.")
    return ExportResult(files={"cortes.csv": _csv(plan["scenes"]), "EXPORT.md": _export_md(plan)})


def register(api):
    """Registra o exportador `exporter_example`."""
    api.exporter("exporter_example", cortes, "Lista de cortes em CSV e um EXPORT.md com as cenas e as pendências")

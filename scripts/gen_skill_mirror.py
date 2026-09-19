#!/usr/bin/env python3
"""Gera `skills/get-brolls/SKILL.md` a partir do `SKILL.md` da raiz.

O `SKILL.md` da raiz é a fonte canônica do fluxo clone-como-skill. O espelho
em `skills/get-brolls/` é o que o plugin do Claude Code descobre; o diff real
entre os dois arquivos é fechado em três regras:

1. o frontmatter é copiado literalmente;
2. a única linha `<!-- ... -->` do corpo é trocada pela linha fixa do
   espelho, que aponta de volta para a raiz;
3. todo caminho relativo do corpo que exista como arquivo no repositório
   recebe o prefixo `${CLAUDE_PLUGIN_ROOT}/`, com guarda de idempotência
   para não prefixar duas vezes. É o `exists()` contra a raiz do repositório
   que impede `BRIEF.md`/`RULES.md` — citados como nome de arquivo, não como
   caminho — de serem prefixados: nenhum dos dois existe na raiz do repo.

Sem dependências externas (stdlib). Uso:

    python3 scripts/gen_skill_mirror.py            # escreve o espelho
    python3 scripts/gen_skill_mirror.py --check     # só compara, sai 1 se divergir

`--check` não escreve nada; imprime um diff unificado e sai com código 1
quando o espelho versionado está fora de sincronia com o que o gerador
produziria.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_SKILL = ROOT / "SKILL.md"
MIRROR_SKILL = ROOT / "skills" / "get-brolls" / "SKILL.md"

FRONTMATTER_RE = re.compile(r"\A(---\n.*?\n---\n)(.*)\Z", re.DOTALL)

# A única linha de comentário HTML do corpo: no arquivo canônico ela descreve
# o espelho; no espelho ela aponta de volta para a raiz.
SYNC_COMMENT_RE = re.compile(r"^<!--.*-->$", re.MULTILINE)
MIRROR_SYNC_COMMENT = (
    "<!-- Gerado a partir do SKILL.md da raiz (fonte canônica do fluxo "
    "clone-como-skill). Ao editar um, sincronize o outro. -->"
)

# Caminho relativo citado no corpo: começa em letra/dígito/`_`, segue com
# esses caracteres mais `.`, `/` e `-`, e termina em `.extensão`. A guarda de
# idempotência (lookbehind negativo) evita prefixar de novo um caminho que já
# viesse com `${CLAUDE_PLUGIN_ROOT}/` no arquivo de origem.
PLUGIN_PREFIX = "${CLAUDE_PLUGIN_ROOT}/"
RELATIVE_PATH_RE = re.compile(r"(?<!\$\{CLAUDE_PLUGIN_ROOT\}/)([\w][\w./\-]*\.[A-Za-z0-9]+)")

# Pastas de topo conhecidas do repositório: um candidato que comece com uma
# delas parece um caminho real do repo (não um nome de arquivo solto como
# `BRIEF.md`), então precisa existir — senão é um caminho quebrado escrito no
# SKILL.md da raiz, e mascará-lo deixando sem prefixo seria pior que falhar.
KNOWN_TOP_LEVEL_DIRS = (
    "scripts/",
    "references/",
    "docs/",
    "commands/",
    "assets/",
    "schemas/",
    "eval/",
)


def split_frontmatter(text: str) -> tuple[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{ROOT_SKILL}: sem frontmatter reconhecível")
    return match.group(1), match.group(2)


def replace_sync_comment(body: str) -> str:
    matches = SYNC_COMMENT_RE.findall(body)
    if len(matches) != 1:
        raise ValueError(
            f"{ROOT_SKILL}: esperava exatamente 1 linha de comentário de sincronia no corpo, achou {len(matches)}"
        )
    return SYNC_COMMENT_RE.sub(MIRROR_SYNC_COMMENT, body, count=1)


class BrokenRepoPathError(ValueError):
    """Um candidato parece caminho do repositório (começa com pasta de topo
    conhecida) mas não existe nem como arquivo nem como pasta."""


def add_plugin_prefix(body: str) -> str:
    def repl(match: re.Match[str]) -> str:
        candidate = match.group(1)
        target = ROOT / candidate
        if target.is_file():
            return PLUGIN_PREFIX + candidate
        if candidate.startswith(KNOWN_TOP_LEVEL_DIRS) and not target.exists():
            raise BrokenRepoPathError(
                f'{display(ROOT_SKILL)}: caminho "{candidate}" parece apontar para o '
                "repositório mas não existe (nem arquivo, nem pasta)."
            )
        return candidate

    return RELATIVE_PATH_RE.sub(repl, body)


def render_mirror(root_text: str) -> str:
    frontmatter, body = split_frontmatter(root_text)
    # Prefixa antes de trocar o comentário: a linha original de sincronia
    # também cita `SKILL.md` e não deve ganhar prefixo — só a linha fixa do
    # espelho, que substitui a original inteira depois.
    body = add_plugin_prefix(body)
    body = replace_sync_comment(body)
    return frontmatter + body


def display(path: Path) -> str:
    """Caminho relativo à raiz do repositório para mensagens; cai no caminho
    absoluto quando `path` está fora dela (ex.: testes com destino em `tmp`)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="só compara o espelho versionado com o gerado; não escreve nada",
    )
    args = parser.parse_args(argv)

    root_text = ROOT_SKILL.read_text(encoding="utf-8")
    try:
        mirror_text = render_mirror(root_text)
    except BrokenRepoPathError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.check:
        current = MIRROR_SKILL.read_text(encoding="utf-8") if MIRROR_SKILL.exists() else ""
        if current == mirror_text:
            print(f"{display(MIRROR_SKILL)} está em sincronia com {display(ROOT_SKILL)}.")
            return 0
        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            mirror_text.splitlines(keepends=True),
            fromfile=display(MIRROR_SKILL),
            tofile=f"{display(MIRROR_SKILL)} (gerado)",
        )
        sys.stdout.writelines(diff)
        return 1

    MIRROR_SKILL.parent.mkdir(parents=True, exist_ok=True)
    MIRROR_SKILL.write_text(mirror_text, encoding="utf-8")
    print(f"{display(MIRROR_SKILL)} gerado a partir de {display(ROOT_SKILL)}.")
    return 0


def _utf8_output() -> None:
    """Print Portuguese text as UTF-8 even where the console default is a legacy code page."""
    for stream in (sys.stdout, sys.stderr):
        # TextIO does not declare `reconfigure`; streams without it fall into the except.
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]


if __name__ == "__main__":
    _utf8_output()
    sys.exit(main())

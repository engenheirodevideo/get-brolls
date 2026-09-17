#!/usr/bin/env python3
"""Valida que toda âncora `GUIDE.md#...` citada nos docs do plugin resolve para
um heading real de `docs/GUIDE.md`.

Sem dependências externas (stdlib). Uso:

    python3 scripts/check_anchors.py

Sai com código 0 e uma linha de confirmação se tudo resolver; código 1 e a
lista de âncoras quebradas caso contrário.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE_PATH = ROOT / "docs" / "GUIDE.md"

# Arquivos onde âncoras GUIDE.md#... aparecem e precisam resolver.
TARGET_FILES = [
    ROOT / "SKILL.md",
    ROOT / "skills" / "get-brolls" / "SKILL.md",
    ROOT / "README.md",
    ROOT / "README.en.md",
    *sorted((ROOT / "commands").glob("*.md")),
]

# Casa `GUIDE.md#anchor` dentro de qualquer link/texto (ex.: `docs/GUIDE.md#instalação)`,
# `${CLAUDE_PLUGIN_ROOT}/docs/GUIDE.md#storyboard)`), parando no primeiro delimitador.
ANCHOR_REF_RE = re.compile(r"GUIDE\.md#([^\s)\"'\]`]+)")

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def slugify(heading: str) -> str:
    """Replica o slug de heading do GitHub: minúsculas, remove pontuação
    (mantém letras acentuadas, dígitos, `-`/`_`), espaço vira hífen."""
    text = heading.strip().lower()
    text = text.replace("`", "")
    text = re.sub(r"[^\w\s-]", "", text)
    text = text.replace(" ", "-")
    return text


def extract_headings(markdown: str) -> set[str]:
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    in_fence = False
    for line in markdown.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if not match:
            continue
        slug = slugify(match.group(2))
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 0
        slugs.add(slug)
    return slugs


def find_anchor_refs(markdown: str) -> list[str]:
    return ANCHOR_REF_RE.findall(markdown)


def check() -> list[str]:
    problems: list[str] = []
    if not GUIDE_PATH.exists():
        return [f"{GUIDE_PATH}: arquivo não encontrado"]
    guide_slugs = extract_headings(GUIDE_PATH.read_text(encoding="utf-8"))
    for path in TARGET_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for anchor in find_anchor_refs(text):
            if anchor not in guide_slugs:
                problems.append(f"{path.relative_to(ROOT)}: âncora quebrada GUIDE.md#{anchor}")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("Âncoras quebradas encontradas:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("Todas as âncoras GUIDE.md#... citadas resolvem para um heading.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

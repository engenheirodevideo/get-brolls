"""`ROTEIRO.md`: o conteúdo do vídeo como roteiro — cenas, diretivas e fala.

O arquivo é da pessoa. Este módulo só lê e valida; quem grava ids e status é
`roteiro_sync`. O frontmatter aceita um subconjunto fechado de YAML (uma linha
`chave: valor`, aspas duplas opcionais): tudo o que o YAML aceita além disso é
recusado com a linha, nunca "interpretado".
"""

import re
import unicodedata
from pathlib import Path

ROTEIRO_FILE = "ROTEIRO.md"
STATUSES = ("draft", "revisado")
ASPECT_TO_FORMAT = {"9:16": "reels", "16:9": "horizontal"}
# Gênero é eixo de conteúdo; proporção de tela é `aspecto`. Nunca misturar com FORMATS.
GENRES = {
    "reels": {
        "aspecto": "9:16",
        "cenas": (
            ("Gancho", "[A-ROLL]", "<gancho: a frase que segura nos 3 primeiros segundos>"),
            ("Problema", "[BROLL: <o que a pessoa vê enquanto você fala>]", "<a dor, em uma frase>"),
            ("Prova", "[SPLIT: <tela ou b-roll> | A-ROLL]", "<o que prova que funciona>"),
            ("CTA", "[FULL: logo]", "<o que a pessoa faz agora>"),
        ),
    },
}
KEYS = ("type", "genero", "aspecto", "duracao_alvo_s", "tema", "legenda", "status")
REQUIRED = ("type", "genero", "tema")
_PAIR = re.compile(r"^([a-z_]+):(.*)$")


class RoteiroError(ValueError):
    """Erros de leitura do roteiro, todos de uma vez, cada um com a linha (1-based)."""

    def __init__(self, errors):
        self.errors = list(errors)
        lines = [f"linha {n}: {msg}" if n else msg for n, msg in self.errors]
        super().__init__("ROTEIRO.md com problema:\n" + "\n".join(lines))


def fold(text):
    """Chave de comparação: minúsculas, sem acento, espaços colapsados."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.lower().split())


def roteiro_path(project):
    return Path(project).expanduser().resolve() / ROTEIRO_FILE


def _scalar(raw):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':  # noqa: PLR2004 - opening + closing quote
        return value[1:-1].replace('\\"', '"'), True
    return value, False


def _field(key, value, quoted, meta):  # noqa: C901, PLR0911 - one return per field rule
    if key == "type":
        return None if value == "roteiro" else '"type" tem que ser roteiro'
    if key == "genero":
        if value not in GENRES:
            return f'"genero" aceita: {", ".join(GENRES)}'
        meta[key] = value
        return None
    if key == "aspecto":
        if value not in ASPECT_TO_FORMAT:
            return f'"aspecto" aceita: {", ".join(ASPECT_TO_FORMAT)}'
        meta[key] = value
        return None
    if key == "duracao_alvo_s":
        if quoted or not value.isdigit() or not 5 <= int(value) <= 600:  # noqa: PLR2004 - spec bounds
            return '"duracao_alvo_s" tem que ser um inteiro de 5 a 600'
        meta[key] = int(value)
        return None
    if key == "legenda":
        if value not in ("true", "false"):
            return '"legenda" tem que ser true ou false'
        meta[key] = value == "true"
        return None
    if key == "status":
        if value not in STATUSES:
            return f'"status" aceita: {", ".join(STATUSES)} (quem muda é o comando, não a mão)'
        meta[key] = value
        return None
    if not value:
        return f'"{key}" precisa de um valor'
    meta[key] = value
    return None


def parse_frontmatter(lines):  # noqa: C901 - existing size; one check per frontmatter line kind
    """Devolve (meta, índice da primeira linha do corpo, erros)."""
    if not lines or lines[0].strip() != "---":
        return {}, 0, [(1, 'o roteiro começa com o frontmatter entre linhas "---"')]
    meta, errors, seen = {}, [], set()
    for index in range(1, len(lines)):
        line = lines[index]
        number = index + 1
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace() or line.lstrip().startswith("-"):
            errors.append((number, "o frontmatter não aceita lista nem bloco: use uma linha chave: valor"))
            continue
        match = _PAIR.match(line)
        if not match:
            errors.append((number, "use chave: valor"))
            continue
        key = match.group(1)
        value, quoted = _scalar(match.group(2))
        if key not in KEYS:
            errors.append((number, f'chave desconhecida "{key}"; aceitas: {", ".join(KEYS)}'))
            continue
        if key in seen:
            errors.append((number, f'chave "{key}" repetida'))
            continue
        seen.add(key)
        if not value:
            errors.append((number, f'"{key}" precisa de um valor'))
            continue
        problem = _field(key, value, quoted, meta)
        if problem:
            errors.append((number, problem))
    else:
        return meta, len(lines), [*errors, (len(lines), 'falta a linha "---" que fecha o frontmatter')]
    errors.extend((1, f'falta "{key}" no frontmatter') for key in REQUIRED if key not in seen)
    if "genero" in meta:
        meta.setdefault("aspecto", GENRES[meta["genero"]]["aspecto"])
    meta.setdefault("legenda", True)
    meta.setdefault("status", "draft")
    return meta, index + 1, errors

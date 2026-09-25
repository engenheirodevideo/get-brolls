"""`ROTEIRO.md`: o conteúdo do vídeo como roteiro — cenas, diretivas e fala.

O arquivo é da pessoa. Este módulo só lê e valida; quem grava ids e status é
`roteiro_sync`. O frontmatter aceita um subconjunto fechado de YAML (uma linha
`chave: valor`, aspas duplas opcionais): tudo o que o YAML aceita além disso é
recusado com a linha, nunca "interpretado".
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .brief import MAX_HINT_S
from .sdk.contracts import NAME_RE

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

LAYOUTS = ("A-ROLL", "BROLL", "SPLIT", "FULL", "UGC")
LAYERS = ("LETTERING", "SFX", "MUSICA", "COMP")
PRESENTER = ("A-ROLL", "UGC")
WORDS_PER_S = 2.5
MIN_SPOKEN_S = 1.5
SILENT_SCENE_S = 2.0
SCENE_ID_RE = re.compile(r"c\d{2,3}")
SCENE_BEAT_RE = re.compile(r"c\d{2,3}(-[ab])?")
_HEADING = re.compile(r"^##\s+(.+?)\s*(?:<!--\s*(\S+?)\s*-->)?\s*$")
_WHOLE = re.compile(r"^\[([^\[\]]+)\]$")
_NOTE = re.compile(r"\[[^\[\]]*\]")
_EXT = re.compile(r"^([a-z][a-z0-9_]{1,31}):([a-z0-9][a-z0-9-]*)(?::(.*))?$")
# Chave = maiúsculas sem acento e sem nada que não seja letra ou número.
_SYNONYMS = {
    "AROLL": "A-ROLL", "APRESENTADOR": "A-ROLL", "TALKINGHEAD": "A-ROLL",
    "BROLL": "BROLL",
    "SPLIT": "SPLIT", "FRAMESPLIT": "SPLIT", "SPLITSCREEN": "SPLIT",
    "FULL": "FULL", "FULLSCREEN": "FULL",
    "UGC": "UGC",
    "LETTERING": "LETTERING", "HEADLINE": "LETTERING", "TEXTO": "LETTERING",
    "SFX": "SFX",
    "MUSICA": "MUSICA", "TRILHA": "MUSICA", "MUSIC": "MUSICA",
    "COMP": "COMP", "COMPOSICAO": "COMP",
}  # fmt: skip
# Quantos argumentos cada diretiva aceita: (mínimo, máximo).
_ARITY = {
    "A-ROLL": (0, 0), "BROLL": (1, 1), "SPLIT": (2, 2), "FULL": (1, 1), "UGC": (1, 1),
    "LETTERING": (1, 2), "SFX": (1, 1), "MUSICA": (1, 1), "COMP": (1, 1),
}  # fmt: skip


@dataclass(frozen=True)
class Directive:
    kind: str
    args: tuple[str, ...]
    line: int
    plugin: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Scene:
    title: str
    scene_id: str | None
    line: int
    layout: Directive
    layers: tuple[Directive, ...]
    extensions: tuple[Directive, ...]
    speech: str
    duration_s: float
    over_cap: bool


@dataclass(frozen=True)
class Roteiro:
    meta: dict
    scenes: tuple[Scene, ...]
    warnings: tuple[str, ...]


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
        if quoted or not (value.isascii() and value.isdigit()) or not 5 <= int(value) <= 600:  # noqa: PLR2004 - spec bounds
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


def parse_frontmatter(lines):  # noqa: C901 - one branch per grammar rule
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


def canonical(keyword):
    key = re.sub(r"[^A-Z0-9]", "", fold(keyword).upper())
    return _SYNONYMS.get(key)


def estimate(speech):
    """Segundos falados (~150 palavras/min em pt-BR); notas `[...]` não contam."""
    words = len(re.findall(r"\w+", _NOTE.sub(" ", speech)))
    if not words:
        return SILENT_SCENE_S, False
    seconds = round(max(MIN_SPOKEN_S, words / WORDS_PER_S), 1)
    if seconds > MAX_HINT_S:
        return float(MAX_HINT_S), True
    return seconds, False


def _split_args(raw):
    """Divide em `|` fora de aspas; `\\"` é aspa literal dentro do texto."""
    parts, current, quoted, index = [], [], False, 0
    while index < len(raw):
        ch = raw[index]
        if ch == "\\" and raw[index + 1 : index + 2] == '"':
            current.append('\\"')
            index += 2
            continue
        if ch == '"':
            quoted = not quoted
        if ch == "|" and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        index += 1
    parts.append("".join(current).strip())
    return [p for p in parts if p] if any(parts) else []


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] == '"':  # noqa: PLR2004 - opening + closing quote
        return value[1:-1].replace('\\"', '"'), True
    return value.replace('\\"', '"'), False


def _directive(inner, number):  # noqa: PLR0911 - one return per validation rule
    """Devolve (Directive | None, erro | None) para o miolo de `[...]` numa linha inteira."""
    ext = _EXT.match(inner.strip())
    if ext and NAME_RE.fullmatch(ext.group(1)) and canonical(ext.group(1)) is None:
        extra = tuple(a for a in (ext.group(3) or "").split("|") if a.strip())
        return Directive("EXT", tuple(a.strip() for a in extra), number, ext.group(1), ext.group(2)), None
    head, _, rest = inner.partition(":")
    kind = canonical(head)
    if kind is None:
        options = [*_SYNONYMS.values()]
        guess = difflib.get_close_matches(re.sub(r"[^A-Z0-9-]", "", fold(head).upper()), options, n=1)
        hint = f" — quis dizer {guess[0]}?" if guess else ("; nota de cena vai no meio da fala, não sozinha na linha")
        return None, f'"[{inner}]" não é uma diretiva{hint}'
    raw_args = _split_args(rest)
    low, high = _ARITY[kind]
    if not low <= len(raw_args) <= high:
        if kind == "SPLIT":
            return None, "SPLIT precisa de dois lados: [SPLIT: esquerda | direita]"
        if high == 0:
            return None, f"{kind} não leva argumento"
        return None, f"{kind} precisa de um alvo: [{kind}: ...]"
    args = []
    for position, value in enumerate(raw_args):
        text, quoted = _unquote(value)
        if kind == "LETTERING" and position == 0 and not quoted:
            return None, 'o texto do LETTERING vai entre aspas: [LETTERING: "texto" | estilo]'
        args.append(text if kind == "LETTERING" and position == 0 else value.replace('\\"', '"'))
    return Directive(kind, tuple(args), number), None


def _close(scene, errors):
    if scene is None:
        return None
    layouts = [d for d in scene["directives"] if d.kind in LAYOUTS]
    if not layouts:
        errors.append((scene["line"], f'a cena "{scene["title"]}" precisa de um layout: {", ".join(LAYOUTS)}'))
        return None
    if len(layouts) > 1:
        errors.append((layouts[1].line, "mais de um layout na mesma cena; separe em duas cenas"))
        return None
    speech = "\n".join(scene["speech"]).strip()
    duration, over = estimate(speech)
    return Scene(
        title=scene["title"],
        scene_id=scene["id"],
        line=scene["line"],
        layout=layouts[0],
        layers=tuple(d for d in scene["directives"] if d.kind in LAYERS),
        extensions=tuple(d for d in scene["directives"] if d.kind == "EXT"),
        speech=speech,
        duration_s=duration,
        over_cap=over,
    )


def parse(text):  # noqa: C901, PLR0912 - one branch per line kind
    lines = text.replace("\r\n", "\n").split("\n")
    meta, start, errors = parse_frontmatter(lines)
    scenes, current, ids = [], None, {}
    for index in range(start, len(lines)):
        number = index + 1
        line = lines[index]
        stripped = line.strip()
        heading = _HEADING.match(line)
        if heading:
            scene = _close(current, errors)
            if scene:
                scenes.append(scene)
            scene_id = heading.group(2)
            if scene_id is not None and not SCENE_ID_RE.fullmatch(scene_id):
                errors.append((number, f'id de cena "{scene_id}" inválido: use c01, c02… (até c999)'))
                scene_id = None
            if scene_id is not None:
                if scene_id in ids:
                    message = (
                        f"id {scene_id} repetido nas linhas {ids[scene_id]} e {number}: "
                        "apague o comentário da cópia e rode o sync para ganhar um id novo"
                    )
                    errors.append((number, message))
                ids.setdefault(scene_id, number)
            current = {"title": heading.group(1), "id": scene_id, "line": number, "directives": [], "speech": []}
            continue
        if current is None:
            if stripped and not (stripped.startswith("# ") and not stripped.startswith("##")):
                errors.append((number, "texto fora de cena: toda fala vai depois de um título ## Cena"))
            continue
        whole = _WHOLE.match(stripped)
        if whole:
            directive, problem = _directive(whole.group(1), number)
            if problem:
                errors.append((number, problem))
            elif directive:
                current["directives"].append(directive)
            continue
        if stripped.startswith("###"):
            continue  # subtítulo é nota da pessoa: não é fala nem diretiva
        current["speech"].append(line.rstrip())
    scene = _close(current, errors)
    if scene:
        scenes.append(scene)
    if errors:
        raise RoteiroError(sorted(errors))
    warnings = [
        f'linha {s.line}: a cena "{s.title}" passa de {int(MAX_HINT_S)} s — divida a cena' for s in scenes if s.over_cap
    ]
    total = round(sum(s.duration_s for s in scenes), 1)
    target = meta.get("duracao_alvo_s")
    if target and total > target:
        warnings.append(f"duração estimada {total} s passa do duracao_alvo_s ({target} s)")
    return Roteiro(meta=meta, scenes=tuple(scenes), warnings=tuple(warnings))

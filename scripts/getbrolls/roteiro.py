"""`ROTEIRO.md`: o conteúdo do vídeo como roteiro — cenas, diretivas e fala.

O arquivo é da pessoa. Este módulo só lê e valida; quem grava ids e status é
`roteiro_sync`. O frontmatter aceita um subconjunto fechado de YAML (uma linha
`chave: valor`, aspas opcionais): tudo o que o YAML aceita além disso é recusado
com a linha, nunca "interpretado". Texto escondido (comentário HTML) é erro: a
revisão humana tem que ver tudo o que vira beat.
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass, replace
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
END_ANCHOR = "fim"
# c01…c999; nunca c00/c000 e só dígitos ASCII (`\d` aceitaria "c٠١").
SCENE_ID_RE = re.compile(r"c(?!0+$)[0-9]{2,3}")
SCENE_BEAT_RE = re.compile(r"c(?!0+(?:-|$))[0-9]{2,3}(?:-[ab])?")
_WHOLE = re.compile(r"^\[([^\[\]]+)\]$")
# Nota de cena no meio da fala: `[risos]`. Não é link (`[x](url)`), wikilink
# (`[[x]]`) nem checkbox; sem quantificador aninhado (linear em linha longa).
_NOTE = re.compile(r"(?<!\[)\[([^\[\]\n]+)\](?![\](])")
_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[[ xX]\]\s*")
_PLACEHOLDER = re.compile(r"<[^<>\n]{1,200}>")
_WORD = re.compile(r"\w+")
_TAKE = re.compile(r"[a-z0-9][a-z0-9_-]{0,19}")
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
    "A-ROLL": (0, 1), "BROLL": (1, 1), "SPLIT": (2, 2), "FULL": (1, 1), "UGC": (1, 1),
    "LETTERING": (1, 2), "SFX": (1, 1), "MUSICA": (1, 1), "COMP": (1, 1),
}  # fmt: skip


@dataclass(frozen=True)
class Directive:
    kind: str
    args: tuple[str, ...]
    line: int
    plugin: str | None = None
    name: str | None = None
    # Um por argumento: o valor veio entre aspas (cartela de texto, não alvo de busca).
    quoted: tuple[bool, ...] = ()
    # Índice (0-based) da linha de fala que vem logo depois, ou "fim"; e quantas
    # palavras faladas a cena já teve antes dela. O exporter ancora o tempo nisso.
    anchor: int | str | None = None
    word_offset: int | None = None


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
    speech_clean: str
    notes: tuple[str, ...]


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


def load_text(project):
    """Texto do ROTEIRO.md sem BOM e com fim de linha `\\n`; erro claro quando ele não existe."""
    path = roteiro_path(project)
    if not path.is_file():
        raise ValueError("Este projeto não tem ROTEIRO.md. Crie com `roteiro --action new --genero reels --tema ...`.")
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def enabled_plugins():
    """Ids de plugin habilitados, pelo inventário pré-carga (manifesto e pin): nenhum código de plugin roda."""
    from .sdk import loader

    try:
        return frozenset(row["id"] for row in loader.inventory() if row["status"] == "enabled")
    except (ValueError, OSError):
        return frozenset()


def _unquote(value):
    """Tira aspas duplas ou simples que envolvem o valor inteiro; `\\"` vira aspa literal."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":  # noqa: PLR2004 - opening + closing quote
        return value[1:-1].replace('\\"', '"'), True
    return value.replace('\\"', '"'), False


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


def parse_frontmatter(lines):  # noqa: C901, PLR0912 - one branch per grammar rule
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
        value, quoted = _unquote(match.group(2).strip())
        if key not in KEYS:
            errors.append((number, f'chave desconhecida "{key}"; aceitas: {", ".join(KEYS)}'))
            continue
        if key in seen:
            errors.append((number, f'chave "{key}" repetida'))
            continue
        seen.add(key)
        if not quoted and (value.startswith("#") or " #" in value):
            message = f'"{key}": comentário no fim da linha não vale aqui; ponha o # numa linha própria'
            errors.append((number, message + " (ou use aspas se ele faz parte do valor)"))
            continue
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


def strip_notes(line):
    """A linha sem as notas de cena `[...]`, com os espaços colapsados; checkbox e link ficam."""
    box = _CHECKBOX.match(line)
    head = box.group(0) if box else ""
    rest = _NOTE.sub(lambda m: " " if m.group(1).strip() else m.group(0), line[len(head) :])
    return (head + " ".join(rest.split())).strip()


def inline_notes(line):
    """Notas de cena no meio da fala, na ordem, sem os colchetes."""
    box = _CHECKBOX.match(line)
    rest = line[box.end() :] if box else line
    return [m.strip() for m in _NOTE.findall(rest) if m.strip()]


def _spoken_words(text):
    return len(_WORD.findall("\n".join(strip_notes(line) for line in text.split("\n"))))


def estimate(speech):
    """Segundos falados (~150 palavras/min em pt-BR); notas `[...]` não contam."""
    words = _spoken_words(speech)
    if not words:
        return SILENT_SCENE_S, False
    seconds = round(max(MIN_SPOKEN_S, words / WORDS_PER_S), 1)
    if seconds > MAX_HINT_S:
        return float(MAX_HINT_S), True
    return seconds, False


def _split_args(raw):
    """Divide em `|` fora de aspas duplas; `\\"` é aspa literal dentro do texto."""
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


def _arity_problem(kind, count):
    low, high = _ARITY[kind]
    if low <= count <= high:
        return None
    if kind == "SPLIT":
        return "SPLIT precisa de dois lados: [SPLIT: esquerda | direita]"
    if kind == "A-ROLL":
        return "A-ROLL aceita no máximo um take: [A-ROLL] ou [A-ROLL: t2]"
    if count < low:
        return f"{kind} precisa de um alvo: [{kind}: ...]"
    return f"{kind} aceita no máximo {high} argumento(s); se o | faz parte do texto, ponha o texto entre aspas"


def _extension(match, inner, number, plugins):
    prefix, name = match.group(1), match.group(2)
    if prefix not in plugins:
        guess = difflib.get_close_matches(prefix, sorted(plugins), n=1, cutoff=0.6)
        hint = (
            f" — quis dizer {guess[0]}:{name}?"
            if guess
            else "; habilite o plugin (plugins --action list) ou tire a diretiva"
        )
        return None, f'"[{inner}]": o plugin "{prefix}" não está habilitado{hint}', None
    args = tuple(_unquote(a)[0] for a in _split_args(match.group(3) or ""))
    return Directive("EXT", args, number, plugin=prefix, name=name), None, None


def _guess(head):
    """Diretiva que `head` parece ser (erro de digitação), ou None: aí é nota de cena."""
    key = re.sub(r"[^A-Z0-9]", "", fold(head).upper())
    match = difflib.get_close_matches(key, list(_SYNONYMS), n=1, cutoff=0.75)
    return _SYNONYMS[match[0]] if match else None


def _directive(inner, number, plugins):  # noqa: PLR0911 - one return per validation rule
    """(Directive, erro, nota) para o miolo de `[...]` numa linha inteira; só um dos três vem preenchido."""
    ext = _EXT.match(inner.strip())
    if ext and NAME_RE.fullmatch(ext.group(1)) and canonical(ext.group(1)) is None:
        return _extension(ext, inner, number, plugins)
    head, _, rest = inner.partition(":")
    kind = canonical(head)
    if kind is None:
        guess = _guess(head)
        if guess:
            return None, f'"[{inner}]" não é uma diretiva — quis dizer {guess}?', None
        return None, None, inner.strip()
    raw_args = _split_args(rest)
    problem = _arity_problem(kind, len(raw_args))
    if problem:
        return None, problem, None
    pairs = [_unquote(value) for value in raw_args]
    texts, flags = [text for text, _ in pairs], tuple(quoted for _, quoted in pairs)
    if kind == "LETTERING" and not flags[0]:
        return None, 'o texto do LETTERING vai entre aspas: [LETTERING: "texto" | estilo]', None
    if kind == "A-ROLL" and texts:
        texts = [fold(texts[0])]
        if not _TAKE.fullmatch(texts[0]):
            return None, "take do A-ROLL: use letras, números, - ou _ (ex.: [A-ROLL: t2])", None
    return Directive(kind, tuple(texts), number, quoted=flags), None, None


def _heading(line):
    """(título, id, erro) de uma linha `##`; None quando ela não é título de cena.

    Sem regex: `strip`/`rfind` são lineares mesmo numa linha de 5000 espaços.
    """
    text = line.rstrip()
    if not text.startswith("##") or text.startswith("###"):
        return None
    rest = text[2:]
    if rest and not rest[0].isspace():
        return None, None, "título de cena precisa de espaço depois de ##: `## Título`"
    rest = rest.strip()
    scene_id = None
    if rest.endswith("-->"):
        start = rest.rfind("<!--")
        if start >= 0:
            scene_id = rest[start + 4 : -3].strip()
            rest = rest[:start].rstrip()
    if "<!--" in rest or "-->" in rest:
        return None, None, "comentário no título só vale no fim e só com o id: `## Título <!-- c01 -->`"
    if not rest:
        return None, None, "título de cena vazio: escreva `## Título`"
    return rest, scene_id, None


def _open_scene(heading, number, errors, ids):
    title, scene_id, problem = heading
    scene = {
        "title": title or "", "id": None, "line": number, "directives": [], "speech": [], "notes": [],
        "broken": problem is not None, "layout_error": False,
    }  # fmt: skip
    if problem:
        errors.append((number, problem))
        return scene
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
    scene["id"] = scene_id
    return scene


def _spoken_count(scene):
    return sum(1 for line in scene["speech"] if line.strip())


def _body_line(scene, line, number, plugins, found):
    """Uma linha dentro de cena: diretiva, nota de cena, subtítulo ou fala."""
    errors, warnings = found
    if "<!--" in line:
        errors.append(
            (number, "comentário HTML esconde texto da revisão: apague o <!-- ... --> (só o id no fim do título vale)")
        )
        return
    placeholder = _PLACEHOLDER.search(line)
    if placeholder:
        warnings.append(f"linha {number}: {placeholder.group(0)} parece texto do esqueleto; troque pelo conteúdo real")
    stripped = line.strip()
    whole = _WHOLE.match(stripped)
    if whole and whole.group(1).strip():
        directive, problem, note = _directive(whole.group(1), number, plugins)
        if problem:
            errors.append((number, problem))
            head = whole.group(1).partition(":")[0]
            if (canonical(head) or _guess(head)) in LAYOUTS:
                scene["layout_error"] = True
        elif note is not None:
            scene["notes"].append(note)
            warnings.append(f'linha {number}: "[{note}]" não é diretiva: tratei como nota de cena (fica fora da fala)')
        else:
            scene["directives"].append((directive, _spoken_count(scene)))
        return
    if stripped.startswith("###"):
        return  # subtítulo é nota da pessoa: não é fala nem diretiva
    scene["notes"].extend(inline_notes(line))
    scene["speech"].append(line.rstrip())


def _close(scene, errors):
    if scene is None or scene["broken"]:
        return None
    layouts = [d for d, _ in scene["directives"] if d.kind in LAYOUTS]
    if not layouts:
        if not scene["layout_error"]:
            errors.append((scene["line"], f'a cena "{scene["title"]}" precisa de um layout: {", ".join(LAYOUTS)}'))
        return None
    if len(layouts) > 1:
        errors.append((layouts[1].line, "mais de um layout na mesma cena; separe em duas cenas"))
        return None
    spoken = [line for line in scene["speech"] if line.strip()]
    offsets = [0]
    for line in spoken:
        offsets.append(offsets[-1] + _spoken_words(line))
    anchored = [
        replace(d, anchor=position if position < len(spoken) else END_ANCHOR, word_offset=offsets[position])
        for d, position in scene["directives"]
    ]
    speech = "\n".join(scene["speech"]).strip()
    duration, over = estimate(speech)
    clean = [strip_notes(line) for line in spoken]
    return Scene(
        title=scene["title"],
        scene_id=scene["id"],
        line=scene["line"],
        layout=next(d for d in anchored if d.kind in LAYOUTS),
        layers=tuple(d for d in anchored if d.kind in LAYERS),
        extensions=tuple(d for d in anchored if d.kind == "EXT"),
        speech=speech,
        duration_s=duration,
        over_cap=over,
        speech_clean="\n".join(line for line in clean if line),
        notes=tuple(scene["notes"]),
    )


def parse(text, plugins=None):
    """Lê o roteiro inteiro; todos os erros de uma vez (`RoteiroError`), avisos em `warnings`.

    `plugins`: ids de plugin aceitos em `[plugin:nome]`; None = os habilitados agora.
    """
    if plugins is None:
        plugins = enabled_plugins()
    lines = text.removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}").replace("\r\n", "\n").split("\n")
    meta, start, errors = parse_frontmatter(lines)
    scenes, warnings, current, ids = [], [], None, {}
    for index in range(start, len(lines)):
        number = index + 1
        line = lines[index]
        heading = _heading(line)
        if heading is not None:
            scene = _close(current, errors)
            if scene:
                scenes.append(scene)
            current = _open_scene(heading, number, errors, ids)
            continue
        if current is None:
            stripped = line.strip()
            if stripped and not (stripped.startswith("# ") and not stripped.startswith("##")):
                errors.append((number, "texto fora de cena: toda fala vai depois de um título ## Cena"))
            continue
        _body_line(current, line, number, plugins, (errors, warnings))
    scene = _close(current, errors)
    if scene:
        scenes.append(scene)
    if errors:
        raise RoteiroError(sorted(errors))
    warnings += [
        f'linha {s.line}: a cena "{s.title}" passa de {int(MAX_HINT_S)} s — divida a cena' for s in scenes if s.over_cap
    ]
    total = round(sum(s.duration_s for s in scenes), 1)
    target = meta.get("duracao_alvo_s")
    if target and total > target:
        warnings.append(f"duração estimada {total} s passa do duracao_alvo_s ({target} s)")
    return Roteiro(meta=meta, scenes=tuple(scenes), warnings=tuple(warnings))

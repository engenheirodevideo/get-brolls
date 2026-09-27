"""`ROTEIRO.md`: o conteúdo do vídeo como roteiro — cenas, diretivas e fala.

O arquivo é da pessoa. Este módulo só lê e valida; quem grava ids e status é
`roteiro_sync`. No frontmatter, as chaves do get-brolls aceitam um subconjunto
fechado de YAML (uma linha `chave: valor`, aspas opcionais): o resto do YAML nelas é
recusado com a linha, nunca "interpretado". Chave de outra ferramenta (as
propriedades do Obsidian, como `tags`, `aliases` e `created`) é ignorada, junto com
as linhas de lista ou bloco que vêm abaixo dela. Texto escondido (comentário HTML,
elemento HTML, `%%` do Obsidian, caractere invisível ou de direção de texto, CR solto)
é erro: a revisão humana tem que ver tudo o que vira beat.
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass, replace

from . import roteiro_frontmatter
from .limits import MAX_HINT_S
from .roteiro_frontmatter import _unquote, fold, parse_frontmatter, roteiro_path
from .sdk.names import NAME_RE

LAYOUTS = ("A-ROLL", "BROLL", "SPLIT", "FULL", "UGC")
# Reexport: quem já importava daqui (`from .roteiro import GENRES`, `roteiro.is_roteiro`
# etc.) continua igual; o corpo deste módulo não os usa (moram em `roteiro_frontmatter`,
# o leaf que `brief_source.retired_beat_ids` lê sem puxar `enabled_plugins`/`sdk.loader`).
ASPECT_TO_FORMAT = roteiro_frontmatter.ASPECT_TO_FORMAT
GENRES = roteiro_frontmatter.GENRES
STATUSES = roteiro_frontmatter.STATUSES
frontmatter_key = roteiro_frontmatter.frontmatter_key
is_roteiro = roteiro_frontmatter.is_roteiro
# Diretivas de direção reservadas: reconhecidas e recusadas até a versão que as implementa.
RESERVED_DIRECTIVES = ("DIRECAO", "TRANSICAO", "RITMO", "VELOCIDADE")
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
_PLACEHOLDER = re.compile(r"\{[^{}\n]{1,200}\}")
# `<` seguido de letra (com `/`, `!` ou `?` no meio) abre elemento HTML, que o Obsidian
# renderiza e pode esconder; `<3` e `a < b` continuam texto.
_HTML_TAG = re.compile(r"<[/!?]?[^\W\d_]")
# Invisíveis e controles de direção que escondem ou reordenam texto na tela. ZWJ (U+200D)
# e ZWNJ (U+200C) ficam: compõem emoji e escritas como o devanágari.
_INVISIBLE = frozenset(
    "\u200b\u2060\ufeff" + "".join(map(chr, range(0x202A, 0x202F))) + "".join(map(chr, range(0x2066, 0x206A)))
)
_WORD = re.compile(r"\w+")
# Aspa simples só fecha o argumento quando vem antes de `|` ou do fim: `d'água` segue texto.
_SINGLE_CLOSE = re.compile(r"\s*(?:\||$)")
_TAKE = re.compile(r"[a-z0-9][a-z0-9_-]{0,19}")
# "a" e "b" são os sufixos dos lados do SPLIT (`c03-a`): take com esse nome, ou que
# comece com "a-"/"b-" (`c03-a-t2` é o take t2 do lado a), colidiria.
RESERVED_TAKES = ("a", "b")
SIDE_PREFIXES = ("a-", "b-")
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
# Contrato testado campo a campo fora desta frente (`.plugin`/`.name`/`.anchor`/`.word_offset`
# em tests/test_roteiro_hardening.py e tests/test_roteiro_parser.py): agrupar os campos
# quebraria essas asserções, que não são desta frente para mudar.
# pylint: disable=too-many-instance-attributes
class Directive:
    """Diretiva `[KIND: args]` (ou nota) de uma linha do roteiro, já com a âncora de tempo."""

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
# Contrato lido por vários módulos (e testado campo a campo fora desta frente): agrupar
# os campos quebraria `roteiro_plan`/`export_plan` e as asserções de teste por nome.
# pylint: disable=too-many-instance-attributes
class Scene:
    """Cena lida do roteiro: título, layout, camadas, extensões, fala e as métricas de tempo dela."""

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
    """Roteiro inteiro já lido e validado: metadados do frontmatter, cenas e avisos."""

    meta: dict
    scenes: tuple[Scene, ...]
    warnings: tuple[str, ...]


class RoteiroError(ValueError):
    """Erros de leitura do roteiro, todos de uma vez, cada um com a linha (1-based)."""

    def __init__(self, errors):
        self.errors = list(errors)
        lines = [f"linha {n}: {msg}" if n else msg for n, msg in self.errors]
        super().__init__("ROTEIRO.md com problema:\n" + "\n".join(lines))


def path_problem(path):
    """Erro de um ROTEIRO.md que existe mas não é arquivo (link quebrado ou em laço, pasta); None no resto.

    Nunca "crie com new": isso empurraria alguém a rodar `new --force` por cima do link.
    """
    if path.is_file():
        return None
    if path.is_symlink():
        try:
            target = path.readlink()
        except OSError:
            target = "?"
        return (
            f"{path} é um link quebrado: aponta para {target}, que não existe ou não é um arquivo. "
            "Conserte o link (ou troque-o pelo arquivo de verdade) e repita."
        )
    if path.exists():
        return f"{path} é uma pasta, não um arquivo: renomeie a pasta e repita."
    return None


def load_text(project):
    """Texto do ROTEIRO.md sem BOM e com fim de linha `\\n`; erro claro quando ele não existe."""
    path = roteiro_path(project)
    problem = path_problem(path)
    if problem:
        raise ValueError(problem)
    if not path.is_file():
        raise ValueError("Este projeto não tem ROTEIRO.md. Crie com `roteiro --action new --genero reels --tema ...`.")
    try:
        return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise ValueError("ROTEIRO.md não está em UTF-8: salve o arquivo como UTF-8 e rode de novo.") from None


def enabled_plugins():
    """Ids de plugin habilitados, pelo inventário pré-carga (manifesto e pin): nenhum código de plugin roda."""
    from .sdk import loader

    try:
        return frozenset(row["id"] for row in loader.inventory() if row["status"] == "enabled")
    except (ValueError, OSError):
        return frozenset()


def canonical(keyword):
    """Nome canônico da diretiva (`A-ROLL`, `BROLL`…) para um sinônimo aceito; None quando não é diretiva."""
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


def spoken_words(text):
    """Palavras faladas de um trecho (notas `[...]` fora): o mesmo contador de `word_offset`."""
    return len(_WORD.findall("\n".join(strip_notes(line) for line in text.split("\n"))))


def estimate(speech):
    """Segundos falados (~150 palavras/min em pt-BR); notas `[...]` não contam."""
    words = spoken_words(speech)
    if not words:
        return SILENT_SCENE_S, False
    seconds = round(max(MIN_SPOKEN_S, words / WORDS_PER_S), 1)
    if seconds > MAX_HINT_S:
        return float(MAX_HINT_S), True
    return seconds, False


def _split_args(raw):
    """Divide em `|` fora de aspas; `\\"` é aspa literal dentro do texto.

    Aspa dupla abre e fecha em qualquer ponto. Aspa simples só abre no começo do
    argumento e só fecha antes de `|` ou do fim, para o apóstrofo (`d'água`) ser texto.
    """
    parts, current, double, single, started, index = [], [], False, False, False, 0
    while index < len(raw):
        ch = raw[index]
        if ch == "\\" and raw[index + 1 : index + 2] == '"':
            current.append('\\"')
            started = True
            index += 2
            continue
        if ch == '"' and not single:
            double = not double
        elif ch == "'" and not double:
            if single:
                single = _SINGLE_CLOSE.match(raw, index + 1) is None
            elif not started:
                single = True
        if ch == "|" and not double and not single:
            parts.append("".join(current).strip())
            current, started = [], False
        else:
            current.append(ch)
            started = started or not ch.isspace()
        index += 1
    parts.append("".join(current).strip())
    return [p for p in parts if p] if any(parts) else []


def take_problem(take):
    """Erro do take de A-ROLL (já dobrado com `fold`), ou None; vale no layout e no lado do SPLIT."""
    if not _TAKE.fullmatch(take):
        return "take do A-ROLL: use letras, números, - ou _ (ex.: [A-ROLL: t2])"
    if take in RESERVED_TAKES:
        return f'take "{take}" é reservado para o lado do SPLIT: use outro nome'
    if take.startswith(SIDE_PREFIXES):
        return f'take "{take}" começa com "a-" ou "b-", que são os nomes dos lados do SPLIT: use outro nome'
    return None


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
    pairs = [_unquote(a) for a in _split_args(match.group(3) or "")]
    args, flags = tuple(text for text, _ in pairs), tuple(quoted for _, quoted in pairs)
    return Directive("EXT", args, number, plugin=prefix, name=name, quoted=flags), None, None


def _guess(head):
    """Diretiva que `head` parece ser (erro de digitação), ou None: aí é nota de cena."""
    key = re.sub(r"[^A-Z0-9]", "", fold(head).upper())
    match = difflib.get_close_matches(key, list(_SYNONYMS), n=1, cutoff=0.75)
    return _SYNONYMS[match[0]] if match else None


def _reserved_directive(head):
    """Nome de diretiva de direção reservada em `head` (com ou sem os dois-pontos), ou None (nota de cena)."""
    head_key = re.sub(r"[^A-Z0-9]", "", fold(head).upper())
    if head_key in RESERVED_DIRECTIVES:
        return head_key
    # Nome reservado sem os dois-pontos (`[RITMO — rápido]`): nome mais um valor de uma palavra.
    # Frase mais longa (`[ritmo acelerado aqui]`) continua nota de cena.
    words = [word.upper() for word in re.findall(r"\w+", fold(head))]
    if words and words[0] in RESERVED_DIRECTIVES and len(words) <= 2:  # noqa: PLR2004 - nome + valor  # pylint: disable=line-too-long
        return words[0]
    return None


def _directive(inner, number, plugins):  # noqa: PLR0911 - one return per validation rule
    # pylint: disable=too-many-return-statements  # one return per validation rule
    """(Directive, erro, nota) para o miolo de `[...]` numa linha inteira; só um dos três vem preenchido."""
    head = inner.partition(":")[0]
    reserved = _reserved_directive(head)
    if reserved:
        message = f'"[{inner}]": {reserved} é reservado para a próxima versão do get-brolls; tire a diretiva'
        return None, message, None
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
        problem = take_problem(texts[0])
        if problem:
            return None, problem, None
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
    return sum(1 for line in scene["speech"] if strip_notes(line))


_HTML = "HTML no roteiro pode esconder texto da revisão; escreva em texto puro"
_LONE_CR = "retorno de carro (CR) solto esconde texto da revisão: salve o arquivo com fim de linha LF ou CRLF"
_HIDDEN = "comentário HTML esconde texto da revisão: apague o <!-- ... --> (só o id no fim do título vale)"
# `%%texto%%` some no modo leitura do Obsidian: a pessoa revisaria sem ver o que vira fala.
_OBSIDIAN = "comentário do Obsidian (%%) esconde texto da revisão: apague os %% (ou o trecho inteiro)"


def _body_line(scene, line, number, plugins, found):
    """Uma linha dentro de cena: diretiva, nota de cena, subtítulo ou fala."""
    errors, warnings = found
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
            warnings.append(f'linha {number}: "[{note}]" não é diretiva: tratei como nota de cena (fica fora da fala)')  # pylint: disable=line-too-long
        else:
            scene["directives"].append((directive, _spoken_count(scene)))
        return
    if stripped.startswith("###"):
        return  # subtítulo é nota da pessoa: não é fala nem diretiva
    scene["notes"].extend(inline_notes(line))
    scene["speech"].append(line.rstrip())


def _line(scene, line, number, plugins, found):
    """Qualquer linha depois do frontmatter que não é título de cena."""
    errors = found[0]
    if "<!--" in line or "-->" in line:
        errors.append((number, _HIDDEN))  # vale fora de cena também: um H1 não pode abrir comentário
        return
    if scene is None:
        stripped = line.strip()
        if stripped and not (stripped.startswith("# ") and not stripped.startswith("##")):
            errors.append((number, "texto fora de cena: toda fala vai depois de um título ## Cena"))
        return
    _body_line(scene, line, number, plugins, found)


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
    # Mesmo critério de `_spoken_count`: a âncora indexa as linhas de `speech_clean`.
    clean = [text for text in (strip_notes(line) for line in scene["speech"]) if text]
    offsets = [0]
    for line in clean:
        offsets.append(offsets[-1] + len(_WORD.findall(line)))
    anchored = [
        replace(d, anchor=position if position < len(clean) else END_ANCHOR, word_offset=offsets[position])
        for d, position in scene["directives"]
    ]
    speech = "\n".join(scene["speech"]).strip()
    duration, over = estimate(speech)
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
        speech_clean="\n".join(clean),
        notes=tuple(scene["notes"]),
    )


def _parse_body(lines, start, plugins, errors):
    """Cenas e avisos do corpo do roteiro (depois do frontmatter); erros de layout entram em `errors`."""
    scenes, warnings, current, ids = [], [], None, {}
    for index in range(start, len(lines)):
        number = index + 1
        line = lines[index]
        if "%%" in line:
            errors.append((number, _OBSIDIAN))  # título, H1, diretiva ou fala: qualquer linha do corpo
        if _HTML_TAG.search(line):
            errors.append((number, _HTML))
        heading = _heading(line)
        if heading is not None:
            scene = _close(current, errors)
            if scene:
                scenes.append(scene)
            current = _open_scene(heading, number, errors, ids)
            continue
        _line(current, line, number, plugins, (errors, warnings))
    scene = _close(current, errors)
    if scene:
        scenes.append(scene)
    return scenes, warnings


def _invisible_problems(lines):
    """Erros de caractere invisível, de controle de direção e de CR solto, em qualquer linha (frontmatter também)."""
    errors = []
    for index, line in enumerate(lines):
        for ch in dict.fromkeys(ch for ch in line if ch in _INVISIBLE):
            name = f"{unicodedata.name(ch)} (U+{ord(ch):04X})"
            errors.append((index + 1, f"caractere invisível {name} esconde texto da revisão: apague-o"))
        if "\r" in line:
            errors.append((index + 1, _LONE_CR))
    return errors


def parse(text, plugins=None):
    """Lê o roteiro inteiro; todos os erros de uma vez (`RoteiroError`), avisos em `warnings`.

    `plugins`: ids de plugin aceitos em `[plugin:nome]`; None = os habilitados agora.
    """
    if plugins is None:
        plugins = enabled_plugins()
    lines = text.removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}").replace("\r\n", "\n").split("\n")
    meta, start, errors = parse_frontmatter(lines)
    errors += _invisible_problems(lines)
    scenes, warnings = _parse_body(lines, start, plugins, errors)
    if errors:
        raise RoteiroError(sorted(errors))
    warnings += [
        f'linha {s.line}: a cena "{s.title}" passa de {int(MAX_HINT_S)} s — divida a cena'
        for s in scenes
        if s.over_cap  # pylint: disable=line-too-long  # mensagem em pt-BR; ruff format só quebra a linha com o comentário
    ]
    total = round(sum(s.duration_s for s in scenes), 1)
    target = meta.get("duracao_alvo_s")
    if target and total > target:
        warnings.append(f"duração estimada {total} s passa do duracao_alvo_s ({target} s)")
    return Roteiro(meta=meta, scenes=tuple(scenes), warnings=tuple(warnings))

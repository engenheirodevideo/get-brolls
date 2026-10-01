"""Gramática do frontmatter do `ROTEIRO.md`: campos, `type: roteiro` e `is_roteiro`.

Módulo folha: só stdlib, nunca `.brief`, `.brief_source` nem `.sdk`. `is_roteiro` mora
aqui (não em `roteiro.py`) para que `brief_source.load_brief`/`retired_beat_ids` o leiam
sem puxar o resto de `roteiro.py` — que importa `sdk.loader` (via `enabled_plugins`) e
reabriria o ciclo `sdk.contracts <-> brief`. `roteiro.py` reexporta tudo daqui para
manter o caminho de import de sempre.
"""

import difflib
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
            ("Gancho", "[A-ROLL]", "{gancho: a frase que segura nos 3 primeiros segundos}"),
            ("Problema", "[BROLL: {o que a pessoa vê enquanto você fala}]", "{a dor, em uma frase}"),
            ("Prova", "[SPLIT: {tela ou b-roll} | A-ROLL]", "{o que prova que funciona}"),
            ("CTA", "[FULL: logo]", "{o que a pessoa faz agora}"),
        ),
    },
}
KEYS = ("type", "genero", "aspecto", "duracao_alvo_s", "tema", "legenda", "status", "cliente", "direcao")
REQUIRED = ("type", "genero", "tema")
# Chaves que só dão nome: o valor passa adiante (plano de export) e nenhum recurso o usa ainda.
SLUG_KEYS = ("cliente", "direcao")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SLUG_MAX = 64
# Espaço antes dos dois-pontos é tolerado: `legenda : false` é a chave `legenda`.
_PAIR = re.compile(r"^([a-z_]+)[ \t]*:(.*)$")
# Chave de outra ferramenta: começa sem espaço, `-`, `#` ou aspas e termina em `:` seguido de espaço ou fim.
_OTHER_KEY = re.compile(r"^([^\s#:\-\"'][^:]*):(?:\s|$)")


def fold(text):
    """Chave de comparação: minúsculas, sem acento, espaços colapsados."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.lower().split())


def roteiro_path(project):
    """Caminho absoluto do `ROTEIRO.md` do projeto."""
    return Path(project).expanduser().resolve() / ROTEIRO_FILE


def is_roteiro(project):
    """True só quando ROTEIRO.md é do get-brolls: o primeiro frontmatter tem `type: roteiro`.

    O roteiro que a pessoa já escreve por conta própria (sem frontmatter, ou com outro
    `type`) conta como ausente: não libera brief sem beats nem aposenta nada. Só lê e
    nunca levanta — arquivo ilegível, pasta ou link quebrado também contam como ausente.
    """
    path = roteiro_path(project)
    try:
        if not path.is_file():
            return False
        lines = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n")
    except (OSError, UnicodeDecodeError):
        return False
    if not lines or lines[0].strip() != "---":
        return False
    _, body, _ = parse_frontmatter(lines)
    for line in lines[1:body]:
        if frontmatter_key(line) == "type":
            return _unquote(line.partition(":")[2].strip())[0] == "roteiro"
    return False


def roteiro_format(project):
    """`reels`/`horizontal` do `aspecto` de um ROTEIRO.md do get-brolls; None sem roteiro ou com erro.

    Só lê e nunca levanta, como `is_roteiro`: quem precisa do erro roda o `check`.
    """
    if not is_roteiro(project):
        return None
    try:
        lines = roteiro_path(project).read_text(encoding="utf-8-sig").replace("\r\n", "\n").split("\n")
    except (OSError, UnicodeDecodeError):
        return None
    meta, _, _ = parse_frontmatter(lines)
    return ASPECT_TO_FORMAT.get(meta.get("aspecto") or "")


def _unquote(value):
    """Tira aspas duplas ou simples que envolvem o valor inteiro; `\\"` vira aspa literal."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":  # noqa: PLR2004 - opening + closing quote
        return value[1:-1].replace('\\"', '"'), True
    return value.replace('\\"', '"'), False


def _field(  # noqa: C901, PLR0911, PLR0912 - one branch and return per field rule
    key, value, quoted, meta
):  # pylint: disable=too-many-return-statements,too-many-branches  # one branch and return per field rule
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
        if quoted or not (value.isascii() and value.isdigit()) or not 5 <= int(value) <= 600:  # noqa: PLR2004 - spec bounds  # pylint: disable=line-too-long
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
    if key in SLUG_KEYS:
        if len(value) > SLUG_MAX or not SLUG_RE.fullmatch(value):
            example = "-".join(re.findall(r"[a-z0-9]+", fold(value))) or "acme"
            return f'"{key}" tem que ser um slug: minúsculas, números e -, sem espaço (ex.: {example[:SLUG_MAX]})'
        meta[key] = value
        return None
    if not value:
        return f'"{key}" precisa de um valor'
    meta[key] = value
    return None


def frontmatter_key(line):
    """Chave do get-brolls numa linha do frontmatter (`status : x` inclusive), ou None."""
    match = _PAIR.match(line)
    return match.group(1) if match else None


def _foreign_key_problem(line):
    """Erro de uma linha que não é chave do get-brolls, ou None quando é chave de outra ferramenta
    (ignorada). Chave nossa com caixa, acento ou espaço trocados, ou quase igual, é erro com sugestão."""
    other = _OTHER_KEY.match(line)
    if other is None:
        return "use chave: valor"
    key = other.group(1).strip()
    normal = fold(key).replace(" ", "_")
    if normal in KEYS:
        return f"chave `{key}`: use `{normal}`"
    close = difflib.get_close_matches(normal, KEYS, n=1, cutoff=0.8)
    if close:
        return f"chave desconhecida `{key}`: quis dizer `{close[0]}`?"
    return None


def parse_frontmatter(lines):  # noqa: C901, PLR0912 - one branch per grammar rule
    # pylint: disable=too-many-branches  # one branch per grammar rule
    """Devolve (meta, índice da primeira linha do corpo, erros)."""
    if not lines or lines[0].strip() != "---":
        return {}, 0, [(1, 'o roteiro começa com o frontmatter entre linhas "---"')]
    meta, errors, seen = {}, [], set()
    # Depois de uma chave de outra ferramenta, a lista ou o bloco indentado dela também é ignorado.
    skipping = False
    for index in range(1, len(lines)):
        line = lines[index]
        number = index + 1
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace() or line.lstrip().startswith("-"):
            if not skipping:
                errors.append((number, "o frontmatter não aceita lista nem bloco: use uma linha chave: valor"))
            continue
        match = _PAIR.match(line)
        if not match or match.group(1) not in KEYS:
            problem = _foreign_key_problem(line)
            if problem:
                errors.append((number, problem))
            skipping = _OTHER_KEY.match(line) is not None
            continue
        skipping = False
        key = match.group(1)
        value, quoted = _unquote(match.group(2).strip())
        if key in seen:
            errors.append((number, f'chave "{key}" repetida'))
            continue
        seen.add(key)
        if not quoted and (value.startswith("#") or " #" in value):
            message = f'"{key}": comentário no fim da linha não vale aqui; ponha o # numa linha própria'
            errors.append((number, message + " (ou use aspas se ele faz parte do valor)"))
            continue
        if key in SLUG_KEYS and (not value or (not quoted and value in ("null", "~"))):
            continue  # propriedade em branco (o Obsidian grava `cliente:`) vale como ausente
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

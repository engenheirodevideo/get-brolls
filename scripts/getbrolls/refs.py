"""Referências (`ref`): um parser e um formatador para toda ref que entra ou sai do core.

Nesta versão só a ref de catálogo existe: `cat:<motor>/<tipo>/<id>@<versão>`, como
`cat:getbrolls/template/reels-acme@3`. As refs locais (`scene:`, `beat:`, `asset:`…)
já têm nome reservado e são recusadas com uma mensagem clara; a API fica a mesma para
que elas entrem depois sem mudar quem chama.

Codificação de `id` e `versão`: o texto é normalizado em NFC e cada um de `%` `:` `/`
`\\` `@` `#` `?`, todo espaço (`str.isspace()`), controle C0/C1 e DEL vira `%XX` (bytes
UTF-8, hexadecimal maiúsculo). O resto, inclusive acento e letra não latina, fica
literal. Na leitura, `%xx` minúsculo é aceito; um reservado literal é erro.

Módulo folha: só stdlib e a regra de slug do frontmatter do roteiro.
"""

import re
import unicodedata
from dataclasses import dataclass

from .roteiro_frontmatter import SLUG_MAX, SLUG_RE

LOCAL_KINDS = ("scene", "beat", "candidate", "clip", "aroll", "asset", "export", "doc", "task")
DOC_NAMES = ("roteiro", "brief", "rules", "storyboard", "status")
CATALOG = "cat"
MAX_REF = 512
MAX_VALUE = 200
TEMPLATE_ENGINE = "getbrolls"
TEMPLATE_TYPE = "template"

_RESERVED = frozenset("%:/\\@#?")
_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_CATALOG_PARTS = 3  # motor, tipo, id
_VERSION_NUMBER_RE = re.compile(r"[1-9][0-9]{0,8}")
_SHAPE = "use tipo:valor ou cat:motor/tipo/id@versão"
_LATER = 'ref "{kind}:" fica disponível a partir da 2.7; nesta versão só cat:motor/tipo/id@versão'
_ECHO_MAX = 80


@dataclass(frozen=True)
class Ref:
    """Ref já decodificada: `kind` é `cat` (ou um de `LOCAL_KINDS`, recusado por enquanto)."""

    kind: str
    parts: tuple[str, ...]  # cat = (motor, tipo, id), valores decodificados
    version: str | None = None  # só em `cat`


def _needs_escape(char):
    # `Cc` é exatamente C0 (U+0000–U+001F), DEL e C1 (U+007F–U+009F).
    return char in _RESERVED or char.isspace() or unicodedata.category(char) == "Cc"


def encode(value: str) -> str:
    """`value` em NFC com os reservados, espaços e controles como `%XX` maiúsculo."""
    text = unicodedata.normalize("NFC", value)
    return "".join(
        "".join(f"%{byte:02X}" for byte in char.encode("utf-8")) if _needs_escape(char) else char for char in text
    )


def decode(text: str) -> str:
    """Inverso de `encode`; `ValueError` com o motivo em `%XX` inválido ou reservado literal."""
    out = bytearray()
    index = 0
    while index < len(text):
        char = text[index]
        if char == "%":
            if not _PCT_RE.match(text, index):
                raise ValueError("%XX inválido")
            out.append(int(text[index + 1 : index + 3], 16))
            index += 3
            continue
        if _needs_escape(char):
            shown = char if char.isprintable() and not char.isspace() else f"U+{ord(char):04X}"
            raise ValueError(f'"{shown}" precisa ser escrito como %XX')
        out.extend(char.encode("utf-8"))
        index += 1
    try:
        decoded = out.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("%XX inválido") from None
    return unicodedata.normalize("NFC", decoded)


def _echo(text):
    shown = str(text)
    return shown if len(shown) <= _ECHO_MAX else shown[: _ECHO_MAX - 1] + "…"


def _invalid(text, reason):
    return ValueError(f'Referência inválida "{_echo(text)}": {reason}.')


def _check_value(value, label):
    """Motivo de um id ou versão decodificado inválido; `None` quando vale."""
    if not value:
        return "valor vazio"
    if len(value) > MAX_VALUE:
        return f"{label} passou de {MAX_VALUE} caracteres"
    return None


def _check_catalog(engine, tipo, ident, version):
    for label, name in (("motor", engine), ("tipo", tipo)):
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            return f'{label} "{_echo(name)}" tem que ter de 1 a 32 caracteres a-z, 0-9, _ ou -, começando por letra'
    for label, value in (("id", ident), ("versão", version)):
        if not isinstance(value, str):
            return f"{label} tem que ser texto"
        problem = _check_value(value, label)
        if problem:
            return problem
    return None


def parse(text: str) -> Ref:
    """A ref de `text`, decodificada; `ValueError` (`Referência inválida "<ref>": <motivo>.`)."""
    if not isinstance(text, str):
        raise _invalid(text, _SHAPE)
    if len(text) > MAX_REF:
        raise _invalid(text, f"passou de {MAX_REF} caracteres")
    kind, colon, body = text.partition(":")
    if not colon or not kind:
        raise _invalid(text, _SHAPE)
    if kind in LOCAL_KINDS:
        raise _invalid(text, _LATER.format(kind=kind))
    if kind != CATALOG:
        raise _invalid(text, f'tipo "{_echo(kind)}" não existe')
    pieces = body.split("/")
    if len(pieces) != _CATALOG_PARTS or pieces[2].count("@") != 1:
        raise _invalid(text, _SHAPE)
    engine, tipo, tail = pieces
    raw_ident, _, raw_version = tail.partition("@")
    try:
        ident, version = decode(raw_ident), decode(raw_version)
    except ValueError as exc:
        raise _invalid(text, str(exc)) from None
    problem = _check_catalog(engine, tipo, ident, version)
    if problem:
        raise _invalid(text, problem)
    return Ref(CATALOG, (engine, tipo, ident), version)


def format_ref(ref: Ref) -> str:
    """Texto canônico de `ref`; `ValueError` quando ela não é uma ref válida."""
    if ref.kind in LOCAL_KINDS:
        raise _invalid(f"{ref.kind}:", _LATER.format(kind=ref.kind))
    if ref.kind != CATALOG or len(ref.parts) != _CATALOG_PARTS:
        raise _invalid(f"{ref.kind}:", _SHAPE)
    engine, tipo, ident = ref.parts
    ident = unicodedata.normalize("NFC", ident) if isinstance(ident, str) else ident
    version = unicodedata.normalize("NFC", ref.version) if isinstance(ref.version, str) else ref.version
    problem = _check_catalog(engine, tipo, ident, version)
    shown = f"cat:{engine}/{tipo}/…"
    if problem:
        raise _invalid(shown, problem)
    # `_check_catalog` já garantiu texto nos dois; `str()` só informa o tipo ao pyright.
    text = f"{CATALOG}:{engine}/{tipo}/{encode(str(ident))}@{encode(str(version))}"
    if len(text) > MAX_REF:
        raise _invalid(shown, f"passou de {MAX_REF} caracteres")
    return text


def canonical(text: str) -> str:
    """`format_ref(parse(text))`: a mesma ref, na grafia canônica."""
    return format_ref(parse(text))


def catalog_ref(engine: str, tipo: str, ident: str, version: str) -> str:
    """Ref canônica `cat:<motor>/<tipo>/<id>@<versão>`, com id e versão codificados."""
    return format_ref(Ref(CATALOG, (engine, tipo, ident), version))


def same(a: str, b: str) -> bool:
    """As duas refs são a mesma? Compara as formas canônicas; ref inválida levanta."""
    return canonical(a) == canonical(b)


def _template_problem(slug, number):
    if not isinstance(slug, str) or len(slug) > SLUG_MAX or not SLUG_RE.fullmatch(slug):
        return "o id do template tem que ser um slug: minúsculas, números e -, até 64 caracteres"
    if type(number) is not int or number < 1:
        return "a versão do template tem que ser um inteiro a partir de 1"
    return None


def template_ref(slug: str, number: int) -> str:
    """`cat:getbrolls/template/<slug>@<n>`; `ValueError` com slug ou versão fora da regra."""
    problem = _template_problem(slug, number)
    if problem:
        raise _invalid(f"cat:{TEMPLATE_ENGINE}/{TEMPLATE_TYPE}/{_echo(slug)}@{number}", problem)
    return catalog_ref(TEMPLATE_ENGINE, TEMPLATE_TYPE, slug, str(number))


def template_parts(text: str) -> tuple[str, int]:
    """`(slug, versão)` de uma ref de template do get-brolls.

    O slug é conferido já decodificado: `%2E%2E`, `%2F` ou `%5C` nunca chegam a virar
    pedaço de caminho. A versão é um número sem zero à esquerda.
    """
    ref = parse(text)
    engine, tipo, slug = ref.parts
    if (engine, tipo) != (TEMPLATE_ENGINE, TEMPLATE_TYPE):
        raise _invalid(text, f"template é cat:{TEMPLATE_ENGINE}/{TEMPLATE_TYPE}/<slug>@<versão>")
    version = ref.version or ""
    if not _VERSION_NUMBER_RE.fullmatch(version):
        raise _invalid(text, "a versão do template tem que ser um inteiro a partir de 1")
    problem = _template_problem(slug, int(version))
    if problem:
        raise _invalid(text, problem)
    return slug, int(version)

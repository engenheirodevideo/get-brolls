"""Referências (`ref`): um parser e um formatador para toda ref que entra ou sai do core.

Valem a ref de catálogo `cat:<motor>/<tipo>/<id>@<versão>` (a receita é
`cat:getbrolls/recipe/<dono>.<slug>@<N>`) e as refs locais `asset:<tipo>:<nome>`,
`handoff:<ambiente>/<NNN>`, `seg:<id>` e `media:<id>`. As outras locais (`scene:`,
`beat:`…) já têm nome reservado e são recusadas com uma mensagem clara.

Codificação de `id` e `versão`: o texto é normalizado em NFC e cada um de `%` `:` `/`
`\\` `@` `#` `?`, todo espaço (`str.isspace()`), controle C0/C1 e DEL vira `%XX` (bytes
UTF-8, hexadecimal maiúsculo). O resto, inclusive acento e letra não latina, fica
literal. Na leitura, `%xx` minúsculo é aceito; um reservado literal é erro.

Folha no import: só stdlib e a regra de slug do frontmatter do roteiro. O nome de
componente (`assets.valid_name`) e o de ambiente (`sdk.names.NAME_RE`) só entram na
conferência, por import tardio.
"""

import re
import unicodedata
from dataclasses import dataclass

from .roteiro_frontmatter import SLUG_MAX, SLUG_RE, fold

LOCAL_KINDS = (
    "scene", "beat", "candidate", "clip", "aroll", "asset", "export", "doc", "task", "handoff", "seg", "media",
)  # fmt: skip
# As refs locais que já valem; as outras têm o nome guardado e recusam com a lista destas.
ACCEPTED_LOCAL = ("asset", "handoff", "seg", "media")
CATALOG = "cat"
MAX_REF = 512
MAX_VALUE = 200
RECIPE_ENGINE = "getbrolls"
RECIPE_TYPE = "recipe"
# Dono das receitas da pasta pessoal (`pessoal.<slug>`); `client add` nunca aceita este slug.
PERSONAL_OWNER = "pessoal"

_RESERVED = frozenset("%:/\\@#?")
_NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_CATALOG_PARTS = 3  # motor, tipo, id
_RECIPE_PIECES = 2  # dono e slug
_VERSION_NUMBER_RE = re.compile(r"[1-9][0-9]{0,8}")
_VERSION_MAX = 999_999_999  # o maior que `_VERSION_NUMBER_RE` aceita
_HANDOFF_NUMBER_RE = re.compile(r"[0-9]{3,9}")
_SEG_RE = re.compile(r"seg_[0-9a-f]{10}")
_MEDIA_RE = re.compile(r"[0-9a-f]{16}")
_SHAPE = "use tipo:valor ou cat:motor/tipo/id@versão"
_LATER = 'ref "{kind}:" ainda não é aceita; valem cat:motor/tipo/id@versão, asset:, handoff:, seg: e media:'
_RECIPE_SHAPE = "use <slug>[@N], <dono>.<slug>[@N] ou cat:getbrolls/recipe/<dono>.<slug>@<N>"
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


def _split_local(kind, body):
    """Os pedaços ainda codificados do corpo de uma ref local; `ValueError` com o motivo."""
    if kind == "asset":
        tipo, colon, name = body.partition(":")
        if not colon:
            raise ValueError("use asset:<tipo>:<nome>")
        return tipo, name
    if kind == "handoff":
        env, slash, number = body.partition("/")
        if not slash:
            raise ValueError("use handoff:<ambiente>/<NNN>")
        return env, number
    return (body,)


def _local_problem(kind, parts):  # noqa: PLR0911 - one return per ref kind rule
    # pylint: disable=too-many-return-statements  # one return per ref kind rule
    """Motivo de uma ref local já decodificada fora da regra do tipo; `None` quando vale."""
    from .assets import valid_name  # tardio: assets puxa o roteiro; este módulo fica folha no import
    from .sdk.names import NAME_RE  # tardio: o pacote sdk carrega o registro

    expected = 2 if kind in ("asset", "handoff") else 1
    if len(parts) != expected or not all(isinstance(part, str) and part for part in parts):
        return "valor vazio"
    if kind == "asset":
        tipo, name = parts
        if not _NAME_RE.fullmatch(tipo):
            return f'tipo "{_echo(tipo)}" tem que ter de 1 a 32 caracteres a-z, 0-9, _ ou -, começando por letra'
        if name != name.strip() or not valid_name(name):
            return "o nome do componente usa letras, números, espaço, - ou _, até 80 caracteres"
        return None
    if kind == "handoff":
        env, number = parts
        if not NAME_RE.fullmatch(env):
            return f'ambiente "{_echo(env)}" tem que ter de 2 a 32 caracteres a-z, 0-9 e _, começando por letra'
        if not _HANDOFF_NUMBER_RE.fullmatch(number):
            return "o número do hand-off tem de 3 a 9 dígitos (ex.: 003)"
        return None
    pattern, example = (_SEG_RE, "seg_3f9a1c2b7d") if kind == "seg" else (_MEDIA_RE, "0123456789abcdef")
    return None if pattern.fullmatch(parts[0]) else f"o id tem a forma {example}"


def _parse_local(text, kind, body):
    try:
        parts = tuple(decode(piece) for piece in _split_local(kind, body))
    except ValueError as exc:
        raise _invalid(text, str(exc)) from None
    problem = _local_problem(kind, parts)
    if problem:
        raise _invalid(text, problem)
    return Ref(kind, parts)


def parse(text: str) -> Ref:
    """A ref de `text`, decodificada; `ValueError` (`Referência inválida "<ref>": <motivo>.`)."""
    if not isinstance(text, str):
        raise _invalid(text, _SHAPE)
    if len(text) > MAX_REF:
        raise _invalid(text, f"passou de {MAX_REF} caracteres")
    kind, colon, body = text.partition(":")
    if not colon or not kind:
        raise _invalid(text, _SHAPE)
    if kind in ACCEPTED_LOCAL:
        return _parse_local(text, kind, body)
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
    if ref.kind in ACCEPTED_LOCAL:
        parts = tuple(unicodedata.normalize("NFC", part) if isinstance(part, str) else part for part in ref.parts)
        problem = _local_problem(ref.kind, parts)
        if problem:
            raise _invalid(f"{ref.kind}:", problem)
        if ref.kind == "asset":
            return f"asset:{parts[0]}:{encode(parts[1])}"
        if ref.kind == "handoff":
            return f"handoff:{parts[0]}/{parts[1]}"
        return f"{ref.kind}:{parts[0]}"
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
    """As duas refs são a mesma? `asset` compara tipo e nome dobrado (`Logo` = `logo`); o resto, a forma canônica."""
    left, right = parse(a), parse(b)
    if left.kind == right.kind == "asset":
        return left.parts[0] == right.parts[0] and fold(left.parts[1]) == fold(right.parts[1])
    return format_ref(left) == format_ref(right)


def _recipe_problem(owner, slug, number):
    """Motivo de dono, slug ou versão de receita fora da regra; dono e versão `None` não são conferidos."""
    for label, value in (("dono", owner), ("slug", slug)):
        if value is None and label == "dono":
            continue
        if not isinstance(value, str) or len(value) > SLUG_MAX or not SLUG_RE.fullmatch(value):
            return f"o {label} da receita tem que ser um slug: minúsculas, números e -, até {SLUG_MAX} caracteres"
    if number is not None and (type(number) is not int or not 1 <= number <= _VERSION_MAX):
        return f"a versão da receita tem que ser um inteiro de 1 a {_VERSION_MAX}"
    return None


def recipe_ref(owner: str, slug: str, number: int) -> str:
    """`cat:getbrolls/recipe/<dono>.<slug>@<n>`; `ValueError` com dono, slug ou versão fora da regra."""
    if owner is None:
        raise _invalid("cat:getbrolls/recipe/…", "falta o dono: a ref canônica traz <dono>.<slug>")
    if number is None:
        raise _invalid("cat:getbrolls/recipe/…", "falta a versão: a ref canônica traz @<N>")
    problem = _recipe_problem(owner, slug, number)
    if problem:
        raise _invalid(f"cat:{RECIPE_ENGINE}/{RECIPE_TYPE}/{_echo(owner)}.{_echo(slug)}@{number}", problem)
    return catalog_ref(RECIPE_ENGINE, RECIPE_TYPE, f"{owner}.{slug}", str(number))


def recipe_parts(text: str) -> tuple[str | None, str, int | None]:
    """`(dono ou None, slug, versão ou None)` de `<slug>[@N]`, `<dono>.<slug>[@N]` ou da ref canônica.

    Sem dono, quem chama usa o cliente do projeto; sem `@N`, a versão que ele escolher. A ref
    canônica é conferida já decodificada (SPEC2 §2): `%2E` vale o ponto entre dono e slug
    (`a%2Ex` é `a.x`), mas `%2F` nunca vira pedaço de caminho, porque dono e slug passam pela
    regra de slug.
    """
    if not isinstance(text, str) or not text or len(text) > MAX_REF:
        raise _invalid(text, _RECIPE_SHAPE)
    if ":" in text:
        ref = parse(text)
        if ref.kind != CATALOG or ref.parts[:2] != (RECIPE_ENGINE, RECIPE_TYPE):
            raise _invalid(text, f"receita é cat:{RECIPE_ENGINE}/{RECIPE_TYPE}/<dono>.<slug>@<versão>")
        ident, raw_number = ref.parts[2], ref.version or ""
        if "." not in ident:
            raise _invalid(text, f"a ref canônica traz o dono: cat:{RECIPE_ENGINE}/{RECIPE_TYPE}/<dono>.<slug>@<N>")
    else:
        ident, at, raw_number = text.partition("@")
        if at and not raw_number:
            raise _invalid(text, _RECIPE_SHAPE)
    if raw_number and not _VERSION_NUMBER_RE.fullmatch(raw_number):
        raise _invalid(text, "a versão da receita tem que ser um inteiro a partir de 1")
    pieces = ident.split(".")
    if len(pieces) > _RECIPE_PIECES:
        raise _invalid(text, "a receita tem no máximo um ponto, entre o dono e o slug")
    owner, slug = (None, pieces[0]) if len(pieces) == 1 else (pieces[0], pieces[1])
    number = int(raw_number) if raw_number else None
    problem = _recipe_problem(owner, slug, number)
    if problem:
        raise _invalid(text, problem)
    return owner, slug, number

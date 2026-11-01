"""Credenciais pela forma: a fonte única das formas de segredo, para `runtime.redact` e para o `event=op`
(`effects`), o que ninguém pôs num `api_key=` nem num cabeçalho.

Cada forma começa por um prefixo que o provedor documenta: chaves `sk-…` (OpenAI, Anthropic, DeepSeek), Stripe
(`sk_live_`, `rk_test_`, `whsec_`…), GitHub (`ghp_`, `github_pat_`), GitLab (`glpat-`), Slack (`xoxb-`,
`xapp-`), Google (`AIza`, `ya29.`, `GOCSPX-`), Hugging Face (`hf_`), npm (`npm_`), PyPI (`pypi-`), Twilio
(`SK` + 32 hex), SendGrid (`SG.`), Mailgun (`key-` + 32 hex), DigitalOcean (`dop_v1_`), Shopify (`shpat_`),
AWS (`AKIA…`), JWT, o token de bot do Discord e do Telegram, a `AccountKey=` do Azure, blocos `PRIVATE KEY`
e outros. Só cobertura: cada forma é um prefixo mais um tamanho mínimo, sem exceção de precisão nenhuma (nada
de "parece palavra" nem de limite à esquerda ou à direita), então um nome como `sk-learn-pipeline-…`,
`xoxo-…` ou `AKIAPRESSRELEASE` também sai `[REDACTED]`: redigir uma palavra é aceitável, vazar uma chave não é.
Todo padrão é linear: nada aninhado, e o JWT é procurado por segmento (`_jwt`).
"""

import re

REDACTED = "[REDACTED]"

# The rest of a key whose own part has a fixed length: anything glued to it goes with it (recall first), so a
# key is never cut short and nothing of it is left (`AKIA…EXAMPLEsecret` goes whole).
_REST = r"[A-Za-z0-9_-]*"
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----(?:(?!-----END )[\s\S]){0,20000}"
    r"(?:-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----)?"
)
# `(prefixos, forma)`: os prefixos são o que o `event=op` procura (`secret_shaped`); a forma, o que sai
# `[REDACTED]`. Sem prefixo: uma forma cujo começo é comum demais para marcar um alvo sozinho (`SK`, `key-`) ou
# que não tem começo fixo (o token do Discord e do Telegram). Nenhuma forma pede limite à esquerda: uma chave
# colada ao texto da frente (`chaveghp_…`) também sai.
SHAPES = (
    (("ghp_", "gho_", "ghu_", "ghs_", "ghr_"), r"gh[pousr]_[A-Za-z0-9]{20,}"),
    (("github_pat_",), r"github_pat_[A-Za-z0-9_]{20,}"),
    (
        ("glpat-", "gldt-", "glrt-", "glptt-", "glft-", "glcbt-", "glsoat-"),
        r"gl(?:pat|dt|rt|ptt|ft|cbt|soat)-[A-Za-z0-9_-]{20,}",
    ),
    (("glsa_",), r"glsa_[A-Za-z0-9_]{32,}"),
    (("sk-",), r"sk-[A-Za-z0-9_-]{20,}"),
    (("xoxa-", "xoxb-", "xoxe", "xoxp-", "xoxr-", "xoxs-"), r"xox[a-z]-[A-Za-z0-9_-]{6,}"),
    (("xapp-",), r"xapp-[A-Za-z0-9_-]{10,}"),
    (
        ("akia", "asia", "agpa", "aida", "aroa", "anpa", "anva", "aipa", "abia", "acca"),
        r"(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA|ABIA|ACCA)[A-Za-z0-9]{16}" + _REST,
    ),
    (
        ("sk_live_", "sk_test_", "rk_live_", "rk_test_", "pk_live_", "pk_test_"),
        r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}",
    ),
    (("whsec_",), r"whsec_[A-Za-z0-9+/=]{20,}"),
    (("aiza",), r"AIza[0-9A-Za-z_-]{35}" + _REST),
    (("ya29.",), r"ya29\.[0-9A-Za-z_-]{20,}"),
    (("gocspx-",), r"GOCSPX-[A-Za-z0-9_-]{20,}"),
    (("hf_",), r"hf_[A-Za-z0-9]{30,}"),
    (("npm_",), r"npm_[A-Za-z0-9]{36}" + _REST),
    (("pypi-",), r"pypi-[A-Za-z0-9_-]{50,}"),
    ((), r"SK[0-9a-fA-F]{32}" + _REST),  # Twilio
    (("sg.",), r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    ((), r"key-[0-9a-f]{32}" + _REST),  # Mailgun
    (("dop_v1_", "doo_v1_", "dor_v1_"), r"do[por]_v1_[a-f0-9]{64}" + _REST),
    (("shpat_", "shpss_", "shpca_", "shppa_"), r"shp(?:at|ss|ca|pa)_[a-fA-F0-9]{32}" + _REST),
    (("gsk_",), r"gsk_[A-Za-z0-9]{40,}"),
    (("pplx-",), r"pplx-[A-Za-z0-9]{40,}"),
    (("xai-",), r"xai-[A-Za-z0-9]{40,}"),
    (("lin_api_",), r"lin_api_[A-Za-z0-9]{32,}"),
    (("ntn_",), r"ntn_[A-Za-z0-9]{40,}"),
    ((), r"secret_[A-Za-z0-9]{40,}"),  # Notion
    (("pmak-",), r"PMAK-[a-f0-9]{24}-[a-f0-9]{30,}"),
    ((), r"dapi[a-f0-9]{32}" + _REST),  # Databricks
    (("sq0atp-", "sq0csp-"), r"sq0(?:atp|csp)-[A-Za-z0-9_-]{22,}"),
    (("age-secret-key-",), r"AGE-SECRET-KEY-1[0-9A-Z]{50,}"),
    ((), r"[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,40}" + _REST),  # Discord bot
    ((), r"\d{8,10}:[A-Za-z0-9_-]{35}" + _REST),  # Telegram bot
)
_SHAPES = re.compile("|".join(f"(?:{pair[1]})" for pair in SHAPES))
# A JWT: `eyJ` and 8 or more key characters, then two more dotted parts (2 or more, then the signature: any
# length, even none: an unsigned token ends with its last dot). Searched per
# segment of a whole dotted run (`_jwt`): a pattern that could start at every `eyJ` of a long run would rescan
# the run from each of them.
_DOTTED = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]*eyJ[A-Za-z0-9_.-]*")
_JWT_MIN = (8, 2, 0)
# The key of an Azure connection string (`AccountKey=…;`, `SharedAccessKey=…`): only the value goes.
_AZURE_KEY = re.compile(r"((?:Account|SharedAccess)Key=)[A-Za-z0-9+/=]{20,}")
# A DSN or URL with its credentials in it (`mongodb+srv://user:pass@host`, `redis://:pass@host`, any scheme):
# the whole user part goes, the host stays. `http(s)` URLs go whole later; this is for the rest. Every class is
# bounded and none of them takes a `/`, so each start looks no further than 256 characters of its own authority.
# The user stops at the first `:` (no class overlaps the `:` that follows it), so there is nothing to backtrack
# but the password's own `@`. The password may hold spaces (`user:pa ss@host`): it goes up to the LAST `@` of
# the line within the bound.
_DSN = re.compile(r"(?<![A-Za-z0-9+.-])([A-Za-z][A-Za-z0-9+.-]{0,30}://)[^\s/@:'\"<>]{0,256}:[^\r\n/'\"<>]{1,256}@")
# Any identifier ENDING in one of these keywords (`access_token`, `api_key`, `apikey`, `client_secret`,
# `X-Amz-Signature`, `X-Amz-Credential`), plus credential-shaped names that don't (`Key-Pair-Id`), then `=` and a
# value. A full URL is already wiped out whole by the `https?://` pass of `runtime.redact`. No nested quantifiers.
# The value after the `=`/`:`: a quoted one whole, spaces and all (bounded: an unclosed quote must not rescan the
# line from every start), an unclosed quote to the end of its line, or up to a blank, `&` or quote. A value already
# redacted is taken as it is: a second pass leaves what follows it (`, y`) alone.
_VALUE = r"(?:\[REDACTED\]|\"[^\"\r\n]{0,500}\"|'[^'\r\n]{0,500}'|[\"'][^\r\n]*|[^&\s\"'<>]+)"
# Only the keyword is matched, never the name in front of it: a prefix scan (`[A-Za-z0-9_.-]{0,40}`) would try
# 40 steps from every position of a long run of `-`. The keyword must be a whole segment of the name (it cannot
# follow a letter or digit: `monkey=` and `turkey=` are not secrets); what comes before it stays as it is.
_QUERY_SECRET = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(api[_-]?key|access_?token|key|token|secret|signature|sig|policy|credential|password|passwd|pwd|senha"
    r"|Key-Pair-Id)"
    r"\s*=\s*" + _VALUE
)
# `Token: abc`, `api-key: abc`, `password: 'a b'`: the same names with a colon, for the few that read as a label
# there (`key:`, `policy:` and `signature:` read as prose, so they stay `=` only). A colon is also prose
# (`the token: is required`, `unexpected token: foo`): an unquoted `token`/`api-key` value goes only when it looks
# like a credential (`_looks_secret`); a quoted or already redacted one always goes. A password is short and
# plain (`senha: abc123`), so for the names of `_PASSWORDS` the next word always goes, whatever it looks like,
# except a lowercase word of `_PROSE` (`password: required`).
_LABEL = re.compile(r"(?i)(?<![A-Za-z0-9])(api[_-]?key|token|secret|password|passwd|pwd|senha)\s*:\s*" + _VALUE)
_PASSWORDS = frozenset({"password", "passwd", "pwd", "senha", "secret"})
_PROSE = frozenset(
    {"is", "required", "invalid", "missing", "expired", "empty", "wrong", "not"}
    | {"obrigatória", "obrigatório", "inválida", "inválido", "incorreta", "vazia", "vazio", "não", "ausente", "expirou"}
)
_SECRET_MIN = 8
PREFIXES = tuple(sorted({prefix for pair in SHAPES for prefix in pair[0]} | {"eyj"}))


def _jwt(found):
    """O run pontuado `found` do primeiro `eyJ` que abre um JWT até o fim, trocado por `[REDACTED]`: cada segmento
    é olhado uma vez (o primeiro `eyJ` dele basta: um mais adiante teria menos caracteres depois)."""
    run = found.group(0)
    parts = run.split(".")
    offset = 0
    for index, part in enumerate(parts):
        start = part.find("eyJ")
        rest = parts[index + 1 : index + 3]
        if (
            start >= 0
            and len(part) - start - 3 >= _JWT_MIN[0]
            and len(rest) == len(_JWT_MIN) - 1
            and all(len(piece) >= size for piece, size in zip(rest, _JWT_MIN[1:], strict=True))
        ):
            return run[: offset + start] + REDACTED
        offset += len(part) + 1
    return run


def hide(text) -> str:
    """`text` com cada credencial pela forma trocada por `[REDACTED]`."""
    value = _PRIVATE_KEY.sub(REDACTED, str(text))
    value = _DOTTED.sub(_jwt, value)
    value = _SHAPES.sub(REDACTED, value)
    value = _AZURE_KEY.sub(lambda found: found.group(1) + REDACTED, value)
    return _DSN.sub(lambda found: f"{found.group(1)}{REDACTED}@", value)


def hide_labelled(text) -> str:
    """`text` com o valor de cada nome de segredo (`token=…`, `passwd: …`, `password="a b"`) trocado."""
    value = _QUERY_SECRET.sub(lambda found: f"{found.group(1)}={REDACTED}", str(text))
    return _LABEL.sub(_label, value)


def _looks_secret(value) -> bool:
    """`value`, the unquoted word after a `name:`, can be a credential: 8 characters or more, no blank, and not a
    flag (`--token`), a path (`/x`, `~/x`) or a tag (`<x>`)."""
    return len(value) >= _SECRET_MIN and value[0] not in "-/~<" and not any(char.isspace() for char in value)


def _is_prose(value) -> bool:
    """`value`, the word after a `password:`, is a lowercase word of `_PROSE` (`required`, `não`), with or without
    the punctuation that ends a sentence."""
    word = value.split(maxsplit=1)[0].rstrip(".,;)")
    return word in _PROSE


def _label(found):
    """The `name: value` of `_LABEL` with its value redacted, or as it was when the value is prose."""
    value = found.string[found.end(1) : found.end()].lstrip().removeprefix(":").lstrip()
    if value[0] not in "\"'" and value != REDACTED:
        prose = _is_prose(value) if found.group(1).lower() in _PASSWORDS else not _looks_secret(value)
        if prose:
            return found.group(0)
    return f"{found.group(1)}: {REDACTED}"


_PREFIX_SEARCH = re.compile(r"(?i)(?:^|[^a-z0-9])(?:" + "|".join(re.escape(prefix) for prefix in PREFIXES) + ")")


def secret_shaped(text) -> bool:
    """`text` tem cara de credencial: começa (ou tem uma palavra que começa) por um prefixo de `SHAPES`, ou
    alguma forma dele seria redigida. É o que faz o `event=op` mostrar só o hash de um alvo."""
    value = str(text)
    return _PREFIX_SEARCH.search(value) is not None or hide(value) != value

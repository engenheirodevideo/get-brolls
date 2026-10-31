"""Redaction of text that is logged, shown or pasted into a report: secrets, URLs and the home folder.

Everything here is a plain string transform over `str(text)`: idempotent and never raising. The key shapes
come from `_tokens` (shared with the `event=op` line of `effects`). This module reads no file and imports
only `_paths` and `_tokens` from the package, so every layer can redact."""

import os
import re
from pathlib import Path

from . import _paths, _tokens

SENSITIVE_HEADERS = ("Authorization", "Cookie", "Set-Cookie", "X-Api-Key")
# `Name: value` and a quoted/JSON form (`"Name": "value"`, `{'Name': 'value'}`, `=` too); the value stops at a quote
# or newline, so the braces/quotes of a dict repr survive. No nested quantifiers — linear on adversarial input.
_HEADER_PATTERN = re.compile(
    r"(?i)\b(" + "|".join(re.escape(h) for h in SENSITIVE_HEADERS) + r")\s*[\"']?\s*[:=]\s*[\"']?[^\r\n\"']+"
)
# A Bearer or Basic credential with no header name in front of it: the whole value, whatever its characters,
# up to a blank, a quote or the line end (base64 `+/=`, a JWT, `%`-encoded text).
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s\"'<>]+")
# Nome de parâmetro/chave com cara de segredo, casado com o nome INTEIRO
# (`fullmatch`): a palavra secreta fecha o nome — `auth_token`, `client_secret`,
# `X-Amz-Signature` casam; `oauth2_token_id`, `monkey`, `profile_id` não. Os nomes
# soltos do fim cobrem assinaturas de CDN (Akamai `__token__`/`hdnts`/`hdnea`,
# CloudFront `Key-Pair-Id`) e os prefixos de assinatura S3/GCS.
SECRET_NAME_RE = re.compile(
    r"(?i)(?:[a-z0-9_.-]{0,40}[_.-])?"
    r"(?:api_?key|apikey|access_?token|key|token|secret|signature|sig|password|passwd|pwd|senha|hmac|jwt"
    r"|credentials?|policy|authorization)"
    r"|__token__|hdnts|hdnea|key-pair-id|x-amz-[a-z0-9-]*|x-goog-[a-z0-9-]*"
)


def secret_name(name):
    """`True` quando `name` (chave de JSON ou de query string) parece guardar um segredo."""
    return isinstance(name, str) and SECRET_NAME_RE.fullmatch(name) is not None


# Quebra de linha ou caractere de controle: o que deixa um valor "fugir" da linha
# dele num Markdown gerado (ORIGEM.md, credits.md) e forjar outra linha.
_LINE_BREAKING = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")


def one_line(value):
    """`value` como texto de uma linha só: controle/quebra de linha viram espaço.

    Texto normal (sem controle) volta idêntico — a saída dos built-ins não muda."""
    return _LINE_BREAKING.sub(" ", str(value))


def scrub_home(text):
    """Replace the user's home folder with `~`, only as a whole path (never inside `/Users/anax`). Never raises.

    Only for text nobody acts on: the diagnostics file and the `repr`/`traceback`
    fields, which people paste into bug reports. It is deliberately NOT part of
    `redact()`: error messages name paths the person (or the agent driving the CLI)
    must open or pass back as an argument, and `~` inside quotes is not expanded by a
    shell nor understood by a file reader. Both the raw and the JSON-escaped form of
    the prefix are replaced, so it also works on an already serialized line on Windows —
    escaped both ways: `ensure_ascii=True` (`Jo\\u00e3o`) and `ensure_ascii=False` (the way
    the diagnostics line is written: only the backslashes escaped).
    """
    value = str(text)
    try:
        home = str(Path.home())
    except RuntimeError:
        # No HOME/USERPROFILE to resolve: nothing to scrub.
        return value
    if home in ("", "/", "\\"):
        return value
    # As written, resolved (a linked HOME) and without `/private`, in every encoding.
    homes = _paths.with_linked({home, os.path.realpath(home)})
    windows = re.match(r"[A-Za-z]:[\\/]", home) is not None  # a Windows home ignores case on any system
    return _paths.replace_spellings(value, homes, "~", ignore_case=windows)


_URL_PATTERN = re.compile(r'https?://[^\s"<>]*')
_HOST_CHAR = re.compile(r"[A-Za-z0-9]")


def _omit_url(match):
    """A URL de verdade vira "[URL omitida]"; a forma citada num texto nosso fica.

    "https://…", "https://," ou "https://<host>" não têm host nenhum (sem letra nem
    dígito antes da primeira `/`): é exemplo de ajuda, não dado de quem chamou."""
    url = match.group(0)
    host = url.split("://", 1)[1].split("/", 1)[0]
    return url if _HOST_CHAR.search(host) is None else "[URL omitida]"


REDACT_LIMIT = 1200


def redact(text, limit: int | None = REDACT_LIMIT):
    """Strip provider keys, secret-shaped headers/query values and URLs.

    Idempotent (running it twice yields the same string) and never raises — every
    step is a plain string replace/regex substitution over `str(text)`. Safe for
    user-facing messages: paths are left intact (see `scrub_home` for why). The result
    keeps the first `limit` characters; `limit=None` keeps everything.
    """
    value = _tokens.hide(text)  # keys by shape first: a URL around a key block must not hide its first line
    for key in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "YOUTUBE_API_KEY"):
        secret = os.getenv(key)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = _HEADER_PATTERN.sub(lambda m: f"{m.group(1)}: [REDACTED]", value)
    value = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)} [REDACTED]", value)
    value = _tokens.hide_labelled(value)
    value = _URL_PATTERN.sub(_omit_url, value)  # last: every credential is out before the URLs go
    return value if limit is None else value[:limit]


# Par `'chave': 'valor'` (repr de dict) ou `"chave": "valor"` (JSON), com o valor entre aspas
# simples ou duplas (e `b'…'` de bytes). Cada lado da alternância do valor exclui o que o
# outro começa (barra invertida): sem retrocesso aninhado, linear em entrada hostil.
_QUOTED_PAIR_PATTERN = re.compile(
    r"""(?P<key>(?P<kq>["'])(?P<name>[A-Za-z0-9_.-]{1,80})(?P=kq))(?P<sep>\s*:\s*)b?"""
    r"""(?P<value>'(?:[^'\\\r\n]|\\.)*'|"(?:[^"\\\r\n]|\\.)*")"""
)


def _hide_quoted_secret(match):
    if not secret_name(match["name"]):
        return match.group(0)
    quote = match["value"][0]
    return f"{match['key']}{match['sep']}{quote}[REDACTED]{quote}"

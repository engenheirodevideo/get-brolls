"""Corpus gerado: cada forma de credencial, com chaves sorteadas, em todo contexto e em todo corte, nas 9 portas.

Um corpus de exemplos fixos deixa passar a exceção que ninguém pensou em testar (três achados seguidos vieram
daí). Aqui cada forma de `_tokens.SHAPES` tem um gerador; cada chave gerada vai colada ao texto dos dois lados,
numa query, numa URL, em JSON (escapado uma e duas vezes), entre aspas, no fim do texto, entre linhas, com o
corpo em maiúsculas e minúsculas trocadas onde o provedor aceita, e cortada em cada posição perto do limite de
cada porta que corta. Nenhum pedaço do corpo da chave pode sobrar. Os valores são sorteados (semente fixa).
"""

import json
import random
import string
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _fake_secrets import joined
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from test_secret_corpus import ENTRY_POINTS

from getbrolls import _tokens, labels, logs, redaction, runtime

ALNUM = string.ascii_letters + string.digits
BASE64URL = ALNUM + "-_"
BASE64 = ALNUM + "+/"
HEX = "0123456789abcdefABCDEF"
HEX_LOWER = "0123456789abcdef"
UPPER_DIGITS = string.ascii_uppercase + string.digits
SAMPLES = 3


def _generators(rng):
    """`nome → função que sorteia uma chave` para cada forma (prefixo documentado, corpo do tamanho de verdade)."""

    def pick(size, alphabet=ALNUM):
        return "".join(rng.choice(alphabet) for _ in range(size))

    def one(*choices):
        return rng.choice(choices)

    return {
        "openai sk-": lambda: "sk-" + pick(48),
        "openai sk-proj-": lambda: "sk-proj-" + pick(64, BASE64URL),
        "anthropic sk-ant-": lambda: "sk-ant-api03-" + pick(93, BASE64URL) + "AA",
        "deepseek sk- hex": lambda: "sk-" + pick(32, HEX_LOWER),
        "sk- de palavras": lambda: "sk-" + "-".join(pick(6, string.ascii_lowercase) for _ in range(4)),
        "github gh*_": lambda: "gh" + one(*"pousr") + "_" + pick(36),
        "github_pat_": lambda: "github_pat_" + pick(22) + "_" + pick(59),
        "gitlab": lambda: one("glpat-", "gldt-", "glrt-") + pick(20, BASE64URL),
        "grafana glsa_": lambda: "glsa_" + pick(32) + "_" + pick(8, HEX_LOWER),
        "slack xox": lambda: "xox" + one(*"abposr") + "-" + pick(12, string.digits) + "-" + pick(24),
        "slack xox sem dígitos": lambda: "xox" + one(*"abp") + "-" + pick(20),
        "slack xoxe": lambda: "xoxe.xoxp-1-" + pick(60),
        "slack xapp-": lambda: "xapp-1-" + pick(11) + "-" + pick(13, string.digits) + "-" + pick(64, HEX_LOWER),
        "aws": lambda: one("AKIA", "ASIA") + pick(16, UPPER_DIGITS),
        "aws de corpo misto": lambda: one("AKIA", "ASIA") + pick(16),
        "jwt": lambda: "eyJ" + pick(20, BASE64URL) + ".eyJ" + pick(30, BASE64URL) + "." + pick(43, BASE64URL),
        "jwt de payload qualquer": lambda: (
            "eyJ" + pick(20, BASE64URL) + "." + pick(30, BASE64URL) + "." + pick(43, BASE64URL)
        ),
        "stripe": lambda: one("sk", "rk", "pk") + "_" + one("live", "test") + "_" + pick(24),
        "stripe whsec_": lambda: "whsec_" + pick(32),
        "google AIza": lambda: "AIza" + pick(35, BASE64URL),
        "google ya29.": lambda: "ya29." + pick(60, BASE64URL),
        "google GOCSPX-": lambda: "GOCSPX-" + pick(28, BASE64URL),
        "huggingface hf_": lambda: "hf_" + pick(34),
        "npm npm_": lambda: "npm_" + pick(36),
        "pypi pypi-": lambda: "pypi-AgEIcHlwaS5vcmc" + pick(60, BASE64URL),
        "twilio SK": lambda: "SK" + pick(32, HEX),
        "sendgrid SG.": lambda: "SG." + pick(22, BASE64URL) + "." + pick(43, BASE64URL),
        "mailgun key-": lambda: "key-" + pick(32, HEX_LOWER),
        "digitalocean": lambda: one("dop", "doo", "dor") + "_v1_" + pick(64, HEX_LOWER),
        "shopify": lambda: one("shpat_", "shpss_", "shpca_", "shppa_") + pick(32, HEX),
        "groq gsk_": lambda: "gsk_" + pick(52),
        "perplexity pplx-": lambda: "pplx-" + pick(48),
        "xai xai-": lambda: "xai-" + pick(80),
        "linear lin_api_": lambda: "lin_api_" + pick(40),
        "notion ntn_": lambda: "ntn_" + pick(46),
        "notion secret_": lambda: "secret_" + pick(43),
        "postman PMAK-": lambda: "PMAK-" + pick(24, HEX_LOWER) + "-" + pick(34, HEX_LOWER),
        "databricks dapi": lambda: "dapi" + pick(32, HEX_LOWER),
        "square sq0": lambda: one("sq0atp-", "sq0csp-") + pick(43, BASE64URL),
        "age": lambda: "AGE-SECRET-KEY-1" + pick(58, UPPER_DIGITS),
        "discord": lambda: one(*"MNO") + pick(25) + "." + pick(6) + "." + pick(38),
        "telegram": lambda: pick(9, string.digits) + ":" + pick(35, BASE64URL),
        "azure AccountKey=": lambda: "AccountKey=" + pick(86, BASE64) + "==",
        "pem": lambda: (
            joined("-----BEGIN ", "RSA PRIVATE KEY-----\n")
            + pick(64, BASE64)
            + "\n"
            + pick(64, BASE64)
            + "\n-----END RSA PRIVATE KEY-----"
        ),
        "pem sem fim": lambda: joined("-----BEGIN ", "PRIVATE KEY-----\n") + pick(64, BASE64) + "\n" + pick(40, BASE64),
    }


def _core(key):
    """O pedaço do corpo que nunca pode sobrar: 12 caracteres perto do fim (longe do prefixo)."""
    return key[-14:-2]


def _swapped(key):
    """A chave com o corpo (o que vem depois do último separador do prefixo) em maiúsculas e minúsculas trocadas;
    sem separador no começo, a chave como veio: o prefixo nunca muda de caixa."""
    end = max(key.rfind(sep, 0, 16) for sep in "_-.")
    return key if end < 0 else key[: end + 1] + key[end + 1 :].swapcase()


def _contexts(key):
    return {
        "sozinha": key,
        "colada à direita": key + "seguido",
        "colada à esquerda": "chave" + key,
        "colada dos dois lados": "abc" + key + "xyz",
        "colada a dígitos": "123" + key + "456",
        "query": "GET /v1/busca?id=1&t=" + key + "&q=gato",
        "url": "https://exemplo.com/a?token=" + key,
        "aspas duplas": 'valor "' + key + '" fim',
        "aspas simples": "valor '" + key + "' fim",
        "crase": "valor `" + key + "` fim",
        "json": json.dumps({"token": key, "outro": 1}),
        "json escapado duas vezes": json.dumps(json.dumps({"token": key})),
        "fim do texto": "o valor é " + key,
        "começo de linha": key + "\nlinha 2",
        "entre linhas": "linha 1\n" + key + "\nlinha 3",
        "tabulação": "valor\t" + key + "\tfim",
        "depois de dois pontos": "token:" + key,
        "depois de igual": "TOKEN=" + key,
        "entre parênteses": "(" + key + ")",
        "depois de partes pontuadas": "v1.2." + key,
    }


# Formas cujo corpo tem caixa fixa pelo provedor (maiúsculas da AWS e do age, hexadecimal minúsculo) e o bloco PEM.
CASE_FIXED = frozenset(
    {"aws", "age", "mailgun key-", "databricks dapi", "digitalocean", "postman PMAK-", "pem", "pem sem fim"}
    | {"deepseek sk- hex", "sk- de palavras"}
)


def _credential_contexts(key):
    """A chave depois de um cabeçalho, de uma atribuição ou de um nome de JSON, ao lado de um caminho ou de uma
    URL, e dentro de um segmento de caminho."""
    return {
        "Authorization: Bearer": "Authorization: Bearer " + key,
        "Authorization: Basic": "Authorization: Basic " + key,
        "Bearer solto": "curl -H x Bearer " + key + " fim",
        "Basic solto": "Basic " + key,
        "token=": "token=" + key,
        "?access_token=": "GET /v1/x?access_token=" + key + "&q=1",
        "json api_key": '{"api_key": "' + key + '"}',
        "X-Api-Key:": "X-Api-Key: " + key,
        "caminho antes": "/Volumes/Disco/pasta/" + key,
        "caminho depois": key + "/Volumes/Disco/pasta",
        "caminho dos dois lados": "/x/" + key + "/y",
        "url antes": "https://exemplo.com/a/" + key,
        "url depois": key + " https://exemplo.com/a?b=1",
        "caminho e url": "/tmp/a " + key + " https://exemplo.com/b",
    }


def _fragments(key, size=8):
    """Cada pedaço de `size` caracteres do corpo da chave (sem o prefixo nem quebra de linha)."""
    body = key[len(key) // 3 :]
    return {body[index : index + size] for index in range(len(body) - size + 1)} - {"PRIVATE ", "RSA PRIV"}


def _keys():
    rng = random.Random(20261011)
    found = []
    for name, make in _generators(rng).items():
        for _sample in range(SAMPLES):
            key = make()
            found.append((name, key))
            if _swapped(key) != key and name not in CASE_FIXED:
                found.append((name + " (caixa trocada)", _swapped(key)))
    return found


# As portas que cortam, e onde: o valor de log, a cauda do stderr, a mensagem e o `repr`.
CUTS = (
    (500, logs._format_value),  # pylint: disable=protected-access
    (600, runtime.stderr_tail),
    (runtime.REDACT_LIMIT, runtime.redact),
    (runtime.REDACT_LIMIT, ENTRY_POINTS["envelope"]),
)


class GeneratedSecretTests(unittest.TestCase):
    def test_no_generated_key_survives_any_context_at_any_entry_point(self):
        keys = _keys()
        self.assertGreater(len(keys), 120)
        for name, key in keys:
            core = _core(key)
            for where, text in _contexts(key).items():
                for entry, run in ENTRY_POINTS.items():
                    out = run(text)
                    if core in out or core.lower() in out.lower().replace("[redacted]", ""):
                        self.fail(f"{name} sobrou ({where}, {entry}): {out[-160:]!r}")

    def test_no_fragment_survives_after_a_credential_name_or_next_to_a_path(self):
        for name, key in _keys()[::SAMPLES]:
            pieces = {piece for piece in _fragments(key) if "\n" not in piece and "-----" not in piece}
            for where, text in _credential_contexts(key).items():
                for entry, run in ENTRY_POINTS.items():
                    out = run(text)
                    left = [piece for piece in pieces if piece in out]
                    if left:
                        self.fail(f"{name} sobrou ({where}, {entry}): {left[:2]} em {out[-120:]!r}")

    def test_a_bearer_or_basic_credential_goes_whole_whatever_its_characters(self):
        rng = random.Random(1011)
        alphabet = ALNUM + "/+=._~-%"
        for _sample in range(40):
            value = rng.choice(ALNUM) + "".join(rng.choice(alphabet) for _ in range(rng.randrange(12, 60)))
            pieces = {value[index : index + 8] for index in range(len(value) - 7)}
            for text in (
                "Authorization: Bearer " + value,
                "Authorization: Basic " + value,
                "Proxy-Authorization: Basic " + value + "\nproxima linha",
                '{"Authorization": "Bearer ' + value + '"}',
                "authorization=Bearer%20" + value,
                "falhou com Bearer " + value + " no fim",
                "falhou com bearer " + value,
                "usou Basic " + value + ", sem sucesso",
                "header 'Authorization: Token " + value + "' recusado",
            ):
                for entry, run in ENTRY_POINTS.items():
                    out = run(text)
                    left = [piece for piece in pieces if piece in out]
                    if left:
                        self.fail(f"{text[:40]!r} ({entry}): sobrou {left[:2]} em {out[-120:]!r}")

    def test_no_generated_key_survives_any_cut(self):
        for name, key in _keys()[::SAMPLES]:
            core = _core(key)
            for cap, run in CUTS:
                for offset in range(max(0, cap - len(key) - 4), cap + 4):
                    out = run("." * offset + " " + key + " fim")
                    if core in out:
                        self.fail(f"{name} sobrou com o corte em {offset} ({cap}): {out[-80:]!r}")


class _Spy:
    """Um padrão (`re.Pattern`) ou uma função que guarda o texto que recebeu antes de fazer o trabalho de sempre."""

    def __init__(self, real, seen):
        self.real, self.seen = real, seen

    def sub(self, repl, text, *args, **kwargs):
        self.seen.append(str(text))
        return self.real.sub(repl, text, *args, **kwargs)

    def finditer(self, text, *args, **kwargs):
        self.seen.append(str(text))
        return self.real.finditer(text, *args, **kwargs)

    def __call__(self, text, *args, **kwargs):
        self.seen.append(str(text))
        return self.real(text, *args, **kwargs)


class PipelineOrderTests(unittest.TestCase):
    """Nenhuma porta passa texto com credencial às marcas de pasta, ao hash do caminho de fora nem à troca de URL:
    as credenciais saem primeiro, do texto inteiro, e só então vêm as marcas e o corte."""

    def test_credentials_go_before_labels_paths_and_urls_at_every_entry_point(self):
        from getbrolls import _paths  # pylint: disable=import-outside-toplevel

        rng = random.Random(7)
        secrets_ = {
            "jwt": "eyJ" + "".join(rng.choice(BASE64URL) for _ in range(30)) + ".eyJabc." + "x" * 20,
            "bearer": "Ab/cD+eF=gH.iJ~kL-mN%2Fop" + "".join(rng.choice(ALNUM) for _ in range(20)),
            "ghp": "ghp_" + "".join(rng.choice(ALNUM) for _ in range(36)),
        }
        for name, secret in secrets_.items():
            pieces = {secret[index : index + 8] for index in range(len(secret) - 7)}
            text = f"falhou em /Volumes/Disco/x com Authorization: Bearer {secret}\ne Bearer {secret} https://h.com/a/b"
            if name != "bearer":  # a value with no shape inside a URL goes with the URL: that pass is its redaction
                text += f" https://h.com/a/{secret} /x/{secret}/y"
            for entry, run in ENTRY_POINTS.items():
                seen = []
                with (
                    patch.object(redaction, "_URL_PATTERN", _Spy(redaction._URL_PATTERN, seen)),  # pylint: disable=protected-access
                    patch.object(labels, "_ABSOLUTE", _Spy(labels._ABSOLUTE, seen)),  # pylint: disable=protected-access
                    patch.object(labels, "_ENCODED", _Spy(labels._ENCODED, seen)),  # pylint: disable=protected-access
                    patch.object(_paths, "replace_folder", _Spy(_paths.replace_folder, seen)),
                ):
                    run(text)
                leaked = [piece for value in seen for piece in pieces if piece in value]
                with self.subTest(secret=name, entry=entry):
                    self.assertEqual([], leaked[:3])


class LabelsOnTheirOwnTests(unittest.TestCase):
    """`labels.text` e `labels.outside` chamados direto (uma mensagem montada à mão) também tiram a chave antes."""

    def test_text_and_outside_never_keep_a_key(self):
        import os  # pylint: disable=import-outside-toplevel
        import tempfile  # pylint: disable=import-outside-toplevel

        key = "ghp_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2"
        with patch.dict(os.environ, {"GB_HOME": tempfile.gettempdir()}):
            for run in (labels.text, labels.outside):
                for text in (f"{tempfile.gettempdir()}/{key}/x", f"falhou {key} em /Volumes/Disco/a"):
                    with self.subTest(run=run.__name__, text=text[-30:]):
                        self.assertNotIn("Ef3Gh4Ij5Kl6", run(text))

    def test_the_folder_labels_never_see_a_key(self):
        import tempfile  # pylint: disable=import-outside-toplevel

        from getbrolls import _paths  # pylint: disable=import-outside-toplevel

        key = "ghp_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2"
        seen = []
        with patch.object(_paths, "replace_folder", _Spy(_paths.replace_folder, seen)):
            labels.text(f"falhou {key} em {tempfile.gettempdir()}/a")
        self.assertTrue(seen)
        self.assertEqual([], [value for value in seen if "Ef3Gh4Ij5Kl6" in value])


class TypedPathTests(unittest.TestCase):
    def test_a_typed_path_with_a_key_in_it_is_given_back_redacted(self):
        import errno  # pylint: disable=import-outside-toplevel
        import tempfile  # pylint: disable=import-outside-toplevel

        key = "ghp_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2"
        typed = f"{tempfile.gettempdir()}/{key}/proj"
        labels.note_typed_project(typed, typed)
        self.addCleanup(labels.forget_typed_project)
        for exc in (
            PermissionError(errno.EACCES, "Permission denied", typed + "/brolls"),
            ValueError(f"Projeto não encontrado em {typed}; nada criado."),
        ):
            with self.subTest(exc=type(exc).__name__):
                out = labels.message_of(exc)
                self.assertNotIn("Ef3Gh4Ij5Kl6", out)
                self.assertIn("[REDACTED]", out)


class CredentialFormsTests(unittest.TestCase):
    """Credenciais sem prefixo de provedor: a senha de um DSN que não é http, os nomes `passwd`/`pwd`/`api-key`/`Token`
    (com `=` e com `:`), o valor entre aspas com espaço e o JWT sem assinatura. Valores sorteados (semente fixa)."""

    SCHEMES = (
        "mongodb",
        "mongodb+srv",
        "postgres",
        "postgresql",
        "mysql",
        "redis",
        "rediss",
        "amqp",
        "amqps",
        "ftp",
        "sftp",
        "smtp",
        "x-custom+db.v2",
    )

    def values(self, count=6):
        rng = random.Random(20261012)
        alphabet = ALNUM + "-._~!$*%"
        # A colon form takes a word that starts like a credential (not `-`, `/`, `~`): the first character is a letter.
        return [
            rng.choice(ALNUM) + "".join(rng.choice(alphabet) for _ in range(rng.randrange(9, 23))) for _ in range(count)
        ]

    def assert_gone(self, text, secret, name):
        pieces = {secret[index : index + 8] for index in range(len(secret) - 7)}
        for entry, run in ENTRY_POINTS.items():
            out = run(text)
            left = [piece for piece in pieces if piece in out]
            if left:
                self.fail(f"{name} ({entry}): sobrou {left[:2]} em {out[-120:]!r}")

    def test_the_password_of_a_non_http_dsn_goes(self):
        for scheme in self.SCHEMES:
            for secret in self.values():
                for user in ("admin", "", "u.ser-1"):
                    dsn = f"{scheme}://{user}:{secret}@db.exemplo.com:5432/base?x=1"
                    for text in (
                        dsn,
                        f"falhou ao conectar em {dsn} agora",
                        f"DATABASE_URL={dsn}",
                        f"'{dsn}'",
                        f"({dsn}),",
                        f"url={dsn}\nlinha 2",
                    ):
                        self.assert_gone(text, secret, scheme)

    def test_a_dsn_keeps_its_host_and_goes_the_same_way_twice(self):
        for text, expected in (
            ("mongodb://u:p4ss@h.exemplo.com/db", "mongodb://[REDACTED]@h.exemplo.com/db"),
            ("mongodb+srv://u:p4ss@h.exemplo.com", "mongodb+srv://[REDACTED]@h.exemplo.com"),
            ("redis://:p4ss@h:6379/0", "redis://[REDACTED]@h:6379/0"),
            ("postgres://u:p4ss@h:5432/db", "postgres://[REDACTED]@h:5432/db"),
        ):
            with self.subTest(text=text):
                self.assertEqual(expected, _tokens.hide(text))
                self.assertEqual(expected, _tokens.hide(_tokens.hide(text)))
                self.assertTrue(_tokens.secret_shaped(text))
        for kept in (
            "ssh://git@github.com:2222/x/y",
            "postgres://h:5432/db",
            "redis://h:6379",
            "http://h:8080/a@b",
            "veja mongodb:// e redis://",
        ):
            with self.subTest(kept=kept):
                self.assertEqual(kept, _tokens.hide(kept))

    def test_the_new_credential_names_go_with_equals_and_with_colon(self):
        for name in ("passwd", "pwd", "api-key", "api_key", "Token", "TOKEN", "Password", "x-goog-api-key", "db_pwd"):
            for secret in self.values(3):
                for text in (
                    f"{name}={secret}",
                    f"{name} = {secret} fim",
                    f"{name}: {secret}",
                    f"curl -H '{name}: {secret}' x",
                    f"?a=1&{name}={secret}&b=2",
                    f"{name}={secret}, depois",
                ):
                    self.assert_gone(text, secret, name)

    def test_a_quoted_value_with_spaces_goes_whole(self):
        for name in ("password", "passwd", "pwd", "api_key", "token", "secret"):
            for quote in ('"', "'"):
                secret = "hun ter2 e mais um pouco"
                for text in (
                    f"{name}={quote}{secret}{quote} next",
                    f"{name}: {quote}{secret}{quote} next",
                    f"{name} = {quote}{secret}{quote}",
                    f"x {name}={quote}{secret}{quote}, y",
                ):
                    out = runtime.redact(text, limit=None)
                    for word in ("hun", "ter2", "mais", "pouco"):
                        self.assertNotIn(word, out, text)
                    self.assertEqual(out, runtime.redact(out, limit=None))
                    self.assert_gone(text, secret, name)
        self.assertEqual("password=[REDACTED] next", runtime.redact('password="hun ter2" next'))
        self.assertEqual("password=[REDACTED] next", runtime.redact("password='hun ter2' next"))

    def test_prose_after_a_colon_stays(self):
        for kept in (
            "the token: is required",
            "unexpected token: foo",
            "Faltou o token: informe --token",
            "token: expirou",
            "secret: não definido",
            "token: --stdin",
            "token: /caminho/do/projeto",
            "token: ~/projeto/segredos",
            "token: <valor>",
            "password: required",
            "senha: obrigatória",
            "password: is required",
            "password: not set",
            "pwd: expirou",
            "secret: vazio.",
            "passwd: invalid (try again)",
            "api-key: abc def",
            "password:",
        ):
            with self.subTest(kept=kept):
                self.assertEqual(kept, runtime.redact(kept))
                self.assertEqual(kept, labels.message_of(ValueError(kept)))

    def test_a_colon_value_that_looks_like_a_credential_goes(self):
        for text in (
            joined("token:", " abcdefgh"),
            joined("Token:", " ab12cd34ef56"),
            joined("senha:", " hunter2hunter2"),
            joined("api-key:", " x9y8z7w6v5"),
        ):
            with self.subTest(text=text):
                out = runtime.redact(text)
                self.assertEqual(text.split(":", maxsplit=1)[0] + ": [REDACTED]", out)
                self.assertEqual(out, runtime.redact(out))
        self.assertEqual("password: [REDACTED] fim", runtime.redact("password: 'a b' fim"))
        self.assertEqual("password: [REDACTED]", runtime.redact('password: "unclosed a b c'))
        self.assertEqual("token: abcdefg", runtime.redact("token: abcdefg"))  # 7 characters: prose

    def test_a_short_password_after_a_colon_goes(self):
        for text in (
            "senha: abc123",
            "password: hunter2",
            "pwd: 1234",
            "passwd: s3cr3t!",
            "password: -Hunter2222",
            "secret: /abcdEFGH12",
            "Password: ~xyz",
            "SENHA: curta",
            "password: Required",  # a capital is not the prose word: the next word goes
            "pwd: /Volumes/pessoa/proj",
            "password: --stdin",
        ):
            with self.subTest(text=text):
                out = runtime.redact(text)
                self.assertEqual(text.split(":", maxsplit=1)[0] + ": [REDACTED]", out)
                self.assertEqual(out, runtime.redact(out))
        self.assertEqual("password: [REDACTED] required", runtime.redact("password: hunter2 required"))
        self.assertEqual("password: required, tente de novo", runtime.redact("password: required, tente de novo"))

    def test_senha_goes_with_equals_and_with_colon(self):
        for text in ("senha=hunter2", "Senha = 'a b c'", "db_senha=hunter2&x=1"):
            with self.subTest(text=text):
                for word in ("hunter2", "a b c"):
                    self.assertNotIn(word, runtime.redact(text))
        self.assertEqual("senha: [REDACTED]", runtime.redact("senha: hunter2hunter2"))
        self.assertTrue(runtime.secret_name("senha"))
        self.assertTrue(runtime.secret_name("db_senha"))

    def test_an_unclosed_quote_after_a_name_goes_to_the_end_of_the_line(self):
        for text, expected in (
            ('password="unclosed a b c', "password=[REDACTED]"),
            ("token='unclosed a b c", "token=[REDACTED]"),
            ('x password="unclosed a b c\nlinha 2 a b', "x password=[REDACTED]\nlinha 2 a b"),
            ('senha="a ' + "b " * 400, "senha=[REDACTED]"),  # a closed quote longer than the bound is not closed
        ):
            with self.subTest(text=text[:40]):
                out = runtime.redact(text, limit=None)
                self.assertEqual(expected, out)
                self.assertEqual(out, runtime.redact(out, limit=None))
        self.assertEqual("password=[REDACTED] next", runtime.redact('password="hun ter2" next'))

    def test_the_password_of_a_dsn_may_hold_blanks(self):
        for text, expected in (
            ("mongodb+srv://user:pa ss@host/db", "mongodb+srv://[REDACTED]@host/db"),
            ("mongodb+srv://user:pa ss word@host", "mongodb+srv://[REDACTED]@host"),
            ("mongodb://user:p@ss w@host/db?x=1", "mongodb://[REDACTED]@host/db?x=1"),
            ("falhou em redis://u:a b c@h:6379/0 de novo", "falhou em redis://[REDACTED]@h:6379/0 de novo"),
        ):
            with self.subTest(text=text):
                for run in (_tokens.hide, runtime.redact, labels.for_log):
                    self.assertEqual(expected, run(text))
        for text in (_tokens.hide, runtime.redact, labels.for_log):
            self.assertNotIn("ss", text("mongodb+srv://user:pa ss@host"))
        for kept in ("redis://h:6379 e mais coisas", "ssh://git@github.com:2222/x/y", "http://h:8080/a@b"):
            with self.subTest(kept=kept):
                self.assertEqual(kept, _tokens.hide(kept))

    def test_an_unsigned_jwt_goes(self):
        rng = random.Random(1012)

        def part(size):
            return "".join(rng.choice(BASE64URL) for _ in range(size))

        for _sample in range(10):
            head, body = "eyJ" + part(17), "eyJ" + part(rng.randrange(20, 50))
            for text in (
                f"{head}.{body}.",
                f"token {head}.{body}. fim",
                f'"{head}.{body}."',
                f"Authorization: Bearer {head}.{body}.",
                f"x={head}.{body}.\nlinha 2",
            ):
                self.assert_gone(text, body, "jwt sem assinatura")
                self.assert_gone(text, head, "jwt sem assinatura")

    def test_ordinary_words_around_these_names_stay(self):
        for kept in (
            "tokens: 5, password policy: strong",
            "the pwdx command",
            "monkey=banana",
            "o token expirou ontem",
            "passwords differ",
        ):
            with self.subTest(kept=kept):
                self.assertEqual(kept, runtime.redact(kept))


if __name__ == "__main__":
    unittest.main()

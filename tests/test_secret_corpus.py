"""Corpus de segredos com a forma de verdade: cada um sai `[REDACTED]` (ou some) em toda porta de saída.

As portas são as que levam texto para fora: `runtime.redact`, o `diagnostics.jsonl` (`redact_diagnostic`, o
`repr` e o traceback), o valor e a mensagem de log, o `stderr_tail` (ferramenta e corpo HTTP), a mensagem do
envelope JSON e `labels.message_of`. Quando precisão e cobertura brigam, ganha a cobertura: redigir uma palavra
normal é aceitável, vazar uma chave não é. Os nomes comuns que começam com `sk-` (`sk-learn-pipeline`) ficam.
"""

import json
import logging
import os
import re
import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _fake_secrets import joined
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _timing import best_of

from getbrolls import labels, logs, runtime

# (texto, o pedaço do segredo que nunca pode sobrar). Os valores são inventados, com a forma de cada provedor.
CORPUS = (
    ("chave sk-" + "Z9y8X7w6V5u4" * 4 + " fim", "Z9y8X7w6V5u4"),
    (joined("chave sk-proj-", "Ab3d_E5fG-7hJ9kL1mN3_pQ5r-S7tU9vW1xY3zAb3dE5fG7h fim"), "7hJ9kL1mN3"),
    (joined("chave sk-proj-", "abc_def-ghi_jkl-mno_pqr-stu_vwx-yz0_123 fim"), "mno_pqr"),
    (joined("chave sk-ant-", "api03-Qw3rTy7uIo9p-As1dFg5hJk_Zx2cV4bN6mQw3rTy7uIo9pAs1d-AA fim"), "Qw3rTy7uIo9p"),
    (joined("chave sk-ant-", "admin01-k_Ey-WiTh-WoRdS-inside-0123456789abcdef fim"), "WiTh-WoRdS"),
    ("chave sk-0123456789abcdef0123456789abcdef fim", "0123456789abcdef0123"),
    (joined("chave sk-svcacct-", "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 fim"), "AbCdEfGhIjKlMnOp"),
    (joined("chave sk-or-v1-", "0123456789abcdef0123456789abcdef0123456789abcdef fim"), "0123456789abcdef0123"),
    ("chave sk-1f0e9d8c-7b6a-4c5d-9e8f-0a1b2c3d4e5f fim", "7b6a-4c5d"),
    ("chave sk-deadbeef-cafebabe-facade-0123456789ab fim", "cafebabe-facade"),
    ("chave sk-" + "a" * 40 + " fim", "a" * 30),
    ("token ghp_" + "A1b2C3d4E5f6G7h8I9j0" * 2 + " fim", "A1b2C3d4E5f6"),
    ("token gho_" + "x" * 36 + " fim", "x" * 20),
    ("token ghs_" + "Q1w2E3r4T5y6U7i8O9p0" * 2 + " fim", "Q1w2E3r4T5y6"),
    ("token github_pat_11ABCDEFG0" + "a1B2c3D4e5_" * 7 + " fim", "a1B2c3D4e5"),
    (joined("slack xoxb-", "1234567890-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx fim"), "AbCdEfGhIjKlMnOp"),
    (
        joined("slack xoxp-", "1234567890-1234567890-1234567890123-abcdef0123456789abcdef0123456789 fim"),
        "abcdef0123456789",
    ),
    (joined("slack xoxa-", "2-1234567890-abcdefABCDEF fim"), "abcdefABCDEF"),
    (joined("slack xoxs-", "1234567890-AbCdEf123456 fim"), "AbCdEf123456"),
    (joined("slack xoxb-", "AbCdEfGhIjKlMnOpQr fim"), "AbCdEfGhIjKl"),
    (joined("slack xoxp-", "T0123ABCD-u4567EFGH-WxYz fim"), "T0123ABCD-u4567"),
    (joined("aws AKIA", "IOSFODNN7EXAMPLE fim"), "IOSFODNN7EXAMPLE"),
    (joined("aws ASIA", "Y34FZKBOKMUTVV7A fim"), "Y34FZKBOKMUTVV7A"),
    ("aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI"),
    ("Authorization: Bearer abc.def-ghi_jkl", "abc.def-ghi"),
    ("authorization=Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
    ('{"Authorization": "token s3cr3tvalue"}', "s3cr3tvalue"),
    (joined("curl -H 'X-Api-Key: ", "k3y-v4lu3-123'"), "k3y-v4lu3"),
    ("Cookie: sessionid=abc123def456", "abc123def456"),
    ("erro Bearer eyJhbGciOiJIUzI1NiJ9.e30.ZRrHA1JJJW8opsbCGfG_HACGpVUMN_a9IV7pAx_Zmeo", "ZRrHA1JJJW8"),
    (
        joined(
            "jwt eyJ",
            "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.",
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ),
        "dozjgNryP4J3",
    ),
    ("GET /v1/search?api_key=abcd1234efgh&q=gato", "abcd1234efgh"),
    ("GET /v1/search?access_token=abcd1234efgh&q=gato", "abcd1234efgh"),
    (joined("token=", "abcd1234efgh5678"), "abcd1234efgh5678"),
    ("password=hunter2hunter2", "hunter2hunter2"),
    (joined("PEXELS_API_KEY=", "abcd1234efgh5678 e GB_TOKEN=", "zyxw9876vuts"), "zyxw9876vuts"),
    ("client_secret=Zm9vYmFyYmF6cXV4", "Zm9vYmFyYmF6cXV4"),
    ("X-Amz-Signature=0123456789abcdef0123", "0123456789abcdef0123"),
    ("https://user:pa55w0rd@exemplo.com/a?b=c", "pa55w0rd"),
    (
        joined(
            "-----BEGIN ",
            "RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n",
            "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK\n-----END RSA PRIVATE KEY-----",
        ),
        "MIIEowIBAAKCAQEAu1SU1LfV",
    ),
    (
        joined("-----BEGIN ", "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\n"),
        "b3BlbnNzaC1rZXktdjEA",
    ),
    (
        joined("-----BEGIN ", "PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"),
        "MIIEvQIBADANBgkq",
    ),
    # Prefixed keys of other providers (the documented prefix, synthetic filler).
    (joined("stripe sk_live_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("stripe sk_test_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("stripe rk_live_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("stripe rk_test_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("stripe pk_live_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("stripe whsec_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("google AIza", "SyAb1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0UvW fim"), "Ef3Gh4Ij5Kl6"),
    (joined("google ya29.", "a0Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv fim"), "Ef3Gh4Ij5Kl6"),
    (joined("google GOCSPX-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr fim"), "Ef3Gh4Ij5Kl6"),
    (joined("huggingface hf_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv fim"), "Ef3Gh4Ij5Kl6"),
    (joined("gitlab glpat-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn fim"), "Ef3Gh4Ij5Kl6"),
    (joined("gitlab glrt-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn fim"), "Ef3Gh4Ij5Kl6"),
    (joined("npm npm_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2 fim"), "Ef3Gh4Ij5Kl6"),
    (
        joined(
            "slack xapp-",
            "1-A0123BCDEFG-1234567890123-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef fim",
        ),
        "1234567890123-01234",
    ),
    (joined("twilio SK", "0123456789abcdef0123456789abcdef fim"), "0123456789abcdef01"),
    (
        joined("sendgrid SG.", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7O.Pq8Rs9Tu0Vw1Xy2Za3Bc4De5Fg6Hi7Jk8Lm9No0Pq1Rs2T fim"),
        "Ef3Gh4Ij5Kl6",
    ),
    (joined("mailgun ke", "y-0123456789abcdef0123456789abcdef fim"), "0123456789abcdef01"),
    (
        joined("digitalocean dop_v1_", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef fim"),
        "0123456789abcdef01",
    ),
    (joined("shopify shpat_", "0123456789abcdef0123456789abcdef fim"), "0123456789abcdef01"),
    (joined("shopify shpss_", "0123456789abcdef0123456789abcdef fim"), "0123456789abcdef01"),
    (joined("pypi pypi-", "AgEIcHlwaS5vcmcCJDAb1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4Cd5Ef6 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("discord MTIzNDU2Nzg5MDEyMzQ1Njc4.", "GaBcDe.Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv fim"), "Ef3Gh4Ij5Kl6"),
    (joined("telegram 123456789:", "AAHb1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uvw fim"), "Ef3Gh4Ij5Kl6"),
    (
        joined("azure AccountName=x;AccountKey=", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv+/Wx2Yz3==;EndpointSuffix=core"),
        "Ef3Gh4Ij5Kl6",
    ),
    (joined("groq gsk_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4Cd5 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("perplexity pplx-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("xai xai-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4Cd5Ef6 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("linear lin_a", "pi_Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("notion ntn_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4 fim"), "Ef3Gh4Ij5Kl6"),
    (joined("notion sec", "ret_Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab fim"), "Ef3Gh4Ij5Kl6"),
    (
        joined("postman PMAK-", "0123456789abcdef01234567-0123456789abcdef0123456789abcdef01 fim"),
        "0123456789abcdef0123",
    ),
    (joined("databricks da", "pi0123456789abcdef0123456789abcdef fim"), "0123456789abcdef01"),
    (joined("square sq0atp-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7O fim"), "Ef3Gh4Ij5Kl6"),
    (joined("square sq0csp-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv1Wx2Yz3Ab4Cd fim"), "Ef3Gh4Ij5Kl6"),
    (joined("age AGE-SECRET-KEY-", "1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQG fim"), "QYQSZQGPQYQSZQGP"),
    (joined("grafana glsa_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0Uv_01234567 fim"), "Ef3Gh4Ij5Kl6"),
    ("sk- random words sk-qzxwvk-plmnbt-rtyxcv-wqzkfh fim", "plmnbt-rtyxcv"),
    (joined("aws exata x AKIA", "ABCDEFGHIJKLMNOP, fim"), "ABCDEFGHIJKLMNOP"),
)

# Nomes comuns que começam com `sk-` (ou têm `sk-` dentro): nunca uma chave.
# Texto comum: nada nele tem a forma de uma chave.
KEPT = (
    "a receita key-lime-pie e o pacote npm_config",
    "commit 3fa2c1d9e8b7a6f5 e sha256 " + "a" * 64,
    "youtube id dQw4w9WgXcQ e playlist PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
    "às 12:30 do dia 10/10 e o placar 3:1",
)
# Nomes que começam como uma chave: só cobertura, então saem `[REDACTED]` também (decisão do guardião).
REDACTED_TOO = (
    ("sk-learn-pipeline-tutorial-for-beginners", "pipeline-tutorial"),
    ("a pasta sk-reels-acme-2026-video-final-v2", "acme-2026"),
    ("ask-me-anything-session-recording-2026", "session-recording"),
    ("o projeto sk-lab-notas-de-aula-2026-v3", "notas-de-aula"),
    ("xoxo-forever-and-always-2026", "forever-and"),
    (joined("AKIA", "PRESSRELEASEJANUARY"), "PRESSRELEASE"),
    ("o vídeo eyJabcdefgh.ab.cdefghij é uma string base64", "abcdefgh.ab"),
)


# As formas que o `_tokens` redigia em 6176c70 (a revisão aprovada): o que elas pegavam, o código de hoje
# pega também, em todo contexto (`test_nothing_redacted_at_the_approved_review_leaks_now`).
BASELINE_6176C70 = (
    re.compile(
        r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----(?:(?!-----END )[\s\S]){0,20000}"
        r"(?:-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----)?"
    ),
    re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|xox[abposr]-[A-Za-z0-9-]{10,}"
        r"|(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[A-Z0-9]{16}"
        r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{8,})"
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
)
# O prefixo que o `event=op` já tratava como segredo em 6176c70 (`effects._SECRET_SHAPED`).
BASELINE_EFFECT_PREFIXES = (
    "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_", "sk-", "xoxa-", "xoxb-", "xoxp-", "xoxr-", "xoxs-",
    "glpat-", "akia", "aiza", "AKIA", "AIza",
)  # fmt: skip


def _tokens_of_corpus():
    """Cada credencial do corpus que sai sozinha: a palavra que tem o pedaço que não pode sobrar."""
    found = []
    from getbrolls import _tokens  # pylint: disable=import-outside-toplevel

    # A key shape, not an `x=` or a header (those have their own entries in CORPUS).
    found.extend(
        (word, core)
        for text, core in CORPUS
        for word in text.split()
        if core in word and core not in _tokens.hide(word)
    )
    return found


def _contexts(token):
    """O token colado em texto, numa query, entre aspas, em JSON, no fim do texto e no meio de linhas."""
    return {
        "sozinho": token,
        "colado à direita": token + "seguido",
        "colado à esquerda": "chave" + token,
        "colado dos dois lados": "abc" + token + "xyz",
        "query": "GET /v1/busca?id=1&t=" + token + "&q=gato",
        "query no fim": "GET /v1/busca?t=" + token,
        "aspas duplas": 'valor "' + token + '" fim',
        "aspas simples": "valor '" + token + "' fim",
        "json": json.dumps({"token": token, "outro": 1}),
        "json escapado": json.dumps(json.dumps({"token": token})),
        "fim do texto": "o valor é " + token,
        "várias linhas": "linha 1\n" + token + "\nlinha 3",
        "depois de dois pontos": "token:" + token,
        "entre parênteses": "(" + token + ")",
    }


def _filtered(text):
    record = logging.LogRecord("getbrolls", logging.INFO, __file__, 1, text, (), None)
    logs.RedactingFilter().filter(record)
    return record.msg


def _envelope(text):
    event = {}
    token = runtime.ACTIVE.set({"warnings": []})
    try:
        runtime._classify_audited_error(event, ValueError(text), None)  # pylint: disable=protected-access
    finally:
        runtime.ACTIVE.reset(token)
    return event["message"] + "\n" + event["repr"]


def _traceback(text):
    try:
        raise ValueError(text)
    except ValueError:
        return runtime.redacted_traceback()


ENTRY_POINTS = {
    "redact": lambda text: runtime.redact(text, limit=None),
    "redact (corte)": runtime.redact,
    "redact_diagnostic": runtime.redact_diagnostic,
    "log value": logs._format_value,  # pylint: disable=protected-access
    "log message": _filtered,
    "stderr_tail": runtime.stderr_tail,
    "envelope": _envelope,
    "traceback": _traceback,
    "labels.message_of": lambda text: labels.message_of(ValueError(text)),
}


class SecretCorpusTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"PEXELS_API_KEY": joined("abcd1234", "efgh5678")})
        env.start()
        self.addCleanup(env.stop)

    def test_every_secret_is_redacted_at_every_entry_point(self):
        for name, run in ENTRY_POINTS.items():
            for text, core in CORPUS:
                with self.subTest(entry=name, secret=text[:28]):
                    out = run(text)
                    self.assertNotIn(core, out, out[-200:])

    def test_a_secret_is_redacted_wherever_the_text_is_cut(self):
        # The cut of a log value (500), a message (1200) or a tail (600) never leaves the start of a key.
        caps = ((500, logs._format_value), (600, runtime.stderr_tail), (1200, runtime.redact))  # pylint: disable=protected-access
        for text, core in CORPUS:
            for cap, run in caps:
                for offset in range(max(0, cap - len(text) - 2), cap + 2):
                    out = run("." * offset + " " + text)  # padding no core can contain
                    if core in out:
                        self.fail(f"{core!r} sobrou com o corte em {offset} ({cap})")

    def test_the_fixtures_written_in_pieces_keep_the_shape_of_a_real_secret(self):
        # `joined` glues the pieces back: a cut in the wrong place would drop or add a character, and the redaction
        # tests would then pass on a text that is no longer shaped like a key.
        self.assertEqual("xoxb-1-2", joined("xoxb-", "1", "-2"))
        for text, core in CORPUS:
            with self.subTest(secret=text[:28]):
                self.assertIn(core, text)
                self.assertNotEqual(text, runtime.redact(text, limit=None))
        whole = "\n".join(text for text, _core in CORPUS)
        for shape in (
            joined("xoxb-", "1234567890-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"),
            joined("sk_live_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8"),
            joined("rk_test_", "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8"),
            joined("AKIA", "IOSFODNN7EXAMPLE"),
            joined("AIza", "SyAb1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0UvW"),
            joined("glpat-", "Ab1Cd2Ef3Gh4Ij5Kl6Mn"),
            joined("-----BEGIN ", "RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfV"),
            joined("-----BEGIN ", "PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"),
            joined("jwt eyJ", "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmN"),
            joined("slack xapp-", "1-A0123BCDEFG-1234567890123-0123456789abcdef"),
        ):
            with self.subTest(shape=shape[:24]):
                self.assertIn(shape, whole)

    def test_a_key_id_is_redacted_whole_with_what_is_glued_to_it(self):
        self.assertEqual("x [REDACTED], fim", runtime.redact(joined("x AKIA", "ABCDEFGHIJKLMNOP, fim")))
        # Recall first: what is glued to a key id goes with it (`AKIA…EXAMPLEsecret`), so a long uppercase word
        # that starts like one goes whole too, never `[REDACTED]ARY`.
        self.assertEqual("x [REDACTED] fim", runtime.redact(joined("x AKIA", "IOSFODNN7EXAMPLEsecret fim")))
        self.assertEqual("[REDACTED]", runtime.redact(joined("AKIA", "PRESSRELEASEJANUARY")))

    def test_effects_and_redact_share_the_secret_shapes(self):
        # A target shaped like any redacted key is hashed in the `event=op` line, never shown.
        from getbrolls import effects  # pylint: disable=import-outside-toplevel

        for text, _core in CORPUS:
            for token in text.split():
                if runtime.redact(token, limit=None) != token and "=" not in token:
                    with self.subTest(token=token[:16]):
                        self.assertTrue(effects._secret_shaped(token), token)  # pylint: disable=protected-access

    def test_every_shape_is_redacted_in_every_context(self):
        tokens = _tokens_of_corpus()
        self.assertGreater(len(tokens), 60)
        for token, core in tokens:
            for name, text in _contexts(token).items():
                for entry, run in (("redact", lambda value: runtime.redact(value, limit=None)), ("log", _filtered)):
                    with self.subTest(token=token[:16], context=name, entry=entry):
                        self.assertNotIn(core, run(text))

    def test_nothing_redacted_at_the_approved_review_leaks_now(self):
        for token, core in _tokens_of_corpus():
            for name, text in _contexts(token).items():
                before = text
                for pattern in BASELINE_6176C70:
                    before = pattern.sub("[REDACTED]", before)
                if core in before:
                    continue  # not redacted then either: the corpus test above covers it now
                with self.subTest(token=token[:16], context=name):
                    self.assertNotIn(core, runtime.redact(text, limit=None))

    def test_the_event_target_check_keeps_every_earlier_prefix(self):
        from getbrolls import effects  # pylint: disable=import-outside-toplevel

        for prefix in BASELINE_EFFECT_PREFIXES:
            with self.subTest(prefix=prefix):
                self.assertTrue(effects._secret_shaped(prefix + "x1"))  # pylint: disable=protected-access

    def test_hostile_text_is_redacted_in_linear_time(self):
        under = 65_000
        units = (
            joined("-----BEGIN ", "PRIVATE KEY-----"),
            "sk-",
            "xoxb-",
            "eyJ",
            "sk-a-",
            "AKIA",
            "eyJaaaaaaaaa.",
            "M" * 25 + ".",
        )
        for unit in (*units, "123456789:", "AccountKey=", "SG.aaaaaaaaaaaaaaaa.", "sk-ab-", "a.", "eyJa.", "xoxe-1"):
            text = (unit * (under // len(unit)))[:under]
            spent, _ = best_of(lambda text=text: runtime.redact(text, limit=None), 1.0)
            self.assertLess(spent, 1.0, unit)

    def test_names_that_start_like_a_key_are_redacted_too(self):
        for name, run in ENTRY_POINTS.items():
            for text, core in REDACTED_TOO:
                with self.subTest(entry=name, text=text[:20]):
                    self.assertNotIn(core, run(text))

    def test_ordinary_text_stays(self):
        for name, run in ENTRY_POINTS.items():
            if name in ("envelope", "traceback"):
                continue
            for kept in KEPT:
                with self.subTest(entry=name, kept=kept):
                    for word in kept.split():
                        self.assertIn(word, run(kept))


if __name__ == "__main__":
    unittest.main()

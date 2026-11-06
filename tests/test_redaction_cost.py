"""A redação de log e de diagnóstico tem custo linear e saída de tamanho limitado, com qualquer entrada.

Cada linha do `getbrolls.log` e do `diagnostics.jsonl` passa por `labels.text` e `labels.outside`. Uma entrada
hostil (uma linha de 1 MB, mil quase-acertos de uma pasta, uma corrida de `%2F` ou de escapes, um caminho
de 200 mil níveis, `/,/,/,…`) não pode travar o comando nem gerar uma linha maior que o teto.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _timing import best_of as best_sample

from getbrolls import labels, logs, runtime


def tracing() -> bool:
    """A execução está sob um rastreador (cobertura: `sys.settrace` ou `sys.monitoring`), que deixa o código
    de três a dez vezes mais lento."""
    if sys.gettrace() is not None:
        return True
    monitoring = getattr(sys, "monitoring", None)
    return monitoring is not None and any(monitoring.get_tool(tool) is not None for tool in range(6))


def scaled(seconds: float) -> float:
    """O teto absoluto `seconds`, ampliado seis vezes sob rastreio."""
    return seconds * (6 if tracing() else 1)


# 0,2 s nesta máquina; o teto do teste dá folga para uma máquina de CI lenta.
BUDGET_S = 1.0


class RedactionCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-cost-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gbhome = self.tmp / "gb"
        self.gbhome.mkdir()
        env = patch.dict(os.environ, {"GB_HOME": str(self.gbhome)})
        env.start()
        self.addCleanup(env.stop)

    def hostile(self):
        gb = str(self.gbhome)
        under = labels.INPUT_MAX - 64  # o maior texto que ainda é redigido
        near = {
            "quase-acertos (no teto)": ((gb + "x/") * (under // (len(gb) + 2)))[:under],
            "acertos (no teto)": ((gb + "/a ") * (under // (len(gb) + 3)))[:under],
            "barra e vírgula (no teto)": ("/," * (under // 2))[:under],
            "%2F (no teto)": ("%2F" * (under // 3))[:under],
        }
        return {
            **near,
            "1 MB sem espaço": "x" * 1_000_000,
            "1 MB de palavras": "palavra " * 125_000,
            "quase-acertos": (gb + "x/") * 20_000,
            "acertos": (gb + "/a ") * 20_000,
            "%2F": "%2F" * 300_000,
            "escapes": "\\\\/" * 400_000,
            "caminho fundo": "/" + "/".join(["d"] * 200_000),
            "barra e vírgula": "/," * 400_000,
            "barra e parêntese": "/(" * 400_000,
        }

    def timed(self, call, text):
        return best_sample(lambda: call(text), scaled(BUDGET_S))

    def test_a_log_value_is_cheap_and_capped(self):
        for name, text in self.hostile().items():
            with self.subTest(name=name):
                spent, out = self.timed(logs._format_value, text)
                self.assertLess(spent, scaled(BUDGET_S))
                # O teto vale para o texto; as aspas e os escapes do JSON vêm por cima.
                shown = json.loads(out) if out.startswith('"') else out
                self.assertLessEqual(len(shown), logs._MAX_VALUE_CHARS)

    def test_a_log_message_is_cheap_and_capped(self):
        for name, text in self.hostile().items():
            with self.subTest(name=name):
                record = logs.logging.LogRecord("getbrolls", logs.logging.INFO, __file__, 1, text, (), None)
                spent, _ = self.timed(logs.RedactingFilter().filter, record)
                self.assertLess(spent, scaled(BUDGET_S))
                self.assertLessEqual(len(record.msg), logs.MAX_MESSAGE_CHARS)

    def test_a_diagnostics_line_is_cheap_and_capped(self):
        for name, text in self.hostile().items():
            with self.subTest(name=name):
                spent, out = self.timed(lambda value: runtime.diagnostic_line({"repr": value}), text)
                self.assertLess(spent, scaled(BUDGET_S))
                self.assertLessEqual(len(json.loads(out)["repr"]), labels.INPUT_MAX)

    def test_free_text_and_outside_are_linear(self):
        for name, text in self.hostile().items():
            for call in (labels.text, labels.outside):
                with self.subTest(name=name, call=call.__name__):
                    spent, out = self.timed(call, text)
                    self.assertLess(spent, scaled(BUDGET_S))
                    self.assertLessEqual(len(out), labels.INPUT_MAX)


class EveryRegexCostTests(unittest.TestCase):
    """Each pattern of the credential and path redaction, with the input that is worst for it, at the cap of the
    redaction itself (65,536 characters): through `runtime.redact`, `labels.message_of` and `labels.for_log`."""

    # A garantia é o crescimento, não o relógio: um custo linear quadruplica quando o texto quadruplica, um
    # quadrático multiplica por dezesseis (seis fica entre os dois, longe do ruído de um e do outro). O tempo é
    # o de CPU do processo (`time.process_time`), não o de parede: outra suíte na mesma máquina (o gate de outra
    # tarefa, a cobertura) rouba tempo de parede de qualquer amostra, mas não muda quanto trabalho a redação
    # faz. Cada rodada mede o quarto e o inteiro lado a lado e a razão é a menor de `ROUNDS` rodadas, nunca a
    # de dois mínimos tirados em momentos diferentes. O teto absoluto fica generoso: o rastreio da cobertura
    # deixa o mesmo código linear mais lento sem que isso seja um defeito.
    LIMIT_S = 1.0
    FAST_S = 0.1  # abaixo disto no tamanho cheio não há o que medir: o ruído do relógio vale mais que o custo
    GROWTH_MAX = 6.0
    SMALL_DIVISOR = 4
    SMALL_FLOOR_S = 0.025  # o quarto do texto roda em ~20 ms: abaixo disto o ruído do relógio infla a razão
    ROUNDS = 5  # rodadas de (quarto, inteiro); a menor razão e o menor inteiro contam

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="gb-cost-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = patch.dict(os.environ, {"GB_HOME": str(tmp)})
        env.start()
        self.addCleanup(env.stop)

    @staticmethod
    def hostile(cap=labels.INPUT_MAX):

        def fill(unit, head=""):
            return (head + unit * (cap // len(unit) + 1))[:cap]

        return {
            # `_DSN`
            "dsn: colons after the scheme": fill(":", "a://"),
            "dsn: many schemes": fill("a://u:"),
            "dsn: user and password colons": fill("a:", "a://"),
            "dsn: no closing at": fill("x", "a://u:"),
            "dsn: password with blanks": fill("p ", "a://u:"),
            "dsn: at signs": fill("@", "a://u:"),
            "dsn: blank and at": fill(" @", "a://u:"),
            "dsn: scheme-like run": fill("a+b.c-"),
            # `_QUERY_SECRET`, `_LABEL` and `_VALUE`
            "name prefix: dashes": "-" * cap,
            "name prefix: underscores": "_" * cap,
            "name prefix: dots and dashes": fill(".-"),
            "name prefix: letters": "a" * cap,
            "keyword then equals": fill("token="),
            "keyword then colon": fill("token:"),
            "keyword colon and letters": fill("a", "token: "),
            "keyword: unclosed double quote": fill('password="'),
            "keyword: unclosed single quote": fill("senha: '"),
            "keyword: unclosed quote then words": fill("a ", 'password="'),
            "keyword: closed quotes": fill("password='x' "),
            "keyword: redacted": fill("token=[REDACTED] "),
            "keyword: api-key": fill("api-key"),
            "keyword: glued": fill("key"),
            # `_ABSOLUTE`, `_START` and the end of a free-text path
            "path: names with blanks": fill("/Users/a b "),
            "path: one long line": fill("a ", "/Users/"),
            "path: lines": fill("/Users/a b\n"),
            "path: opening quotes": fill("'/"),
            "path: quote and escapes": fill("\\'/a", '"'),
            "path: escaped quotes": fill('/\\"', '"'),
            "path: unclosed quote then words": fill("a ", "'/Users/"),
            "path: opening brackets": fill("(/a"),
            "path: nested brackets": fill("(", "(/Users/a "),
            "path: curly quotes": fill("\u201c/a"),
            "path: single backslash": fill("\\a\\"),
            "path: single backslash names": fill("\\Users\\a b "),
            "path: drive": fill("C:\\"),
            "path: unc": fill("\\\\a\\b "),
            "path: file url": fill("file:///a b "),
            "path: slash and comma": fill("/,"),
            "path: option glued": fill("-o/a "),
            "path: root names": fill("/Users "),
            "path: quoted starts": fill("'/usr/a"),
            "path: quoted starts and blanks": fill('"/usr/a b'),
            "path: bracketed starts": fill("(/usr/a)"),
            "path: reasons": fill("/Users/a: "),
            "path: reasons and parentheses": fill("(/Users/a: "),
            "path: colon joined": fill("/bin:"),
            "path: climbs out of a label": fill("<projeto>/../a "),
            "path: climbs out of a home": fill("~/a/../../b "),
            "path: climbs from where the text stands": fill("..\\"),
            "path: json slashes": fill("\\/a"),
            "path: network shares": fill("//a/b "),
            "path: file urls": fill("FILE:///a b "),
            # `_ENCODED`
            "encoded: slashes": fill("%2F"),
            "encoded: double": fill("%252F"),
            "encoded: long run": fill("a", "%2F"),
            "encoded: deep": fill("%2525252525252F"),
            # `_DOTTED`, the JWT
            "jwt: eyJ run": fill("eyJ"),
            "jwt: dotted eyJ": fill("eyJ."),
            "jwt: segments": fill("eyJabcdefgh."),
        }

    @staticmethod
    def cpu_time(call, text):
        started = time.process_time()
        call(text)
        return time.process_time() - started

    def assert_cheap(self, name, call, text):
        """`call(text)` cabe no teto absoluto e cresce em linha reta: o tempo do texto cheio, dividido pelo do mesmo
        texto com um quarto do tamanho (`hostile`), fica abaixo de `GROWTH_MAX`. Abaixo de `FAST_S` não se mede."""
        full = self.cpu_time(call, text)
        if full < scaled(self.FAST_S):
            return
        quarter = self.hostile(len(text) // self.SMALL_DIVISOR)[name]
        growth = float("inf")
        for _ in range(self.ROUNDS):
            small = self.cpu_time(call, quarter)
            now = self.cpu_time(call, text)
            full = min(full, now)
            growth = min(growth, now / max(small, self.SMALL_FLOOR_S))
        self.assertLess(full, scaled(self.LIMIT_S), f"{name}: {full:.2f} s")
        self.assertLess(growth, self.GROWTH_MAX, f"{name}: x{growth:.1f} ao quadruplicar o texto ({full:.2f} s)")

    def test_the_check_catches_a_quadratic_cost_and_passes_a_linear_one(self):
        # A conferência da própria conferência, com um relógio de mentira: 0,8 s no texto cheio; o quadrático
        # gasta um dezesseis avos disso no quarto, o linear um quarto.
        clock = [0.0]

        def costing(power):
            def call(text):
                clock[0] += scaled(0.8) * (len(text) / labels.INPUT_MAX) ** power

            return call

        name = "name prefix: letters"
        with patch.object(time, "process_time", side_effect=lambda: clock[0]):
            self.assert_cheap(name, costing(1), self.hostile()[name])
            with self.assertRaisesRegex(AssertionError, "ao quadruplicar"):
                self.assert_cheap(name, costing(2), self.hostile()[name])

    def test_redact_is_cheap_with_every_hostile_input(self):
        for name, text in self.hostile().items():
            with self.subTest(name=name):
                self.assertEqual(labels.INPUT_MAX, len(text))
                self.assert_cheap(name, lambda value: runtime.redact(value, limit=None), text)

    def test_message_of_is_cheap_with_every_hostile_input(self):
        for name, text in self.hostile().items():
            with self.subTest(name=name):
                self.assert_cheap(name, lambda value: labels.message_of(ValueError(value)), text)

    def test_for_log_is_cheap_with_every_hostile_input(self):
        for name, text in self.hostile().items():
            with self.subTest(name=name):
                self.assert_cheap(name, labels.for_log, text)

    def test_a_long_run_of_dashes_costs_little(self):
        text = "-" * 100_000
        spent, _ = best_sample(lambda: runtime.redact(text, limit=None), scaled(0.2))
        self.assertLess(spent, scaled(0.2))


class SiblingProbeTests(unittest.TestCase):
    """The sibling check (`Projeto 2` next to `Projeto`) asks the disk at most a few times per text: a slow disk
    (a network `$GB_HOME`) never makes a log line slow."""

    def test_a_slow_disk_is_asked_a_bounded_number_of_times(self):
        """What is bounded is the number of probes, so that is what is asserted, never a stopwatch: a probe per
        line would be 200 consultations, the cap is 16, and a loaded shared runner cannot move a count (a
        wall-clock budget did: 1.014 s against 1.0 s on a busy macOS runner while the sleeps summed to 0.16 s).
        It also asks for at least one probe, so a sibling check removed altogether does not pass."""
        tmp = Path(tempfile.mkdtemp(prefix="gb-sib-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "gbh"
        root.mkdir()
        calls = []

        def slow(path):
            calls.append(path)
            return False

        unit = f"{root} a b c d e f g h i j k l m n o p q r s t u v w x y z\n"
        with patch.dict(os.environ, {"GB_HOME": str(root)}), patch("os.path.lexists", side_effect=slow):
            shown = labels.text(unit * 200)
        self.assertEqual(200, shown.count("$GB_HOME a b c"))
        self.assertGreater(len(calls), 0)
        self.assertLessEqual(len(calls), 16, f"{len(calls)} consultas ao disco")

    def test_many_different_names_after_the_folder_cost_the_same_cap(self):
        tmp = Path(tempfile.mkdtemp(prefix="gb-sib-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "gbh"
        root.mkdir()
        calls = []

        def slow(path):
            calls.append(path)
            return False

        text = "".join(f"{root} nome{index} e mais\n" for index in range(300))
        with patch.dict(os.environ, {"GB_HOME": str(root)}), patch("os.path.lexists", side_effect=slow):
            shown = labels.text(text)
        self.assertEqual(300, shown.count("$GB_HOME nome"))
        self.assertLessEqual(len(calls), 16, f"{len(calls)} consultas ao disco")

    def test_the_same_name_is_asked_once_per_text(self):
        tmp = Path(tempfile.mkdtemp(prefix="gb-sib-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "gbh"
        root.mkdir()
        calls = []

        def slow(path):
            calls.append(path)
            return False

        with patch.dict(os.environ, {"GB_HOME": str(root)}), patch("os.path.lexists", side_effect=slow):
            shown = labels.text(f"{root} a, fim\n" * 200)
        self.assertEqual(200, shown.count("$GB_HOME a, fim"))
        # Two names can follow it (`a` and `a,`), each asked once for the 200 lines.
        self.assertEqual([f"{root} a", f"{root} a,"], calls)

    def test_the_cap_keeps_a_real_sibling_seen(self):
        tmp = Path(tempfile.mkdtemp(prefix="gb-sib-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "gbh"
        root.mkdir()
        Path(f"{root} 2").mkdir()
        with patch.dict(os.environ, {"GB_HOME": str(root)}):
            shown = labels.text(f"em {root} 2 e em {root} a b c d e f g h i j, fim")
        self.assertNotIn("$GB_HOME 2", shown)
        self.assertIn("e em $GB_HOME a b c", shown)


if __name__ == "__main__":
    unittest.main()

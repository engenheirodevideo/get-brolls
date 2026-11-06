"""`doctor` diz a situação do proxy: quais variáveis estão postas e quem as respeita (`install.proxy`)."""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import CLI_ARGV, ROOT, WHEEL_MODE

from getbrolls import http

PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
HINT = (
    "curl e yt-dlp usam o proxy do ambiente nas transferências HTTPS; o navegador segue o proxy do sistema, não o do "
    "ambiente; o transporte HTTP interno conecta direto"
)
SHAPE = {"honored_by": ["curl", "yt-dlp", "browser"], "ignored_by": ["http"]}
SECRET_PROXY = "http://usuario:senha-do-proxy@proxy.example.test:3128"


def environment_with(**extra):
    """O ambiente do teste sem nenhuma variável de proxy (nem a de quem roda), mais `extra`."""
    kept = {key: value for key, value in os.environ.items() if key.upper() not in PROXY_NAMES}
    return {**kept, **extra}


class ProxyReportTests(unittest.TestCase):
    def report(self, **env):
        with patch.dict(os.environ, environment_with(**env), clear=True):
            return http.proxy_report()

    def present(self, **env):
        report = self.report(**env)
        assert report is not None
        return report

    def test_the_install_entry_exists_only_with_a_report(self):
        with patch.dict(os.environ, environment_with(), clear=True):
            self.assertEqual({}, http.proxy_install_entry())
        with patch.dict(os.environ, environment_with(HTTPS_PROXY="http://p:3"), clear=True):
            self.assertEqual(["HTTPS_PROXY"], http.proxy_install_entry()["proxy"]["env"])

    def test_without_proxy_variables_there_is_no_report(self):
        self.assertIsNone(self.report())

    def test_a_proxy_variable_is_named_never_valued_and_brings_the_one_hint(self):
        report = self.report(HTTPS_PROXY=SECRET_PROXY)
        self.assertEqual({"env": ["HTTPS_PROXY"], **SHAPE, "hint": HINT}, report)
        self.assertNotIn("senha-do-proxy", json.dumps(report))
        self.assertNotIn("proxy.example.test", json.dumps(report))

    def test_every_proxy_variable_counts_and_the_names_come_sorted(self):
        report = self.present(
            ALL_PROXY="socks5://p:1", NO_PROXY="example.org", HTTP_PROXY="http://p:2", HTTPS_PROXY="http://p:3"
        )
        self.assertEqual(["ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"], report["env"])
        self.assertEqual(HINT, report["hint"])

    def test_the_lowercase_spelling_curl_and_yt_dlp_also_read_is_reported_as_set(self):
        if os.name == "nt":
            self.skipTest("o Windows não distingue maiúsculas nos nomes de variáveis")
        report = self.present(https_proxy="http://p:3", no_proxy="example.org")
        self.assertEqual(["https_proxy", "no_proxy"], report["env"])
        self.assertEqual(HINT, report["hint"])

    def test_an_empty_variable_is_not_a_proxy(self):
        self.assertIsNone(self.report(HTTPS_PROXY="", ALL_PROXY="   "))

    def test_the_hint_is_for_the_variables_curl_and_yt_dlp_apply_to_https(self):
        self.assertEqual({"env": ["HTTP_PROXY"], **SHAPE}, self.report(HTTP_PROXY="http://p:2"))
        self.assertEqual({"env": ["ALL_PROXY"], **SHAPE, "hint": HINT}, self.report(ALL_PROXY="socks5://p:1"))
        self.assertEqual(HINT, self.present(HTTP_PROXY="http://p:2", HTTPS_PROXY="http://p:3")["hint"])

    def test_the_hint_says_who_follows_the_environment_and_who_does_not(self):
        self.assertIn("curl e yt-dlp usam o proxy do ambiente", HINT)
        self.assertIn("navegador segue o proxy do sistema", HINT)
        self.assertIn("transporte HTTP interno conecta direto", HINT)

    def test_no_proxy_alone_is_listed_but_gives_no_reason_for_the_hint(self):
        self.assertEqual({"env": ["NO_PROXY"], **SHAPE}, self.report(NO_PROXY="example.org"))


def run_doctor(**env):
    done = subprocess.run(
        [*CLI_ARGV, "doctor"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment_with(**env),
        timeout=120,
        check=False,
    )
    return done, json.loads(done.stdout)


class DoctorProxyTests(unittest.TestCase):
    def test_doctor_reports_the_proxy_under_install_without_leaking_its_value(self):
        done, payload = run_doctor(HTTPS_PROXY=SECRET_PROXY, NO_PROXY="example.org")
        self.assertEqual({"env": ["HTTPS_PROXY", "NO_PROXY"], **SHAPE, "hint": HINT}, payload["install"]["proxy"])
        self.assertNotIn("senha-do-proxy", done.stdout + done.stderr)

    def test_doctor_without_a_proxy_has_no_proxy_key_at_all(self):
        _done, payload = run_doctor()
        self.assertNotIn("proxy", payload["install"])

    def test_the_proxy_never_changes_what_is_ready_or_missing(self):
        without, plain = run_doctor()
        with_proxy, proxied = run_doctor(HTTPS_PROXY=SECRET_PROXY)
        self.assertEqual(without.returncode, with_proxy.returncode)
        self.assertEqual(plain["ready"], proxied["ready"])
        self.assertEqual(plain["summary"], proxied["summary"])
        self.assertEqual(plain.get("warnings"), proxied.get("warnings"))

    @unittest.skipIf(WHEEL_MODE, "o contrato fica no checkout")
    def test_the_cli_contract_documents_the_field(self):
        text = (ROOT / "docs" / "CLI_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("`install.proxy`", text)
        self.assertIn("só aparece quando há variável de proxy no ambiente", text)
        self.assertIn(HINT, text)


if __name__ == "__main__":
    unittest.main()

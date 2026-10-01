"""`plugins`/`x`: códigos de saída coerentes e mensagens sem detalhe interno.

Flag faltando, flag com formato errado (`--commit`, `--ref`, `--subdir`, `--id`,
`<id>@<marketplace>`, `--source`) e `--yes` sem `--expect` são erro de uso (exit 2,
`USAGE_ERROR`) em toda ação; conteúdo ruim (manifesto, árvore, índice) continua 1.
A mensagem nunca mostra o nome da pasta de staging (`.install-<epoch>-<hash>`) e as
dicas citam comandos que existem.
"""

import json
import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_install import HAS_GIT, InstallTestCase, git, write_plugin
from test_sdk_loader import MANIFEST

from getbrolls.errors import UsageError
from getbrolls.sdk import install as install_mod
from getbrolls.sdk import loader, marketplace, marketplace_install

USAGE = 2


class UsageExitTests(InstallTestCase):
    def usage(self, *args):
        err = run_cli("plugins", *args, expect=USAGE, env=self.env())
        self.assertEqual("USAGE_ERROR", err["error_code"])
        return err

    def test_missing_flags_are_usage_errors(self):
        for args in (
            ("--action", "install"),
            ("--action", "enable"),
            ("--action", "disable"),
            ("--action", "update"),
            ("--action", "remove"),
            ("--action", "check"),
            ("--action", "new", "--id", "sem_tipo"),
        ):
            with self.subTest(args=args):
                self.usage(*args)

    def test_malformed_flag_values_are_usage_errors(self):
        source = write_plugin(self.work / "demo_src")
        for args in (
            ("--action", "install", "--source", "https://example.com/p.git", "--commit", "abc"),
            ("--action", "install", "--source", "https://example.com/p.git", "--ref", "-x"),
            ("--action", "install", "--source", "https://example.com/p.git", "--subdir", "../fora"),
            ("--action", "install", "--source", "ftp://example.com/p"),
            ("--action", "install", "--source", "nao/existe"),
            ("--action", "install", "--source", str(source), "--commit", "a" * 40),
            ("--action", "install", "--id", "a@b@c"),
            ("--action", "enable", "--id", "Ruim"),
            ("--action", "marketplace-add", "--source", "https://example.com/i.git", "--commit", "xyz"),
            ("--action", "marketplace-add", "--source", str(source)),
            ("--action", "marketplace-update", "--marketplace", "exemplo", "--commit", "xyz"),
        ):
            with self.subTest(args=args):
                self.usage(*args)

    def test_yes_without_expect_is_a_usage_error_before_any_work(self):
        source = write_plugin(self.work / "demo_src")
        err = self.usage("--action", "install", "--source", str(source), "--yes")
        self.assertIn("--expect", err["error"])
        self.assertFalse((self.home / "plugins").exists() and any((self.home / "plugins").iterdir()))
        with self.assertRaises(UsageError):
            loader.check_expect(None, "a" * 64)

    def test_plain_id_without_source_points_at_both_forms(self):
        err = self.usage("--action", "install", "--id", "demo")
        self.assertIn("<id>@<marketplace>", err["error"])
        self.assertIn("--source", err["error"])

    def test_x_argument_format_is_a_usage_error(self):
        run_cli("x", expect=USAGE, env=self.env())
        run_cli("x", "demo", "contar", "--arg", "sem_igual", expect=USAGE, env=self.env())

    def test_content_problems_stay_exit_1(self):
        bad = write_plugin(self.work / "bad_src", {**MANIFEST, "extra": 1})
        err = run_cli("plugins", "--action", "install", "--source", str(bad), expect=1, env=self.env())
        self.assertEqual("INVALID_DATA", err["error_code"])
        err = run_cli("plugins", "--action", "install", "--id", "demo@nada", expect=1, env=self.env())
        self.assertEqual("INVALID_DATA", err["error_code"])


@unittest.skipUnless(HAS_GIT, "git required")
class MessageTests(InstallTestCase):
    def test_staging_folder_name_never_reaches_the_message(self):
        repo = write_plugin(self.work / "repo", {**MANIFEST, "extra": 1})
        git(repo, "init", "--quiet")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "x")
        with self.assertRaises(ValueError) as ctx:
            install_mod.install(str(repo), confirm=False)
        self.assertNotIn(".install-", str(ctx.exception))
        self.assertIn("campo desconhecido", str(ctx.exception))
        err = run_cli("plugins", "--action", "install", "--source", str(repo), expect=1, env=self.env())
        self.assertNotIn(".install-", json.dumps(err, ensure_ascii=False))

    def test_doctor_marketplace_texts_carry_no_machine_path(self):
        state = loader.read_state()
        state.setdefault("sources", {})["demo"] = {"source": "x", "commit": "a" * 40, "marketplace": "exemplo"}
        loader.write_state(state)
        (loader.plugins_root() / "demo").mkdir(parents=True)
        write_plugin(loader.plugins_root() / "demo")
        marketplace.state_path().write_text("{nao json", encoding="utf-8")
        doctor = run_cli("doctor", env=self.env())
        self.assertIn("marketplaces.json", doctor["marketplaces_error"])
        notice = doctor["plugins"][0]["origin"]["marketplace_notice"]
        self.assertIn("marketplaces.json", notice)
        for text in (doctor["marketplaces_error"], notice):
            self.assertNotIn(str(self.home), text)
            self.assertNotIn(str(self.home.resolve()), text)

    def test_profile_hint_names_a_real_command(self):
        self.assertIs(marketplace.not_allowed, marketplace_install.not_allowed)
        refusal = str(marketplace.not_allowed("x"))
        self.assertIn("profile show", refusal)
        self.assertNotIn("--action show", refusal)


if __name__ == "__main__":
    unittest.main()

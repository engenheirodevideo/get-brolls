"""Qual `.env` vale, os avisos que ele gera, a guarda do `GB_HOME` e os aliases `GETBROLLS_*`."""

import os
import shutil
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import _paths as paths
from getbrolls import cli, commands, config, runtime
from getbrolls.errors import UsageError
from getbrolls.runtime import OperationError

_STRIPPED = ("GB_HOME", "GETBROLLS_HOME", "GB_ENV_FILE", "GB_GIF_WIDTH", "GETBROLLS_GIF_WIDTH", "GB_LIBRARY")


def _touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fake_checkout(base):
    root = base / "repo"
    for sentinel in ("scripts/gb.py", "docs/RULES.md", "assets/brand-logo.png"):
        _touch(root / sentinel)
    pkg = root / "scripts" / "getbrolls"
    pkg.mkdir(parents=True)
    return paths.detect(pkg)


def _fake_wheel(base):
    pkg = base / "site" / "getbrolls"
    _touch(pkg / paths.WHEEL_MANIFEST)
    return paths.detect(pkg)


def _checkout_root(inst):
    assert inst.checkout_root is not None
    return inst.checkout_root


class EnvFileOrderTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gb-env-order-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.home = self.base / "home"
        self.home.mkdir()
        self.checkout = _fake_checkout(self.base)
        env = {key: value for key, value in os.environ.items() if key not in _STRIPPED}
        env["GB_HOME"] = str(self.home)
        env_patch = patch.dict(os.environ, env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        install_patch = patch.object(paths, "install", return_value=self.checkout)
        self.install = install_patch.start()
        self.addCleanup(install_patch.stop)
        self.addCleanup(config.load_env, self.base / "nao-existe.env")

    def env(self, where, text):
        return _touch(where, text)

    def checkout_env(self, text):
        return self.env(_checkout_root(self.checkout) / ".env", text)

    def home_env(self, text):
        return self.env(self.home / ".env", text)

    def execute(self, env_file=None, command="providers"):
        """Roda `commands.execute` e devolve os avisos registrados."""
        event = {"warnings": []}
        token = runtime.ACTIVE.set(event)
        try:
            commands.execute(Namespace(command=command, env_file=str(env_file) if env_file else None))
        finally:
            runtime.ACTIVE.reset(token)
        return event["warnings"]

    @staticmethod
    def codes(warnings):
        return [warning["code"] for warning in warnings]

    def test_flag_file_is_loaded(self):
        self.checkout_env("GB_GIF_WIDTH=200\n")
        flag = self.env(self.base / "flag.env", "GB_GIF_WIDTH=300\n")
        warnings = self.execute(flag)
        self.assertEqual("300", os.environ["GB_GIF_WIDTH"])
        self.assertEqual([], warnings)

    def test_gb_env_file_is_loaded_without_flag(self):
        self.checkout_env("GB_GIF_WIDTH=200\n")
        chosen = self.env(self.base / "var.env", "GB_GIF_WIDTH=320\n")
        os.environ["GB_ENV_FILE"] = str(chosen)
        self.execute()
        self.assertEqual("320", os.environ["GB_GIF_WIDTH"])

    def test_checkout_env_loads_without_a_warning_per_command(self):
        """A origem do `.env` fica só no relatório do `doctor`, nunca em todo `warnings[]`."""
        self.checkout_env("GB_GIF_WIDTH=240\n")
        warnings = self.execute()
        self.assertEqual("240", os.environ["GB_GIF_WIDTH"])
        self.assertEqual([], warnings)

    def test_doctor_install_report_explains_a_checkout_env(self):
        self.checkout_env("GB_GIF_WIDTH=240\n")
        report = paths.install_report()["env_file"]
        self.assertEqual("checkout", report["source"])
        self.assertIn("$GB_HOME/.env", report["note"])
        self.assertNotIn(str(self.base), report["note"])

    def test_doctor_install_report_has_no_note_for_a_home_env(self):
        self.install.return_value = _fake_wheel(self.base)
        self.home_env("GB_GIF_WIDTH=260\n")
        self.assertIsNone(paths.install_report()["env_file"]["note"])

    def test_home_env_loads_for_a_wheel_install(self):
        self.install.return_value = _fake_wheel(self.base)
        self.home_env("GB_GIF_WIDTH=260\n")
        warnings = self.execute()
        self.assertEqual("260", os.environ["GB_GIF_WIDTH"])
        self.assertEqual([], warnings)

    def test_two_env_files_warn_and_never_merge(self):
        self.checkout_env("GB_GIF_WIDTH=240\n")
        self.home_env("GB_GIF_WIDTH=260\nGB_LIBRARY=off\n")
        warnings = self.execute()
        self.assertEqual("240", os.environ["GB_GIF_WIDTH"])
        self.assertNotIn("GB_LIBRARY", os.environ)
        self.assertEqual(["ENV_FILE_SHADOWED"], self.codes(warnings))
        self.assertNotIn(str(self.base), warnings[0]["message"])
        self.assertIn("doctor", warnings[0]["message"])

    def test_shadowed_warning_reaches_every_command_output(self):
        self.checkout_env("GB_GIF_WIDTH=240\n")
        self.home_env("GB_GIF_WIDTH=260\n")
        result = cli.main(["providers"])
        self.assertEqual(["ENV_FILE_SHADOWED"], self.codes(result["warnings"]))

    def test_gb_home_in_gb_home_env_is_a_usage_error(self):
        self.install.return_value = _fake_wheel(self.base)
        self.home_env("GB_GIF_WIDTH=260\nGB_HOME=/outro\n")
        with self.assertRaises(UsageError) as caught:
            self.execute()
        self.assertIn("GB_HOME", str(caught.exception))
        self.assertIn("linha 2", str(caught.exception))
        # Nada foi exportado antes da recusa.
        self.assertNotIn("GB_GIF_WIDTH", os.environ)
        self.assertEqual(str(self.home), os.environ["GB_HOME"])

    def test_gb_home_from_a_checkout_env_is_honored_but_deprecated(self):
        other = self.base / "other-home"
        self.checkout_env(f"GB_HOME={other}\n")
        del os.environ["GB_HOME"]
        with patch.object(Path, "home", return_value=self.base / "user"):
            warnings = self.execute()
        self.assertEqual(str(other), os.environ["GB_HOME"])
        self.assertEqual(["DEPRECATED"], self.codes(warnings))
        self.assertIn("2.7", warnings[0]["message"])

    def test_gb_home_from_a_flag_env_is_honored_but_deprecated(self):
        other = self.base / "other-home"
        flag = self.env(self.base / "flag.env", f"GB_HOME={other}\n")
        del os.environ["GB_HOME"]
        with patch.object(Path, "home", return_value=self.base / "user"):
            warnings = self.execute(flag)
        self.assertEqual(str(other), os.environ["GB_HOME"])
        self.assertEqual(["DEPRECATED"], self.codes(warnings))

    def test_gb_home_in_a_env_is_not_deprecated_when_the_process_already_had_it(self):
        """O ambiente do processo vence: a linha do `.env` não valeu, então não há o que avisar."""
        flag = self.env(self.base / "flag.env", f"GB_HOME={self.base / 'other-home'}\n")
        warnings = self.execute(flag)
        self.assertEqual(str(self.home), os.environ["GB_HOME"])
        self.assertEqual([], warnings)

    def test_gb_home_deprecation_survives_the_early_load_in_main(self):
        other = self.base / "other-home"
        self.checkout_env(f"GB_HOME={other}\n")
        del os.environ["GB_HOME"]
        with patch.object(Path, "home", return_value=self.base / "user"):
            result = cli.main(["providers"])
        self.assertEqual(str(other), os.environ["GB_HOME"])
        self.assertEqual(["DEPRECATED"], self.codes(result["warnings"]))

    def test_gb_home_env_is_refused_by_path_whatever_route_loads_it(self):
        """`$GB_HOME/.env` é o mesmo arquivo por `GB_ENV_FILE` ou `--env-file`: `GB_HOME` ali é recusado."""
        self.home_env("GB_GIF_WIDTH=260\nGB_HOME=/outro\n")
        for route in ("GB_ENV_FILE", "--env-file"):
            with self.subTest(route=route):
                if route == "GB_ENV_FILE":
                    os.environ["GB_ENV_FILE"] = str(self.home / "." / ".env")
                    flag = None
                else:
                    os.environ.pop("GB_ENV_FILE", None)
                    flag = self.home / ".env"
                with self.assertRaises(UsageError) as caught:
                    self.execute(flag)
                self.assertIn("GB_HOME (linha 2)", str(caught.exception))
                self.assertNotIn("GB_GIF_WIDTH", os.environ)
                self.assertEqual(str(self.home), os.environ["GB_HOME"])

    def test_gb_home_env_by_gb_env_file_exits_2(self):
        self.home_env("GB_HOME=/outro\n")
        os.environ["GB_ENV_FILE"] = str(self.home / ".env")
        with self.assertRaises(OperationError) as caught:
            cli.main(["providers"])
        self.assertEqual("USAGE_ERROR", caught.exception.payload["error_code"])
        self.assertEqual(2, runtime.exit_code_for(caught.exception.payload["error_code"]))

    def test_a_env_pointing_gb_home_at_its_own_folder_is_refused(self):
        """`GB_HOME=<pasta deste .env>` faria o arquivo virar `$GB_HOME/.env`: recusado já na primeira leitura."""
        folder = self.base / "pessoal"
        flag = self.env(folder / ".env", f"GB_HOME={folder}\n")
        del os.environ["GB_HOME"]
        with self.assertRaises(UsageError):
            self.execute(flag)

    def test_gb_env_file_inside_a_env_is_refused(self):
        for where in (self.base / "flag.env", self.home / ".env", _checkout_root(self.checkout) / ".env"):
            with self.subTest(where=where.name):
                path = self.env(where, "GB_GIF_WIDTH=300\nGB_ENV_FILE=/x\n")
                with self.assertRaises(UsageError) as caught:
                    config.load_env(path, source="flag")
                self.assertIn("GB_ENV_FILE (linha 2)", str(caught.exception))
                self.assertNotIn("GB_GIF_WIDTH", os.environ)
                path.unlink()

    def test_missing_flag_file_is_a_usage_error_before_the_project_is_touched(self):
        project = self.base / "project"
        project.mkdir()
        with self.assertRaises(OperationError) as caught:
            cli.main(["--env-file", str(self.base / "nao.env"), "rules", "--project", str(project)])
        self.assertIn("--env-file", str(caught.exception))
        self.assertFalse((project / "brolls").exists())

    def test_missing_gb_env_file_is_a_usage_error_before_the_project_is_touched(self):
        project = self.base / "project"
        project.mkdir()
        os.environ["GB_ENV_FILE"] = str(self.base / "nao.env")
        with self.assertRaises(OperationError) as caught:
            cli.main(["rules", "--project", str(project)])
        self.assertIn("GB_ENV_FILE", str(caught.exception))
        self.assertFalse((project / "brolls").exists())

    def test_refused_env_key_never_touches_the_project(self):
        project = self.base / "project"
        project.mkdir()
        self.install.return_value = _fake_wheel(self.base)
        self.home_env("GB_HOME=/outro\n")
        with self.assertRaises(OperationError) as caught:
            cli.main(["rules", "--project", str(project)])
        self.assertIn("GB_HOME", str(caught.exception))
        self.assertFalse((project / "brolls").exists())

    def test_missing_flag_file_is_a_usage_error_in_execute(self):
        with self.assertRaises(UsageError):
            self.execute(self.base / "nao.env")

    def test_absent_env_is_not_an_error(self):
        self.install.return_value = _fake_wheel(self.base)
        self.assertEqual([], self.execute())
        self.assertNotIn("GB_GIF_WIDTH", os.environ)

    def test_getbrolls_alias_reaches_the_core(self):
        os.environ["GETBROLLS_GIF_WIDTH"] = "400"
        cli.main(["providers"])
        self.assertEqual("400", os.environ["GB_GIF_WIDTH"])

    def test_gb_key_beats_its_alias(self):
        os.environ["GETBROLLS_GIF_WIDTH"] = "400"
        os.environ["GB_GIF_WIDTH"] = "500"
        cli.main(["providers"])
        self.assertEqual("500", os.environ["GB_GIF_WIDTH"])

    def test_process_alias_beats_the_env_file(self):
        """O alias é ambiente do processo: vence o `.env` como o próprio `GB_*`."""
        self.checkout_env("GB_GIF_WIDTH=240\n")
        os.environ["GETBROLLS_GIF_WIDTH"] = "400"
        cli.main(["providers"])
        self.assertEqual("400", os.environ["GB_GIF_WIDTH"])


if __name__ == "__main__":
    unittest.main()

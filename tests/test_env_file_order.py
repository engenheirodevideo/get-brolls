"""Qual `.env` vale, os avisos que ele gera e a guarda do `GB_HOME`."""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from _paths import CLI_ARGV

from getbrolls import _paths as paths
from getbrolls import cli, commands, config, env_loader, install_report, runtime
from getbrolls.errors import UsageError
from getbrolls.runtime import OperationError

_STRIPPED = (
    "GB_HOME",
    "GETBROLLS_HOME",
    "GB_ENV_FILE",
    "GB_GIF_WIDTH",
    "GETBROLLS_GIF_WIDTH",
    "GB_LIBRARY",
    "GB_CACHE_DIR",
    "GETBROLLS_CACHE_DIR",
)


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
        self.addCleanup(env_loader.load_env, self.base / "nao-existe.env")

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
        report = install_report.install_report()["env_file"]
        self.assertEqual("checkout", report["source"])
        self.assertIn("$GB_HOME/.env", report["note"])
        self.assertNotIn(str(self.base), report["note"])

    def test_doctor_install_report_has_no_note_for_a_home_env(self):
        self.install.return_value = _fake_wheel(self.base)
        self.home_env("GB_GIF_WIDTH=260\n")
        self.assertIsNone(install_report.install_report()["env_file"]["note"])

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
                    env_loader.load_env(path, source="flag")
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

    def test_env_with_bom_is_read(self):
        # O Bloco de Notas do Windows salva "UTF-8 com BOM": sem tirar o BOM, a 1ª chave
        # virava `\ufeffGB_GIF_WIDTH`, desconhecida.
        flag = self.base / "bom.env"
        flag.write_text("GB_GIF_WIDTH=300\nGB_LIBRARY=off\n", encoding="utf-8-sig")
        self.assertTrue(flag.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual([], self.execute(flag))
        self.assertEqual("300", os.environ["GB_GIF_WIDTH"])
        self.assertEqual("off", os.environ["GB_LIBRARY"])

    def test_env_in_utf16_is_a_portuguese_usage_error(self):
        flag = self.base / "utf16.env"
        flag.write_text("GB_GIF_WIDTH=300\n", encoding="utf-16")
        with self.assertRaises(OperationError) as caught:
            cli.main(["--env-file", str(flag), "providers"])
        payload = caught.exception.payload
        self.assertEqual("USAGE_ERROR", payload["error_code"])
        self.assertEqual(2, runtime.exit_code_for(payload["error_code"]))
        self.assertIn("UTF-8", payload["message"])
        # O `--env-file` que a pessoa digitou volta como ela o digitou.
        self.assertIn(f"{flag} não está em UTF-8", payload["message"])
        self.assertNotIn("codec", payload["message"])
        self.assertNotIn("GB_GIF_WIDTH", os.environ)

    def test_missing_flag_file_is_a_usage_error_in_execute(self):
        with self.assertRaises(UsageError):
            self.execute(self.base / "nao.env")

    def test_absent_env_is_not_an_error(self):
        self.install.return_value = _fake_wheel(self.base)
        self.assertEqual([], self.execute())
        self.assertNotIn("GB_GIF_WIDTH", os.environ)

    def test_getbrolls_alias_is_ignored(self):
        """`GETBROLLS_X` saiu (rota dupla da 2.6 em testes): só `GB_X` vale, como na 2.5."""
        os.environ["GETBROLLS_GIF_WIDTH"] = "400"
        cli.main(["providers"])
        self.assertNotIn("GB_GIF_WIDTH", os.environ)

    def test_getbrolls_cache_dir_still_works_but_warns_once_naming_the_replacement(self):
        os.environ["GETBROLLS_CACHE_DIR"] = "/onde/for"
        self.assertEqual(Path("/onde/for"), config.cache_root())
        warnings = self.execute()
        self.assertEqual(["DEPRECATED"], self.codes(warnings))
        self.assertIn("GETBROLLS_CACHE_DIR", warnings[0]["message"])
        self.assertIn("GB_CACHE_DIR", warnings[0]["message"])
        self.assertNotIn("/onde/for", warnings[0]["message"])
        result = cli.main(["providers"])
        self.assertEqual(["DEPRECATED"], self.codes(result["warnings"]))

    def test_getbrolls_cache_dir_does_not_warn_when_it_is_not_the_one_in_use(self):
        os.environ["GETBROLLS_CACHE_DIR"] = "/antigo"
        os.environ["GB_CACHE_DIR"] = "/novo"
        self.assertEqual([], self.execute())
        os.environ["GB_CACHE_DIR"] = ""
        self.assertEqual(["DEPRECATED"], self.codes(self.execute()))
        os.environ["GETBROLLS_CACHE_DIR"] = ""
        os.environ.pop("GB_CACHE_DIR")
        self.assertEqual([], self.execute())

    def test_env_file_with_a_getbrolls_name_is_refused_pointing_to_the_gb_name(self):
        flag = self.env(self.base / "antigo.env", "GETBROLLS_GIF_WIDTH=400\n")
        with self.assertRaises(OperationError) as caught:
            cli.main(["--env-file", str(flag), "providers"])
        payload = caught.exception.payload
        self.assertEqual("INVALID_DATA", payload["error_code"])
        self.assertEqual(1, runtime.exit_code_for(payload["error_code"]))
        self.assertIn("variável desconhecida na linha 1: GETBROLLS_GIF_WIDTH", payload["message"])
        self.assertIn("Use `GB_GIF_WIDTH`", payload["message"])
        other = self.env(self.base / "outro.env", "GETBROLLS_SEM_PAR=1\n")
        with self.assertRaises(OperationError) as caught:
            cli.main(["--env-file", str(other), "providers"])
        self.assertNotIn("Use `GB_", caught.exception.payload["message"])


_NO_CHMOD = os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0)


def _refusal(*args):
    """Roda a CLI com o espelho do log: (código, envelope do erro, `reason` do `command_refused`)."""
    done = subprocess.run(
        [*CLI_ARGV, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "GB_LOG_STDERR": "1"},
        check=False,
    )
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    refused = [re.search(r"reason=(\S+)", line) for line in lines if "event=command_refused" in line]
    reason = refused[-1].group(1) if refused and refused[-1] else None
    return done.returncode, json.loads(lines[-1]), reason


class UnreadableEnvFileTests(unittest.TestCase):
    @unittest.skipIf(_NO_CHMOD, "chmod 000 não barra")
    def test_unreadable_env_file_is_io_error_and_creates_nothing(self):
        # Um `.env` que existe mas não dá para ler é erro de E/S da pessoa (permissão), não bug:
        # sai antes do log e da auditoria, como os outros erros do `.env`, sem criar `brolls/`.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "nao-projeto"
            folder.mkdir()
            locked = _touch(Path(tmp) / "travado.env", "GB_LOG_LEVEL=INFO\n")
            locked.chmod(0)
            try:
                code, error, reason = _refusal("--env-file", locked, "status", "--project", folder)
            finally:
                locked.chmod(0o600)
            self.assertEqual(1, code)
            self.assertEqual("IO_ERROR", error["error_code"])
            self.assertIn(f"{locked} (", error["error"])
            self.assertEqual("env_unreadable", reason)
            self.assertFalse((folder / "brolls").exists())

    def test_an_unreadable_folder_is_not_reported_as_the_env(self):
        # O que falha antes do comando sem ser o `.env` (o perfil procurado numa pasta sem
        # permissão, um `$GB_HOME` fechado) é E/S comum: `reason=io`, e a mensagem não chama
        # a pasta de arquivo.
        folder = "/pasta/fechada"
        denied = PermissionError(13, "Permission denied", folder)
        args = Namespace(command="status", project=folder + "/p", env_file=None)
        with (
            patch.object(env_loader, "load_environment", side_effect=denied),
            patch.object(cli, "_log_early_refusal") as refused,
            self.assertRaises(OperationError) as caught,
        ):
            cli._load_env_early(args)  # pylint: disable=protected-access
        refused.assert_called_once_with(args, "IO_ERROR", "io")
        payload = caught.exception.payload
        self.assertEqual("IO_ERROR", payload["error_code"])
        self.assertRegex(payload["message"], r"<pasta fora do projeto>/#[0-9a-f]{8} \(")
        self.assertNotIn(Path(folder).name, payload["message"])
        self.assertIn("Confira as permissões e repita", payload["message"])
        self.assertNotIn("arquivo", payload["message"])

    @unittest.skipIf(_NO_CHMOD, "chmod 000 não barra")
    def test_foreground_serve_reports_an_unreadable_env_as_io_error(self):
        # `serve` em primeiro plano lê o `.env` dentro de `execute()`, antes da recusa
        # antecipada: o `OSError` dele também é IO_ERROR (saída 1), nunca INTERNAL_ERROR.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "projeto"
            (project / "brolls").mkdir(parents=True)
            locked = _touch(Path(tmp) / "travado.env", "GB_LOG_LEVEL=INFO\n")
            locked.chmod(0)
            try:
                done = subprocess.run(
                    [*CLI_ARGV, "--env-file", str(locked), "serve", "--project", str(project), "--port", "9"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=60,
                )
            finally:
                locked.chmod(0o600)
            self.assertEqual(1, done.returncode, done.stdout + done.stderr)
            self.assertNotIn("Traceback", done.stdout + done.stderr)
            payload = json.loads(done.stdout.strip().splitlines()[-1])
            self.assertEqual("IO_ERROR", payload["error_code"])
            self.assertIn(f"{locked} (", payload["error"])
            self.assertIn("Confira as permissões", payload["error"])

    def test_foreground_serve_reports_a_denied_port_as_io_error(self):
        # A porta que o sistema nega (EACCES abaixo de 1024 no Linux) não tem arquivo: a
        # mensagem genérica de E/S, saída 1, nunca INTERNAL_ERROR.
        out = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(commands, "execute", side_effect=PermissionError(13, "Permission denied")),
            contextlib.redirect_stdout(out),
            self.assertRaises(SystemExit) as caught,
        ):
            cli.main(["serve", "--project", tmp, "--port", "80"])
        self.assertEqual(1, caught.exception.code)
        payload = json.loads(out.getvalue())
        self.assertEqual("IO_ERROR", payload["error_code"])
        self.assertIn("sem permissão", payload["error"])
        self.assertIn("Confira as permissões e repita", payload["error"])


if __name__ == "__main__":
    unittest.main()

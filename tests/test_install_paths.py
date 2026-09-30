"""Onde a instalação mora: dados, CLI, runtime e `.env`, por sentinela e sem chute."""

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import _paths as paths
from getbrolls.errors import DataRootError, PrerequisiteError, UsageError

GB = ROOT / "scripts" / "gb.py"


def _touch(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fake_wheel(base, entries=paths.REQUIRED_DATA):
    """Pacote instalado com `_data/MANIFEST` e os arquivos pedidos."""
    pkg = base / "site" / "getbrolls"
    data = pkg / "_data"
    _touch(data / "MANIFEST", "# cabeçalho\n" + "".join(f"{entry}\n" for entry in paths.REQUIRED_DATA))
    for entry in entries:
        _touch(data / entry, entry)
    return paths.detect(pkg)


def _fake_checkout(base):
    """Checkout mínimo: só as sentinelas do repositório."""
    root = base / "repo"
    for sentinel in ("scripts/gb.py", "docs/RULES.md", "assets/brand-logo.png"):
        _touch(root / sentinel)
    pkg = root / "scripts" / "getbrolls"
    pkg.mkdir(parents=True)
    return paths.detect(pkg)


def _dir(path):
    """Pasta que a instalação falsa sempre tem (o pyright não sabe disso)."""
    assert path is not None
    return path


def _clean_env(**values):
    names = ("GB_HOME", "GETBROLLS_HOME", "GB_RUNTIME_DIR", "GB_ENV_FILE")
    env = {key: value for key, value in os.environ.items() if key not in names}
    env.update(values)
    return env


class DetectTests(unittest.TestCase):
    def setUp(self):
        paths.install.cache_clear()
        self.addCleanup(paths.install.cache_clear)

    def test_checkout_is_recognized_by_repo_sentinels(self):
        inst = paths.install()
        self.assertEqual("checkout", inst.origin)
        self.assertEqual(ROOT, inst.data_root)
        self.assertEqual(ROOT, inst.checkout_root)
        self.assertEqual("checkout", paths.origin())

    def test_wheel_sentinel_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = _fake_wheel(Path(tmp))
            self.assertEqual("wheel", inst.origin)
            self.assertEqual(Path(tmp) / "site" / "getbrolls" / "_data", inst.data_root)
            self.assertIsNone(inst.checkout_root)

    def test_lib_python_parent_is_never_a_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "lib" / "python3.11"
            pkg = lib / "site-packages" / "getbrolls"
            pkg.mkdir(parents=True)
            _touch(lib / "docs" / "RULES.md")
            _touch(lib / "assets" / "brand-logo.png")
            inst = paths.detect(pkg)
            self.assertEqual("unknown", inst.origin)
            self.assertIsNone(inst.data_root)
            self.assertIsNone(inst.checkout_root)
            with patch.object(paths, "install", return_value=inst):
                self.assertEqual("unknown", paths.origin())
                with self.assertRaises(DataRootError) as caught:
                    paths.data_root()
                self.assertIsInstance(caught.exception, PrerequisiteError)
                message = str(caught.exception)
                self.assertIn("Reinstale", message)
                self.assertNotIn(tmp, message)
                self.assertNotIn(str(Path.home()), message)
                with self.assertRaises(DataRootError):
                    paths.verify_data()

    def test_manifest_file_equals_required_data(self):
        self.assertEqual(tuple(sorted(paths.REQUIRED_DATA)), paths.REQUIRED_DATA)
        self.assertEqual(paths.REQUIRED_DATA, paths.manifest_entries(paths.install()))
        for entry in paths.REQUIRED_DATA:
            self.assertTrue((ROOT / entry).is_file(), entry)
        self.assertEqual([], paths.verify_data())

    def test_verify_data_names_missing_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            present = [entry for entry in paths.REQUIRED_DATA if entry != "docs/BRIEF.md"]
            inst = _fake_wheel(Path(tmp), present)
            self.assertEqual(paths.REQUIRED_DATA, paths.manifest_entries(inst))
            with patch.object(paths, "install", return_value=inst):
                self.assertEqual(["docs/BRIEF.md"], paths.verify_data())
                self.assertEqual(_dir(inst.data_root) / "docs" / "RULES.md", paths.data_path("docs", "RULES.md"))

    def test_one_missing_data_file_is_a_prerequisite_error_without_the_path(self):
        # Um arquivo só apagado do `_data` instalado: erro de pré-requisito (exit 4) que
        # nomeia a entrada e manda reinstalar, nunca `FileNotFoundError` com o caminho.
        with tempfile.TemporaryDirectory() as tmp:
            present = [entry for entry in paths.REQUIRED_DATA if entry != "docs/RULES.md"]
            inst = _fake_wheel(Path(tmp), present)
            with patch.object(paths, "install", return_value=inst), self.assertRaises(DataRootError) as caught:
                paths.data_path("docs", "RULES.md")
        message = str(caught.exception)
        self.assertIn("docs/RULES.md", message)
        self.assertIn("Reinstale", message)
        self.assertNotIn(tmp, message)


class HomeAndRuntimeTests(unittest.TestCase):
    def test_gb_home_wins_then_alias_then_default(self):
        with patch.dict(os.environ, _clean_env(GB_HOME="/a", GETBROLLS_HOME="/b"), clear=True):
            self.assertEqual(Path("/a"), paths.gb_home())
        with patch.dict(os.environ, _clean_env(GETBROLLS_HOME="/b"), clear=True):
            self.assertEqual(Path("/b"), paths.gb_home())
        with patch.dict(os.environ, _clean_env(), clear=True):
            self.assertEqual(Path.home() / ".getbrolls", paths.gb_home())

    def test_empty_gb_home_is_ignored(self):
        with patch.dict(os.environ, _clean_env(GB_HOME="", GETBROLLS_HOME="/b"), clear=True):
            self.assertEqual(Path("/b"), paths.gb_home())
        with patch.dict(os.environ, _clean_env(GB_HOME="", GETBROLLS_HOME=""), clear=True):
            self.assertEqual(Path.home() / ".getbrolls", paths.gb_home())

    def test_runtime_dir_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, _clean_env(GB_RUNTIME_DIR=tmp), clear=True):
            self.assertEqual(paths.RuntimePart(Path(tmp) / ".venv", "GB_RUNTIME_DIR"), paths.venv_dir())
            self.assertEqual(paths.RuntimePart(Path(tmp) / ".tools", "GB_RUNTIME_DIR"), paths.tools_dir())
            self.assertTrue(paths.runtime_info()["explicit"])

    def test_shared_runtime_is_keyed_by_requirements_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inst = _fake_wheel(base)
            home = base / "home"
            with (
                patch.object(paths, "install", return_value=inst),
                patch.dict(os.environ, _clean_env(GB_HOME=str(home)), clear=True),
            ):
                _touch(_dir(inst.data_root) / "requirements.txt", "yt-dlp==1\n")
                _touch(_dir(inst.data_root) / "package-lock.json", "{}\n")
                first = paths.requirements_sha()
                expected = hashlib.sha256(b"yt-dlp==1\n" + b"\0" + b"{}\n").hexdigest()[:16]
                self.assertEqual(expected, first)
                _touch(_dir(inst.data_root) / "requirements.txt", "yt-dlp==2\n")
                second = paths.requirements_sha()
                assert second is not None
                self.assertNotEqual(first, second)
                self.assertEqual(paths.RuntimePart(home / "runtime" / second / ".venv", "gb_home"), paths.venv_dir())
                self.assertEqual(paths.RuntimePart(home / "runtime" / second / ".tools", "gb_home"), paths.tools_dir())
                info = paths.runtime_info()
                self.assertEqual(second, info["req_sha"])
                self.assertEqual({"path": str(home / "runtime" / second / ".venv"), "source": "gb_home"}, info["venv"])
                self.assertFalse(info["explicit"])

    def test_requirements_sha_is_none_without_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = _fake_wheel(Path(tmp), ())
            with patch.object(paths, "install", return_value=inst):
                self.assertIsNone(paths.requirements_sha())

    def test_checkout_venv_is_the_fallback_only_for_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            checkout = _fake_checkout(base)
            (_dir(checkout.checkout_root) / ".venv").mkdir()
            wheel = _fake_wheel(base)
            (wheel.package_dir.parent / ".venv").mkdir()
            with patch.dict(os.environ, _clean_env(GB_HOME=str(base / "home")), clear=True):
                with patch.object(paths, "install", return_value=checkout):
                    self.assertEqual(
                        paths.RuntimePart(_dir(checkout.checkout_root) / ".venv", "checkout"), paths.venv_dir()
                    )
                with patch.object(paths, "install", return_value=wheel):
                    self.assertEqual("gb_home", paths.venv_dir().source)

    def test_shared_runtime_beats_the_checkout_venv_once_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            checkout = _fake_checkout(base)
            _touch(_dir(checkout.checkout_root) / "requirements.txt", "yt-dlp\n")
            _touch(_dir(checkout.checkout_root) / "package-lock.json", "{}\n")
            (_dir(checkout.checkout_root) / ".venv").mkdir()
            home = base / "home"
            with (
                patch.object(paths, "install", return_value=checkout),
                patch.dict(os.environ, _clean_env(GB_HOME=str(home)), clear=True),
            ):
                shared = home / "runtime" / str(paths.requirements_sha()) / ".venv"
                shared.mkdir(parents=True)
                self.assertEqual(paths.RuntimePart(shared, "gb_home"), paths.venv_dir())


class EnvFileOrderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with  # limpo no addCleanup
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.checkout = _fake_checkout(self.base)
        self.home = self.base / "home"
        self.checkout_env = _touch(_dir(self.checkout.checkout_root) / ".env")
        self.home_env = _touch(self.home / ".env")
        self.explicit = _touch(self.base / "explicit.env")
        env_patch = patch.dict(os.environ, _clean_env(GB_HOME=str(self.home)), clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        install_patch = patch.object(paths, "install", return_value=self.checkout)
        self.install = install_patch.start()
        self.addCleanup(install_patch.stop)

    def test_flag_beats_everything(self):
        os.environ["GB_ENV_FILE"] = str(self.home_env)
        choice = paths.env_file(str(self.explicit))
        self.assertEqual(self.explicit, choice.path)
        self.assertEqual("flag", choice.source)

    def test_gb_env_file_beats_checkout_and_home(self):
        os.environ["GB_ENV_FILE"] = str(self.explicit)
        choice = paths.env_file()
        self.assertEqual(self.explicit, choice.path)
        self.assertEqual("GB_ENV_FILE", choice.source)

    def test_checkout_env_beats_home_and_lists_it_as_ignored(self):
        choice = paths.env_file()
        self.assertEqual(self.checkout_env, choice.path)
        self.assertEqual("checkout", choice.source)
        self.assertEqual((self.home_env,), choice.ignored)

    def test_home_env_is_used_by_a_wheel_install(self):
        self.install.return_value = _fake_wheel(self.base)
        choice = paths.env_file()
        self.assertEqual(self.home_env, choice.path)
        self.assertEqual("gb_home", choice.source)
        self.assertEqual((), choice.ignored)

    def test_missing_flag_is_a_usage_error_with_the_2_5_text(self):
        with self.assertRaises(UsageError) as caught:
            paths.env_file(str(self.base / "nada.env"))
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual("--env-file não existe. Confira o caminho.", str(caught.exception))

    def test_missing_gb_env_file_is_a_usage_error(self):
        os.environ["GB_ENV_FILE"] = str(self.base / "nada.env")
        with self.assertRaises(UsageError) as caught:
            paths.env_file()
        self.assertEqual(
            "GB_ENV_FILE aponta para um arquivo que não existe. Confira o caminho ou remova a variável.",
            str(caught.exception),
        )

    def test_nothing_found_is_reported_not_silent(self):
        self.checkout_env.unlink()
        self.home_env.unlink()
        choice = paths.env_file()
        self.assertIsNone(choice.path)
        self.assertIsNone(choice.source)
        self.assertGreaterEqual(len(choice.searched), 1)
        self.assertIn(self.checkout_env, choice.searched)
        self.assertIn(self.home_env, choice.searched)

    def test_install_report_never_raises_on_a_bad_env_file(self):
        os.environ["GB_ENV_FILE"] = str(self.base / "nada.env")
        report = paths.install_report()
        self.assertIn("error", report["env_file"])
        self.assertEqual("checkout", report["origin"])
        self.assertEqual(str(self.home), report["gb_home"])


class CliInvocationTests(unittest.TestCase):
    def setUp(self):
        paths.install.cache_clear()
        self.addCleanup(paths.install.cache_clear)

    def _wheel(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with  # limpo no addCleanup
        self.addCleanup(tmp.cleanup)
        return _fake_wheel(Path(tmp.name))

    def test_checkout_argv_runs_the_script(self):
        argv = paths.cli_argv()
        self.assertEqual([sys.executable, str(GB)], argv)
        done = subprocess.run([*argv, "--version"], capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(0, done.returncode, done.stderr)

    def test_wheel_argv_is_isolated_module_mode(self):
        with patch.object(paths, "install", return_value=self._wheel()):
            self.assertEqual([sys.executable, "-P", "-m", "getbrolls"], paths.cli_argv())

    def test_cli_argv_returns_a_fresh_list(self):
        first = paths.cli_argv()
        first.append("x")
        self.assertNotIn("x", paths.cli_argv())

    def test_checkout_prefix_is_byte_identical_on_posix(self):
        self.assertEqual(f'python3 "{GB}"', paths.cli_prefix_text("posix"))
        self.assertEqual(["python3", str(GB)], paths.cli_command("posix"))

    def test_windows_prefix_uses_python(self):
        self.assertEqual(f'python "{GB}"', paths.cli_prefix_text("nt"))
        self.assertEqual(["python", str(GB)], paths.cli_command("nt"))

    def test_wheel_prefix_prefers_console_script(self):
        script = str(Path(sys.executable).parent / "getbrolls")
        with (
            patch.object(paths, "install", return_value=self._wheel()),
            patch.object(paths.shutil, "which", return_value=script),
        ):
            self.assertEqual(["getbrolls"], paths.cli_command("posix"))
            self.assertEqual("getbrolls", paths.cli_prefix_text("posix"))

    def test_wheel_prefix_falls_back_to_python_dash_m(self):
        fallback = [sys.executable, "-P", "-m", "getbrolls"]
        with patch.object(paths, "install", return_value=self._wheel()):
            with patch.object(paths.shutil, "which", return_value=None):
                self.assertEqual(fallback, paths.cli_command("posix"))
                self.assertEqual(
                    " ".join(paths.quote_arg(a, "posix") for a in fallback), paths.cli_prefix_text("posix")
                )
            # Outro `getbrolls` no PATH (outra instalação) não é o deste interpretador.
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch.object(paths.shutil, "which", return_value=str(Path(tmp) / "getbrolls")),
            ):
                self.assertEqual(fallback, paths.cli_command("posix"))

    def test_module_command_by_origin(self):
        self.assertEqual(
            ["python3", str(ROOT / "scripts" / "getbrolls" / "serve.py")],
            paths.module_command("getbrolls.serve", "posix"),
        )
        with patch.object(paths, "install", return_value=self._wheel()):
            self.assertEqual([sys.executable, "-m", "getbrolls.serve"], paths.module_command("getbrolls.serve"))


class QuotingTests(unittest.TestCase):
    CASES = ("a b", r"C:\Program Files\x y\gb.py", "it's", 'say "hi"', "", "ação", "tail\\", r"C:\a\b")

    def setUp(self):
        paths.install.cache_clear()
        self.addCleanup(paths.install.cache_clear)

    def test_round_trip_posix_and_nt(self):
        for os_name in ("posix", "nt"):
            prefix = paths.cli_command(os_name)
            for case in self.CASES:
                with self.subTest(os_name=os_name, case=case):
                    text = paths.command_text(case, os_name=os_name)
                    self.assertEqual([case], paths.split_command(text, os_name)[len(prefix) :])
            with self.subTest(os_name=os_name, case="all"):
                text = paths.command_text(*self.CASES, os_name=os_name)
                self.assertEqual(list(self.CASES), paths.split_command(text, os_name)[len(prefix) :])

    def test_nt_quotes_any_backslash_for_bash(self):
        self.assertEqual('"C:\\a\\b"', paths.quote_arg(r"C:\a\b", "nt"))
        self.assertEqual("plain-name_1.txt", paths.quote_arg("plain-name_1.txt", "nt"))
        self.assertEqual('""', paths.quote_arg("", "nt"))
        self.assertEqual('"tail\\\\"', paths.quote_arg("tail\\", "nt"))
        self.assertEqual('"say \\"hi\\""', paths.quote_arg('say "hi"', "nt"))

    def test_hint_is_path_free(self):
        self.assertEqual("python3 scripts/gb.py doctor", paths.cli_hint("doctor"))
        with tempfile.TemporaryDirectory() as tmp, patch.object(paths, "install", return_value=_fake_wheel(Path(tmp))):
            self.assertEqual("getbrolls doctor", paths.cli_hint("doctor"))

    def test_installer_hint_by_origin(self):
        hint = paths.installer_hint()
        self.assertIn(str(ROOT / "scripts" / "install.sh"), hint)
        self.assertIn("install.ps1", hint)
        self.assertIn("no Windows", hint)
        with tempfile.TemporaryDirectory() as tmp, patch.object(paths, "install", return_value=_fake_wheel(Path(tmp))):
            self.assertIn("setup --check", paths.installer_hint())


class AliasTests(unittest.TestCase):
    def test_alias_fills_unset_core_key(self):
        environ = {"GETBROLLS_LOG_LEVEL": "DEBUG"}
        self.assertEqual(["GETBROLLS_LOG_LEVEL"], paths.apply_env_aliases(environ))
        self.assertEqual("DEBUG", environ["GB_LOG_LEVEL"])
        self.assertEqual(["GETBROLLS_LOG_LEVEL"], paths.aliased_env_names(environ))

    def test_alias_never_overrides_gb_key(self):
        environ = {"GETBROLLS_LOG_LEVEL": "DEBUG", "GB_LOG_LEVEL": "ERROR"}
        self.assertEqual([], paths.apply_env_aliases(environ))
        self.assertEqual("ERROR", environ["GB_LOG_LEVEL"])
        self.assertEqual([], paths.aliased_env_names(environ))

    def test_unknown_getbrolls_name_is_ignored(self):
        environ = {"GETBROLLS_NAO_EXISTE": "1"}
        self.assertEqual([], paths.apply_env_aliases(environ))
        self.assertNotIn("GB_NAO_EXISTE", environ)

    def test_process_only_key_alias(self):
        environ = {"GETBROLLS_ENV_FILE": "/x/.env"}
        self.assertEqual(["GETBROLLS_ENV_FILE"], paths.apply_env_aliases(environ))
        self.assertEqual("/x/.env", environ["GB_ENV_FILE"])


class BuildInfoTests(unittest.TestCase):
    def setUp(self):
        paths.install.cache_clear()
        self.addCleanup(paths.install.cache_clear)

    def test_build_info_names_the_checkout(self):
        info = paths.build_info()
        self.assertEqual("checkout", info["origin"])
        self.assertEqual(str(ROOT), info["data_root"])
        self.assertEqual(".".join(map(str, sys.version_info[:3])), info["python"])

    def test_install_report_for_an_unknown_install_lists_every_file_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "getbrolls"
            pkg.mkdir()
            with patch.object(paths, "install", return_value=paths.detect(pkg)):
                report = paths.install_report()
        self.assertEqual("unknown", report["origin"])
        self.assertIsNone(report["data_root"])
        self.assertEqual(list(paths.REQUIRED_DATA), report["data_missing"])
        self.assertIsNone(report["runtime"]["req_sha"])


# Único dono de "onde a instalação mora". Fora dele, cada ocorrência abaixo é uma linha
# exata, com o motivo; uma linha nova que ache dados ou a CLI por conta própria quebra
# o wheel (o pacote instalado não tem `scripts/gb.py` nem pastas acima dele).
LOCATOR_OWNER = "_paths.py"
LOCATOR_PATTERNS = ("parents[", "gb.py", "__file__")
LOCATOR_ALLOWLIST = {
    (
        "instagram_pairs.py",
        "_package_dir = Path(__file__).resolve().parent",
    ): "fallback do modo script (`python3 scripts/getbrolls/instagram_pairs.py`): tira a pasta "
    "do pacote de sys.path e importa o próprio pacote; não localiza dados",
    (
        "social.py",
        '"após /plugin update é preciso reinstalar. Confira com python3 scripts/gb.py doctor."',
    ): "texto só do checkout: o ramo `_from_checkout()`; o pacote usa `_paths.cli_hint`",
    (
        "sdk/api.py",
        '`[python, caminho/gb.py]` num checkout ou `[python, "-P", "-m", "getbrolls"]` no pacote',
    ): "docstring de `PluginApi.cli_argv`, que delega a `_paths.cli_argv`",
    (
        "sdk/scaffold.py",
        "FOLDER = Path(__file__).resolve().parent.parent",
    ): "texto do teste gerado para o plugin novo: aponta para a pasta do plugin, não do getbrolls",
    (
        "sdk/loader.py",
        "# lado do `__file__`) não pode custar a memória/tempo dele a cada comando: passou do",
    ): "comentário sobre o plugin carregado",
    (
        "sdk/loader.py",
        "module.__file__ = str(entry_path)",
    ): "define o `__file__` do módulo do plugin, não lê o do getbrolls",
}


class LocatorGuardTests(unittest.TestCase):
    """Guarda estática: `parents[`, `gb.py` e `__file__` só em `_paths.py` ou na allowlist."""

    def test_only_paths_locates_the_install(self):
        package = ROOT / "scripts" / "getbrolls"
        seen, problems = set(), []
        for path in sorted(package.rglob("*.py")):
            rel = path.relative_to(package).as_posix()
            if rel == LOCATOR_OWNER:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not any(pattern in line for pattern in LOCATOR_PATTERNS):
                    continue
                key = (rel, line.strip())
                if key in LOCATOR_ALLOWLIST:
                    seen.add(key)
                else:
                    problems.append(f"{rel}:{number}: {line.strip()}")
        self.assertEqual([], problems, "use getbrolls._paths (ou justifique na LOCATOR_ALLOWLIST)")
        self.assertEqual(set(LOCATOR_ALLOWLIST), seen, "entrada da allowlist que não existe mais: tire-a")

    def test_guard_catches_a_new_parents_lookup(self):
        line = 'ROOT = Path(__file__).resolve().parents[2] / "docs"'
        self.assertTrue(any(pattern in line for pattern in LOCATOR_PATTERNS))
        self.assertNotIn(("commands.py", line), LOCATOR_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()

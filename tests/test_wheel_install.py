"""O wheel de verdade: monta, instala num venv limpo e usa a CLI fora do repositório.

O build sai de uma cópia temporária só com os arquivos que o git rastreia (conteúdo da
árvore de trabalho), nunca da raiz: nada não rastreado entra no sdist e nenhum `dist/`
aparece no repositório. Sem `uv`, sem `git` ou sem rede para baixar o hatchling, o
módulo é pulado com o motivo — a não ser com `GB_REQUIRE_WHEEL_TESTS=1` (job `package`
do CI), em que a falta vira falha.

Tudo roda com `GB_HOME` próprio, sem `PYTHONPATH` e com a pasta atual fora do repositório.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import urllib.request
import zipfile
from pathlib import Path
from typing import NoReturn

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT, split_command

from getbrolls import __version__
from getbrolls import _paths as install_paths

REQUIRED = os.environ.get("GB_REQUIRE_WHEEL_TESTS") == "1"
BUILD_TIMEOUT = 600
RUN_TIMEOUT = 120
# O que o sdist pode trazer além da allowlist do pyproject: metadados que o hatchling gera.
LOCATE_PACKAGE = "import getbrolls, pathlib; print(pathlib.Path(getbrolls.__file__).parent)"
SDIST_EXTRAS = ("pyproject.toml", "PKG-INFO", ".gitignore")
FORBIDDEN_IN_SDIST = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "eval", "tests", ".github")

PLUGIN_MANIFEST = {
    "id": "chama_cli",
    "name": "Chama a CLI",
    "description": "Teste do wheel: o register() chama esta mesma instalação pela api.",
    "version": "0.1.0",
    "sdk_api": 1,
    "requires_getbrolls": ">=2.6,<3",
    "entry": "plugin.py",
    "contributes": {"commands": ["versao"]},
    "permissions": {"network": [], "env": [], "paths": []},
}
PLUGIN_CODE = """import subprocess

from getbrolls.sdk import PluginError


def versao(args, ctx):
    return {"ok": True}


def register(api):
    done = subprocess.run([*api.cli_argv(), "--version"], capture_output=True, text=True, check=False)
    if done.returncode != 0 or "dados: wheel" not in done.stdout:
        raise PluginError(f"api.cli_argv() não rodou o wheel: {done.returncode} {done.stdout!r}")
    api.command("versao", versao, "Confere a CLI desta instalação")
"""


def _sdist_allowlist():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(config["tool"]["hatch"]["build"]["targets"]["sdist"]["only-include"])


def _unavailable(reason) -> NoReturn:
    """Pula o módulo inteiro, ou falha quando o CI exige os testes do wheel."""
    if REQUIRED:
        raise AssertionError(f"GB_REQUIRE_WHEEL_TESTS=1, mas o wheel não pôde ser testado: {reason}")
    raise unittest.SkipTest(reason)


def _tail(done):
    return (done.stderr or done.stdout or "")[-2000:]


def _export_tracked_tree(target):
    """Copia para `target` só os arquivos rastreados da allowlist, com o conteúdo atual."""
    git = shutil.which("git")
    if not git:
        _unavailable("git ausente: sem ele não há como exportar só os arquivos rastreados")
    listed = subprocess.run(
        [git, "-C", str(ROOT), "ls-files", "-z", "--", *_sdist_allowlist(), *SDIST_EXTRAS],
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        _unavailable(f"git ls-files falhou: {listed.stderr.decode('utf-8', 'replace')[-500:]}")
    for name in filter(None, listed.stdout.decode("utf-8").split("\0")):
        source = ROOT / name
        if not source.is_file():  # apagado na árvore de trabalho: fica de fora, como no commit
            continue
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


class WheelInstallTests(unittest.TestCase):
    tmp: Path
    dist: Path
    wheel: Path
    sdist: Path
    venv_python: Path
    venv_bin: Path
    data_dir: Path
    env: dict

    @classmethod
    def setUpClass(cls):
        uv = shutil.which("uv")
        if not uv:
            _unavailable("uv ausente no PATH")
        cls.tmp = Path(tempfile.mkdtemp(prefix="gb-wheel-"))
        try:
            cls._build_and_install(uv)
        except BaseException:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise

    @classmethod
    def _build_and_install(cls, uv):
        source, cls.dist = cls.tmp / "src", cls.tmp / "dist"
        _export_tracked_tree(source)
        built = subprocess.run(
            [uv, "build", "-o", str(cls.dist), str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=BUILD_TIMEOUT,
            check=False,
        )
        if built.returncode != 0:
            _unavailable(f"uv build falhou (offline sem hatchling?): {_tail(built)}")
        cls.wheel = next(cls.dist.glob("getbrolls-*.whl"))
        cls.sdist = next(cls.dist.glob("getbrolls-*.tar.gz"))
        venv = cls.tmp / "venv"
        for step in (
            [uv, "venv", "--quiet", str(venv), "--python", sys.executable],
            [uv, "pip", "install", "--quiet", "--python", str(cls._python_in(venv)), str(cls.wheel)],
        ):
            done = subprocess.run(
                step, capture_output=True, text=True, encoding="utf-8", timeout=BUILD_TIMEOUT, check=False
            )
            if done.returncode != 0:
                _unavailable(f"{' '.join(step[:3])} falhou: {_tail(done)}")
        cls.venv_python = cls._python_in(venv)
        cls.venv_bin = cls.venv_python.parent
        located = subprocess.run(
            [str(cls.venv_python), "-P", "-c", LOCATE_PACKAGE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        cls.data_dir = Path(located.stdout.strip()) / "_data"
        (cls.tmp / "home").mkdir()
        (cls.tmp / "work").mkdir()
        cls.env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        cls.env["GB_HOME"] = str(cls.tmp / "home")

    @staticmethod
    def _python_in(venv):
        return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------------

    def run_argv(self, argv, env=None):
        return subprocess.run(
            [str(part) for part in argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=self.tmp / "work",
            env=env or self.env,
            timeout=RUN_TIMEOUT,
            check=False,
        )

    def gb(self, *args, env=None):
        return self.run_argv([self.venv_python, "-P", "-m", "getbrolls", *args], env=env)

    def gb_json(self, *args, expect=0, env=None):
        done = self.gb(*args, env=env)
        self.assertEqual(expect, done.returncode, _tail(done))
        self.assertNotIn("Traceback", done.stderr)
        return json.loads(done.stdout if expect == 0 else done.stderr)

    def project(self, name):
        path = self.tmp / "work" / name
        path.mkdir()
        return path

    def console_script(self):
        return self.venv_bin / ("getbrolls.exe" if os.name == "nt" else "getbrolls")

    # -- o que o build produz ---------------------------------------------------------

    def test_build_leaves_no_dist_in_the_repository(self):
        self.assertFalse((ROOT / "dist").exists())

    def test_wheel_ships_the_manifest_and_every_data_file(self):
        with zipfile.ZipFile(self.wheel) as archive:
            names = set(archive.namelist())
        self.assertIn("getbrolls/_data/MANIFEST", names)
        for entry in install_paths.REQUIRED_DATA:
            self.assertIn(f"getbrolls/_data/{entry}", names, entry)
        for name in names:
            self.assertFalse(name.startswith(("tests/", ".github/", "eval/", "getbrolls/tests/")), name)

    def test_sdist_has_only_the_allowlist(self):
        allowed = (*_sdist_allowlist(), *SDIST_EXTRAS)
        with tarfile.open(self.sdist) as archive:
            members = [member.name.split("/", 1)[1] for member in archive.getmembers() if member.isfile()]
        self.assertTrue(members)
        for name in members:
            self.assertTrue(any(name == item or name.startswith(f"{item}/") for item in allowed), name)
            self.assertNotIn(name.split("/", 1)[0], FORBIDDEN_IN_SDIST, name)

    # -- a CLI instalada --------------------------------------------------------------

    def test_console_script_and_module_report_the_wheel(self):
        for argv in ([self.console_script(), "--version"], [self.venv_python, "-m", "getbrolls", "--version"]):
            done = self.run_argv(argv)
            self.assertEqual(0, done.returncode, _tail(done))
            self.assertTrue(done.stdout.startswith(f"getbrolls {__version__}"), done.stdout)
            self.assertIn("dados: wheel", done.stdout)

    def test_rules_init_rules_init_brief_review(self):
        project = self.project("fluxo")
        self.gb_json("rules", "--project", project)
        self.gb_json("init-rules", "--project", project)
        self.gb_json("init-brief", "--project", project)
        review = self.gb_json("review", "--project", project)
        self.assertEqual((ROOT / "docs/RULES.md").read_bytes(), (project / "RULES.md").read_bytes())
        self.assertEqual((ROOT / "docs/BRIEF.md").read_bytes(), (project / "BRIEF.md").read_bytes())
        self.assertIn("data:image/png;base64,", Path(review["review"]).read_text(encoding="utf-8"))

    def test_serve_background_and_stop(self):
        project = self.project("servidor")
        self.gb_json("review", "--project", project)
        started = self.gb_json("serve", "--background", "--project", project)
        try:
            url = next(url for url in started["urls"] if "127.0.0.1" in url)
            with urllib.request.urlopen(url, timeout=30) as response:
                self.assertEqual(200, response.status)
        finally:
            stopped = self.gb_json("serve", "--stop", "--project", project)
        self.assertTrue(stopped["stopped"])

    def test_plugins_check_hyperframes_example_from_the_installed_data(self):
        example = self.data_dir / "examples" / "plugins" / "hyperframes"
        self.assertTrue(example.is_dir())
        report = self.gb_json("plugins", "--action", "check", "--path", example)
        self.assertTrue(report["ok"])
        self.assertEqual("hyperframes", report["id"])

    def test_status_suggestion_runs_as_printed(self):
        project = self.project("sugestao")
        self.gb_json("init-rules", "--project", project)
        env = {**self.env, "PATH": f"{self.venv_bin}{os.pathsep}{self.env.get('PATH', '')}"}
        do = self.gb_json("status", "--project", project, env=env)["summary"]["do"]
        self.assertEqual("init-brief", do["step"])
        argv = split_command(do["command"])
        if argv[0] == "getbrolls":
            found = shutil.which("getbrolls", path=str(self.venv_bin))
            self.assertIsNotNone(found)
        else:
            self.assertEqual(Path(argv[0]), self.venv_python)
        done = self.run_argv(argv, env=env)
        self.assertEqual(0, done.returncode, _tail(done))
        self.assertTrue((project / "BRIEF.md").is_file())

    def test_doctor_and_setup_check_exit_codes_match_ready(self):
        for args in (("doctor",), ("setup", "--check")):
            done = self.gb(*args)
            self.assertNotIn("Traceback", done.stderr)
            payload = json.loads(done.stdout)
            self.assertEqual(0 if payload["ready"] else 4, done.returncode, args)
            self.assertEqual(payload["ready"], not payload["summary"]["missing"], args)
        doctor = json.loads(self.gb("doctor").stdout)
        self.assertEqual("wheel", doctor["install"]["origin"])
        self.assertEqual([], doctor["install"]["data_missing"])

    def test_capabilities_json(self):
        manifest = self.gb_json("capabilities", "--json")
        self.assertEqual("getbrolls", manifest["prog"])
        self.assertEqual(__version__, manifest["version"])
        names = {command["name"] for command in manifest["commands"]}
        self.assertLessEqual({"doctor", "status", "capabilities"}, names)

    def test_instagram_pairs_module_help(self):
        done = self.run_argv([self.venv_python, "-P", "-m", "getbrolls.instagram_pairs", "--help"])
        self.assertEqual(0, done.returncode, _tail(done))
        self.assertIn("usage:", done.stdout)

    def test_usage_error_json_and_no_traceback(self):
        project = self.project("erros")
        typo = self.gb("serch", "--project", project)
        self.assertEqual(2, typo.returncode, _tail(typo))
        payload = json.loads(typo.stderr)
        self.assertEqual("USAGE_ERROR", payload["error_code"])
        self.assertEqual("search", payload["suggestion"])
        missing = self.gb_json("fetch", "--candidate", "nope", "--project", project, expect=1)
        self.assertEqual("INVALID_DATA", missing["error_code"])
        self.assertNotIn("traceback", missing)

    def test_env_file_from_gb_home_is_read(self):
        home = self.tmp / "home-env"
        home.mkdir()
        env = {**self.env, "GB_HOME": str(home)}
        (home / ".env").write_text("GB_GIF_WIDTH=321\n", encoding="utf-8")
        doctor = json.loads(self.gb("doctor", env=env).stdout)
        self.assertEqual("gb_home", doctor["install"]["env_file"]["source"])
        (home / ".env").write_text(f"GB_HOME={home}\n", encoding="utf-8")
        refused = self.gb_json("doctor", expect=2, env=env)
        self.assertEqual("USAGE_ERROR", refused["error_code"])

    def test_plugin_can_call_the_cli_through_api(self):
        plugin = self.tmp / "plugin"
        plugin.mkdir()
        (plugin / "getbrolls-plugin.json").write_text(json.dumps(PLUGIN_MANIFEST), encoding="utf-8")
        (plugin / "plugin.py").write_text(PLUGIN_CODE, encoding="utf-8")
        report = self.gb_json("plugins", "--action", "check", "--path", plugin)
        self.assertTrue(report["ok"])

    def test_missing_data_file_exits_4_with_a_reinstall_hint(self):
        # Um arquivo só apagado do `_data` instalado; volta no fim para os outros testes.
        template = self.data_dir / "docs" / "RULES.md"
        hidden = template.with_name("RULES.md.hidden")
        template.rename(hidden)
        try:
            refused = self.gb_json("rules", "--project", self.project("sem-dados"), expect=4)
            doctor = json.loads(self.gb("doctor").stdout)
        finally:
            hidden.rename(template)
        self.assertEqual("PREREQUISITE_MISSING", refused["error_code"])
        self.assertIn("docs/RULES.md", refused["error"])
        self.assertIn("reinstall", refused["error"])
        self.assertNotIn(str(self.data_dir), refused["error"])
        self.assertEqual(["docs/RULES.md"], doctor["install"]["data_missing"])
        self.assertFalse(doctor["ready"])


if __name__ == "__main__":
    unittest.main()

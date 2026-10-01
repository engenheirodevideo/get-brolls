"""Um `getbrolls.toml` reproduz o que o script de variáveis de ambiente do workspace já faz.

O molde em `tests/fixtures/workspace_wrapper/` tem o mesmo conjunto de ajustes de um
wrapper de workspace (`GB_HOME`, cache, venv, yt-dlp, FFmpeg e plugins desligados) escrito
das duas formas. Os testes rodam o `doctor` com cada uma e exigem o mesmo resultado.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT, WHEEL_MODE, cli

from getbrolls import __version__, profile

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "workspace_wrapper"
NT = os.name == "nt"
BIN, EXE = ("Scripts", ".cmd") if NT else ("bin", "")
# O que o ambiente de quem roda não pode decidir por estes testes.
_DROPPED = {
    *profile.FIELDS.values(),
    "GB_PROFILE",
    "GB_PROFILE_SHA256",
    "GB_ENV_FILE",
    "GB_RUNTIME_DIR",
    "GB_PLUGINS",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_exports(text):
    """Variáveis de um `env` de wrapper: só as linhas `export NOME="valor"`."""
    found = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export ") and "=" in line:
            name, _, value = line[len("export ") :].partition("=")
            found[name] = value.strip('"')
    return found


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "precisa de ffmpeg e ffprobe")
class WorkspaceParityCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.ws = self.tmp / "ws"
        self.elsewhere = self.tmp / "elsewhere"
        self.user = self.tmp / "user"
        for folder in (self.ws, self.elsewhere, self.user):
            folder.mkdir()
        self._make_ytdlp()
        self.render_all(__version__)

    def _make_ytdlp(self):
        venv_bin = self.ws / "tools" / "beta" / ".venv" / BIN
        venv_bin.mkdir(parents=True)
        stub = venv_bin / f"yt-dlp{EXE}"
        stub.write_text("@echo 2026.8.19\r\n" if NT else "#!/bin/sh\necho 2026.8.19\n", encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def render(self, name, target, version):
        text = (FIXTURE / f"{name}.in").read_text(encoding="utf-8")
        for key, value in {
            "@WS@": self.ws.as_posix(),
            "@BIN@": BIN,
            "@EXE@": EXE,
            "@FFMPEG@": Path(shutil.which("ffmpeg") or "").as_posix(),
            "@FFPROBE@": Path(shutil.which("ffprobe") or "").as_posix(),
            "@CHECKOUT@": ROOT.as_posix(),
            "@VERSION@": version,
        }.items():
            text = text.replace(key, value)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def render_all(self, version):
        self.toml = self.render("getbrolls.toml", self.ws / profile.PROFILE_NAME, version)
        if not NT:
            self.toml.chmod(0o644)
        self.env_file = self.render("get-brolls.env", self.ws / "env" / "get-brolls.env", version)
        self.wrapper = self.render("gb", self.ws / "gb", version)
        self.wrapper.chmod(0o755)

    def base_env(self):
        environ = {key: value for key, value in os.environ.items() if key not in _DROPPED}
        environ["HOME"] = environ["USERPROFILE"] = str(self.user)
        return environ

    def wrapper_env(self):
        """O ambiente que o script do workspace monta, com o `mkdir` que ele faz antes do `exec`."""
        exports = parse_exports(self.env_file.read_text(encoding="utf-8"))
        for name in ("GB_HOME", "GB_CACHE_DIR"):
            Path(exports[name]).mkdir(parents=True, exist_ok=True)
        return {**self.base_env(), **exports, "GB_PROFILE": "off"}

    def run_cli(self, *args, cwd, env):
        return subprocess.run(
            cli(*args), cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", check=False
        )

    def trust(self, env):
        done = self.run_cli("profile", "trust", "--yes", "--expect", sha(self.toml), cwd=self.ws, env=env)
        self.assertEqual(0, done.returncode, done.stderr)

    @staticmethod
    def normalized(done):
        doctor = json.loads(done.stdout)
        # Origem é o que muda de propósito (ambiente × perfil); o valor tem que ser o mesmo.
        del doctor["install"]["profile"]
        del doctor["install"]["gb_home_source"]
        return doctor

    def doctor_a(self):
        return self.run_cli("doctor", cwd=self.elsewhere, env=self.wrapper_env())

    def doctor_b(self):
        env = self.base_env()
        self.trust(env)
        return self.run_cli("doctor", cwd=self.ws, env=env)


class ProfileParityTests(WorkspaceParityCase):
    def test_profile_reproduces_the_env_wrapper_doctor(self):
        a, b = self.doctor_a(), self.doctor_b()
        self.assertEqual(a.returncode, b.returncode, (a.stderr, b.stderr))
        doc_a, doc_b = self.normalized(a), self.normalized(b)
        self.assertEqual(doc_a, doc_b)
        self.assertEqual(str(self.ws / ".getbrolls"), doc_b["install"]["gb_home"])
        self.assertEqual(doc_a["ready"], doc_b["ready"])
        self.assertEqual(doc_a["summary"], doc_b["summary"])
        for key in ("tool_paths", "resolved"):
            self.assertEqual(doc_a[key], doc_b[key], key)
        paths = doc_b["tool_paths"]
        self.assertEqual(str(self.ws / "tools" / "beta" / ".venv"), paths["GB_VENV_PATH"])
        self.assertEqual(str(self.ws / "tools" / "beta" / ".venv" / BIN / f"yt-dlp{EXE}"), paths["GB_YTDLP_PATH"])
        for name, tool in (("GB_FFMPEG_PATH", "ffmpeg"), ("GB_FFPROBE_PATH", "ffprobe")):
            self.assertEqual(os.path.realpath(shutil.which(tool) or ""), os.path.realpath(paths[name]), name)
        self.assertIsNotNone(doc_b["executables"]["yt-dlp"])
        shown = json.loads(b.stdout)["install"]["profile"]
        self.assertEqual("trusted", shown["trust"])
        self.assertEqual("off", shown["values"]["plugins"]["value"])
        self.assertEqual("profile", shown["values"]["plugins"]["source"])
        self.assertEqual({"GB_PLUGINS", "GB_HOME", "GB_CACHE_DIR"} - set(shown["applied"]), set())

    def test_profile_show_sources_match_the_wrapper_values(self):
        exports = parse_exports(self.env_file.read_text(encoding="utf-8"))
        env = self.base_env()
        self.trust(env)
        done = self.run_cli("profile", "show", cwd=self.ws, env=env)
        self.assertEqual(0, done.returncode, done.stderr)
        values = json.loads(done.stdout)["profile"]["values"]
        for field, name in profile.FIELDS.items():
            if name in exports:
                self.assertEqual(Path(exports[name]).as_posix(), Path(values[field]["value"]).as_posix(), name)
                self.assertEqual("profile", values[field]["source"], name)
        self.assertEqual("off", values["plugins"]["value"])

    def test_profile_needs_no_wrapper_directories(self):
        a = self.doctor_a()
        shutil.rmtree(self.ws / ".getbrolls")
        shutil.rmtree(self.ws / ".cache")
        b = self.doctor_b()
        self.assertEqual(a.returncode, b.returncode, b.stderr)

    @unittest.skipIf(NT or WHEEL_MODE or not shutil.which("bash"), "o script do workspace é bash sobre o checkout")
    def test_env_wrapper_script_is_equivalent(self):
        self.wrapper_env()  # o script faz o mesmo `mkdir` que o ambiente de A
        env = {**self.base_env(), "GB_PROFILE": "off"}
        env["PATH"] = self.pin_python3() + os.pathsep + env.get("PATH", "")
        script = self.run_script("doctor", env=env)
        direct = self.doctor_a()
        self.assertEqual(direct.returncode, script.returncode, script.stderr)
        self.assertEqual(self.normalized(direct), self.normalized(script))

    def pin_python3(self):
        """Pasta com um `python3` que executa o interpretador do teste.

        O script do workspace chama `python3` do PATH; sem isto ele poderia ser outra versão
        que a de A e a comparação mediria a máquina, não o script.
        """
        shim_dir = self.tmp / "pinned_python"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "python3"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
        return str(shim_dir)

    def run_script(self, *args, env):
        return subprocess.run(
            ["bash", str(self.wrapper), *args],
            cwd=self.elsewhere,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )


class ProfileGuardTests(WorkspaceParityCase):
    def test_requires_pins_the_pinned_clone_version(self):
        self.render_all("0.0.1")
        env = self.base_env()
        self.trust(env)
        project = self.ws / "p"
        (project / "brolls").mkdir(parents=True)
        done = self.run_cli("status", "--project", project, cwd=self.ws, env=env)
        self.assertEqual(4, done.returncode, done.stderr)
        self.assertEqual("PREREQUISITE_MISSING", json.loads(done.stderr)["error_code"])

    @unittest.skipIf(NT, "link simbólico exige privilégio no Windows")
    def test_symlink_into_the_home_never_applies(self):
        env = self.base_env()
        home = self.ws / ".getbrolls"
        home.mkdir(exist_ok=True)
        shutil.copyfile(self.toml, home / profile.PROFILE_NAME)
        linked = self.tmp / "linked"
        linked.mkdir()
        (linked / profile.PROFILE_NAME).symlink_to(home / profile.PROFILE_NAME)
        done = self.run_cli("doctor", cwd=linked, env=env)
        shown = json.loads(done.stdout)["install"]["profile"]
        self.assertEqual("invalid", shown["trust"])
        self.assertEqual([], shown["applied"])
        (linked / "p" / "brolls").mkdir(parents=True)
        blocked = self.run_cli("status", "--project", linked / "p", cwd=linked, env=env)
        self.assertEqual(2, blocked.returncode, blocked.stderr)

    def test_child_with_an_edited_toml_is_changed(self):
        env = self.base_env()
        self.trust(env)
        project = self.ws / "p"
        (project / "brolls").mkdir(parents=True)
        inherited = {**env, "GB_PROFILE": str(self.toml), "GB_PROFILE_SHA256": sha(self.toml)}
        self.assertEqual(0, self.run_cli("status", "--project", project, cwd=self.ws, env=inherited).returncode)
        self.toml.write_text(self.toml.read_text(encoding="utf-8") + "# editado\n", encoding="utf-8")
        done = self.run_cli("status", "--project", project, cwd=self.ws, env=inherited)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("mudou", json.loads(done.stderr)["error"])


if __name__ == "__main__":
    unittest.main()

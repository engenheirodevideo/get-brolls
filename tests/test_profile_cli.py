"""getbrolls.toml na CLI: `--profile`, `profile show|trust|untrust`, ordem das camadas e `doctor`."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import cli, split_command
from _plugin_pins import pin_plugins
from test_sdk_loader import MANIFEST, PLUGIN_CODE

from getbrolls import _paths, capabilities, config, profile, serve
from getbrolls import cli as gb_cli
from getbrolls.errors import UsageError
from getbrolls.sdk import loader

POSIX = os.name != "nt"
# O que o ambiente de quem roda não pode decidir por estes testes.
_DROPPED = {*profile.FIELDS.values(), "GB_PROFILE", "GB_PROFILE_SHA256", "GB_ENV_FILE"}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if POSIX:
        path.chmod(0o644)
    return path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ProfileCliCase(unittest.TestCase):
    """Um workspace temporário com getbrolls.toml, um GB_HOME próprio e a descoberta ligada."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.home = self.tmp / "gbhome"
        self.home.mkdir()
        self.toml = self.ws / profile.PROFILE_NAME

    def env(self, **extra):
        environ = {key: value for key, value in os.environ.items() if key not in _DROPPED}
        environ["GB_HOME"] = str(self.home)
        # Pastas padrão (cache, `~/.getbrolls`) caem num usuário de mentira, nunca no real.
        environ["HOME"] = environ["USERPROFILE"] = str(self.tmp / "user")
        for key, value in extra.items():
            if value is None:
                environ.pop(key, None)
            else:
                environ[key] = value
        return environ

    def run_cli(self, *args, env=None, cwd=None):
        return subprocess.run(
            cli(*args),
            cwd=cwd or self.ws,
            env=env if env is not None else self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def json_out(self, done, code=0):
        self.assertEqual(code, done.returncode, done.stderr or done.stdout)
        return json.loads(done.stdout if done.stdout.strip() else done.stderr)

    def project(self):
        """Um projeto que o `status` reconhece (já tem `brolls/`)."""
        (self.ws / "p" / "brolls").mkdir(parents=True, exist_ok=True)
        return self.ws / "p"

    def trust(self, path=None, env=None):
        path = path or self.toml
        done = self.run_cli("profile", "trust", path, "--yes", "--expect", sha(path), env=env)
        self.assertTrue(self.json_out(done)["trusted"])


class StrictCommandTests(ProfileCliCase):
    def test_untrusted_profile_blocks_a_project_command_before_touching_it(self):
        write(self.toml, 'cache_dir = "c"\n')
        (self.ws / "p").mkdir()
        done = self.run_cli("status", "--project", self.ws / "p")
        self.assertEqual(2, done.returncode, done.stderr)
        error = json.loads(done.stderr)
        self.assertEqual("USAGE_ERROR", error["error_code"])
        self.assertIn("profile trust", error["error"])
        self.assertFalse((self.ws / "p" / "brolls").exists())
        self.assertFalse((self.ws / "c").exists())

    def test_profile_trust_preview_then_confirm(self):
        write(self.toml, 'cache_dir = "c"\n')
        preview = self.json_out(self.run_cli("profile", "trust"))
        self.assertIs(False, preview["trusted"])
        self.assertEqual(sha(self.toml), preview["sha256"])
        self.assertEqual({"GB_CACHE_DIR": str(self.ws / "c")}, preview["would_set"])
        argv = split_command(preview["confirm"]["command"])
        self.assertEqual(["profile", "trust"], argv[argv.index("profile") : argv.index("profile") + 2])
        confirmed = self.json_out(self.run_cli(*argv[argv.index("profile") :]))
        self.assertIs(True, confirmed["trusted"])
        store = json.loads((self.home / profile.TRUST_FILE).read_text(encoding="utf-8"))
        self.assertEqual([sha(self.toml)], [entry["sha256"] for entry in store["profiles"].values()])
        self.assertEqual(0, self.run_cli("status", "--project", self.project()).returncode)

    def test_edited_profile_needs_trust_again_and_untrust_undoes(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.trust()
        self.assertEqual(0, self.run_cli("status", "--project", self.project()).returncode)
        write(self.toml, 'cache_dir = "outro"\n')
        done = self.run_cli("status", "--project", self.ws / "p")
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("mudou", json.loads(done.stderr)["error"])
        self.trust()
        removed = self.json_out(self.run_cli("profile", "untrust"))
        self.assertIs(True, removed["removed"])
        self.assertEqual(2, self.run_cli("status", "--project", self.ws / "p").returncode)

    def test_requires_mismatch_is_exit_4_for_commands_and_missing_in_doctor(self):
        write(self.toml, 'requires = ">=99"\n')
        self.trust()
        (self.ws / "p").mkdir()
        done = self.run_cli("status", "--project", self.ws / "p")
        self.assertEqual(4, done.returncode, done.stderr)
        self.assertEqual("PREREQUISITE_MISSING", json.loads(done.stderr)["error_code"])
        doctor = self.json_out(self.run_cli("doctor"), 4)
        entry = next(e for e in doctor["summary"]["missing"] if e["item"] == "perfil getbrolls.toml")
        self.assertIn(">=99", entry["note"])
        self.assertIs(False, doctor["install"]["profile"]["requires"]["ok"])

    def test_tolerant_commands_report_instead_of_failing(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.assertEqual(0, self.run_cli("capabilities").returncode)
        self.assertIn(self.run_cli("setup", "--check").returncode, (0, 4))
        shown = self.json_out(self.run_cli("profile", "show"))
        self.assertEqual("untrusted", shown["profile"]["trust"])
        self.assertEqual([], shown["profile"]["applied"])

    def test_setup_where_and_check_answer_under_an_untrusted_profile_but_install_stops(self):
        # Os launchers perguntam `setup --where` de qualquer pasta: um perfil não
        # confiável no caminho não pode derrubá-los. Instalar continua exigindo confiança.
        write(self.toml, 'runtime_dir = "rt"\n')
        where = self.json_out(self.run_cli("setup", "--where", "venv"))
        self.assertEqual("venv", where["part"])
        self.assertEqual("gb_home", where["source"])  # o runtime_dir não confiável não vale
        self.assertIn("in_use", where)
        self.assertIn(self.run_cli("setup", "--check").returncode, (0, 4))
        done = self.run_cli("setup")
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("profile trust", json.loads(done.stderr)["error"])
        self.assertFalse((self.ws / "rt").exists())
        self.assertFalse((self.home / "runtime").exists())


class DoctorTests(ProfileCliCase):
    def test_trusted_profile_reaches_doctor(self):
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            self.skipTest("ffprobe fora do PATH")
        write(self.toml, f"[tools]\nffprobe = {json.dumps(ffprobe)}\n")
        self.trust()
        doctor = json.loads(self.run_cli("doctor").stdout)
        self.assertEqual(str(Path(ffprobe).resolve()), doctor["tool_paths"]["GB_FFPROBE_PATH"])
        block = doctor["install"]["profile"]
        self.assertEqual("cwd", block["source"])
        self.assertEqual("trusted", block["trust"])
        self.assertEqual("profile", block["values"]["tools.ffprobe"]["source"])
        self.assertEqual(str(self.home / profile.TRUST_FILE), block["trust_file"])
        self.assertNotIn("perfil getbrolls.toml", [e["item"] for e in doctor["summary"]["missing"]])

    def test_doctor_reports_an_untrusted_profile_as_missing_and_exits_4(self):
        write(self.toml, 'cache_dir = "c"\n')
        doctor = self.json_out(self.run_cli("doctor"), 4)
        self.assertIs(False, doctor["ready"])
        self.assertEqual("untrusted", doctor["install"]["profile"]["trust"])
        entry = next(e for e in doctor["summary"]["missing"] if e["item"] == "perfil getbrolls.toml")
        self.assertIn("profile trust", entry["fix"])
        self.assertFalse((self.ws / "c").exists())

    def test_doctor_reports_an_invalid_profile_without_crashing(self):
        write(self.toml, "campo_que_nao_existe = 1\n")
        doctor = self.json_out(self.run_cli("doctor"), 4)
        block = doctor["install"]["profile"]
        self.assertEqual("invalid", block["trust"])
        self.assertIn("campo_que_nao_existe", block["error"])
        entry = next(e for e in doctor["summary"]["missing"] if e["item"] == "perfil getbrolls.toml")
        self.assertIn("profile show", entry["fix"])


class LayerOrderTests(ProfileCliCase):
    def test_profile_show_lists_every_field_with_its_source(self):
        user = self.tmp / "user"
        write(self.toml, 'home = "h"\ncache_dir = "c"\n')
        env = self.env(GB_HOME=None, HOME=str(user), USERPROFILE=str(user), GB_CACHE_DIR=str(self.tmp / "cache"))
        self.trust(env=env)
        shown = self.json_out(self.run_cli("profile", "show", env=env))
        values = shown["profile"]["values"]
        self.assertEqual({"source": "profile", "env": "GB_HOME", "value": str(self.ws / "h")}, values["home"])
        self.assertEqual("env", values["cache_dir"]["source"])
        self.assertEqual("default", values["runtime_dir"]["source"])
        self.assertEqual(set(profile.FIELDS), set(values))
        self.assertIn("source", shown["env_file"])
        self.assertEqual(str(user / ".getbrolls" / profile.TRUST_FILE), shown["profile"]["trust_file"])
        env_file = write(self.tmp / "neutro.env", "")
        flagged = self.json_out(self.run_cli("--env-file", env_file, "profile", "show", env=env))
        self.assertEqual("flag", flagged["env_file"]["source"])

    def test_profile_home_decides_which_home_env_is_read(self):
        user = self.tmp / "user"
        write(self.toml, 'home = "h"\n')
        write(self.ws / "h" / ".env", "GB_GIF_WIDTH=333\n")
        env = self.env(GB_HOME=None, HOME=str(user), USERPROFILE=str(user), GB_GIF_WIDTH=None)
        self.trust(env=env)
        doctor = json.loads(self.run_cli("doctor", env=env).stdout)
        self.assertEqual("gb_home", doctor["install"]["env_file"]["source"])
        self.assertEqual(str(self.ws / "h" / ".env"), doctor["install"]["env_file"]["path"])
        self.assertEqual(333, doctor["preview"]["width"])
        self.assertEqual(str(self.ws / "h"), doctor["install"]["gb_home"])

    def test_env_file_beats_profile(self):
        write(self.home / ".env", f"GB_CACHE_DIR={self.tmp / 'from-env'}\n")
        write(self.toml, 'cache_dir = "c"\n')
        self.trust()
        values = self.json_out(self.run_cli("profile", "show"))["profile"]["values"]
        self.assertEqual(
            {"env": "GB_CACHE_DIR", "value": str(self.tmp / "from-env"), "source": "env_file"}, values["cache_dir"]
        )

    def test_profile_flag_and_off(self):
        write(self.toml, 'cache_dir = "c"\n')
        other = write(self.tmp / "other" / "other.toml", 'cache_dir = "d"\n')
        self.trust(other)
        shown = self.json_out(self.run_cli("--profile", other, "profile", "show"))["profile"]
        self.assertEqual(("flag", "trusted"), (shown["source"], shown["trust"]))
        self.assertEqual(str(self.tmp / "other" / "d"), shown["values"]["cache_dir"]["value"])
        off = self.json_out(self.run_cli("--profile", "off", "profile", "show"))["profile"]
        self.assertEqual(("disabled", None), (off["trust"], off["path"]))
        self.assertEqual(0, self.run_cli("--profile", "off", "status", "--project", self.project()).returncode)

    def test_child_rejects_an_inherited_profile_that_changed(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.project()
        inherited = self.env(GB_PROFILE=str(self.toml), GB_PROFILE_SHA256=sha(self.toml))
        self.assertEqual(0, self.run_cli("status", "--project", self.ws / "p", env=inherited).returncode)
        write(self.toml, 'cache_dir = "outro"\n')
        done = self.run_cli("status", "--project", self.ws / "p", env=inherited)
        self.assertEqual(2, done.returncode, done.stderr)
        self.assertIn("mudou", json.loads(done.stderr)["error"])


class PluginCeilingTests(ProfileCliCase):
    def test_plugins_ceiling_reaches_the_loader(self):
        folder = self.home / "plugins" / MANIFEST["id"]
        write(folder / "getbrolls-plugin.json", json.dumps(MANIFEST))
        write(folder / "plugin.py", PLUGIN_CODE)
        pin_plugins(MANIFEST["id"], home=self.home)
        write(self.toml, "plugins = []\n")
        self.trust()
        listed = self.json_out(self.run_cli("plugins", "--action", "list"))
        self.assertEqual("GB_PLUGINS", listed["selection"])
        self.assertTrue(listed["plugins"])
        for row in listed["plugins"]:
            self.assertEqual(("disabled", loader.GB_PLUGINS_REASON), (row["status"], row["reason"]), row)

    def test_loader_hint_names_the_profile_when_the_ceiling_is_its(self):
        row = {"id": "demo", "status": "disabled", "reason": loader.GB_PLUGINS_REASON}
        with patch.dict(os.environ, {"GB_PLUGINS": "off"}), patch.dict(_paths._PROFILE_ENV, {}, clear=True):  # pylint: disable=protected-access
            self.assertIn("Inclua demo em GB_PLUGINS", loader.status_hint(row, "padrão"))
            _paths.note_profile_env({"GB_PLUGINS": "off"})
            hint = loader.status_hint(row, "padrão")
        self.assertIn("perfil getbrolls.toml", hint)
        self.assertNotIn("Inclua demo em GB_PLUGINS", hint)


class InProcessCase(ProfileCliCase):
    """`cli.main` e `load_environment` no próprio processo, com ambiente e estado isolados."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, self.env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        profile.reset_state()
        self.addCleanup(profile.reset_state)
        saved = dict(config._ENV_APPLIED)  # pylint: disable=protected-access
        self.addCleanup(lambda: (config._ENV_APPLIED.clear(), config._ENV_APPLIED.update(saved)))  # pylint: disable=protected-access
        self.addCleanup(config.load_env, self.tmp / "nao-existe.env")

    def trust_here(self, path=None):
        path = path or self.toml
        located = profile.locate(None, str(path))
        profile.trust(path, located=located, yes=True, expect=sha(path))

    def main(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.chdir(self.ws):
            return gb_cli.main(list(argv))

    @staticmethod
    def args(**values):
        return Namespace(**{"command": "status", "project": None, "env_file": None, "profile": None, **values})


class InProcessTests(InProcessCase):
    def test_runtime_dir_from_the_profile_reports_source_profile(self):
        write(self.toml, 'runtime_dir = "rt"\n')
        self.trust_here()
        doctor = self.main("--profile", str(self.toml), "doctor")
        self.assertEqual("profile", doctor["install"]["runtime"]["venv"]["source"])
        self.assertEqual(str(self.ws / "rt" / ".venv"), doctor["install"]["runtime"]["venv"]["path"])

    def test_second_load_in_execute_keeps_the_discovery_source(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.trust_here()
        doctor = self.main("doctor")
        self.assertEqual(
            ("cwd", "trusted"), (doctor["install"]["profile"]["source"], doctor["install"]["profile"]["trust"])
        )

    def test_children_inherit_the_active_profile(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.trust_here()
        with contextlib.chdir(self.ws):
            config.load_environment(self.args(), warn=False)
        self.assertEqual(str(self.toml), os.environ["GB_PROFILE"])
        self.assertEqual(sha(self.toml), os.environ["GB_PROFILE_SHA256"])
        self.assertEqual(str(self.ws / "c"), os.environ["GB_CACHE_DIR"])
        child = serve._child_environment(os.environ, None)  # pylint: disable=protected-access
        self.assertEqual(str(self.toml), child["GB_PROFILE"])
        self.assertEqual(sha(self.toml), child["GB_PROFILE_SHA256"])

    def test_no_profile_exports_off_to_children(self):
        with contextlib.chdir(self.ws):
            config.load_environment(self.args(), warn=False)
        self.assertEqual("off", os.environ["GB_PROFILE"])
        self.assertNotIn("GB_PROFILE_SHA256", os.environ)

    def test_a_new_activation_never_trusts_what_this_process_exported(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.trust_here()
        with contextlib.chdir(self.ws):
            config.load_environment(self.args(), warn=False)
            self.assertEqual(str(self.toml), os.environ["GB_PROFILE"])
            profile.untrust(self.toml, located=profile.locate(None, str(self.toml)))
            with self.assertRaises(UsageError):
                profile.activate(self.args())
        self.assertNotIn("GB_PROFILE", os.environ)
        self.assertNotIn("GB_CACHE_DIR", os.environ)

    def test_same_args_reuse_the_activation(self):
        write(self.toml, 'cache_dir = "c"\n')
        self.trust_here()
        args = self.args()
        with contextlib.chdir(self.ws):
            first = profile.activate(args)
            self.assertIs(first, profile.activate(args))
            self.assertIsNot(first, profile.activate(self.args()))

    def test_tolerance_table(self):
        self.assertTrue(profile.tolerant(self.args(command="doctor")))
        self.assertTrue(profile.tolerant(self.args(command="capabilities")))
        self.assertTrue(profile.tolerant(self.args(command="setup", check=True)))
        self.assertTrue(profile.tolerant(self.args(command="setup", check=False, where="venv")))
        self.assertFalse(profile.tolerant(self.args(command="setup", check=False, where=None)))
        self.assertFalse(profile.tolerant(self.args(command="status")))

    def test_active_ceilings_follow_the_applied_profile(self):
        write(self.toml, 'plugins = ["beta"]\nmarketplaces = ["oficial"]\n')
        self.assertIsNone(profile.plugin_ceiling())
        with contextlib.chdir(self.ws):
            profile.activate(self.args(command="doctor"))
            self.assertIsNone(profile.marketplace_ceiling())  # não confiável: nada vale
            self.trust_here()
            profile.activate(self.args())
        self.assertEqual(frozenset({"beta"}), profile.plugin_ceiling())
        self.assertEqual(frozenset({"oficial"}), profile.marketplace_ceiling())


class CapabilitiesTests(unittest.TestCase):
    def test_capabilities_lists_profile_and_the_global_flag(self):
        manifest = capabilities.describe(gb_cli.build_parser())
        rows = {row["name"]: row for row in manifest["commands"]}
        self.assertIn("profile", rows)
        self.assertIs(True, rows["profile"]["read_only"])
        self.assertFalse(rows["profile"]["requires_project"])
        self.assertEqual(gb_cli.SUMMARIES["profile"], rows["profile"]["summary"])
        flags = {flag for option in manifest["global_options"] for flag in option["flags"]}
        self.assertIn("--profile", flags)
        action = next(o for o in rows["profile"]["positionals"] if o["dest"] == "profile_action")
        self.assertEqual(["show", "trust", "untrust"], action["choices"])


if __name__ == "__main__":
    unittest.main()

"""getbrolls.toml: leitura, descoberta, confiança, `requires` e aplicação no ambiente."""

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import

from getbrolls import config, profile
from getbrolls.errors import LockedError, PrerequisiteError, UsageError

POSIX = os.name != "nt"


def write_profile(folder, text, name=profile.PROFILE_NAME):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if POSIX:
        path.chmod(0o644)
    return path


class ProfileCase(unittest.TestCase):
    """Cada teste roda num ambiente limpo, com a descoberta ligada e estado zerado."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "gbhome"
        self.home.mkdir()
        environ = {k: v for k, v in os.environ.items() if k not in {*profile.FIELDS.values(), "GB_PROFILE"}}
        environ["GB_HOME"] = str(self.home)
        patcher = patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        profile.reset_state()
        self.addCleanup(profile.reset_state)
        saved = dict(config._ENV_APPLIED)
        self.addCleanup(lambda: (config._ENV_APPLIED.clear(), config._ENV_APPLIED.update(saved)))


class ParseTests(ProfileCase):
    def test_full_profile_maps_to_gb_keys(self):
        ffmpeg = str(self.tmp / "bin" / "ffmpeg")
        path = write_profile(
            self.tmp / "ws",
            "schema_version = 1\n"
            'requires = ">=2.6"\n'
            'home = "state"\n'
            'cache_dir = "../cache"\n'
            'runtime_dir = "rt"\n'
            'plugins = ["alfa", "beta"]\n'
            f"[tools]\nffmpeg = {json.dumps(ffmpeg)}\n",
        )
        loaded = profile.load(path)
        folder = self.tmp / "ws"
        self.assertEqual(str(folder / "state"), loaded.values["GB_HOME"])
        self.assertEqual(str(self.tmp / "cache"), loaded.values["GB_CACHE_DIR"])
        self.assertEqual(str(folder / "rt"), loaded.values["GB_RUNTIME_DIR"])
        self.assertEqual("alfa,beta", loaded.values["GB_PLUGINS"])
        self.assertEqual(ffmpeg, loaded.values["GB_FFMPEG_PATH"])
        self.assertEqual(">=2.6", loaded.requires)
        self.assertEqual(path.resolve(), loaded.path)
        self.assertEqual(("alfa", "beta"), loaded.plugins)
        empty = profile.load(write_profile(self.tmp / "ws2", "plugins = []\n"))
        self.assertEqual("off", empty.values["GB_PLUGINS"])
        self.assertEqual((), empty.plugins)

    def test_absolute_dirs_are_kept_and_nothing_is_expanded(self):
        absolute = str(self.tmp / "elsewhere")
        loaded = profile.load(write_profile(self.tmp / "ws", f"home = {json.dumps(absolute)}\n"))
        self.assertEqual(absolute, loaded.values["GB_HOME"])
        with self.assertRaises(UsageError):
            profile.load(write_profile(self.tmp / "ws", 'home = "~/x"\n'))
        with self.assertRaises(UsageError):
            profile.load(write_profile(self.tmp / "ws", 'home = ""\n'))

    def test_unknown_field_is_a_usage_error_with_suggestion(self):
        with self.assertRaises(UsageError) as caught:
            profile.load(write_profile(self.tmp, 'hom = "x"\n'))
        self.assertIn("hom", str(caught.exception))
        self.assertIn("home", str(caught.exception))

    def test_unknown_tool_is_refused(self):
        with self.assertRaises(UsageError) as caught:
            profile.load(write_profile(self.tmp, '[tools]\nffmepg = "/x"\n'))
        self.assertIn("ffmpeg", str(caught.exception))

    def test_relative_or_tilde_tool_path_is_refused(self):
        for value in ("bin/ffmpeg", "~/bin/ffmpeg", ""):
            with self.subTest(value=value), self.assertRaises(UsageError) as caught:
                profile.load(write_profile(self.tmp, f"[tools]\nffmpeg = {json.dumps(value)}\n"))
            self.assertIn("tools.ffmpeg", str(caught.exception))

    def test_plugins_must_be_a_list_of_ids(self):
        for value in ('"off"', '"a"', "[1]", '["../x"]', '["ok_id", "ok_id"]'):
            with self.subTest(value=value), self.assertRaises(UsageError):
                profile.load(write_profile(self.tmp, f"plugins = {value}\n"))

    def test_marketplaces_must_be_a_list_of_names(self):
        loaded = profile.load(write_profile(self.tmp, 'marketplaces = ["oficial", "casa-2"]\n'))
        self.assertEqual(("oficial", "casa-2"), loaded.marketplaces)
        for value in ('"oficial"', "[1]", '["../x"]', '[""]'):
            with self.subTest(value=value), self.assertRaises(UsageError):
                profile.load(write_profile(self.tmp, f"marketplaces = {value}\n"))

    def test_bad_toml_is_a_usage_error(self):
        with self.assertRaises(UsageError) as caught:
            profile.load(write_profile(self.tmp, "home = \n"))
        self.assertNotIn(str(self.tmp), str(caught.exception))

    def test_tools_must_be_a_table(self):
        with self.assertRaises(UsageError):
            profile.load(write_profile(self.tmp, 'tools = "x"\n'))

    def test_schema_version_other_than_1_is_refused(self):
        for value in ("2", "0", '"1"', "true"):
            with self.subTest(value=value), self.assertRaises(UsageError):
                profile.load(write_profile(self.tmp, f"schema_version = {value}\n"))
        self.assertIsNotNone(profile.load(write_profile(self.tmp, "schema_version = 1\n")))

    def test_oversized_profile_is_refused(self):
        path = write_profile(self.tmp, "# " + "x" * profile.MAX_BYTES + "\n")
        with self.assertRaises(UsageError):
            profile.load(path)

    def test_hash_is_of_the_exact_bytes(self):
        path = write_profile(self.tmp, 'home = "x"\r\n')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), profile.load(path).sha256)

    def test_invalid_requires_is_a_usage_error(self):
        for value in ('"2.6"', '"~=2.6"', "3"):
            with self.subTest(value=value), self.assertRaises(UsageError) as caught:
                profile.load(write_profile(self.tmp, f"requires = {value}\n"))
            self.assertIn("requires", str(caught.exception))
            self.assertIn("getbrolls.toml", str(caught.exception))

    def test_symlinked_profile_is_refused(self):
        real = write_profile(self.tmp / "real", 'home = "x"\n')
        link = self.tmp / "ws" / profile.PROFILE_NAME
        link.parent.mkdir()
        try:
            link.symlink_to(real)
        except OSError:
            self.skipTest("symlinks indisponíveis")
        with self.assertRaises(UsageError) as caught:
            profile.load(link)
        self.assertIn("link simbólico", str(caught.exception))

    def test_relative_dirs_resolve_against_the_located_folder_not_the_symlink_target(self):
        real = self.tmp / "real"
        write_profile(real, 'home = "state"\n')
        alias = self.tmp / "alias"
        try:
            alias.symlink_to(real, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks indisponíveis")
        loaded = profile.load(alias / profile.PROFILE_NAME)
        self.assertEqual(str(alias / "state"), loaded.values["GB_HOME"])

    @unittest.skipUnless(POSIX, "permissões POSIX")
    def test_group_or_world_writable_profile_is_refused(self):
        for mode in (0o664, 0o646):
            path = write_profile(self.tmp, 'home = "x"\n')
            path.chmod(mode)
            with self.subTest(mode=oct(mode)), self.assertRaises(UsageError) as caught:
                profile.load(path)
            self.assertIn("gravável", str(caught.exception))

    @unittest.skipUnless(POSIX, "dono POSIX")
    def test_profile_owned_by_another_user_is_refused(self):
        path = write_profile(self.tmp, 'home = "x"\n')
        with patch("os.getuid", return_value=os.getuid() + 1), self.assertRaises(UsageError) as caught:
            profile.load(path)
        self.assertIn("dono", str(caught.exception))


class LocateTests(ProfileCase):
    def test_walks_up_from_the_project_first(self):
        path = write_profile(self.tmp / "ws", "")
        project = self.tmp / "ws" / "a" / "b"
        project.mkdir(parents=True)
        located = profile.locate(str(project), None, cwd=self.tmp)
        self.assertEqual(path, located.path)
        self.assertEqual("project", located.source)
        self.assertFalse(located.disabled)
        self.assertEqual(project / profile.PROFILE_NAME, located.searched[0])

    def test_falls_back_to_cwd(self):
        path = write_profile(self.tmp / "cwd", "")
        project = self.tmp / "proj"
        project.mkdir()
        located = profile.locate(str(project), None, cwd=self.tmp / "cwd")
        self.assertEqual(path, located.path)
        self.assertEqual("cwd", located.source)

    def test_project_profile_beats_cwd_profile(self):
        project_profile = write_profile(self.tmp / "proj", "")
        write_profile(self.tmp / "cwd", "")
        located = profile.locate(str(self.tmp / "proj"), None, cwd=self.tmp / "cwd")
        self.assertEqual(project_profile, located.path)

    def test_nothing_found_lists_what_was_searched(self):
        (self.tmp / "p").mkdir()
        located = profile.locate(str(self.tmp / "p"), None, cwd=self.tmp / "p")
        self.assertIsNone(located.path)
        self.assertIsNone(located.source)
        self.assertEqual(len(set(located.searched)), len(located.searched))
        self.assertIn(self.tmp / "p" / profile.PROFILE_NAME, located.searched)

    def test_flag_beats_env_beats_discovery(self):
        flagged = write_profile(self.tmp / "flag", "")
        from_env = write_profile(self.tmp / "env", "")
        write_profile(self.tmp / "proj", "")
        environ = {"GB_PROFILE": str(from_env)}
        both = profile.locate(str(self.tmp / "proj"), str(flagged), cwd=self.tmp, environ=environ)
        self.assertEqual((flagged, "flag"), (both.path, both.source))
        env_only = profile.locate(str(self.tmp / "proj"), None, cwd=self.tmp, environ=environ)
        self.assertEqual((from_env, "GB_PROFILE"), (env_only.path, env_only.source))

    def test_off_disables_discovery(self):
        write_profile(self.tmp / "proj", "")
        for flag, environ in (("off", {}), (None, {"GB_PROFILE": "off"}), ("OFF", {})):
            with self.subTest(flag=flag, environ=environ):
                located = profile.locate(str(self.tmp / "proj"), flag, cwd=self.tmp, environ=environ)
                self.assertTrue(located.disabled)
                self.assertIsNone(located.path)

    def test_missing_explicit_profile_is_a_usage_error(self):
        missing = str(self.tmp / "nope.toml")
        with self.assertRaises(UsageError) as caught:
            profile.locate(None, missing, cwd=self.tmp)
        self.assertIn("--profile", str(caught.exception))
        with self.assertRaises(UsageError) as caught:
            profile.locate(None, None, cwd=self.tmp, environ={"GB_PROFILE": missing})
        self.assertIn("GB_PROFILE", str(caught.exception))
        with self.assertRaises(UsageError):
            profile.locate(None, str(self.tmp), cwd=self.tmp)

    def test_missing_project_starts_at_the_nearest_existing_parent(self):
        path = write_profile(self.tmp / "ws", "")
        located = profile.locate(str(self.tmp / "ws" / "new" / "deeper"), None, cwd=self.tmp)
        self.assertEqual(path, located.path)
        self.assertEqual("project", located.source)

    def test_directory_named_like_the_profile_is_ignored(self):
        (self.tmp / "ws" / "sub" / profile.PROFILE_NAME).mkdir(parents=True)
        path = write_profile(self.tmp / "ws", "")
        located = profile.locate(str(self.tmp / "ws" / "sub"), None, cwd=self.tmp)
        self.assertEqual(path, located.path)

    def test_discovered_symlink_is_located_and_then_refused(self):
        real = write_profile(self.tmp / "real", "")
        (self.tmp / "ws").mkdir()
        try:
            (self.tmp / "ws" / profile.PROFILE_NAME).symlink_to(real)
        except OSError:
            self.skipTest("symlinks indisponíveis")
        located = profile.locate(str(self.tmp / "ws"), None, cwd=self.tmp)
        self.assertEqual(self.tmp / "ws" / profile.PROFILE_NAME, located.path)
        assert located.path is not None
        with self.assertRaises(UsageError):
            profile.load(located.path)


class TrustTests(ProfileCase):
    def _located(self, path, source="project"):
        return profile.Located(path, source, False, ())

    def _state(self, path, source="project", environ=None):
        loaded = profile.load(path)
        return profile.trust_state(loaded, self._located(path, source), self.home, environ=environ or {})

    def test_outside_home_is_untrusted_until_trusted(self):
        path = write_profile(self.tmp / "ws", 'home = "x"\n')
        self.assertEqual("untrusted", self._state(path))
        sha = profile.load(path).sha256
        profile.trust(path, located=self._located(path), yes=True, expect=sha)
        self.assertEqual("trusted", self._state(path))

    def test_trust_is_two_steps_with_expect(self):
        path = write_profile(self.tmp / "ws", 'home = "x"\n')
        store = self.home / profile.TRUST_FILE
        preview = profile.trust(path, located=self._located(path), yes=False, expect=None)
        self.assertFalse(preview["trusted"])
        sha = profile.load(path).sha256
        self.assertEqual(sha, preview["sha256"])
        self.assertIn("GB_HOME", preview["would_set"])
        self.assertIn(f"--yes --expect {sha}", preview["confirm"]["command"])
        self.assertFalse(store.exists())
        with self.assertRaises(UsageError):
            profile.trust(path, located=self._located(path), yes=True, expect=None)
        with self.assertRaises(UsageError) as caught:
            profile.trust(path, located=self._located(path), yes=True, expect="0" * 64)
        self.assertIn("mudou", str(caught.exception))
        self.assertFalse(store.exists())
        done = profile.trust(path, located=self._located(path), yes=True, expect=sha)
        self.assertTrue(done["trusted"])
        data = json.loads(store.read_text(encoding="utf-8"))
        self.assertEqual(1, data["schema_version"])
        entry = data["profiles"][profile._key(path)]
        self.assertEqual(sha, entry["sha256"])
        self.assertIn("trusted_at", entry)
        if POSIX:
            self.assertEqual(0o600, stat.S_IMODE(store.stat().st_mode))

    def test_trust_without_a_profile_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            profile.trust(None, located=profile.Located(None, None, False, ()), yes=False, expect=None)

    def test_changed_profile_is_untrusted_again(self):
        path = write_profile(self.tmp / "ws", 'home = "x"\n')
        profile.trust(path, located=self._located(path), yes=True, expect=profile.load(path).sha256)
        write_profile(self.tmp / "ws", 'home = "y"\n')
        self.assertEqual("changed", self._state(path))

    def test_profile_inside_the_pre_profile_home_is_trusted(self):
        path = write_profile(self.home, 'cache_dir = "c"\n')
        self.assertEqual("inside_home", self._state(path))

    def test_only_the_exact_home_profile_is_trusted_without_a_store_entry(self):
        nested = write_profile(self.home / "sub", 'cache_dir = "c"\n')
        self.assertEqual("untrusted", self._state(nested))
        other_name = write_profile(self.home, 'cache_dir = "c"\n', name="other.toml")
        self.assertEqual("untrusted", self._state(other_name))

    def test_symlink_into_the_home_is_refused_before_trust(self):
        real = write_profile(self.tmp / "outside", 'home = "x"\n')
        link = self.home / profile.PROFILE_NAME
        try:
            link.symlink_to(real)
        except OSError:
            self.skipTest("symlinks indisponíveis")
        with self.assertRaises(UsageError):
            profile.load(link)

    def test_gb_profile_from_the_process_is_trusted(self):
        path = write_profile(self.tmp / "ws", 'home = "x"\n')
        self.assertEqual("env", self._state(path, "GB_PROFILE"))
        sha = profile.load(path).sha256
        self.assertEqual("env", self._state(path, "GB_PROFILE", {"GB_PROFILE_SHA256": sha}))
        self.assertEqual("changed", self._state(path, "GB_PROFILE", {"GB_PROFILE_SHA256": "0" * 64}))

    def test_store_is_read_from_the_pre_profile_home_not_the_profile_home(self):
        path = write_profile(self.tmp / "ws", 'home = "evil"\n')
        loaded = profile.load(path)
        evil = self.tmp / "ws" / "evil"
        evil.mkdir()
        forged = {"schema_version": 1, "profiles": {profile._key(path): {"sha256": loaded.sha256, "trusted_at": "x"}}}
        (evil / profile.TRUST_FILE).write_text(json.dumps(forged), encoding="utf-8")
        user = self.tmp / "user"
        del os.environ["GB_HOME"]
        with patch("pathlib.Path.home", return_value=user):
            applied = profile.apply_home(loaded)
            self.assertEqual(str(evil), applied["GB_HOME"])
            self.assertEqual(str(evil), os.environ["GB_HOME"])
            self.assertEqual(user / ".getbrolls", profile.trust_home())
            state = profile.trust_state(loaded, self._located(path), profile.trust_home(), environ={})
        self.assertEqual("untrusted", state)
        self.assertEqual("trusted", profile.trust_state(loaded, self._located(path), evil, environ={}))

    def test_untrust_removes_the_entry(self):
        path = write_profile(self.tmp / "ws", 'home = "x"\n')
        profile.trust(path, located=self._located(path), yes=True, expect=profile.load(path).sha256)
        result = profile.untrust(path, located=self._located(path))
        self.assertTrue(result["removed"])
        self.assertEqual("untrusted", self._state(path))
        self.assertFalse(profile.untrust(path, located=self._located(path))["removed"])

    def test_invalid_store_is_a_usage_error_without_the_path(self):
        (self.home / profile.TRUST_FILE).write_text("{not json", encoding="utf-8")
        with self.assertRaises(UsageError) as caught:
            profile.read_store(self.home)
        self.assertIn(profile.TRUST_FILE, str(caught.exception))
        self.assertNotIn(str(self.home), str(caught.exception))
        (self.home / profile.TRUST_FILE).write_text('{"schema_version": 1, "profiles": []}', encoding="utf-8")
        with self.assertRaises(UsageError):
            profile.read_store(self.home)

    def test_missing_store_is_empty(self):
        self.assertEqual({"schema_version": 1, "profiles": {}}, profile.read_store(self.home))

    def test_concurrent_writer_holding_the_lock_blocks_until_timeout(self):
        path = write_profile(self.tmp / "ws", 'home = "x"\n')
        sha = profile.load(path).sha256
        with (
            patch.object(profile, "LOCK_TIMEOUT_S", 0.05),
            patch("getbrolls.runtime._acquire_lock", side_effect=BlockingIOError),
            self.assertRaises(LockedError) as caught,
        ):
            profile.trust(path, located=self._located(path), yes=True, expect=sha)
        self.assertIn("trusted-profiles.json", str(caught.exception))

    @unittest.skipUnless(os.name == "nt", "normcase só muda o caminho no Windows")
    def test_trust_key_is_case_normalized_on_windows(self):
        path = write_profile(self.tmp / "WS", 'home = "x"\n')
        self.assertEqual(profile._key(path), profile._key(Path(str(path).lower())))


class RequiresTests(ProfileCase):
    def test_matching_requires_has_no_problem(self):
        loaded = profile.load(write_profile(self.tmp, 'requires = ">=2.6,<3"\n'))
        self.assertIsNone(profile.requires_problem(loaded, "2.6.0"))
        self.assertIsNone(profile.requires_problem(loaded, "2.6.0rc1"))
        self.assertIsNone(profile.requires_problem(profile.load(write_profile(self.tmp, "")), "2.6.0"))

    def test_mismatch_is_reported_with_both_versions(self):
        loaded = profile.load(write_profile(self.tmp, 'requires = "==2.6.0"\n'))
        problem = profile.requires_problem(loaded, "2.7.1")
        assert problem is not None
        self.assertIn("==2.6.0", problem)
        self.assertIn("2.7.1", problem)
        self.assertIn("getbrolls.toml", problem)
        with self.assertRaises(PrerequisiteError):
            profile.check_requires(loaded, "2.7.1")
        profile.check_requires(loaded, "2.6.0")

    def test_short_equality_only_matches_the_zero_patch(self):
        loaded = profile.load(write_profile(self.tmp, 'requires = "==2.6"\n'))
        self.assertIsNone(profile.requires_problem(loaded, "2.6.0"))
        self.assertIsNotNone(profile.requires_problem(loaded, "2.6.1"))

    def test_installed_version_counts_only_its_numeric_prefix(self):
        loaded = profile.load(write_profile(self.tmp, 'requires = "==2.6.0"\n'))
        self.assertIsNone(profile.requires_problem(loaded, "2.6.0rc1"))
        self.assertIsNone(profile.requires_problem(loaded, " 2.6.0.dev4+g1"))
        self.assertIsNotNone(profile.requires_problem(loaded, "2.6.1rc1"))


class ApplyTests(ProfileCase):
    def _load(self, text):
        return profile.load(write_profile(self.tmp / "ws", text))

    def test_env_beats_profile(self):
        loaded = self._load('home = "h"\ncache_dir = "c"\n')
        environ = {"GB_HOME": "/from/env", "GB_CACHE_DIR": "/cache/env"}
        self.assertEqual({}, profile.apply_home(loaded, environ))
        self.assertEqual({}, profile.apply_rest(loaded, environ))
        self.assertEqual({"GB_HOME": "/from/env", "GB_CACHE_DIR": "/cache/env"}, environ)

    def test_empty_env_counts_as_unset(self):
        loaded = self._load('cache_dir = "c"\n')
        environ = {"GB_CACHE_DIR": ""}
        profile.apply_rest(loaded, environ)
        self.assertEqual(str(self.tmp / "ws" / "c"), environ["GB_CACHE_DIR"])

    def test_apply_home_only_touches_gb_home(self):
        loaded = self._load('home = "h"\ncache_dir = "c"\n')
        environ: dict[str, str] = {}
        self.assertEqual({"GB_HOME": str(self.tmp / "ws" / "h")}, profile.apply_home(loaded, environ))
        self.assertEqual(["GB_HOME"], list(environ))
        self.assertNotIn("GB_HOME", profile.apply_rest(loaded, environ))

    def test_profile_fills_unset_keys_and_registers_them(self):
        ytdlp = str(self.tmp / "bin" / "yt-dlp")
        loaded = self._load(f'runtime_dir = "rt"\n[tools]\nytdlp = {json.dumps(ytdlp)}\n')
        environ: dict[str, str] = {}
        applied = profile.apply_rest(loaded, environ)
        expected = {"GB_RUNTIME_DIR": str(self.tmp / "ws" / "rt"), "GB_YTDLP_PATH": ytdlp}
        self.assertEqual(expected, applied)
        self.assertEqual(expected, environ)
        self.assertEqual(expected, profile.from_profile())

    def test_plugins_from_a_profile_are_a_ceiling(self):
        ceiling = self._load('plugins = ["beta", "gama"]\n')
        cases = (
            ({"GB_PLUGINS": "alfa,beta"}, ceiling, "beta"),
            ({"GB_PLUGINS": "alfa, beta"}, ceiling, "beta"),
            ({"GB_PLUGINS": "off"}, ceiling, "off"),
            ({"GB_PLUGINS": "alfa"}, ceiling, "off"),
            ({}, ceiling, "beta,gama"),
        )
        for environ, loaded, expected in cases:
            with self.subTest(environ=environ):
                profile.apply_rest(loaded, environ)
                self.assertEqual(expected, environ["GB_PLUGINS"])
        none = profile.load(write_profile(self.tmp / "none", "plugins = []\n"))
        environ = {"GB_PLUGINS": "alfa"}
        profile.apply_rest(none, environ)
        self.assertEqual("off", environ["GB_PLUGINS"])

    def test_apply_is_idempotent(self):
        loaded = self._load('home = "h"\ncache_dir = "c"\nplugins = ["beta", "gama"]\n')
        environ = {"GB_PLUGINS": "alfa,beta,gama"}
        profile.apply_home(loaded, environ)
        profile.apply_rest(loaded, environ)
        first = dict(environ)
        registered = profile.from_profile()
        profile.apply_home(loaded, environ)
        profile.apply_rest(loaded, environ)
        self.assertEqual(first, environ)
        self.assertEqual(registered, profile.from_profile())
        self.assertEqual("beta,gama", environ["GB_PLUGINS"])

    def test_ceilings_are_pure_functions_of_the_profile(self):
        loaded = self._load('plugins = ["beta"]\nmarketplaces = ["oficial"]\n')
        self.assertEqual(frozenset({"beta"}), profile.plugin_ceiling(loaded))
        self.assertEqual(frozenset({"oficial"}), profile.marketplace_ceiling(loaded))
        bare = profile.load(write_profile(self.tmp / "bare", ""))
        self.assertIsNone(profile.plugin_ceiling(bare))
        self.assertIsNone(profile.marketplace_ceiling(bare))
        self.assertIsNone(profile.plugin_ceiling(None))
        self.assertIsNone(profile.marketplace_ceiling(None))
        empty = profile.load(write_profile(self.tmp / "empty", "plugins = []\nmarketplaces = []\n"))
        self.assertEqual(frozenset(), profile.plugin_ceiling(empty))
        self.assertEqual(frozenset(), profile.marketplace_ceiling(empty))

    def test_value_sources_name_profile_env_env_file_and_default(self):
        loaded = self._load('cache_dir = "c"\n')
        environ = {"GB_FFMPEG_PATH": "/usr/bin/ffmpeg", "GB_RUNTIME_DIR": "/rt"}
        config._ENV_APPLIED["GB_RUNTIME_DIR"] = "/rt"
        applied = profile.apply_rest(loaded, environ)
        active = profile.Active(
            located=profile.Located(loaded.path, "project", False, ()),
            profile=loaded,
            trust="trusted",
            error=None,
            requires_problem=None,
            pre_home=self.home,
            applied=applied,
        )
        sources = profile.value_sources(active, environ)
        self.assertEqual(set(profile.FIELDS), set(sources))
        self.assertEqual("profile", sources["cache_dir"]["source"])
        self.assertEqual("GB_CACHE_DIR", sources["cache_dir"]["env"])
        self.assertEqual("env", sources["tools.ffmpeg"]["source"])
        self.assertEqual("/usr/bin/ffmpeg", sources["tools.ffmpeg"]["value"])
        self.assertEqual("env_file", sources["runtime_dir"]["source"])
        self.assertEqual("default", sources["tools.venv"]["source"])
        self.assertIsNone(sources["tools.venv"]["value"])
        self.assertEqual("default", profile.value_sources(None, {})["cache_dir"]["source"])


if __name__ == "__main__":
    unittest.main()

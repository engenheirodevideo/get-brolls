"""getbrolls.toml depois dos gates: avisos da prévia de confiança e o `GB_HOME` do perfil.

Um `home`/`runtime_dir`/`cache_dir` dentro da pasta do toml fica gravável por quem grava
o repositório; o `.env` e o `plugins.json` desse `GB_HOME` não entram no sha do perfil.
Por isso a prévia avisa e, com o `GB_HOME` vindo do perfil, esses dois arquivos passam
pelas mesmas recusas do próprio toml (link, outro dono, gravável por grupo/outros).
"""

import json
import os
import unittest
import unittest.mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from test_profile_cli import ProfileCliCase, write

from getbrolls import profile
from getbrolls.sdk import marketplace

POSIX = os.name != "nt"


class TrustPreviewWarningTests(ProfileCliCase):
    def test_dirs_inside_the_profile_folder_are_warned(self):
        write(self.toml, 'home = "state"\nruntime_dir = "rt"\ncache_dir = "../outside"\n')
        preview = self.json_out(self.run_cli("profile", "trust"))
        warnings = "\n".join(preview["warnings"])
        self.assertIn("`home`", warnings)
        self.assertIn("`runtime_dir`", warnings)
        self.assertNotIn("`cache_dir`", warnings)
        self.assertIn(".env", warnings)

    def test_absolute_dirs_outside_the_folder_are_not_warned(self):
        write(self.toml, f'home = "{(self.tmp / "elsewhere").as_posix()}"\n')
        preview = self.json_out(self.run_cli("profile", "trust"))
        self.assertEqual([], preview["warnings"])

    def test_unknown_plugin_ids_are_warned(self):
        (self.home / "plugins" / "conhecido").mkdir(parents=True)
        write(self.toml, 'plugins = ["conhecido", "fantasma"]\n')
        preview = self.json_out(self.run_cli("profile", "trust"))
        warnings = "\n".join(preview["warnings"])
        self.assertIn("fantasma", warnings)
        self.assertNotIn("conhecido", warnings)


@unittest.skipUnless(POSIX, "dono e permissões de grupo/outros são do POSIX")
class ProfileHomeGuardTests(ProfileCliCase):
    def trusted_home(self):
        write(self.toml, 'home = "h"\n')
        env = self.env(GB_HOME=None, GB_GIF_WIDTH=None)
        self.trust(env=env)
        return self.ws / "h", env

    def test_world_writable_env_in_the_profile_home_is_refused(self):
        home, env = self.trusted_home()
        dotenv = write(home / ".env", "GB_GIF_WIDTH=333\n")
        dotenv.chmod(0o666)
        done = self.run_cli("status", "--project", self.project(), env=env)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertIn("gravável por outros", done.stderr)

    def test_symlinked_env_in_the_profile_home_is_refused(self):
        home, env = self.trusted_home()
        real = write(self.tmp / "real.env", "GB_GIF_WIDTH=333\n")
        home.mkdir(parents=True, exist_ok=True)
        (home / ".env").symlink_to(real)
        done = self.run_cli("status", "--project", self.project(), env=env)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertIn("link", done.stderr)

    def test_world_writable_plugins_json_in_the_profile_home_is_refused(self):
        home, env = self.trusted_home()
        state = write(home / "plugins.json", '{"enabled": {}}\n')
        state.chmod(0o666)
        done = self.run_cli("plugins", "--action", "list", env=env)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertIn("plugins.json", done.stderr)

    def test_symlinked_marketplaces_in_the_profile_home_is_refused(self):
        home, env = self.trusted_home()
        (self.tmp / "fora").mkdir()
        home.mkdir(parents=True, exist_ok=True)
        (home / "marketplaces").symlink_to(self.tmp / "fora", target_is_directory=True)
        done = self.run_cli("plugins", "--action", "marketplace-list", env=env)
        self.assertEqual(2, done.returncode, done.stdout + done.stderr)
        self.assertIn("marketplaces", done.stderr)
        self.assertIn("link", done.stderr)

    def test_marketplace_remove_refuses_a_linked_cache_folder(self):
        (self.tmp / "fora" / "exemplo").mkdir(parents=True)
        (self.home / "marketplaces").symlink_to(self.tmp / "fora", target_is_directory=True)
        with unittest.mock.patch.dict(os.environ, {"GB_HOME": str(self.home)}), self.assertRaises(ValueError) as raised:
            marketplace.remove("exemplo")
        self.assertIn("link", str(raised.exception))
        self.assertTrue((self.tmp / "fora" / "exemplo").is_dir(), "nada fora de $GB_HOME é apagado")

    def test_a_private_env_in_the_profile_home_still_works(self):
        home, env = self.trusted_home()
        write(home / ".env", "GB_GIF_WIDTH=333\n")
        done = self.run_cli("doctor", env=env)
        self.assertIn(done.returncode, (0, 4), done.stderr)
        self.assertEqual(333, json.loads(done.stdout)["preview"]["width"])

    def test_the_guard_is_only_for_a_home_from_the_profile(self):
        dotenv = write(self.home / ".env", "GB_GIF_WIDTH=333\n")
        dotenv.chmod(0o666)
        done = self.run_cli("--profile", "off", "status", "--project", self.project())
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertFalse(profile.PROFILE_NAME in done.stderr)


if __name__ == "__main__":
    unittest.main()

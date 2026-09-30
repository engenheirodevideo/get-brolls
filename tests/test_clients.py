"""Pastas de cliente: `client.json` na pasta, registro em `$GB_HOME/clients.json` e o comando `client`."""

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import ROOT

from getbrolls import _paths, capabilities, clients, runtime
from getbrolls.cli import build_parser
from getbrolls.sdk import jsonschema

FOLDERS = ("marca", "lettering", "sfx", "musica", "imagem", "composicoes", "outros")


def _schema(name):
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


def _closed_top(schema):
    """O schema publicado com o topo fechado: o core grava só o que ele lista."""
    closed = copy.deepcopy(schema)
    closed["additionalProperties"] = False
    return closed


def _can_symlink(folder):
    probe = Path(folder) / "probe-link"
    try:
        probe.symlink_to(folder, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


class ClientsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-clients-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "gb-home"
        env = patch.dict(os.environ, {"GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.base = self.tmp / "Clientes"
        self.base.mkdir()

    def registry(self):
        return json.loads((self.home / "clients.json").read_text(encoding="utf-8"))


class AddTests(ClientsTestCase):
    def test_add_creates_the_tree_and_registers_it(self):
        result = clients.add("acme", str(self.base), name="ACME Ltda")
        folder = self.base / "acme"
        self.assertTrue(result["created"])
        self.assertEqual("acme", result["client"]["slug"])
        self.assertEqual("ACME Ltda", result["client"]["name"])
        for name in FOLDERS:
            self.assertTrue((folder / "components" / name).is_dir(), name)
        self.assertTrue((folder / "templates").is_dir())
        data = json.loads((folder / "client.json").read_text(encoding="utf-8"))
        self.assertEqual("getbrolls.client/1", data["schema"])
        self.assertEqual(("acme", "ACME Ltda"), (data["slug"], data["name"]))
        registry = self.registry()
        self.assertEqual("getbrolls.clients/1", registry["schema"])
        self.assertEqual([("acme", str(folder))], [(row["slug"], row["root"]) for row in registry["clients"]])
        self.assertEqual(folder, clients.client_root("acme"))
        self.assertEqual("ACME Ltda", clients.load_client("acme")["name"])
        self.assertEqual(clients.registry_path(), _paths.gb_home() / "clients.json")
        self.assertEqual([], [p.name for p in self.home.iterdir() if p.name.endswith(".tmp")])

    def test_component_folders_are_the_asset_folders(self):
        self.assertEqual(FOLDERS, clients.COMPONENT_FOLDERS)

    def test_written_files_match_the_published_schemas(self):
        clients.add("acme", str(self.base))
        client_json = json.loads((self.base / "acme" / "client.json").read_text(encoding="utf-8"))
        self.assertEqual([], jsonschema.errors(client_json, _closed_top(_schema("client"))))
        self.assertEqual([], jsonschema.errors(self.registry(), _closed_top(_schema("clients"))))
        self.assertEqual("acme", client_json["name"])  # sem --name, o nome é o slug

    def test_duplicate_slug_is_refused(self):
        clients.add("acme", str(self.base))
        other = self.tmp / "Outra"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "já está registrado"):
            clients.add("acme", str(other))
        self.assertFalse((other / "acme").exists())

    def test_existing_client_folder_is_registered_again(self):
        clients.add("acme", str(self.base), name="ACME")
        (self.base / "acme" / "components" / "marca" / "logo.png").write_bytes(b"x")
        clients.remove("acme")
        result = clients.add("acme", str(self.base), name="ignorado")
        self.assertFalse(result["created"])
        self.assertEqual("ACME", result["client"]["name"])
        self.assertTrue((self.base / "acme" / "components" / "marca" / "logo.png").is_file())

    def test_existing_client_json_with_another_slug_is_refused(self):
        clients.add("acme", str(self.base))
        clients.remove("acme")
        (self.base / "outro").mkdir()
        shutil.copy(self.base / "acme" / "client.json", self.base / "outro" / "client.json")
        with self.assertRaisesRegex(ValueError, "acme"):
            clients.add("outro", str(self.base))
        self.assertEqual([], self.registry()["clients"])

    def test_bad_slug_or_name_is_refused(self):
        for slug in ("ACME", "a b", "../x", "a" * 65, "", "-a"):
            with self.subTest(slug=slug), self.assertRaisesRegex(ValueError, "slug"):
                clients.add(slug, str(self.base))
        for name in ("", "  ", "linha\noutra", "x" * 121):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "--name"):
                clients.add("acme", str(self.base), name=name)
        self.assertEqual([], list(self.base.iterdir()))


class RootSafetyTests(ClientsTestCase):
    def assert_refused(self, root, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            clients.add("acme", str(root))
        self.assertFalse((self.home / "clients.json").exists())

    def test_home_and_disk_root_are_refused(self):
        self.assert_refused(Path.home(), "ampla demais")
        self.assert_refused(Path(Path.home().anchor), "ampla demais")

    def test_relative_or_tilde_root_is_refused(self):
        self.assert_refused("~", "absoluto")
        self.assert_refused("Clientes", "absoluto")

    def test_missing_root_or_file_is_refused(self):
        self.assert_refused(self.tmp / "nao-existe", "pasta existente")
        file = self.tmp / "arquivo"
        file.write_text("x", encoding="utf-8")
        self.assert_refused(file, "pasta existente")

    def test_link_as_root_is_refused(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        link = self.tmp / "atalho"
        link.symlink_to(self.base, target_is_directory=True)
        self.assert_refused(link, "link")
        self.assertFalse((self.base / "acme").exists())

    def test_client_folder_that_is_a_link_or_a_file_is_refused(self):
        if _can_symlink(self.tmp):
            elsewhere = self.tmp / "fora"
            elsewhere.mkdir()
            (self.base / "acme").symlink_to(elsewhere, target_is_directory=True)
            self.assert_refused(self.base, "link")
            self.assertEqual([], list(elsewhere.iterdir()))
            (self.base / "acme").unlink()
        (self.base / "acme").write_text("x", encoding="utf-8")
        self.assert_refused(self.base, "não é uma pasta")

    def test_linked_subfolder_inside_the_client_is_refused(self):
        if not _can_symlink(self.tmp):
            self.skipTest("sem permissão para criar link simbólico")
        elsewhere = self.tmp / "fora"
        elsewhere.mkdir()
        (self.base / "acme").mkdir()
        (self.base / "acme" / "components").symlink_to(elsewhere, target_is_directory=True)
        self.assert_refused(self.base, "link")
        self.assertEqual([], list(elsewhere.iterdir()))

    def test_installation_and_plugin_folders_are_refused(self):
        self.assert_refused(ROOT, "instalação do get-brolls")
        (self.home / "plugins").mkdir(parents=True)
        self.assert_refused(self.home / "plugins", "instalação do get-brolls")
        self.assertEqual([], list((self.home / "plugins").iterdir()))

    def test_folder_containing_gb_home_is_refused(self):
        inside = str(self.base / "acme" / "home")
        with patch.dict(os.environ, {"GB_HOME": inside}), self.assertRaisesRegex(ValueError, "GB_HOME"):
            clients.add("acme", str(self.base))
        self.assertFalse((self.base / "acme").exists())


class RegistryTests(ClientsTestCase):
    def write_registry(self, text):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "clients.json").write_text(text, encoding="utf-8")

    def test_absent_registry_is_empty(self):
        self.assertEqual({"schema": "getbrolls.clients/1", "clients": []}, clients.load_registry())
        self.assertIsNone(clients.client_root("acme"))
        self.assertFalse(self.home.exists())

    def test_remove_keeps_the_files(self):
        clients.add("acme", str(self.base))
        result = clients.remove("acme")
        self.assertEqual("acme", result["removed"]["slug"])
        self.assertTrue(result["files_kept"])
        self.assertTrue((self.base / "acme" / "client.json").is_file())
        self.assertIsNone(clients.client_root("acme"))
        with self.assertRaisesRegex(ValueError, "não está registrado"):
            clients.remove("acme")

    def test_corrupted_registry_is_a_clear_error(self):
        for text in ("{", "[]", '{"schema": "getbrolls.clients/1", "clients": [{"slug": "ACME"}]}'):
            with self.subTest(text=text):
                self.write_registry(text)
                with self.assertRaisesRegex(ValueError, "clients.json inválido"):
                    clients.load_registry()

    def test_newer_registry_is_refused(self):
        self.write_registry('{"schema": "getbrolls.clients/2", "clients": []}')
        with self.assertRaisesRegex(ValueError, "versão mais nova"):
            clients.load_registry()
        with self.assertRaisesRegex(ValueError, "versão mais nova"):
            clients.add("acme", str(self.base))
        self.assertFalse((self.base / "acme").exists())

    def test_registry_without_schema_reads_as_version_one(self):
        self.write_registry('{"clients": []}')
        self.assertEqual([], clients.load_registry()["clients"])

    def test_wrong_family_is_invalid(self):
        self.write_registry('{"schema": "getbrolls.client/1", "clients": []}')
        with self.assertRaisesRegex(ValueError, "clients.json inválido"):
            clients.load_registry()

    def test_newer_client_json_is_refused(self):
        clients.add("acme", str(self.base))
        path = self.base / "acme" / "client.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "schema": "getbrolls.client/2"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "versão mais nova"):
            clients.load_client("acme")

    def test_vanished_or_linked_client_folder(self):
        clients.add("acme", str(self.base))
        shutil.rmtree(self.base / "acme")
        with self.assertRaisesRegex(ValueError, "não existe mais"):
            clients.load_client("acme")
        listed = clients.list_clients()["clients"][0]
        self.assertIn("não existe mais", listed["problem"])
        if _can_symlink(self.tmp):
            elsewhere = self.tmp / "fora"
            elsewhere.mkdir()
            (self.base / "acme").symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "link"):
                clients.load_client("acme")

    def test_busy_lock_is_a_clear_error(self):
        self.home.mkdir(parents=True)
        held = runtime.exclusive_lock(self.home / ".clients.lock", "ocupado")
        with patch.object(clients, "LOCK_WAIT_S", 0), held, self.assertRaisesRegex(ValueError, "Outro comando"):
            clients.add("acme", str(self.base))

    def test_list_and_show_scrub_the_home_folder(self):
        with patch("pathlib.Path.home", return_value=self.tmp):
            clients.add("acme", str(self.base))
            listed = clients.list_clients()
            shown = clients.show("acme")
        text = json.dumps([listed, shown], ensure_ascii=False)
        self.assertNotIn(str(self.tmp), text)
        self.assertEqual("~/Clientes/acme".replace("/", os.sep), listed["clients"][0]["root"])
        self.assertIsNone(listed["clients"][0]["problem"])
        self.assertEqual("acme", shown["client"]["slug"])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gb-clients-cli-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {"GB_HOME": str(self.tmp / "home")}
        (self.tmp / "Clientes").mkdir()

    def test_add_list_show_remove(self):
        root = str(self.tmp / "Clientes")
        added = run_cli("client", "--action", "add", "--slug", "acme", "--root", root, "--name", "ACME", env=self.env)
        self.assertTrue(added["created"])
        listed = run_cli("client", "--action", "list", env=self.env)
        self.assertEqual(["acme"], [row["slug"] for row in listed["clients"]])
        shown = run_cli("client", "--action", "show", "--slug", "acme", env=self.env)
        self.assertEqual("ACME", shown["client"]["name"])
        removed = run_cli("client", "--action", "remove", "--slug", "acme", env=self.env)
        self.assertTrue(removed["files_kept"])
        self.assertEqual([], run_cli("client", "--action", "list", env=self.env)["clients"])

    def test_missing_flag_is_a_usage_error(self):
        error = run_cli("client", "--action", "add", "--slug", "acme", expect=2, env=self.env)
        self.assertEqual("USAGE_ERROR", error["error_code"])
        self.assertIn("--root", error["message"])
        self.assertNotIn("hint", error)

    def test_corrupted_registry_exits_one(self):
        (self.tmp / "home").mkdir()
        (self.tmp / "home" / "clients.json").write_text("{", encoding="utf-8")
        error = run_cli("client", "--action", "list", expect=1, env=self.env)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("clients.json inválido", error["message"])

    def test_capabilities_marks_list_and_show_read_only(self):
        command = next(row for row in capabilities.describe(build_parser())["commands"] if row["name"] == "client")
        self.assertEqual("by_action", command["read_only"])
        self.assertEqual(["list", "show"], command["read_only_actions"])
        self.assertFalse(command["requires_project"])
        action = next(o for o in command["options"] if o["dest"] == "action")
        self.assertEqual(["add", "list", "remove", "show"], action["choices"])


if __name__ == "__main__":
    unittest.main()

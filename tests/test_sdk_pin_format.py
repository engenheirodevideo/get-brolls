"""O hash da pasta amarra o conteúdo: cada arquivo entra com o tamanho do nome e o sha256 dele.

O formato antigo (`caminho\\0conteúdo\\0` por arquivo, sem tamanho) deixava duas árvores diferentes com o
mesmo hash. Um pin sem a chave `digest` é desse formato: o plugin fica `suspended` até um novo `enable`.
"""

import hashlib
import json
import re

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_sdk_loader import MANIFEST, LoaderTestCase

from getbrolls.errors import UsageError
from getbrolls.sdk import loader
from getbrolls.sdk.files import counted_files
from getbrolls.sdk.registry import get_registry, reset_registry

HEX64 = re.compile(r"[0-9a-f]{64}")


def legacy_digest(folder):
    """O sha256 que um `plugins.json` de antes da 2.6.0 guarda: `caminho\\0conteúdo\\0` por arquivo."""
    digest = hashlib.sha256()
    for rel, path in counted_files(folder):
        digest.update(rel.as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def old_entry(folder, manifest=None):
    """Uma entrada de pin como a 2.5 gravava: sem a chave `digest`, com o sha256 antigo."""
    manifest = MANIFEST if manifest is None else manifest
    return {
        "version": manifest["version"],
        "sha256": legacy_digest(folder),
        "permissions": {"network": manifest["permissions"]["network"], "env": manifest["permissions"]["env"]},
        "files": loader.file_digests(folder),
    }


class FramedDigestTests(LoaderTestCase):
    def tree(self, name, files):
        folder = self.home / name
        folder.mkdir()
        for rel, data in files.items():
            (folder / rel).write_bytes(data)
        return folder

    def test_trees_that_shared_the_old_digest_now_differ(self):
        one = self.tree("a", {"d.txt": b"AAA", "e.txt": b"BBB"})
        other = self.tree("b", {"d.txt": b"AAA\0e.txt\0BBB"})
        self.assertEqual(legacy_digest(one), legacy_digest(other))
        self.assertNotEqual(loader.folder_digest(one), loader.folder_digest(other))

    def test_moving_bytes_between_name_and_content_changes_the_digest(self):
        one = self.tree("a", {"ab": b"c"})
        other = self.tree("b", {"a": b"bc"})
        self.assertNotEqual(loader.folder_digest(one), loader.folder_digest(other))

    def test_digest_stays_64_hex_and_folder_and_pin_digests_agree(self):
        folder = self.install()
        whole, files = loader.pin_digests(folder)
        self.assertRegex(whole, HEX64)
        self.assertEqual(loader.folder_digest(folder), whole)
        self.assertEqual(loader.file_digests(folder), files)
        self.assertNotEqual(legacy_digest(folder), whole)

    def test_enable_records_the_digest_format(self):
        self.install()
        loader.enable("demo", confirm=True)
        entry = json.loads(loader.state_path().read_text(encoding="utf-8"))["enabled"]["demo"]
        self.assertEqual(loader.DIGEST_FORMAT, entry["digest"])
        self.assertEqual("enabled", loader.inventory()[0]["status"])


class OldFormatPinTests(LoaderTestCase):
    def write_state(self, **sections):
        loader.state_path().write_text(json.dumps({"enabled": {}, **sections}), encoding="utf-8")

    def test_old_format_pin_is_suspended_with_the_reason_and_the_fix(self):
        folder = self.install()
        self.write_state(enabled={"demo": old_entry(folder)})
        row = loader.inventory()[0]
        self.assertEqual("suspended", row["status"])
        self.assertIn("formato", row["reason"])
        self.assertIn("plugins --action enable --id demo", row["reason"])
        self.assertNotIn("demo", get_registry().provider_names())

    def test_pin_without_the_format_key_is_old_even_when_the_sha_matches(self):
        folder = self.install()
        self.write_state(enabled={"demo": {"version": "0.1.0", "sha256": loader.folder_digest(folder)}})
        self.assertEqual("suspended", loader.inventory()[0]["status"])
        self.assertNotIn("demo", get_registry().provider_names())

    def test_re_enable_says_why_the_file_diff_is_empty_and_needs_expect(self):
        folder = self.install()
        self.write_state(enabled={"demo": old_entry(folder)})
        preview = loader.enable("demo", confirm=False)
        self.assertEqual({"added": [], "removed": [], "changed": []}, preview["diff"]["files"])
        self.assertIn("formato", preview["diff"]["note"])
        self.assertIn("--expect", preview["note"])
        with self.assertRaises(UsageError):
            loader.enable("demo", confirm=True)
        done = loader.enable("demo", confirm=True, expect=preview["plugin"]["sha256"])
        self.assertTrue(done["enabled"])
        reset_registry()
        self.assertEqual("enabled", loader.inventory()[0]["status"])
        self.assertIn("demo", get_registry().provider_names())

    def test_old_last_pin_needs_expect_too(self):
        folder = self.install()
        self.write_state(last_pins={"demo": old_entry(folder)})
        self.assertEqual("disabled", loader.inventory()[0]["status"])
        preview = loader.enable("demo", confirm=False)
        self.assertIn("formato", preview["diff"]["note"])
        with self.assertRaises(UsageError):
            loader.enable("demo", confirm=True)

    def test_list_remove_and_doctor_work_with_many_old_pins(self):
        ids = ("alfa", "beta", "gama")
        enabled = {}
        for ident in ids:
            manifest = {**MANIFEST, "id": ident, "contributes": {"providers": [], "presets": []}}
            enabled[ident] = old_entry(self.install(manifest, "def register(api):\n    pass\n"), manifest)
        self.write_state(enabled=enabled)
        env = {"GB_HOME": str(self.home)}
        listed = run_cli("plugins", "--action", "list", env=env)
        self.assertEqual(dict.fromkeys(ids, "suspended"), {row["id"]: row["status"] for row in listed["plugins"]})
        self.assertTrue(run_cli("plugins", "--action", "remove", "--id", "beta", "--yes", env=env)["removed"])
        self.assertIn("youtube", run_cli("providers", env=env))
        run_cli("doctor", env=env)
        listed = run_cli("plugins", "--action", "list", env=env)
        self.assertEqual(["alfa", "gama"], [row["id"] for row in listed["plugins"]])

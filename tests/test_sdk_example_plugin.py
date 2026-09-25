"""O plugin de exemplo instala, habilita e busca de ponta a ponta pela CLI."""

import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import ROOT
from test_sdk_loader import LoaderTestCase

EXAMPLE = ROOT / "examples" / "plugins" / "pasta_local"


class ExamplePluginTests(LoaderTestCase):
    def test_public_sdk_surface(self):
        from getbrolls.sdk import SDK_API, Preset, ProviderCapabilities

        self.assertEqual(1, SDK_API)
        self.assertTrue(ProviderCapabilities(search=True).search)
        self.assertEqual("x", Preset("x", "u", "t").name)

    def test_example_checks_installs_and_searches(self):
        media = Path(tempfile.mkdtemp(prefix="gb-pasta-"))
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        (media / "por do sol na praia.mp4").write_bytes(b"\x00")
        (media / "reuniao.mp4").write_bytes(b"\x00")
        env = {"GB_HOME": str(self.home), "PASTA_LOCAL_DIR": str(media)}
        self.assertTrue(run_cli("plugins", "--action", "check", "--path", EXAMPLE, env=env)["ok"])
        shutil.copytree(EXAMPLE, self.home / "plugins" / "pasta_local")
        run_cli("plugins", "--action", "enable", "--id", "pasta_local", "--yes", env=env)
        with tempfile.TemporaryDirectory() as project:
            found = run_cli(
                "search", "--provider", "pasta_local", "--query", "praia", "--dry-run", project=project, env=env
            )
        titles = [item["title"] for item in found["items"]]
        self.assertEqual(["por do sol na praia"], titles)


if __name__ == "__main__":
    unittest.main()

"""browser.plan: recusas antes de montar qualquer comando ou pasta de captura."""

import json
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  # pylint: disable=unused-import  # isola GB_HOME antes dos imports do getbrolls
from _paths import ROOT  # noqa: F401  # pylint: disable=unused-import  # insere scripts/ em sys.path

from getbrolls import _paths
from getbrolls.browser import plan
from getbrolls.ledger import Ledger
from getbrolls.rules import load_rules

URL = "https://www.nasa.gov/news/"


class BrowserPlanRefusalTests(unittest.TestCase):
    def setUp(self):
        # O ciclo de vida cobre o teste inteiro; a limpeza é feita via addCleanup.
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ledger = Ledger(self.root)
        self.rules = load_rules(self.root)

    def test_screenshots_disabled_in_the_rules_are_refused_and_create_nothing(self):
        self.rules["asset_types"] = ["image", "video"]
        with self.assertRaisesRegex(ValueError, "Screenshots desabilitados"):
            plan(self.ledger, URL, self.rules)
        self.assertFalse((self.root / "output").exists())

    def test_either_screenshot_type_enables_the_plan_and_news_wins(self):
        self.rules["asset_types"] = ["web_screenshot"]
        self.assertEqual("web_screenshot", plan(self.ledger, URL, self.rules)["asset_type"])
        self.rules["asset_types"] = ["web_screenshot", "news_screenshot"]
        self.assertEqual("news_screenshot", plan(self.ledger, URL, self.rules)["asset_type"])

    def test_a_url_with_credentials_is_refused(self):
        with self.assertRaisesRegex(ValueError, "URL pública HTTPS"):
            plan(self.ledger, "https://usuario:senha@www.nasa.gov/", self.rules)


if __name__ == "__main__":
    unittest.main()


class BrowserPlanCommandsTests(BrowserPlanRefusalTests):
    def test_the_commands_name_the_capture_relative_to_the_project_they_run_in(self):
        result = plan(self.ledger, URL, self.rules)
        project = self.ledger.root.parent
        screenshot = result["commands"][-1]
        filename = next(arg for arg in screenshot if arg.startswith("--filename="))
        self.assertRegex(filename, r"^--filename=output/playwright/capture-\d{8}T\d{12}Z\.png$")
        self.assertEqual(str(project), result["cwd"])
        self.assertEqual(str(project / filename.removeprefix("--filename=")), result["output"])
        self.assertFalse(any(str(project) in arg for command in result["commands"] for arg in command))


class BrowserPlanPinnedLauncherTests(BrowserPlanRefusalTests):
    """O `npx` do plano nunca baixa "o que houver": a versão vem do `package.json` que o `setup` instala."""

    def pinned_version(self):
        manifest = json.loads(_paths.data_path("package.json").read_text(encoding="utf-8"))
        return manifest["dependencies"]["@playwright/cli"]

    def test_the_version_in_package_json_is_an_exact_pin(self):
        self.assertRegex(self.pinned_version(), r"^\d+\.\d+\.\d+$")

    def test_every_command_runs_the_playwright_cli_at_the_version_of_package_json(self):
        spec = "@playwright/cli@" + self.pinned_version()
        commands = plan(self.ledger, URL, self.rules)["commands"]
        self.assertEqual(4, len(commands))
        for command in commands:
            with self.subTest(command=command[-3:]):
                self.assertEqual(["npx", "--yes", "--package", spec, "playwright-cli"], command[:5])
                self.assertNotIn("@playwright/cli", command)

    def test_the_package_argument_is_the_only_place_that_names_the_package(self):
        commands = plan(self.ledger, URL, self.rules)["commands"]
        named = {arg for command in commands for arg in command if "playwright/cli" in arg}
        self.assertEqual({"@playwright/cli@" + self.pinned_version()}, named)

"""Regras em camadas: global (`GB_HOME`) → `GB_RULES_FILE` → projeto."""

import json
import os
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls.rules import load_rules


def write_block(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Regras\n\nProsa preservada.\n\n```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```\n",
        encoding="utf-8",
    )
    return path


class RulesLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir(parents=True)
        for key in ("GB_HOME", "GB_RULES_FILE", "GB_LIBRARY"):
            old = os.environ.get(key)
            self.addCleanup(
                lambda k=key, v=old: os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
            )
            os.environ.pop(key, None)
        os.environ["GB_HOME"] = str(self.home)

    def test_scalars_come_from_the_most_specific_layer(self):
        base = load_rules(self.project)
        write_block(self.home / "RULES.md", {"video_format": "reels"})
        self.assertEqual("reels", load_rules(self.project)["video_format"])
        write_block(self.project / "RULES.md", dict(base, video_format="horizontal"))
        rules = load_rules(self.project)
        self.assertEqual("horizontal", rules["video_format"])
        self.assertEqual(str(self.project / "RULES.md"), rules["sources"]["video_format"])

    def test_lists_join_and_blocked_domains_only_accumulate(self):
        base = load_rules(self.project)
        write_block(
            self.home / "RULES.md",
            {"blocked_domains": ["global.test"], "editorial_rules": ["Regra global"]},
        )
        write_block(
            self.project / "RULES.md",
            dict(
                base,
                blocked_domains=["projeto.test"],
                editorial_rules=["Regra do projeto"],
            ),
        )
        rules = load_rules(self.project)
        self.assertEqual(["global.test", "projeto.test"], rules["blocked_domains"])
        self.assertEqual(["Regra global", "Regra do projeto"], rules["editorial_rules"])

    def test_copyright_never_inherits_from_the_global_layer(self):
        write_block(
            self.home / "RULES.md",
            {
                "copyright": {
                    "mode": "user_declaration",
                    "responsible_person": "Alguém de outro projeto",
                    "declaration": "Assumo a responsabilidade por tudo.",
                }
            },
        )
        rules = load_rules(self.project)
        self.assertEqual("per_item_evidence", rules["copyright"]["mode"])
        self.assertIsNone(rules["copyright"]["responsible_person"])
        self.assertTrue(any("copyright" in w for w in rules["rules_warnings"]), rules["rules_warnings"])

    def test_gb_rules_file_sits_between_global_and_project(self):
        base = load_rules(self.project)
        write_block(self.home / "RULES.md", {"video_format": "reels"})
        middle = write_block(Path(self.tmp.name) / "equipe" / "RULES.md", {"video_format": "horizontal"})
        os.environ["GB_RULES_FILE"] = str(middle)
        rules = load_rules(self.project)
        self.assertEqual("horizontal", rules["video_format"])
        self.assertEqual(str(middle), rules["sources"]["video_format"])
        write_block(self.project / "RULES.md", dict(base, video_format="native"))
        self.assertEqual("native", load_rules(self.project)["video_format"])

    def test_gb_rules_file_also_cannot_sign_for_anyone(self):
        middle = write_block(
            Path(self.tmp.name) / "equipe" / "RULES.md",
            {
                "copyright": {
                    "mode": "user_declaration",
                    "responsible_person": "Equipe",
                    "declaration": "Assumimos a responsabilidade por tudo.",
                }
            },
        )
        os.environ["GB_RULES_FILE"] = str(middle)
        rules = load_rules(self.project)
        self.assertEqual("per_item_evidence", rules["copyright"]["mode"])
        self.assertIsNone(rules["copyright"]["responsible_person"])
        self.assertTrue(
            any(str(middle) in w for w in rules["rules_warnings"]),
            rules["rules_warnings"],
        )

    def test_a_broken_global_layer_is_ignored_with_a_warning(self):
        (self.home).mkdir(parents=True, exist_ok=True)
        (self.home / "RULES.md").write_text("sem bloco json", encoding="utf-8")
        rules = load_rules(self.project)
        self.assertEqual("per_item_evidence", rules["copyright"]["mode"])
        self.assertTrue(rules["rules_warnings"])

    def test_missing_gb_rules_file_still_fails_loudly(self):
        os.environ["GB_RULES_FILE"] = str(Path(self.tmp.name) / "nao-existe.md")
        with self.assertRaisesRegex(ValueError, "GB_RULES_FILE"):
            load_rules(self.project)

    def test_project_layer_alone_behaves_like_before(self):
        rules = load_rules(self.project)
        self.assertEqual(1, rules["version"])
        self.assertEqual([], rules["rules_warnings"])
        self.assertIn("asset_types", rules["sources"])


if __name__ == "__main__":
    unittest.main()

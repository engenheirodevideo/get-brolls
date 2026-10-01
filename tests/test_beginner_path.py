"""Caminho de quem começa: `init` → `roteiro new` → `check` → `init-brief` → `status`, sem editar JSON.

Cada `summary.do` dessa sequência tem que apontar para um comando que dá certo no estado
em que o projeto está — o roteiro em 9:16 não pode travar num RULES.md em "native" que
ninguém escolheu, nem o BRIEF.md nascer em "native" com um beat de exemplo.
"""

import json
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from test_roteiro_cli import REVIEW, CliCase

from getbrolls.brief import load_brief


def _json_block(path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text.split("```json", 1)[1].split("```", 1)[0])


def _tail(command):
    """Argumentos depois do prefixo da CLI (`python3 .../gb.py`), para rodar com `run_cli`."""
    argv = shlex.split(command)
    for index, arg in enumerate(argv):
        if arg.endswith("gb.py") or arg == "getbrolls":
            return argv[index + 1 :]
    raise AssertionError(command)


class BeginnerPathTests(CliCase):
    def setUp(self):
        super().setUp()
        base = Path(tempfile.mkdtemp(prefix="gb-begin-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.project = base / "p"

    def follow(self, do, expect=0):
        """Roda o `summary.do.command` como veio, sem trocar nada."""
        return run_cli(*_tail(do["command"]), expect=expect)

    def test_init_roteiro_new_then_check_succeeds_without_rules(self):
        self.cli("init")
        created = self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "x")
        rules = self.project / "RULES.md"
        self.assertTrue(rules.is_file())
        self.assertEqual("reels", _json_block(rules)["video_format"])
        self.assertEqual(rules.resolve(), Path(created["rules"]).resolve())
        self.assertIn("RULES.md", created["summary"]["line"])
        do = created["summary"]["do"]
        self.assertEqual("roteiro-check", do["step"])
        checked = self.follow(do)
        self.assertEqual(4, len(checked["scenes"]))

    def test_roteiro_new_keeps_an_existing_rules_file(self):
        self.cli("init-rules", "--format", "horizontal")
        before = (self.project / "RULES.md").read_bytes()
        created = self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "x")
        self.assertIsNone(created["rules"])
        self.assertEqual(before, (self.project / "RULES.md").read_bytes())
        refused = self.cli("roteiro", "--action", "check", expect=1)
        self.assertIn("init-rules --format reels", refused["error"])

    def test_check_without_project_rules_uses_the_roteiro_aspect(self):
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "x")
        (self.project / "RULES.md").unlink()
        checked = self.cli("roteiro", "--action", "check")
        self.assertEqual(4, len(checked["scenes"]))

    def test_init_brief_inherits_the_roteiro_format_and_has_no_placeholder_beat(self):
        self.cli("init")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "x")
        made = self.cli("init-brief")
        data = _json_block(Path(made["brief"]))
        self.assertEqual("reels", data["video"]["delivery"]["format"])
        self.assertEqual([], data["beats"])
        self.assertIn("ROTEIRO.md", made["summary"]["line"])
        self.cli("brief", "--validate")
        self.assertEqual([], load_brief(self.project)["beats"])
        # O check não trava no BRIEF.md recém-criado.
        self.cli("roteiro", "--action", "check")

    def test_init_brief_without_roteiro_keeps_the_template(self):
        made = self.cli("init-brief")
        data = _json_block(Path(made["brief"]))
        self.assertEqual("native", data["video"]["delivery"]["format"])
        self.assertEqual("abertura", data["beats"][0]["id"])

    def test_status_do_walks_check_review_sync_without_editing_json(self):
        self.cli("init")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "x")
        self.fill_skeleton()
        self.cli("init-brief")
        do = self.cli("status")["summary"]["do"]
        self.assertEqual("roteiro-check", do["step"])
        checked = self.follow(do)
        sha = checked["review"]["sha256"]
        self.cli("roteiro", "--action", "review", *REVIEW, "--expect", sha)
        do = self.cli("status")["summary"]["do"]
        self.assertEqual("roteiro-sync", do["step"])
        synced = self.follow(do)
        self.assertTrue(synced["summary"]["line"].startswith("Sincronizado"))
        after = self.cli("status")["summary"]["do"]
        self.assertNotIn(after["step"], ("roteiro-check", "roteiro-sync"))

    def test_status_do_points_to_editing_while_the_skeleton_is_untouched(self):
        self.cli("init")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "x")
        self.cli("init-brief")
        do = self.cli("status")["summary"]["do"]
        self.assertEqual("roteiro-check", do["step"])
        self.assertIn("Edite o ROTEIRO.md", do["for_human"])
        self.assertIn("esqueleto", do["why"])
        self.fill_skeleton()
        do = self.cli("status")["summary"]["do"]
        self.assertNotIn("esqueleto", do["why"])


if __name__ == "__main__":
    unittest.main()

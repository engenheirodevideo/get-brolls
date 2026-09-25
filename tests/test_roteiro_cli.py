"""CLI do roteiro e das rotas de componentes, ponta a ponta (subprocesso)."""

import contextlib
import io
import json
import os
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _cli import run_cli
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import models
from getbrolls.cli import build_parser
from getbrolls.guidance import command_for
from getbrolls.ledger import Ledger
from getbrolls.runtime import READ_ONLY_ACTIONS

BRIEF = {
    "version": 1,
    "video": {"title": "T", "objective": "O", "audience": None,
              "delivery": {"format": "reels", "duration_s": 45, "platform": None}},
    "rights": {"posture": "per_item_evidence", "stock_allowed": False, "notes": None},
    "defaults": {"allowed_sources": ["youtube"], "intent": "literal", "duration_hint_s": 4, "stock": False},
    "beats": [],
}  # fmt: skip
AUDIT_TRAIL = "brolls/diagnostics.jsonl"
MISSING = "não tem ROTEIRO.md"
REVIEW = ("--by", "Bruno Moreira", "--statement", "aprovado, pode seguir")


def tree(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


class CliCase(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-rcli-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def cli(self, *args, expect=0):
        return run_cli(*args, project=self.project, expect=expect)

    def fill_skeleton(self, path=None):
        path = path or self.project / "ROTEIRO.md"
        text = path.read_text(encoding="utf-8")
        for old, new in (
            ("<gancho: a frase que segura nos 3 primeiros segundos>", "Você não precisa editar 4 horas."),
            ("<o que a pessoa vê enquanto você fala>", "timeline cheia"),
            ("<a dor, em uma frase>", "Todo mundo trava."),
            ("<tela ou b-roll>", "tela do app"),
            ("<o que prova que funciona>", "Ele acha e corta."),
            ("<o que a pessoa faz agora>", "Comenta BROLL."),
        ):
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

    def write_brief(self):
        (self.project / "BRIEF.md").write_text(
            "# Brief\n\n```json\n" + json.dumps(BRIEF, indent=2) + "\n```\n", encoding="utf-8"
        )

    def reviewed(self):
        """Projeto com RULES.md em reels, roteiro preenchido e revisado, BRIEF.md sem beats."""
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        self.fill_skeleton()
        self.write_brief()
        self.cli("roteiro", "--action", "review", *REVIEW)

    def ready(self):
        """Projeto revisado e sincronizado."""
        self.reviewed()
        return self.cli("roteiro", "--action", "sync")

    def edit(self, old, new):
        path = self.project / "ROTEIRO.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text)
        path.write_text(text.replace(old, new), encoding="utf-8")

    def approved(self, shot, source_id="abc"):
        ledger = Ledger(self.project)
        c = models.candidate("youtube", source_id, "t", f"https://www.youtube.com/watch?v={source_id}")
        c["shot"] = shot
        models.set_segment(c, 0, 4)
        models.approve(c, "Bruno Moreira", "chat", "aprovo")
        ledger.add(c)
        ledger.save("test", c)
        return c["id"]


class RoteiroCliTests(CliCase):
    def test_new_check_review_plan_sync_flow(self):
        created = self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        for folder in (
            "aroll",
            "assets/marca",
            "assets/lettering",
            "assets/sfx",
            "assets/musica",
            "assets/composicoes",
        ):
            self.assertTrue((self.project / folder).is_dir(), folder)
        self.assertIsNone(created["backup"])
        refused = self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA", expect=2)
        self.assertIn("--force", refused["error"])
        (self.project / "ROTEIRO.md").write_text(
            (self.project / "ROTEIRO.md").read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        forced = self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA", "--force")
        self.assertTrue(Path(forced["backup"]).read_text(encoding="utf-8").endswith("\n\n"))

        native = self.cli("roteiro", "--action", "check", expect=2)
        self.assertIn("init-rules --format reels", native["error"])
        self.cli("init-rules", "--format", "reels")
        skeleton = self.cli("roteiro", "--action", "check")
        self.assertIn("parece texto do esqueleto", " ".join(skeleton["warnings"]))
        self.fill_skeleton()
        checked = self.cli("roteiro", "--action", "check")
        self.assertEqual(len(checked["scenes"]), 4)
        self.assertFalse(checked["review"]["reviewed"])
        self.assertNotIn("esqueleto", " ".join(checked["warnings"]))

        self.assertIn("revisão humana", self.cli("roteiro", "--action", "sync", expect=2)["error"])
        self.cli("roteiro", "--action", "review", "--by", "Bruno Moreira", expect=2)
        self.cli("roteiro", "--action", "review", *REVIEW)
        self.assertIn("status: revisado", (self.project / "ROTEIRO.md").read_text(encoding="utf-8"))
        self.assertIn("/get-brolls-brief", self.cli("roteiro", "--action", "sync", expect=2)["error"])

        self.write_brief()
        planned = self.cli("roteiro", "--action", "plan")
        self.assertEqual((planned["written"], planned["new"]), ([], ["c02", "c03-a"]))
        synced = self.cli("roteiro", "--action", "sync")
        self.assertEqual(synced["new"], ["c02", "c03-a"])
        self.assertIn("Sincronizado", synced["summary"]["line"])
        validated = self.cli("brief", "--validate")
        self.assertEqual(validated["beats"], 2)

    def test_check_and_plan_are_read_only_on_a_synced_project(self):
        self.ready()
        for action in ("check", "plan"):
            with self.subTest(action=action):
                before = tree(self.project)
                before.pop(AUDIT_TRAIL, None)
                self.cli("roteiro", "--action", action)
                after = tree(self.project)
                self.assertIn(AUDIT_TRAIL, after)  # só a trilha de auditoria cresce, como em `status`
                after.pop(AUDIT_TRAIL)
                self.assertEqual(before, after)

    def test_assets_are_read_only_on_a_synced_project(self):
        self.ready()
        (self.project / "assets/sfx/whoosh.wav").write_text("x", encoding="utf-8")
        for argv in (
            ("assets", "--action", "list"),
            ("assets", "--action", "where", "--kind", "sfx", "--name", "whoosh"),
        ):
            with self.subTest(argv=argv):
                before = tree(self.project)
                before.pop(AUDIT_TRAIL, None)
                self.cli(*argv)
                after = tree(self.project)
                after.pop(AUDIT_TRAIL, None)
                self.assertEqual(before, after)

    def test_read_only_actions_refuse_a_pending_transaction(self):
        self.ready()
        (self.project / "brolls" / ".pending-transaction.json").write_text("{}", encoding="utf-8")
        for action in ("check", "plan"):
            with self.subTest(action=action):
                self.assertIn("gravação interrompida", self.cli("roteiro", "--action", action, expect=2)["error"])

    def test_unknown_plugin_prefix_is_refused_end_to_end(self):
        self.ready()
        path = self.project / "ROTEIRO.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("[A-ROLL]", "[A-ROLL]\n[hf:zoom-in]", 1), encoding="utf-8"
        )
        self.assertIn("não está habilitado", self.cli("roteiro", "--action", "check", expect=2)["error"])

    def test_assets_list_and_where(self):
        (self.project / "assets/sfx").mkdir(parents=True)
        (self.project / "assets/sfx/whoosh.wav").write_text("x", encoding="utf-8")
        listed = self.cli("assets", "--action", "list")
        self.assertEqual([r["name"] for r in listed["assets"]], ["whoosh"])
        where = self.cli("assets", "--action", "where", "--kind", "sfx", "--name", "whoosh")
        self.assertEqual(where["status"], "found")
        self.cli("assets", "--action", "where", "--kind", "sfx", "--name", "../x", expect=2)
        self.assertFalse((self.project / "brolls").exists())


class GuidanceCommandTests(CliCase):
    def test_status_sync_command_parses_and_runs(self):
        self.reviewed()
        do = self.cli("status")["summary"]["do"]
        self.assertEqual(do["step"], "roteiro-sync")
        argv = shlex.split(do["command"])
        self.assertTrue(argv[1].endswith("gb.py"), argv[1])
        parsed = build_parser().parse_args(argv[2:])
        self.assertEqual((parsed.command, parsed.action), ("roteiro", "sync"))
        synced = run_cli(*argv[2:])
        self.assertEqual(synced["new"], ["c02", "c03-a"])
        self.assertEqual(self.cli("brief", "--validate")["beats"], 2)

    def test_command_for_roteiro_sync_parses(self):
        parsed = build_parser().parse_args(shlex.split(command_for("roteiro-sync", "/tmp/x y"))[2:])
        self.assertEqual((parsed.command, parsed.action, parsed.project), ("roteiro", "sync", "/tmp/x y"))


class NewForceBackupTests(CliCase):
    def new(self, *extra, expect=0):
        return self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA", *extra, expect=expect)

    def test_force_never_overwrites_a_backup_nor_the_sync_backup(self):
        self.new()
        path = self.project / "ROTEIRO.md"
        sync_bak = self.project / "ROTEIRO.md.sync.bak"
        sync_bak.write_text("do sync", encoding="utf-8")
        contents = []
        for round_ in range(3):
            path.write_text(f"versão {round_}\n", encoding="utf-8")
            contents.append(f"versão {round_}\n")
            forced = self.new("--force")
            self.assertIsNotNone(forced["backup"])
        self.assertEqual((self.project / "ROTEIRO.md.bak").read_text(encoding="utf-8"), "versão 0\n")
        backups = sorted(self.project.glob("ROTEIRO.md.*.bak"))
        stamped = [p for p in backups if p.name != "ROTEIRO.md.sync.bak"]
        self.assertEqual(len(stamped), 2, [p.name for p in backups])
        for p in stamped:
            self.assertRegex(p.name, r"^ROTEIRO\.md\.\d{8}-\d{6}(-\d+)?\.bak$")
        saved = sorted(p.read_text(encoding="utf-8") for p in [self.project / "ROTEIRO.md.bak", *stamped])
        self.assertEqual(saved, contents)
        self.assertEqual(sync_bak.read_text(encoding="utf-8"), "do sync")

    def test_force_writes_through_a_roteiro_link(self):
        notes = self.project / "notas"
        notes.mkdir()
        real = notes / "roteiro.md"
        real.write_text("antigo\n", encoding="utf-8")
        (self.project / "ROTEIRO.md").symlink_to(real)
        forced = self.new("--force")
        self.assertTrue((self.project / "ROTEIRO.md").is_symlink())
        self.assertIn("type: roteiro", real.read_text(encoding="utf-8"))
        self.assertEqual(Path(forced["backup"]).read_text(encoding="utf-8"), "antigo\n")


class ReviewWriteTests(CliCase):
    def base(self):
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        self.fill_skeleton()
        self.write_brief()

    def test_review_normalizes_bom_and_crlf_and_sync_accepts_it(self):
        self.base()
        path = self.project / "ROTEIRO.md"
        path.write_bytes(b"\xef\xbb\xbf" + path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
        self.cli("roteiro", "--action", "review", *REVIEW)
        raw = path.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", raw)
        self.assertIn(b"status: revisado", raw)
        self.assertTrue(self.cli("roteiro", "--action", "check")["review"]["reviewed"])
        self.assertIn("Sincronizado", self.cli("roteiro", "--action", "sync")["summary"]["line"])

    def test_review_writes_through_a_roteiro_link(self):
        self.base()
        notes = self.project / "notas"
        notes.mkdir()
        real = notes / "roteiro.md"
        shutil.move(self.project / "ROTEIRO.md", real)
        (self.project / "ROTEIRO.md").symlink_to(real)
        self.cli("roteiro", "--action", "review", *REVIEW)
        self.assertTrue((self.project / "ROTEIRO.md").is_symlink())
        self.assertIn("status: revisado", real.read_text(encoding="utf-8"))


class TargetGateSummaryTests(CliCase):
    def test_plan_lists_and_confirmed_sync_echoes_invalidated_approvals(self):
        self.ready()
        candidate = self.approved("c02")
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.cli("roteiro", "--action", "review", *REVIEW)
        planned = self.cli("roteiro", "--action", "plan")
        self.assertEqual(planned["affected_approvals"], [{"candidate": candidate, "beat": "c02"}])
        self.assertIn(candidate, planned["summary"]["line"])
        refused = self.cli("roteiro", "--action", "sync", expect=2)
        self.assertIn("--confirm-target-change", refused["error"])
        synced = self.cli("roteiro", "--action", "sync", "--confirm-target-change")
        self.assertEqual(synced["invalidated"], [candidate])
        self.assertIn(candidate, synced["summary"]["line"])
        self.assertIn("pendente", synced["summary"]["line"])

    def test_refused_plan_says_refused_and_never_zero_beats(self):
        self.ready()
        self.approved("c03-a", source_id="prova")
        self.edit("## Prova <!-- c03 -->\n[SPLIT: tela do app | A-ROLL]", "## Prova\n[SPLIT: tela nova | A-ROLL]")
        planned = self.cli("roteiro", "--action", "plan")
        self.assertIsNone(planned["beats"])
        self.assertIsNone(planned["total_s"])
        line = planned["summary"]["line"]
        self.assertTrue(line.startswith("Plano recusado: "), line)
        self.assertIn("c03", line)
        self.assertNotIn("0 beat", line)


class BrokenRoteiroPathTests(CliCase):
    """ROTEIRO.md que existe mas não é arquivo: nunca "crie com new" (empurraria para new --force)."""

    ACTIONS = (
        ("check",),
        ("plan",),
        ("review", *REVIEW),
        ("sync",),
        ("new", "--genero", "reels", "--tema", "IA"),
        ("new", "--genero", "reels", "--tema", "IA", "--force"),
    )

    def setUp(self):
        super().setUp()
        self.cli("init-rules", "--format", "reels")
        self.write_brief()

    def assert_refused(self, expected):
        for action in self.ACTIONS:
            with self.subTest(action=action):
                before = self.content()
                error = self.cli("roteiro", "--action", *action, expect=2)["error"]
                self.assertNotIn(MISSING, error)
                self.assertNotIn("Crie com", error)
                self.assertIn("ROTEIRO.md", error)
                self.assertIn(expected, error)
                self.assertEqual(before, self.content())

    def content(self):
        """A árvore fora de brolls/ (trava, logs e trilha de auditoria de quem grava ficam lá)."""
        return {name: data for name, data in tree(self.project).items() if not name.startswith("brolls")}

    def test_dangling_link(self):
        (self.project / "ROTEIRO.md").symlink_to(self.project / "sumiu.md")
        self.assert_refused("link quebrado")
        self.assertIn("sumiu.md", self.cli("roteiro", "--action", "check", expect=2)["error"])
        self.assertTrue((self.project / "ROTEIRO.md").is_symlink())

    def test_looping_link(self):
        (self.project / "ROTEIRO.md").symlink_to(self.project / "ROTEIRO.md")
        self.assert_refused("link quebrado")

    def test_directory(self):
        (self.project / "ROTEIRO.md").mkdir()
        self.assert_refused("pasta")


@unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "precisa de permissão POSIX")
class WriteErrorTests(CliCase):
    def lock_project_root(self):
        self.project.chmod(0o555)  # raiz só leitura de propósito; a limpeza devolve a escrita
        self.addCleanup(self.project.chmod, 0o755)

    def assert_clean(self, error):
        self.assertEqual(error["error_code"], "IO_ERROR")
        self.assertIn("permissão", error["error"])
        self.assertNotIn("Errno", error["error"])
        self.assertNotIn("Traceback", error["error"])

    def test_review_new_and_sync_render_permission_errors_in_portuguese(self):
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        self.fill_skeleton()
        self.write_brief()
        self.cli("roteiro", "--action", "check")  # cria brolls/ antes de travar a raiz
        (self.project / "brolls").mkdir(exist_ok=True)
        self.lock_project_root()
        self.assert_clean(self.cli("roteiro", "--action", "review", *REVIEW, expect=2))
        self.assert_clean(
            self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA", "--force", expect=2)
        )
        self.assert_clean(self.cli("roteiro", "--action", "sync", expect=2))


class ParserTests(unittest.TestCase):
    def test_read_only_actions_are_registered(self):
        for pair in (("roteiro", "check"), ("roteiro", "plan"), ("assets", "list"), ("assets", "where")):
            self.assertIn(pair, READ_ONLY_ACTIONS)
        for pair in (("roteiro", "sync"), ("roteiro", "new"), ("roteiro", "review")):
            self.assertNotIn(pair, READ_ONLY_ACTIONS)

    def test_roteiro_and_assets_do_not_take_the_format_flag(self):
        parser = build_parser()
        for argv in (
            ["roteiro", "--project", "/tmp/x", "--action", "check", "--confirm-format-change"],
            ["assets", "--project", "/tmp/x", "--action", "list", "--confirm-format-change"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)
        parsed = parser.parse_args(["roteiro", "--project", "/tmp/x", "--action", "sync", "--confirm-target-change"])
        self.assertTrue(parsed.confirm_target_change)


if __name__ == "__main__":
    unittest.main()

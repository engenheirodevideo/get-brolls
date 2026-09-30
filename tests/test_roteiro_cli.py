"""CLI do roteiro e das rotas de componentes, ponta a ponta (subprocesso)."""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _paths import (  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
    ROOT,
    suggested_argv,
)
from test_logging_trail import DEBUG_ENV, _events, _log_path

from getbrolls import layout, models, roteiro, roteiro_commands, roteiro_review
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

    def cli(self, *args, expect=0, env=None):
        return run_cli(*args, project=self.project, expect=expect, env=env)

    def fill_skeleton(self, path=None):
        path = path or self.project / "ROTEIRO.md"
        text = path.read_text(encoding="utf-8")
        for old, new in (
            ("{gancho: a frase que segura nos 3 primeiros segundos}", "Você não precisa editar 4 horas."),
            ("{o que a pessoa vê enquanto você fala}", "timeline cheia"),
            ("{a dor, em uma frase}", "Todo mundo trava."),
            ("{tela ou b-roll}", "tela do app"),
            ("{o que prova que funciona}", "Ele acha e corta."),
            ("{o que a pessoa faz agora}", "Comenta BROLL."),
        ):
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

    def review(self, expect=0):
        """`review` com o `--expect` do hash que o `check` mostra, como a skill faz depois de mostrar o roteiro."""
        sha = self.cli("roteiro", "--action", "check")["review"]["sha256"]
        return self.cli("roteiro", "--action", "review", *REVIEW, "--expect", sha, expect=expect)

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
        self.review()

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
            "assets/imagem",
            "assets/composicoes",
            "assets/outros",
        ):
            self.assertTrue((self.project / folder).is_dir(), folder)
        self.assertEqual(["aroll", *(f"assets/{n}" for n in layout.ASSET_FOLDERS)], created["folders"])
        self.assertIsNone(created["backup"])
        refused = self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA", expect=1)
        self.assertIn("--force", refused["error"])
        (self.project / "ROTEIRO.md").write_text(
            (self.project / "ROTEIRO.md").read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        forced = self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA", "--force")
        self.assertTrue(Path(forced["backup"]).read_text(encoding="utf-8").endswith("\n\n"))

        native = self.cli("roteiro", "--action", "check", expect=1)
        self.assertIn("init-rules --format reels", native["error"])
        self.cli("init-rules", "--format", "reels")
        skeleton = self.cli("roteiro", "--action", "check")
        self.assertIn("parece texto do esqueleto", " ".join(skeleton["warnings"]))
        self.fill_skeleton()
        checked = self.cli("roteiro", "--action", "check")
        self.assertEqual(len(checked["scenes"]), 4)
        self.assertFalse(checked["review"]["reviewed"])
        self.assertNotIn("esqueleto", " ".join(checked["warnings"]))

        self.assertIn("revisão humana", self.cli("roteiro", "--action", "sync", expect=1)["error"])
        self.cli("roteiro", "--action", "review", "--by", "Bruno Moreira", expect=1)
        self.review()
        self.assertIn("status: revisado", (self.project / "ROTEIRO.md").read_text(encoding="utf-8"))
        self.assertIn("/get-brolls-brief", self.cli("roteiro", "--action", "sync", expect=1)["error"])

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
                self.assertIn("gravação interrompida", self.cli("roteiro", "--action", action, expect=1)["error"])

    def test_unknown_plugin_prefix_is_refused_end_to_end(self):
        self.ready()
        path = self.project / "ROTEIRO.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace("[A-ROLL]", "[A-ROLL]\n[hf:zoom-in]", 1), encoding="utf-8"
        )
        self.assertIn("não está habilitado", self.cli("roteiro", "--action", "check", expect=1)["error"])

    def test_assets_list_and_where(self):
        (self.project / "assets/sfx").mkdir(parents=True)
        (self.project / "assets/sfx/whoosh.wav").write_text("x", encoding="utf-8")
        listed = self.cli("assets", "--action", "list")
        self.assertEqual([r["name"] for r in listed["assets"]], ["whoosh"])
        where = self.cli("assets", "--action", "where", "--kind", "sfx", "--name", "whoosh")
        self.assertEqual(where["status"], "found")
        self.cli("assets", "--action", "where", "--kind", "sfx", "--name", "../x", expect=1)
        self.assertFalse((self.project / "brolls").exists())


class FrontmatterCliTests(CliCase):
    OBSIDIAN = (
        "created: 2026-09-26\nupdated: 2026-09-26\ntags:\n  - get-brolls\n  - reels\n"
        "aliases:\n  - Roteiro IA\ncssclasses:\n  - wide\n"
    )

    def with_head(self, extra):
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        self.fill_skeleton()
        self.edit("type: roteiro\n", "type: roteiro\n" + extra)

    def test_a_full_obsidian_frontmatter_passes_check(self):
        self.with_head(self.OBSIDIAN + "cliente: acme-corp\ndirecao: rampa-e-whip\n")
        checked = self.cli("roteiro", "--action", "check")
        self.assertEqual(4, len(checked["scenes"]))
        self.assertEqual(("acme-corp", "rampa-e-whip"), (checked["meta"]["cliente"], checked["meta"]["direcao"]))
        self.assertFalse({"tags", "aliases", "created", "updated", "cssclasses"} & set(checked["meta"]))

    def test_a_client_that_is_not_a_slug_is_a_clear_error(self):
        self.with_head("cliente: Acme Corp\n")
        error = self.cli("roteiro", "--action", "check", expect=1)["error"]
        self.assertIn('"cliente" tem que ser um slug', error)
        self.assertIn("acme-corp", error)


class GuidanceCommandTests(CliCase):
    def test_status_sync_command_parses_and_runs(self):
        self.reviewed()
        do = self.cli("status")["summary"]["do"]
        self.assertEqual(do["step"], "roteiro-sync")
        argv = suggested_argv(do["command"])
        parsed = build_parser().parse_args(argv)
        self.assertEqual((parsed.command, parsed.action), ("roteiro", "sync"))
        synced = run_cli(*argv)
        self.assertEqual(synced["new"], ["c02", "c03-a"])
        self.assertEqual(self.cli("brief", "--validate")["beats"], 2)

    def test_command_for_roteiro_sync_parses(self):
        command = command_for("roteiro-sync", "/tmp/x y")
        assert command is not None
        parsed = build_parser().parse_args(suggested_argv(command))
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
        self.review()
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
        self.review()
        self.assertTrue((self.project / "ROTEIRO.md").is_symlink())
        self.assertIn("status: revisado", real.read_text(encoding="utf-8"))


class ReviewExpectTests(CliCase):
    CHANGED = "O roteiro mudou desde a versão revisada; mostre de novo e revise."

    def setUp(self):
        super().setUp()
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        self.fill_skeleton()
        self.write_brief()
        self.path = self.project / "ROTEIRO.md"

    def reviews(self):
        return self.project / "brolls" / "roteiro-reviews.jsonl"

    def test_review_requires_the_hash_that_check_and_plan_show(self):
        refused = self.cli("roteiro", "--action", "review", *REVIEW, expect=1)
        self.assertIn("--expect", refused["error"])
        self.assertIn("review.sha256", refused["error"])
        checked = self.cli("roteiro", "--action", "check")["review"]["sha256"]
        planned = self.cli("roteiro", "--action", "plan")["review"]["sha256"]
        self.assertEqual(checked, planned)
        self.assertRegex(checked, r"^[0-9a-f]{64}$")
        entry = self.cli("roteiro", "--action", "review", *REVIEW, "--expect", checked.upper())["review"]
        self.assertEqual(entry["sha256"], checked)
        self.assertTrue(self.cli("roteiro", "--action", "plan")["review"]["reviewed"])

    def test_review_of_another_version_is_refused_without_writing(self):
        sha = self.cli("roteiro", "--action", "check")["review"]["sha256"]
        self.edit("Todo mundo trava.", "Todo mundo desiste.")
        before = self.path.read_bytes()
        refused = self.cli("roteiro", "--action", "review", *REVIEW, "--expect", sha, expect=1)
        self.assertIn(self.CHANGED, refused["error"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.reviews().exists())

    def test_edit_during_the_review_is_refused_without_writing(self):
        sha = self.cli("roteiro", "--action", "check")["review"]["sha256"]
        edited = self.path.read_text(encoding="utf-8").replace("Todo mundo trava.", "Todo mundo desiste.")
        real_set_status = roteiro_review.set_status

        def autosave_in_the_middle(text, status):
            self.path.write_text(edited, encoding="utf-8")  # o Obsidian grava entre a leitura e a escrita
            return real_set_status(text, status)

        args = build_parser().parse_args(
            ["roteiro", "--action", "review", *REVIEW, "--expect", sha, "--project", str(self.project)]
        )
        with (
            mock.patch.object(roteiro_review, "set_status", autosave_in_the_middle),
            self.assertRaises(ValueError) as ctx,
        ):
            roteiro_commands.run(args)
        self.assertIn(self.CHANGED, str(ctx.exception))
        self.assertEqual(edited, self.path.read_text(encoding="utf-8"))
        self.assertFalse(self.reviews().exists())


class TargetGateSummaryTests(CliCase):
    def test_plan_lists_and_confirmed_sync_echoes_invalidated_approvals(self):
        self.ready()
        candidate = self.approved("c02")
        self.edit("[BROLL: timeline cheia]", "[BROLL: mesa de edição]")
        self.review()
        planned = self.cli("roteiro", "--action", "plan")
        self.assertEqual(planned["affected_approvals"], [{"candidate": candidate, "beat": "c02"}])
        self.assertIn(candidate, planned["summary"]["line"])
        refused = self.cli("roteiro", "--action", "sync", expect=1)
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
        ("review", *REVIEW, "--expect", "0" * 64),
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
                error = self.cli("roteiro", "--action", *action, expect=1)["error"]
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
        self.assertIn("sumiu.md", self.cli("roteiro", "--action", "check", expect=1)["error"])
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
        sha = self.cli("roteiro", "--action", "check")["review"]["sha256"]  # cria brolls/ antes de travar a raiz
        (self.project / "brolls").mkdir(exist_ok=True)
        self.lock_project_root()
        self.assert_clean(self.cli("roteiro", "--action", "review", *REVIEW, "--expect", sha, expect=1))
        # O review que não gravou o ROTEIRO.md não registra nada; o sync abaixo precisa de uma revisão válida.
        self.assertFalse((self.project / "brolls" / "roteiro-reviews.jsonl").exists())
        doc = roteiro.parse(roteiro.load_text(self.project))
        roteiro_review.record_review(self.project, doc, "Bruno Moreira", "chat", "aprovado, pode seguir")
        self.assert_clean(
            self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA", "--force", expect=1)
        )
        self.assert_clean(self.cli("roteiro", "--action", "sync", expect=1))


class RoteiroLoggingTests(CliCase):
    """`roteiro_review` e `roteiro_sync` no `getbrolls.log`: campos-base, sem caminho nem texto do roteiro."""

    def test_review_and_sync_log_the_right_fields(self):
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        self.fill_skeleton()
        self.write_brief()
        sha = self.cli("roteiro", "--action", "check")["review"]["sha256"]
        self.cli("roteiro", "--action", "review", *REVIEW, "--expect", sha, env=DEBUG_ENV)
        synced = self.cli("roteiro", "--action", "sync", env=DEBUG_ENV)

        text = _log_path(self.project).read_text(encoding="utf-8")
        review_event = _events(text, "roteiro_review")[-1]
        self.assertEqual(sha, review_event["sha256"])
        self.assertEqual("chat", review_event["channel"])
        self.assertIsNone(review_event["projeto_id"])  # projeto sem plugin/board nunca ganhou id

        sync_event = _events(text, "roteiro_sync")[-1]
        self.assertIsNone(sync_event["projeto_id"])
        self.assertEqual(str(len(synced["new"])), sync_event["beats_criados"])
        alterados = len(set(synced["target_changed"]) | set(synced["speech_changed"]))
        self.assertEqual(str(alterados), sync_event["beats_alterados"])
        self.assertEqual(str(len(synced["retired"])), sync_event["beats_aposentados"])

        self.assertNotIn(str(self.project), text)
        self.assertNotIn(REVIEW[3], text)  # a frase de revisão nunca vaza
        self.assertNotIn("Você não precisa editar 4 horas", text)  # nem a fala do roteiro


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

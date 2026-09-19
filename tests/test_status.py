"""Observabilidade: `status` responde onde o projeto está e os comandos resumem o resultado."""

import copy
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import CLI, ROOT

from getbrolls.commands import (
    FLOW_SUMMARIES,
    STATUS_STAGES,
    status_next,
    with_summary,
)
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment

# Infraestrutura de auditoria: `audited()` grava estes arquivos em qualquer comando.
AUDIT_FILES = {"diagnostics.jsonl", ".command.lock"}


def run_cli(test, *args):
    """Chamada barata de CLI: o ciclo real com FFmpeg vive em tests/test_cli.py."""
    done = subprocess.run(
        [sys.executable, str(CLI), *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    test.assertEqual(0, done.returncode, done.stderr)
    return json.loads(done.stdout)


def fixture(root):
    """Projeto sintético com um item em cada etapa do fluxo."""
    ledger = Ledger(root)
    pending = candidate("local", "a", "Sem prévia")
    set_segment(pending, 0, 1)

    delivered = candidate("local", "b", "Entregue")
    set_segment(delivered, 0, 2)
    delivered["preview"]["gif_path"] = "previews/b.gif"
    delivered["approval"] = {
        "status": "approved",
        "by": "Revisor humano",
        "at": now(),
        "revision": 1,
    }
    delivered["rights"]["status"] = "permitted"
    delivered["rights"]["evidence"] = ["Evidência sintética do teste"]
    delivered["output"] = {"path": "clips/b.mp4", "sha256": "0" * 64, "verified": True}
    delivered["state"] = "verified"

    rejected = candidate("local", "c", "Rejeitado")
    set_segment(rejected, 0, 1)
    rejected["preview"]["contact_sheet_path"] = "previews/c.jpg"
    rejected["approval"]["status"] = "rejected"
    rejected["state"] = "rejected"

    # Tem quadro e ninguém decidiu: este é o único item de decisão pendente de verdade.
    undecided = candidate("local", "d", "Aguardando decisão")
    set_segment(undecided, 0, 1)
    undecided["preview"]["contact_sheet_path"] = "previews/d.jpg"
    undecided["state"] = "awaiting_approval"

    items = [ledger.add(item) for item in (pending, delivered, rejected, undecided)]
    ledger.save_many("fixture", items)
    return ledger


def snapshot(root):
    """Conteúdo e mtime de cada arquivo do projeto, fora da auditoria da CLI."""
    state = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_dir():
            state[relative] = "dir"
            continue
        if path.name in AUDIT_FILES:
            continue
        state[relative] = (
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return state


class StatusCommandTests(unittest.TestCase):
    def call(self, *args):
        return run_cli(self, *args)

    def test_status_counts_and_lists_every_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            payload = self.call("status", "--project", tmp)
            self.assertEqual(
                {
                    "candidates": 4,
                    "previews": 3,
                    "pending": 1,
                    "approved": 1,
                    "rejected": 1,
                    "permitted": 1,
                    "delivered": 1,
                    "verified": 1,
                },
                payload["counts"],
            )
            # `local:a` não tem quadro: ninguém pode decidir sobre ele, então ele não
            # é uma decisão pendente — é um item que ainda precisa de prévia.
            self.assertEqual(["local:d"], payload["stages"]["pending"])
            self.assertEqual(["local:b"], payload["stages"]["delivered"])
            self.assertEqual(["local:c"], payload["stages"]["rejected"])
            self.assertEqual(
                ["local:a", "local:b", "local:c", "local:d"],
                [item["id"] for item in payload["items"]],
            )
            self.assertEqual(4, payload["journal"]["events"])
            self.assertEqual("fixture", payload["journal"]["last"]["operation"])
            self.assertIsNone(payload["review_page"])

    def test_status_summary_comes_first_and_names_the_next_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            payload = self.call("status", "--project", tmp)
            self.assertEqual("summary", next(iter(payload)))
            summary = payload["summary"]
            # `line/stages/next` continuam na frente; `do` e `brief` são aditivos.
            self.assertEqual(["line", "stages", "next"], list(summary)[:3])
            self.assertLessEqual({"line", "stages", "next", "do", "brief"}, set(summary))
            self.assertIn("candidatos encontrados", summary["line"])
            self.assertEqual(
                [plural for _, _, plural in STATUS_STAGES],
                [stage["stage"] for stage in summary["stages"]],
            )
            # O item `d` tem prévia e ninguém decidiu: a decisão humana ganha do
            # degrau de gerar mais prévia para quem sobrou sem quadro.
            self.assertIn("decisão humana", summary["next"])
            # `do` é aditivo e tem degraus a mais que `next`: sem BRIEF.md o passo
            # real é fazer o brief, e `summary["brief"]` fica None.
            self.assertEqual("init-brief", summary["do"]["step"])
            self.assertIn("init-brief --project", summary["do"]["command"])
            self.assertIn("/get-brolls-brief", summary["do"]["for_human"])
            self.assertFalse(summary["do"]["blocking_human"])
            self.assertIsNone(summary["do"]["url"])
            self.assertIsNone(summary["brief"])

    def test_status_counts_brief_coverage_without_writing(self):
        from tests.test_brief import VALID, write_brief

        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            write_brief(tmp, VALID)
            before = sorted(p.name for p in Path(tmp).iterdir())
            payload = self.call("status", "--project", tmp)
            summary = payload["summary"]
            self.assertEqual({"beats": 2, "covered": 0, "missing": 2, "blocked": 0}, summary["brief"])
            # Nenhum beat tem candidato, mas o item `d` tem prévia e ninguém decidiu:
            # a decisão humana ganha do degrau de buscar material para o primeiro beat.
            self.assertEqual("approve", summary["do"]["step"])
            self.assertTrue(summary["do"]["blocking_human"])
            self.assertNotIn("abertura", summary["do"]["for_human"])
            self.assertEqual(before, sorted(p.name for p in Path(tmp).iterdir()))

    def test_brief_search_comes_back_once_nobody_owes_a_decision(self):
        from tests.test_brief import VALID, write_brief

        with tempfile.TemporaryDirectory() as tmp:
            ledger = fixture(tmp)
            # Decide o único item com prévia pendente: a escada volta ao beat sem material.
            undecided = next(c for c in ledger.data["items"] if c["title"] == "Aguardando decisão")
            undecided["approval"]["status"] = "rejected"
            undecided["state"] = "rejected"
            ledger.save_many("decide", [undecided])
            write_brief(tmp, VALID)
            summary = self.call("status", "--project", tmp)["summary"]
            self.assertEqual("brief-search", summary["do"]["step"])
            self.assertIn("abertura", summary["do"]["for_human"])
            self.assertIn("search --project", summary["do"]["command"])

    def test_status_tells_a_broken_brief_apart_from_a_missing_one(self):
        from tests.test_brief import VALID, write_brief

        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            broken = copy.deepcopy(VALID)
            broken["beats"][1]["id"] = broken["beats"][0]["id"]
            write_brief(tmp, broken)
            summary = self.call("status", "--project", tmp)["summary"]
            self.assertEqual("brief-invalid", summary["do"]["step"])
            self.assertIn("brief --validate", summary["do"]["command"])
            self.assertTrue(summary["do"]["why"])
            self.assertIsNone(summary["brief"])

    def test_status_command_points_at_the_same_project_it_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            payload = self.call("status", "--project", tmp)
            # `project` do relatório é a pasta brolls/; o comando aponta para a raiz
            # dela, sem resolver links simbólicos por conta própria.
            self.assertIn(
                shlex.quote(str(Path(payload["project"]).parent)),
                payload["summary"]["do"]["command"],
            )

    def test_status_next_step_follows_the_flow(self):
        base = dict.fromkeys(
            (
                "candidates",
                "previews",
                "pending",
                "approved",
                "rejected",
                "permitted",
                "delivered",
                "verified",
            ),
            0,
        )
        self.assertIn("search", status_next(base))
        self.assertIn("preview", status_next({**base, "candidates": 2}))
        self.assertIn("review", status_next({**base, "candidates": 2, "previews": 2}))
        self.assertIn(
            "permit",
            status_next({**base, "candidates": 2, "previews": 2, "approved": 2}),
        )
        self.assertIn(
            "fetch",
            status_next({**base, "candidates": 2, "previews": 2, "approved": 2, "permitted": 2}),
        )
        self.assertIn(
            "verify",
            status_next(
                {
                    **base,
                    "candidates": 2,
                    "previews": 2,
                    "approved": 2,
                    "permitted": 2,
                    "delivered": 2,
                }
            ),
        )
        self.assertIn(
            "completo",
            status_next(
                {
                    **base,
                    "candidates": 2,
                    "previews": 2,
                    "approved": 2,
                    "permitted": 2,
                    "delivered": 2,
                    "verified": 2,
                }
            ),
        )

    def test_a_candidate_without_a_preview_is_not_a_pending_decision(self):
        """Contar como "decisão pendente" quem ninguém pode decidir inflava o número."""
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            payload = self.call("status", "--project", tmp)
            self.assertNotIn("local:a", payload["stages"]["pending"])
            self.assertEqual(["local:d"], payload["stages"]["pending"])
            self.assertIn("decisão humana", payload["summary"]["next"])

    def test_a_storyboard_without_previews_is_reported_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            bare = candidate("local", "a", "Sem prévia")
            set_segment(bare, 0, 1)
            ledger.save_many("fixture", [ledger.add(bare)])
            reviewed = self.call("review", "--project", tmp)
            self.assertIn(
                "EMPTY_STORYBOARD",
                [w["code"] for w in reviewed.get("warnings", [])],
            )
            payload = self.call("status", "--project", tmp)
            self.assertTrue(any("Storyboard vazio" in w for w in payload["summary"]["warnings"]))

    def test_a_fully_delivered_project_reports_the_flow_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            done = candidate("local", "b", "Entregue")
            set_segment(done, 0, 2)
            done["preview"]["gif_path"] = "previews/b.gif"
            done["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1}
            done["rights"]["status"] = "permitted"
            done["rights"]["evidence"] = ["Evidência sintética"]
            done["output"] = {"path": "clips/b.mp4", "sha256": "0" * 64, "verified": True}
            done["delivery"] = {"path": "entrega/00-b/b.mp4", "method": "hardlink"}
            done["state"] = "verified"
            # Um item rejeitado e sem prévia não pode prender a escada em "gerar prévias".
            out = candidate("local", "c", "Rejeitado")
            set_segment(out, 0, 1)
            out["approval"]["status"] = "rejected"
            out["state"] = "rejected"
            ledger.save_many("fixture", [ledger.add(done), ledger.add(out)])
            payload = self.call("status", "--project", tmp)
            self.assertIn("completo", payload["summary"]["next"])

    def test_a_finished_delivery_is_not_undone_by_an_undecided_candidate(self):
        """Fricção 1 da rodada 2: `do` mandava inspecionar um descarte depois da entrega."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            done = candidate("local", "b", "Entregue")
            set_segment(done, 0, 2)
            done["preview"]["gif_path"] = "previews/b.gif"
            done["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1}
            done["rights"]["status"] = "permitted"
            done["rights"]["evidence"] = ["Evidência sintética"]
            done["output"] = {"path": "clips/b.mp4", "sha256": "0" * 64, "verified": True}
            done["delivery"] = {"path": "entrega/00-b/b.mp4", "method": "hardlink"}
            done["state"] = "verified"
            # Candidato que o agente largou pelo caminho: sem prévia, sem duração, sem decisão.
            leftover = candidate("youtube", "descartado", "Livestream 24/7")
            leftover["source_url"] = "https://www.youtube.com/watch?v=descartadoXY"
            ledger.save_many("fixture", [ledger.add(done), ledger.add(leftover)])
            payload = self.call("status", "--project", tmp)
            self.assertIn("completo", payload["summary"]["next"])
            # O descarte vira aparte com a saída dita, nunca o próximo passo.
            self.assertIn("1 candidato sem decisão", payload["summary"]["next"])
            self.assertIn("reject", payload["summary"]["next"])
            self.assertNotIn("prévia", payload["summary"]["next"])

    def test_a_partial_approval_does_not_close_the_flow(self):
        """Aprovar 2 de 5 e entregar não é "acabou": 3 prévias seguem sem decisão."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            items = []
            for index in range(2):
                done = candidate("local", f"ok{index}", f"Entregue {index}")
                set_segment(done, 0, 2)
                done["preview"]["gif_path"] = f"previews/ok{index}.gif"
                done["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1}
                done["rights"]["status"] = "permitted"
                done["rights"]["evidence"] = ["Evidência sintética"]
                done["output"] = {"path": f"clips/ok{index}.mp4", "sha256": "0" * 64, "verified": True}
                done["delivery"] = {"path": f"entrega/00-ok{index}/ok{index}.mp4", "method": "hardlink"}
                done["state"] = "verified"
                items.append(done)
            for index in range(3):
                waiting = candidate("local", f"espera{index}", f"Esperando {index}")
                set_segment(waiting, 0, 2)
                waiting["preview"]["contact_sheet_path"] = f"previews/espera{index}.jpg"
                waiting["state"] = "awaiting_approval"
                items.append(waiting)
            ledger.save_many("fixture", [ledger.add(item) for item in items])
            payload = self.call("status", "--project", tmp)
            self.assertNotIn("completo", payload["summary"]["next"])
            self.assertIn("decisão humana", payload["summary"]["next"])
            self.assertEqual(
                3, next(s["count"] for s in payload["summary"]["stages"] if s["stage"] == "decisões pendentes")
            )

    def test_a_decision_waiting_on_the_human_outranks_more_previews(self):
        """Com prévia na mesa, o degrau é decidir — não gerar prévia de quem sobrou."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            waiting = candidate("local", "b", "Esperando decisão")
            set_segment(waiting, 0, 2)
            waiting["preview"]["gif_path"] = "previews/b.gif"
            leftover = candidate("youtube", "semquadro", "Sem prévia e sem duração")
            leftover["source_url"] = "https://www.youtube.com/watch?v=semquadroXY"
            ledger.save_many("fixture", [ledger.add(waiting), ledger.add(leftover)])
            payload = self.call("status", "--project", tmp)
            self.assertIn("decisão humana", payload["summary"]["next"])
            self.assertNotIn("Gere prévias", payload["summary"]["next"])

    def test_status_never_writes_to_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            root = Path(tmp) / "brolls"
            before = snapshot(root)
            self.call("status", "--project", tmp)
            self.call("status", "--project", tmp)
            self.assertEqual(before, snapshot(root))
            self.assertTrue(before, "fixture vazio não provaria nada")

    def test_status_on_missing_project_fails_without_creating_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "projeto-inexistente"
            done = subprocess.run(
                [sys.executable, str(CLI), "status", "--project", str(absent)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(2, done.returncode, done.stdout)
            self.assertIn("Projeto não encontrado", done.stderr)
            self.assertIn("nenhum arquivo foi criado", done.stderr)
            self.assertEqual([], list(Path(tmp).iterdir()), "status criou arquivos")

    def test_status_reports_pending_write_without_completing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            root = Path(tmp) / "brolls"
            pending = root / ".pending-transaction.json"
            pending.write_text(
                json.dumps({"data": {"schema_version": 1, "items": []}, "events": []}),
                encoding="utf-8",
            )
            before = snapshot(root)
            payload = self.call("status", "--project", tmp)
            self.assertEqual("pending", payload["journal"]["recovered_write"])
            self.assertIn("gravação interrompida", payload["summary"]["line"])
            self.assertTrue(pending.is_file(), "status concluiu a transação pendente")
            self.assertEqual(before, snapshot(root))
            self.assertEqual(4, payload["counts"]["candidates"])

    def test_status_answers_while_another_command_holds_the_lock(self):
        from getbrolls.runtime import project_lock

        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            with project_lock(tmp):
                payload = self.call("status", "--project", tmp)
            self.assertEqual(4, payload["counts"]["candidates"])

    def test_status_degrades_on_corrupt_journal_and_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            root = Path(tmp) / "brolls"
            (root / "events.jsonl").write_text("{não é json}\n", encoding="utf-8")
            (root / "references.json").write_text("[]", encoding="utf-8")
            payload = self.call("status", "--project", tmp)
            self.assertIsNone(payload["journal"]["last"])
            self.assertIn("events.jsonl", payload["journal"]["error"])
            self.assertEqual(0, payload["references"])
            self.assertIn("references.json", payload["references_error"])

    def test_status_warns_when_editorial_rules_changed_the_target_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            rules = Path(tmp) / "RULES.md"
            source = json.loads(
                (ROOT / "docs" / "RULES.md").read_text(encoding="utf-8").split("```json")[1].split("```")[0]
            )
            source["video_format"] = "reels"
            rules.write_text(
                "# Regras\n\n```json\n" + json.dumps(source) + "\n```\n",
                encoding="utf-8",
            )
            payload = self.call("status", "--project", tmp)
            self.assertEqual(4, payload["format_pending"])
            self.assertIsNone(payload["rules_error"])
            self.assertTrue(all(item["format_pending"] for item in payload["items"]))
            self.assertIn("regras editoriais mudaram", payload["summary"]["next"])

    def test_status_reports_broken_rules_instead_of_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture(tmp)
            (Path(tmp) / "RULES.md").write_text("sem bloco json", encoding="utf-8")
            payload = self.call("status", "--project", tmp)
            self.assertIn("RULES.md", payload["rules_error"])
            self.assertEqual(0, payload["format_pending"])
            self.assertIsNone(payload["items"][0]["format_pending"])
            self.assertEqual(4, payload["counts"]["candidates"])


class BoardUrlTests(unittest.TestCase):
    """A URL do board sai do servidor real; `serve --background` usa porta livre."""

    def project(self, tmp, *, pid_record=None):
        ledger = Ledger(tmp)
        item = candidate("local", "d", "Com prévia")
        set_segment(item, 0, 1)
        item["preview"]["gif_path"] = "previews/d.gif"
        ledger.save_many("fixture", [ledger.add(item)])
        (ledger.root / "review.html").write_text("<html></html>", encoding="utf-8")
        if pid_record is not None:
            (ledger.root / ".serve.pid").write_text(json.dumps(pid_record), encoding="utf-8")
        return ledger

    def do(self, ledger):
        """Mesmo `summary.do` do `status`, com o brief já dado como válido."""
        from getbrolls.commands import _flow_state
        from getbrolls.guidance import next_action

        brief = {"beats": 1, "covered": 1, "missing": [], "conflicts": []}
        return next_action(_flow_state(ledger, None, brief=brief))

    def test_without_a_pid_file_no_port_is_promised(self):
        with tempfile.TemporaryDirectory() as tmp:
            do = self.do(self.project(tmp))
            self.assertEqual("approve", do["step"])
            self.assertIsNone(do["url"])
            self.assertIn("serve --background", do["for_human"])
            self.assertNotIn("127.0.0.1", do["for_human"])

    def test_a_stale_pid_file_does_not_become_a_url(self):
        """PID morto: `serve.state` responde `running: False` e nada é prometido."""
        with tempfile.TemporaryDirectory() as tmp:
            record = {"pid": 999999999, "port": 57114, "session": "x", "urls": []}
            do = self.do(self.project(tmp, pid_record=record))
            self.assertIsNone(do["url"])
            self.assertNotIn("57114", do["for_human"])

    def test_a_running_server_lends_its_own_port(self):
        from unittest import mock

        from getbrolls import serve

        with tempfile.TemporaryDirectory() as tmp:
            record = {"pid": 4321, "port": 57219, "session": "s", "urls": []}
            ledger = self.project(tmp, pid_record=record)
            live = {
                "running": True,
                "pid": 4321,
                "port": 57219,
                "urls": [
                    "http://localhost:57219/review.html",
                    "http://127.0.0.1:57219/review.html",
                ],
                "pid_file": str(ledger.root / ".serve.pid"),
            }
            with mock.patch.object(serve, "state", return_value=live):
                do = self.do(ledger)
            self.assertEqual("http://127.0.0.1:57219/review.html", do["url"])
            self.assertIn("http://127.0.0.1:57219/review.html", do["for_human"])
            self.assertNotIn("8767", do["for_human"])

    def test_asking_the_server_never_writes_to_the_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = {"pid": 999999999, "port": 57114, "session": "x", "urls": []}
            ledger = self.project(tmp, pid_record=record)
            before = snapshot(ledger.root)
            self.do(ledger)
            self.do(ledger)
            self.assertEqual(before, snapshot(ledger.root))


class ProgressSummaryTests(unittest.TestCase):
    def test_every_flow_command_declares_a_one_line_summary(self):
        for command in (
            "search",
            "resolve",
            "preview",
            "approve",
            "reject",
            "review",
            "import-review",
            "permit",
            "fetch",
            "verify",
            "status",
        ):
            self.assertIn(command, FLOW_SUMMARIES, command)

    def test_summary_is_additive_and_keeps_existing_keys(self):
        result = {"items": [{"id": "a"}], "errors": [], "excluded_by_rules": 2}
        enriched = with_summary("search", result)
        self.assertEqual(result, {k: v for k, v in enriched.items() if k != "summary"})
        self.assertNotIn("summary", result, "o resultado original não pode ser mutado")
        self.assertIn("1 registrado", enriched["summary"]["line"])
        self.assertIn("2 excluídos pelas regras", enriched["summary"]["line"])

    def test_summary_lines_describe_verb_object_and_result(self):
        lines = {
            "preview": with_summary(
                "preview",
                {"id": "local:a", "state": "awaiting_approval", "approval": {"status": "pending"}},
            )["summary"]["line"],
            "fetch": with_summary("fetch", {"id": "local:a", "output": {"path": "clips/a.mp4"}})["summary"]["line"],
            "verify": with_summary("verify", {"verified": [], "count": 2})["summary"]["line"],
            "permit": with_summary("permit", {"id": "local:a", "rights": {"status": "permitted"}})["summary"]["line"],
            "import-review": with_summary("import-review", {"imported": 3, "by": "Revisor"})["summary"]["line"],
        }
        self.assertIn("Gerei a prévia de local:a", lines["preview"])
        self.assertIn("clips/a.mp4", lines["fetch"])
        self.assertIn("2 arquivos coletados", lines["verify"])
        self.assertIn("permitted", lines["permit"])
        self.assertIn("3 decisões", lines["import-review"])

    def test_unknown_and_already_summarised_results_are_untouched(self):
        self.assertEqual({"a": 1}, with_summary("doctor", {"a": 1}))
        self.assertEqual({"summary": {"line": "x"}}, with_summary("status", {"summary": {"line": "x"}}))

    def test_search_summary_keeps_the_provider_note(self):
        enriched = with_summary(
            "search",
            {"items": [], "errors": [], "note": "APIs atuais pesquisam vídeos."},
        )
        self.assertIn("0 registrados", enriched["summary"]["line"])
        self.assertIn("APIs atuais pesquisam vídeos.", enriched["summary"]["line"])

    def test_reference_only_preview_says_it_generated_static_reference(self):
        line = with_summary(
            "preview",
            {"id": "local:a", "state": "reference_only", "approval": {"status": "pending"}},
        )["summary"]["line"]
        self.assertIn("somente a referência estática", line)

    def test_singular_and_plural_agree_with_the_counts(self):
        self.assertIn(
            "1 arquivo coletado: íntegro e decodificável",
            with_summary("verify", {"verified": [], "count": 1})["summary"]["line"],
        )
        self.assertIn(
            "2 arquivos coletados: íntegros e decodificáveis",
            with_summary("verify", {"verified": [], "count": 2})["summary"]["line"],
        )
        line = FLOW_SUMMARIES["status"]({"counts": {"candidates": 1, "previews": 2}})
        self.assertIn("1 candidato encontrado", line)
        self.assertIn("2 prévias geradas", line)

    def test_approve_and_reject_have_their_own_summary(self):
        self.assertIn(
            "Registrei a aprovação humana de local:a",
            with_summary(
                "approve",
                {"id": "local:a", "state": "approved", "approval": {"by": "Humano"}},
            )["summary"]["line"],
        )
        rejected = with_summary("reject", {"id": "local:a", "state": "rejected"})["summary"]
        assert isinstance(rejected, dict)
        self.assertIn("Rejeitei local:a", rejected["line"])


class EverySummaryIsAnObjectWithALine(unittest.TestCase):
    """Uma leitura só serve para todo comando: `summary.line`."""

    SAMPLES: ClassVar = {
        "search": {"items": [], "errors": [], "excluded_by_rules": 0},
        "resolve": {"id": "local:a", "state": "registered"},
        "preview": {"id": "local:a", "state": "awaiting_approval", "approval": {"status": "pending"}},
        "approve": {"id": "local:a", "state": "approved", "approval": {"by": "Humano"}},
        "reject": {"id": "local:a", "state": "rejected"},
        "review": {"items": 2, "path": "brolls/review.html"},
        "import-review": {"imported": 1, "by": "Revisor"},
        "permit": {"id": "local:a", "rights": {"status": "permitted"}},
        "fetch": {"id": "local:a", "output": {"path": "clips/a.mp4"}},
        "verify": {"verified": [], "count": 1},
        "status": {"counts": {"candidates": 1}},
    }

    def test_every_flow_command_answers_with_a_dict_carrying_line(self):
        for command in FLOW_SUMMARIES:
            with self.subTest(command=command):
                summary = with_summary(command, dict(self.SAMPLES.get(command, {})))["summary"]
                self.assertIsInstance(summary, dict, f"{command} devolveu summary fora de objeto")
                self.assertIn("line", summary)
                self.assertTrue(str(summary["line"]).strip())

    def test_a_command_that_already_built_its_own_summary_is_left_alone(self):
        self.assertEqual({"summary": {"line": "x"}}, with_summary("status", {"summary": {"line": "x"}}))


class StepCandidateTests(unittest.TestCase):
    """Cada degrau nomeia um item da própria etapa, nunca um rejeitado da frente."""

    def project(self, tmp):
        ledger = Ledger(tmp)
        # Chamariz: rejeitado, primeiro na ordem do manifesto. Era ele que saía como
        # `--candidate` do `permit`, e a CLI recusava o comando que a escada entregou.
        decoy = candidate("local", "rejeitado", "Descartado")
        set_segment(decoy, 0, 1)
        decoy["preview"]["contact_sheet_path"] = "previews/r.jpg"
        decoy["approval"]["status"] = "rejected"
        decoy["state"] = "rejected"

        sem_previa = candidate("local", "sem-previa", "Sem quadro")
        set_segment(sem_previa, 0, 1)
        sem_previa["source_url"] = "https://exemplo.test/video"

        pendente = candidate("local", "pendente", "Esperando decisão")
        set_segment(pendente, 0, 1)
        pendente["preview"]["contact_sheet_path"] = "previews/p.jpg"

        aprovado = candidate("local", "aprovado", "Aprovado sem permit")
        set_segment(aprovado, 0, 1)
        aprovado["preview"]["gif_path"] = "previews/a.gif"
        aprovado["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1}

        permitido = candidate("local", "permitido", "Permitido sem corte")
        set_segment(permitido, 0, 1)
        permitido["preview"]["gif_path"] = "previews/pe.gif"
        permitido["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1}
        permitido["rights"]["status"] = "permitted"

        coletado = candidate("local", "coletado", "Coletado sem verify")
        set_segment(coletado, 0, 1)
        coletado["preview"]["gif_path"] = "previews/co.gif"
        coletado["approval"] = {"status": "approved", "by": "Humano", "at": now(), "revision": 1}
        coletado["rights"]["status"] = "permitted"
        coletado["output"] = {"path": "clips/co.mp4", "sha256": "0" * 64, "verified": False}

        order = (decoy, sem_previa, pendente, aprovado, permitido, coletado)
        ledger.save_many("fixture", [ledger.add(item) for item in order])
        return ledger

    def test_each_rung_names_an_item_of_its_own_stage(self):
        from getbrolls.commands import _step_candidates

        with tempfile.TemporaryDirectory() as tmp:
            chosen = _step_candidates(self.project(tmp).data["items"])
        self.assertEqual(
            {
                "inspect": "local:sem-previa",
                "preview": "local:sem-previa",
                "approve": "local:pendente",
                "permit": "local:aprovado",
                "fetch": "local:permitido",
                "verify": "local:coletado",
            },
            chosen,
        )

    def test_no_rung_ever_names_the_rejected_decoy(self):
        from getbrolls.commands import _step_candidates

        with tempfile.TemporaryDirectory() as tmp:
            chosen = _step_candidates(self.project(tmp).data["items"])
        for step, ident in chosen.items():
            with self.subTest(step=step):
                self.assertNotEqual("local:rejeitado", ident)

    def test_the_permit_rung_command_carries_the_approved_item(self):
        """O bug real: `permit --candidate <rejeitado>` saía pronto no `status.do`."""
        from getbrolls.commands import _flow_state
        from getbrolls.guidance import next_action

        brief = {"beats": 1, "covered": 1, "missing": [], "conflicts": []}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.project(tmp)
            # Descarta o que estava em aberto para a escada chegar ao degrau de permit;
            # os dois viram rejeitados e passam a disputar a frente da ordem com o chamariz.
            for item in ledger.data["items"]:
                if item["id"] in ("local:pendente", "local:sem-previa"):
                    item["approval"]["status"] = "rejected"
                    item["state"] = "rejected"
            ledger.save_many("decide", ledger.data["items"])
            action = next_action(_flow_state(ledger, None, brief=brief))
        self.assertEqual("permit", action["step"])
        self.assertIn("--candidate local:aprovado", action["command"])
        self.assertNotIn("local:rejeitado", action["command"])

    def test_the_approve_rung_command_approves_instead_of_starting_a_server(self):
        """Degrau e comando têm que nomear a mesma ação."""
        from getbrolls.commands import _flow_state
        from getbrolls.guidance import next_action

        brief = {"beats": 1, "covered": 1, "missing": [], "conflicts": []}
        with tempfile.TemporaryDirectory() as tmp:
            ledger = self.project(tmp)
            (ledger.root / "review.html").write_text("<html></html>", encoding="utf-8")
            action = next_action(_flow_state(ledger, None, brief=brief))
        self.assertEqual("approve", action["step"])
        self.assertIn(" approve --project", action["command"])
        self.assertIn("--candidate local:pendente", action["command"])
        self.assertNotIn(" serve ", action["command"])
        # A rota do board continua oferecida, mas na frase e na url, não no comando.
        self.assertIn("Storyboard", action["for_human"])


if __name__ == "__main__":
    unittest.main()

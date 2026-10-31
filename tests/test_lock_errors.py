"""Trava ocupada é `LockedError` (LOCKED, saída 1), não erro de uso nem de dados.

O store de perfis confiáveis e o índice da biblioteca pessoal entram na mesma família
das outras travas (projeto, analysis/, runtime, plugins, clientes, templates).
"""

import argparse
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _paths  # noqa: F401  (efeito de import: põe scripts/ no sys.path)  # pylint: disable=unused-import
from _cli import run_cli

from getbrolls import library, profile, profile_trust, runtime
from getbrolls.errors import LockedError


class LockedErrorTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-locks-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def test_busy_trust_store_is_locked_error(self):
        with (
            patch.object(profile_trust, "LOCK_TIMEOUT_S", 0.0),
            patch.object(runtime, "_acquire_lock", side_effect=BlockingIOError),
            self.assertRaises(LockedError) as raised,
        ):
            profile._update_store(self.home, lambda _profiles: False)  # pylint: disable=protected-access
        self.assertIn(profile.TRUST_FILE, str(raised.exception))

    def test_busy_library_index_is_locked_error(self):
        with patch.dict(os.environ, {"GB_HOME": str(self.home)}):
            lock = library.library_dir() / "index.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("", encoding="utf-8")
            with (
                patch.object(library, "LOCK_TIMEOUT_S", 0.0),
                self.assertRaises(LockedError) as raised,
                library._locked(),  # pylint: disable=protected-access
            ):
                pass
        self.assertIn("index.lock", str(raised.exception))
        self.assertTrue(lock.exists(), "a trava de outro processo não é apagada")


class MissingProjectTests(unittest.TestCase):
    def test_stage_command_on_a_missing_project_creates_nothing(self):
        # Um typo no `--project` de um comando de etapa não pode criar um projeto vazio
        # (nem `brolls/`, nem a trava, nem o log) só para recusar o candidato depois.
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "naoexiste"
            error = run_cli("approve", "--candidate", "x", "--by", "Ana Teste", project=missing, expect=1)
            self.assertEqual("INVALID_DATA", error["error_code"])
            self.assertIn("Projeto não encontrado em", error["error"])
            self.assertIn("nenhum arquivo foi criado", error["error"])
            self.assertFalse(missing.exists())

    def test_every_stage_command_refuses_a_folder_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "pasta"
            for command in runtime.STAGE_COMMANDS:
                with self.subTest(command=command):
                    with self.assertRaises(runtime.OperationError) as raised:
                        runtime.audited(
                            argparse.Namespace(command=command, project=str(missing)),
                            lambda _args: self.fail("o comando não pode rodar"),
                        )
                    self.assertIn("Projeto não encontrado em", raised.exception.payload["message"])
                    self.assertFalse(missing.exists())

    def test_a_stage_command_still_runs_in_an_existing_folder_without_brolls(self):
        # Só o typo (pasta que não existe) é recusado: numa pasta que já existe, `review`
        # monta o Storyboard vazio e `verify`/`deliver` respondem com zero itens, como antes.
        for command in ("review", "verify", "deliver"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                run_cli(command, project=tmp)
                self.assertTrue((Path(tmp) / "brolls").is_dir())


class ConsultCommandTests(unittest.TestCase):
    CONSULTS = (("rules",), ("library", "--search", "drone"), ("references",))

    def test_consult_commands_take_no_lock_and_create_nothing(self):
        # `rules`, `library --search` e `references` só consultam: não podem esperar pela
        # trava de um `fetch` longo, nem criar `brolls/` numa pasta qualquer passada como --project.
        with tempfile.TemporaryDirectory() as tmp:
            busy = Path(tmp) / "ocupado"
            (busy / "brolls").mkdir(parents=True)
            fresh = Path(tmp) / "nova"
            fresh.mkdir()
            with runtime.project_lock(busy):
                for argv in self.CONSULTS:
                    with self.subTest(argv=argv, project="ocupado"):
                        run_cli(*argv, project=busy)
            for argv in self.CONSULTS:
                with self.subTest(argv=argv, project="nova"):
                    run_cli(*argv, project=fresh)
            self.assertEqual([], list(fresh.iterdir()))

    def test_references_leaves_the_pending_write_of_the_lock_holder_alone(self):
        # Sem a trava, `references` não pode concluir o journal de um `approve` ou `fetch` que
        # ainda está gravando: o journal fica para quem o escreveu, e o manifesto não muda.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "ocupado"
            brolls = project / "brolls"
            brolls.mkdir(parents=True)
            pending = brolls / ".pending-transaction.json"
            journal = {
                "data": {"schema_version": 1, "items": []},
                "events": [{"transaction": "t:0", "at": "x", "operation": "approve", "id": None}],
            }
            pending.write_text(json.dumps(journal), encoding="utf-8")
            with runtime.project_lock(project):
                result = run_cli("references", project=project)
            self.assertTrue(pending.exists(), "o journal é de quem segura a trava")
            self.assertFalse((brolls / "manifest.json").exists())
            codes = [warning.get("code") for warning in result.get("warnings", [])]
            self.assertNotIn("RECOVERED_WRITE", codes)


if __name__ == "__main__":
    unittest.main()

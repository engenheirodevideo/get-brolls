"""Uma rodada de verdade por área deixa no getbrolls.log as linhas `event=op` que a área promete.

Cada teste roda o comando real (a CLI num subprocesso, com `GB_HOME` temporário) e confere só a
estrutura: as operações esperadas estão contidas nas que o comando registrou (subconjunto, nunca
a lista inteira nem a ordem) e toda linha `event=op` segue o esquema de `getbrolls.effects`. Nenhum
texto de mensagem é conferido.

O ouro da 2.5 (`tests/fixtures/golden_v25/expected`) guarda as linhas do getbrolls.log de cada
caso: o esquema de toda linha `event=op` gravada ali também é conferido.
"""

import json
import os
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _golden
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _logs import events, home_log_lines
from _media import skip_unless_ffmpeg
from _paths import ROOT
from test_export_cli import ExportCase
from test_logging_trail import APPROVAL_STATEMENT, APPROVER_NAME, EVIDENCE_TEXT, _log_path, _project

from getbrolls import effects

# Campos que toda linha do getbrolls.log tem além dos do evento.
LINE_FIELDS = {"level", "logger", "event"}
SERVE_WAIT_S = 20.0


def op_rows(lines):
    """As linhas `event=op`, como dicionários."""
    return events(lines, "op")


def schema_problems(row):
    """O que a linha `event=op` tem de errado contra o esquema de `effects` (lista vazia: nada)."""
    problems = []
    missing = set(effects.FIELDS) - set(row)
    if missing:
        problems.append(f"faltam {sorted(missing)}")
    extra = set(row) - set(effects.FIELDS) - effects.EXTRAS - LINE_FIELDS
    if extra:
        problems.append(f"chaves fora do esquema {sorted(extra)}")
    if row.get("operation") not in effects.OPERATIONS:
        problems.append(f"operação desconhecida {row.get('operation')!r}")
    if row.get("outcome") not in effects.OUTCOMES:
        problems.append(f"outcome {row.get('outcome')!r}")
    if not re.fullmatch(r"\d+|<MS>", str(row.get("duration_ms"))):
        problems.append(f"duration_ms {row.get('duration_ms')!r}")
    failed = row.get("outcome") in ("error", "refused")
    if failed != (row.get("error_code") is not None) or failed != (row.get("error") is not None):
        problems.append("error_code/error só (e sempre) numa falha")
    target = str(row.get("target") or "")
    if target.startswith(("/", "http:", "https:")) or re.match(r"^[A-Za-z]:[\\/]", target) or "?" in target:
        problems.append(f"alvo sem redação {target!r}")
    return problems


class SchemaMixin(unittest.TestCase):
    def assert_schema(self, rows):
        self.assertTrue(rows, "nenhuma linha event=op")
        problems = {f"{row.get('operation')} {row.get('target')}": schema_problems(row) for row in rows}
        self.assertEqual({}, {key: value for key, value in problems.items() if value})

    def assert_ops(self, rows, expected, command=None):
        seen = {row["operation"] for row in rows if command is None or row.get("command") == command}
        self.assertLessEqual(set(expected), seen, f"{command}: {sorted(seen)}")


@skip_unless_ffmpeg
class FetchDeliverTests(SchemaMixin):
    def test_fetch_and_deliver_log_their_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = _project(tmp)
            base = ["--project", str(root)]
            cid = run_cli("resolve", "--file", str(src), *base)["id"]
            candidate = ["--candidate", cid, *base]
            run_cli("preview", *candidate, "--start", 0, "--end", 1)
            run_cli("approve", *candidate, "--by", APPROVER_NAME, "--statement", APPROVAL_STATEMENT)
            run_cli("permit", *candidate, "--evidence", EVIDENCE_TEXT)
            run_cli("fetch", *candidate)
            run_cli("deliver", *base)
            rows = op_rows(_log_path(root).read_text(encoding="utf-8").splitlines())
        self.assert_schema(rows)
        self.assert_ops(rows, {"proc.run", "fs.replace", "fs.write", "ledger.commit"}, command="fetch")
        self.assert_ops(rows, {"deliver.place", "fs.mkdir", "ledger.commit"}, command="deliver")
        placed = {row["operation"] for row in rows if row.get("command") == "deliver"}
        self.assertTrue(placed & {"fs.link", "fs.copy"}, placed)
        # Projeto em pasta temporária: o alvo é relativo a ele, nunca o caminho absoluto.
        self.assertTrue(all(not str(row["target"]).startswith(str(root)) for row in rows))


class ExportTests(ExportCase, SchemaMixin):
    def test_export_logs_each_placed_media_and_the_promotion(self):
        self.install()
        self.export()
        rows = op_rows(_log_path(self.project).read_text(encoding="utf-8").splitlines())
        self.assert_schema(rows)
        self.assert_ops(rows, {"export.place", "fs.write", "fs.rename", "fs.mkdir", "plugin.load"}, command="export")
        places = [row for row in rows if row["operation"] == "export.place"]
        self.assertTrue(all(row["outcome"] == "ok" and row["target"].startswith("exports/") for row in places))


class PluginInstallTests(SchemaMixin):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="gb-effects-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        env = mock.patch.dict(os.environ, {"GB_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GB_PLUGINS", None)

    def test_install_and_remove_log_to_the_installation_log(self):
        source = Path(tempfile.mkdtemp(prefix="gb-effects-source-")) / "pasta_local"
        self.addCleanup(shutil.rmtree, source.parent, ignore_errors=True)
        shutil.copytree(ROOT / "examples" / "plugins" / "pasta_local", source)
        plugin_id = json.loads((source / "getbrolls-plugin.json").read_text(encoding="utf-8"))["id"]
        sha = run_cli("plugins", "--action", "install", "--source", source)["plugin"]["sha256"]
        run_cli("plugins", "--action", "install", "--source", source, "--yes", "--expect", sha)
        run_cli("plugins", "--action", "remove", "--id", plugin_id, "--yes")
        rows = op_rows(home_log_lines(self.home))
        self.assert_schema(rows)
        self.assert_ops(rows, {"plugin.install", "fs.copy", "fs.replace", "fs.write"}, command="plugins")
        self.assert_ops(rows, {"plugin.remove", "fs.rmtree"}, command="plugins")
        installed = [row for row in rows if row["operation"] in ("plugin.install", "plugin.remove")]
        self.assertEqual([plugin_id, plugin_id], [row["target"] for row in installed])
        self.assertTrue(all(row["target"].startswith("$GB_HOME") for row in rows if "/" in str(row["target"])))


class MigrateTests(SchemaMixin):
    def test_apply_logs_the_migration_and_its_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "video-01"
            (project / "brolls" / "clips").mkdir(parents=True)
            manifest = {"schema_version": 1, "project_id": "4f6c1a7e-1b8f-4a9e-9a43-2a5f0e8d7c11", "items": []}
            (project / "brolls" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            run_cli("migrate", "--action", "apply", project=project)
            rows = op_rows(_log_path(project).read_text(encoding="utf-8").splitlines())
        self.assert_schema(rows)
        self.assert_ops(rows, {"migrate.apply", "fs.write"}, command="migrate")
        self.assertIn("project.json", {row["target"] for row in rows if row["operation"] == "migrate.apply"})


class ServeTests(SchemaMixin):
    def test_background_start_and_stop_log_the_server_lifecycle(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "proj"
            (project / "brolls").mkdir(parents=True)
            (project / "brolls" / "review.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
            log = _log_path(project)
            started = run_cli("serve", "--background", project=project)
            try:
                # `server.start` vem do processo do servidor, que pode escrever depois do pai responder.
                deadline = time.monotonic() + SERVE_WAIT_S
                while time.monotonic() < deadline and not any(
                    row["operation"] == "server.start" for row in op_rows(log.read_text(encoding="utf-8").splitlines())
                ):
                    time.sleep(0.05)
            finally:
                stopped = run_cli("serve", "--stop", project=project)
            self.assertTrue(started["background"])
            self.assertTrue(stopped["stopped"])
            rows = op_rows(log.read_text(encoding="utf-8").splitlines())
        self.assert_schema(rows)
        self.assert_ops(rows, {"proc.spawn", "fs.write", "server.start"})
        self.assert_ops(rows, {"server.stop", "proc.kill", "fs.unlink"}, command="serve")
        spawned = next(row for row in rows if row["operation"] == "proc.spawn")
        self.assertEqual(str(started["pid"]), spawned["pid"])

    def test_a_stop_with_nothing_to_stop_writes_no_log(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "proj"
            (project / "brolls").mkdir(parents=True)
            run_cli("serve", "--stop", project=project)
            self.assertFalse(_log_path(project).exists())


class GoldenSchemaTests(SchemaMixin):
    """Toda linha `event=op` que o ouro gravou (dezenas de comandos reais) segue o esquema."""

    def test_every_op_line_recorded_in_the_golden_follows_the_schema(self):
        rows = []
        for path in sorted(_golden.EXPECTED.glob("*/*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            for rel, lines in record["appends"].items():
                if rel.endswith("getbrolls.log"):
                    rows += op_rows(lines)
        self.assert_schema(rows)


if __name__ == "__main__":
    unittest.main()

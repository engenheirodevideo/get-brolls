"""Eventos de log do `setup`: cada passo, cada parte e cada recusa, com duração e sem saída de processo.

O `FakeRunner` do teste de instalação toma o lugar de `bootstrap_steps.run_step`: nada abre processo nem
fala com a rede. Os testes conferem os eventos e que nada sensível chegou à linha: caminho da máquina,
linha de comando, chave de API, token ou a saída do pip e do npm.
"""

import logging
import os
import shutil
import sys
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _logs import LogCase
from _symlink import needs_symlink
from test_setup_install import WINDOWS, FakeRunner, _Setup, _touch

from getbrolls import bootstrap, file_locks, runtime_parts
from getbrolls.errors import LockedError, UsageError


class SetupLogTests(_Setup, LogCase):
    def setUp(self):
        super().setUp()
        # O `_Setup` usa "k" como chave de teste: a redação trocaria todo `k` do log por [REDACTED].
        os.environ["PEXELS_API_KEY"] = "SEGREDO-PEXELS-CHAVE-123"
        os.environ["X_TOKEN"] = "SEGREDO-X-TOKEN-456"

    def fresh(self):
        """Runtime vazio e um `FakeRunner` novo: cada caso de uma lista parte do zero."""
        shutil.rmtree(self.home, ignore_errors=True)
        self.runner = FakeRunner()
        self.found.update(npm=self.npm, node=str(self.base / "bin" / ("node.exe" if WINDOWS else "node")))

    def logged_install(self, **kwargs):
        with self.captured() as cm:
            result = self.install(**kwargs)
        return result, self.rows(cm)

    def test_a_setup_logs_each_step_and_each_part_with_durations_and_no_command_line(self):
        _result, rows = self.logged_install()
        started = [row["step"] for row in rows.all("setup_step_start")]
        self.assertEqual(["venv", "pip-bootstrap", "pip", "probe", "version", "node", "npm", "playwright"], started)
        ended = rows.all("setup_step_end")
        self.assertEqual(started, [row["step"] for row in ended])
        for row in ended:
            self.assertEqual({"status": "ok", "exit": "0"}, {key: row[key] for key in ("status", "exit")})
            self.assertTrue(row["ms"].isdigit())
        self.assertEqual({"kind": "pip"}, {"kind": rows.one("setup_step_start", step="pip")["kind"]})
        venv = rows.one("setup_part", part="venv")
        self.assertEqual(
            {"status": "installed", "version": "2026.8.19"}, {key: venv[key] for key in ("status", "version")}
        )
        tools = rows.one("setup_part", part="tools")
        self.assertEqual(
            {"status": "installed", "version": "0.1.21"}, {key: tools[key] for key in ("status", "version")}
        )
        done = rows.one("setup_done")
        self.assertEqual({"venv": "installed", "tools": "installed"}, {key: done[key] for key in ("venv", "tools")})
        self.assertTrue(done["ms"].isdigit())
        self.assert_clean(rows, self.base, self.npm, "--ignore-scripts", "SEGREDO", "PEXELS", sys.executable)

    def test_a_failed_step_is_a_warning_with_the_reason_and_never_the_pip_output(self):
        self.runner.fail["pip"] = (
            1,
            ["WARNING: Retrying ... NewConnectionError: Failed to establish a new connection"],
        )
        _result, rows = self.logged_install()
        end = rows.one("setup_step_end", step="pip")
        self.assertEqual({"status": "error", "exit": "1"}, {key: end[key] for key in ("status", "exit")})
        self.assertEqual(logging.WARNING, rows.level_of("setup_step_end", step="pip"))
        failed = rows.one("setup_part_failed", part="venv")
        self.assertEqual({"reason": "pip_network", "exit": "1"}, {key: failed[key] for key in ("reason", "exit")})
        self.assertEqual("failed", rows.one("setup_part", part="venv")["status"])
        self.assertEqual(logging.WARNING, rows.level_of("setup_part", part="venv"))
        self.assertEqual("False", rows.one("setup_done")["ready"])
        self.assert_clean(rows, "NewConnectionError", "Retrying")

    def test_the_other_failure_reasons_are_distinct(self):
        cases = (
            ("venv", (1, ["Error: ensurepip is not available"]), "venv", "ensurepip_missing"),
            (
                "pip",
                (1, ["ERROR: Package 'x' requires a different Python: 3.10.0 not in '>=3.11'"]),
                "venv",
                "pip_python_version",
            ),
            ("pip", (1, ["ERROR: ResolutionImpossible"]), "venv", "pip_conflict"),
            ("pip", (1, ["boom"]), "venv", "pip_failed"),
            ("pip", (bootstrap.TIMED_OUT, ["baixando"]), "venv", "timeout"),
            ("probe", (1, ["ModuleNotFoundError"]), "venv", "probe_failed"),
            ("npm", (1, ["npm ERR!"]), "tools", "npm_failed"),
            ("npm", (bootstrap.TIMED_OUT, ["x"]), "tools", "timeout"),
        )
        for label, failure, part, reason in cases:
            with self.subTest(label=label, reason=reason):
                self.fresh()
                self.runner.fail = {label: failure}
                _result, rows = self.logged_install()
                self.assertEqual(reason, rows.one("setup_part_failed", part=part)["reason"])

    def test_the_venv_health_probe_is_a_debug_line_with_its_duration(self):
        self.install()
        with self.captured() as cm:
            bootstrap.venv_health(self.root() / ".venv")
        rows = self.rows(cm)
        row = rows.one("setup_health")
        self.assertEqual({"status": "ok", "exit": "0"}, {key: row[key] for key in ("status", "exit")})
        self.assertTrue(row["ms"].isdigit())
        self.assertEqual(logging.DEBUG, rows.level_of("setup_health"))
        self.runner.health = 1
        with self.captured() as cm:
            bootstrap.venv_health(self.root() / ".venv")
        self.assertEqual("error", self.rows(cm).one("setup_health")["status"])

    def test_a_timeout_is_its_own_step_status(self):
        self.runner.fail["npm"] = (bootstrap.TIMED_OUT, ["x"])
        _result, rows = self.logged_install()
        end = rows.one("setup_step_end", step="npm")
        self.assertEqual(
            {"status": "timeout", "exit": str(bootstrap.TIMED_OUT)}, {key: end[key] for key in ("status", "exit")}
        )

    def test_an_interrupted_step_is_logged_before_the_interrupt_propagates(self):
        self.runner.interrupt = "pip"
        with self.captured() as cm, self.assertRaises(KeyboardInterrupt):
            self.install()
        end = self.rows(cm).one("setup_step_end", step="pip")
        self.assertEqual({"status": "interrupted", "exit": None}, {key: end[key] for key in ("status", "exit")})

    def test_missing_node_or_npm_or_an_old_node_skip_the_tools_with_a_reason(self):
        for found, node, reason in (
            ({"npm": None}, "v22.11.0", "npm_missing"),
            ({"node": None, "npm": None}, "v22.11.0", "node_missing"),
            ({}, "v18.0.0", "node_too_old"),
        ):
            with self.subTest(reason=reason):
                self.fresh()
                self.found.update(found)
                self.runner.node = node
                _result, rows = self.logged_install()
                self.assertEqual(reason, rows.one("setup_part_skipped", part="tools")["reason"])
                self.assertEqual("skipped", rows.one("setup_part", part="tools")["status"])

    def test_the_upgrade_is_logged_with_the_versions(self):
        self.install()
        self.runner.upgrade_to = "2026.9.1"
        _result, rows = self.logged_install(upgrade="ytdlp")
        row = rows.one("setup_upgrade")
        self.assertEqual(
            {"status": "upgraded", "previous": "2026.8.19", "version": "2026.9.1"},
            {key: row[key] for key in ("status", "previous", "version")},
        )
        self.assertEqual("upgraded", rows.one("setup_done")["upgrade"])

    def test_a_pinned_ytdlp_refuses_the_upgrade_with_its_reason_and_error_code(self):
        os.environ["GB_YTDLP_PATH"] = sys.executable
        with self.captured() as cm, self.assertRaises(UsageError):
            self.install(upgrade="ytdlp")
        row = self.rows(cm).one("setup_refused", reason="upgrade_pinned")
        self.assertEqual("USAGE_ERROR", row["error_code"])

    def test_a_locked_runtime_logs_the_busy_lock(self):
        root = self.root()
        root.mkdir(parents=True)
        with (root / bootstrap.LOCK_NAME).open("a+", encoding="utf-8") as held:
            file_locks._acquire_lock(held)  # pylint: disable=protected-access
            try:
                with self.captured() as cm, self.assertRaises(LockedError):
                    self.install()
            finally:
                file_locks._release_lock(held)  # pylint: disable=protected-access
        row = self.rows(cm).one("lock_busy")
        self.assertEqual(
            {"lock": bootstrap.LOCK_NAME, "error_code": "LOCKED"}, {key: row[key] for key in ("lock", "error_code")}
        )

    @needs_symlink
    def test_a_runtime_part_that_is_a_link_is_refused_by_reason(self):
        target = self.base / "elsewhere"
        _touch(target / "keep")
        root = self.root()
        root.mkdir(parents=True)
        (root / ".venv").symlink_to(target, target_is_directory=True)
        runtime_parts.write_marker("venv", root, "building")
        with self.captured() as cm, self.assertRaises(UsageError):
            self.install()
        rows = self.rows(cm)
        self.assertEqual("USAGE_ERROR", rows.one("setup_refused", reason="not_a_folder")["error_code"])
        self.assert_clean(rows, "elsewhere", self.base)
        self.assertTrue((target / "keep").exists())


if __name__ == "__main__":
    unittest.main()

"""Regression coverage for the queue-pacing review findings: load() integrity checks,
claim_next/mark contracts, hint()'s min-over-free-providers, corrupted-timestamp
propagation, RULES.md pacing reaching the queue, and `queue --action status` staying
read-only.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import CLI, ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import commands, queue
from getbrolls.runtime import READ_ONLY_ACTIONS, audited, project_lock

T0 = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
REEL = "https://www.instagram.com/reel/ABC123xyz/"
REEL_2 = "https://www.instagram.com/reel/DEF456uvw/"
TIKTOK = "https://www.tiktok.com/@user/video/1234567890"


def rng(value=0.0):
    import random

    generator = random.Random(1)
    generator.uniform = lambda a, b: a + value * (b - a)
    return generator


def clean_env():
    return {key: value for key, value in os.environ.items() if not key.startswith(("GB_PACE_", "GB_MAX_PER_"))}


VALID_RULES = textwrap.dedent(
    """\
    ```json
    {
      "version": 1,
      "asset_types": ["video"],
      "video_format": "native",
      "preferred_providers": {"literal": ["youtube"], "illustrative": []},
      "preferred_domains": [],
      "blocked_domains": [],
      "editorial_rules": [],
      "copyright": {"mode": "per_item_evidence", "responsible_person": null, "declaration": null},
      "browser": {"viewport": "mobile", "mobile_width": 390, "mobile_height": 844, "desktop_width": 1440, "desktop_height": 900, "full_page": false},
      "pacing": {"instagram": {"min_s": 1, "max_s": 2, "max_per_hour": 20, "max_per_day": 4}}
    }
    ```
    """
)


def queue_args(project, action, **extra):
    base = {
        "command": "queue",
        "project": str(project),
        "action": action,
        "provider": None,
        "urls": [],
        "url": None,
        "id": None,
        "done": False,
        "failed": False,
        "skipped": False,
        "reason": None,
        "env_file": None,
    }
    base.update(extra)
    return SimpleNamespace(**base)


class LoadIntegrityTests(unittest.TestCase):
    """Achado 1: `load()` chama `_check()` e recusa um queue.json inconsistente."""

    def _write(self, tmp, data):
        path = queue.queue_path(tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_duplicate_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = {
                "id": "instagram:ABC123xyz",
                "provider": "instagram",
                "url": REEL,
                "status": "pending",
                "added_at": T0.isoformat(),
                "started_at": None,
                "finished_at": None,
                "reason": None,
            }
            data = {"schema_version": 1, "items": [item, dict(item)], "providers": {}}
            path = self._write(tmp, data)
            with self.assertRaisesRegex(ValueError, "id duplicado"):
                queue.load(path)

    def test_active_item_without_started_at_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = {
                "id": "instagram:ABC123xyz",
                "provider": "instagram",
                "url": REEL,
                "status": "active",
                "added_at": T0.isoformat(),
                "started_at": None,
                "finished_at": None,
                "reason": None,
            }
            path = self._write(tmp, {"schema_version": 1, "items": [item], "providers": {}})
            with self.assertRaisesRegex(ValueError, "sem started_at"):
                queue.load(path)

    def test_more_than_one_active_item_per_provider_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:

            def active(id_):
                return {
                    "id": id_,
                    "provider": "instagram",
                    "url": REEL,
                    "status": "active",
                    "added_at": T0.isoformat(),
                    "started_at": T0.isoformat(),
                    "finished_at": None,
                    "reason": None,
                }

            data = {"schema_version": 1, "items": [active("a"), active("b")], "providers": {}}
            path = self._write(tmp, data)
            with self.assertRaisesRegex(ValueError, "mais de um item ativo"):
                queue.load(path)

    def test_done_item_without_finished_at_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = {
                "id": "instagram:ABC123xyz",
                "provider": "instagram",
                "url": REEL,
                "status": "done",
                "added_at": T0.isoformat(),
                "started_at": T0.isoformat(),
                "finished_at": None,
                "reason": None,
            }
            path = self._write(tmp, {"schema_version": 1, "items": [item], "providers": {}})
            with self.assertRaisesRegex(ValueError, "sem finished_at"):
                queue.load(path)

    def test_schema_version_from_a_newer_script_gets_a_distinct_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {"schema_version": 99, "items": [], "providers": {}})
            with self.assertRaisesRegex(ValueError, "versão mais nova"):
                queue.load(path)
            older = self._write(tmp, {"schema_version": 0, "items": [], "providers": {}})
            with self.assertRaisesRegex(ValueError, "incompatível"):
                queue.load(older)


class FinishedSinceCountsMissingAsNowTests(unittest.TestCase):
    """`_finished_since` não deve deixar um item sem finished_at escapar da janela."""

    def test_missing_finished_at_counts_as_now(self):
        data = queue.empty_state()
        data["items"].append(
            {
                "id": "instagram:x",
                "provider": "instagram",
                "url": REEL,
                "status": "done",
                "added_at": T0.isoformat(),
                "started_at": T0.isoformat(),
                "finished_at": None,
                "reason": None,
            }
        )
        finished = queue._finished_since(data, "instagram", T0 - timedelta(hours=1), now=T0)
        self.assertEqual([T0], finished)


class ClaimNextRenameTests(unittest.TestCase):
    """Achado 7: `claim_next` é o nome atual; `next_item` continua funcionando como alias."""

    def test_next_item_is_an_alias_of_claim_next(self):
        self.assertIs(queue.next_item, queue.claim_next)

    def test_report_and_hint_do_not_mutate_provider_state(self):
        # Achado 7: _provider_state fazia setdefault em caminhos somente leitura.
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        self.assertNotIn("instagram", data["providers"])
        queue.report(data, at=T0)
        self.assertNotIn("instagram", data["providers"], "report() não deve gravar estado do provedor")


class MarkTerminalTransitionTests(unittest.TestCase):
    """Achado 8: mark() recusa transição a partir de um estado final."""

    def setUp(self):
        patcher = patch.dict(os.environ, clean_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mark_done_twice_is_rejected_and_does_not_reset_strikes(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        got = queue.next_item(data, at=T0, rng=rng(0.0))
        queue.mark(data, got["item"]["id"], "failed", reason="HTTP 429 Too Many Requests", at=T0)
        strikes_before = data["providers"]["instagram"]["cooldown_strikes"]
        self.assertEqual(1, strikes_before)
        with self.assertRaisesRegex(ValueError, "estado final"):
            queue.mark(data, got["item"]["id"], "done", at=T0)
        self.assertEqual(strikes_before, data["providers"]["instagram"]["cooldown_strikes"])
        self.assertEqual("failed", got["item"]["status"])


class SkippedDoesNotConsumeQuotaTests(unittest.TestCase):
    """Achado 17: itens `skipped` não contam para os tetos de hora/dia."""

    def setUp(self):
        patcher = patch.dict(
            os.environ,
            {**clean_env(), "GB_MAX_PER_HOUR": "1", "GB_MAX_PER_DAY": "5", "GB_PACE_MIN_S": "0", "GB_PACE_MAX_S": "0"},
            clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_many_skips_never_open_the_hourly_hold(self):
        data = queue.empty_state()
        urls = [f"https://www.instagram.com/reel/SKP{i:03d}/" for i in range(5)]
        queue.add(data, "instagram", urls, at=T0)
        for index in range(5):
            got = queue.next_item(data, at=T0 + timedelta(seconds=index), rng=rng())
            self.assertIsNotNone(got["item"])
            queue.mark(data, got["item"]["id"], "skipped", at=T0 + timedelta(seconds=index))
        report = queue.report(data, at=T0 + timedelta(seconds=10))
        self.assertIsNone(report["providers"]["instagram"]["hold"])


class ActiveItemNextTests(unittest.TestCase):
    """Achado 18: next de item ativo devolve reason=active, wait 0, sem tocar timestamps."""

    def setUp(self):
        patcher = patch.dict(os.environ, clean_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_repeated_next_on_active_item_is_a_pure_read(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        first = queue.claim_next(data, at=T0, rng=rng(0.0))
        started_at = first["item"]["started_at"]
        next_allowed_at = data["providers"]["instagram"]["next_allowed_at"]
        again = queue.claim_next(data, at=T0 + timedelta(seconds=5), rng=rng(0.0))
        self.assertEqual("active", again["reason"])
        self.assertEqual(0, again["wait_seconds"])
        self.assertEqual(started_at, again["item"]["started_at"])
        self.assertEqual(next_allowed_at, data["providers"]["instagram"]["next_allowed_at"])

    def test_cli_next_does_not_rewrite_queue_json_when_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**clean_env(), "GB_PACE_MIN_S": "30", "GB_PACE_MAX_S": "30"}
            subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "queue",
                    "--project",
                    tmp,
                    "--action",
                    "add",
                    "--provider",
                    "instagram",
                    REEL,
                    "--url",
                    REEL_2,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=True,
            )
            subprocess.run(
                [sys.executable, str(CLI), "queue", "--project", tmp, "--action", "next"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=True,
            )
            path = Path(tmp) / "work/queue.json"
            before = (path.stat().st_mtime_ns, path.read_bytes())
            blocked = subprocess.run(
                [sys.executable, str(CLI), "queue", "--project", tmp, "--action", "next"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=True,
            )
            payload = json.loads(blocked.stdout)
            self.assertEqual("active", payload["reason"])
            self.assertEqual(before, (path.stat().st_mtime_ns, path.read_bytes()))


class MultiProviderClaimNextTests(unittest.TestCase):
    """Achado 16: instagram em cooldown não deve bloquear tiktok livre."""

    def setUp(self):
        patcher = patch.dict(os.environ, clean_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_free_provider_is_returned_while_another_is_in_cooldown(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        queue.add(data, "tiktok", [TIKTOK], at=T0)
        ig = queue.claim_next(data, provider="instagram", at=T0, rng=rng(0.0))
        queue.mark(data, ig["item"]["id"], "failed", reason="HTTP 429 Too Many Requests", at=T0)
        got = queue.claim_next(data, at=T0, rng=rng(0.0))
        self.assertIsNotNone(got["item"])
        self.assertEqual("tiktok", got["item"]["provider"])


class CorruptedTimestampPropagatesTests(unittest.TestCase):
    """Achado 32: timestamp corrompido vira erro de fila, não "permitido agora" silencioso."""

    def test_report_raises_instead_of_masking_the_hold(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        data["providers"]["instagram"] = {
            "last_action_at": None,
            "next_allowed_at": None,
            "cooldown_until": "not-a-timestamp",
            "cooldown_strikes": 1,
        }
        with self.assertRaisesRegex(ValueError, "timestamp corrompido"):
            queue.report(data, at=T0)

    def test_hint_returns_an_error_instead_of_claiming_allowed_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = queue.queue_path(tmp)
            data = queue.empty_state()
            queue.add(data, "instagram", [REEL], at=T0)
            data["providers"]["instagram"] = {
                "last_action_at": None,
                "next_allowed_at": None,
                "cooldown_until": "not-a-timestamp",
                "cooldown_strikes": 1,
            }
            queue.save(path, data)
            result = queue.hint(tmp)
            assert result is not None
            self.assertIn("error", result)
            self.assertNotIn("permitido agora", (result.get("line") or ""))


class Schema99StatusStaysExit0Tests(unittest.TestCase):
    """Achado 14: queue.json com schema estranho não derruba `status`, hint só reporta erro."""

    def test_status_exit_0_and_hint_returns_error_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            from getbrolls.ledger import Ledger

            Ledger(tmp)
            path = queue.queue_path(tmp)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"schema_version": 99, "items": [], "providers": {}}), encoding="utf-8")
            before = (path.stat().st_mtime_ns, path.read_bytes())
            done = subprocess.run(
                [sys.executable, str(CLI), "status", "--project", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, done.returncode, done.stderr)
            payload = json.loads(done.stdout)
            self.assertIn("error", payload["queue"])
            self.assertIn("Fila indisponível", payload["summary"]["line"])
            self.assertEqual(before, (path.stat().st_mtime_ns, path.read_bytes()))


class HintMinOverFreeProvidersTests(unittest.TestCase):
    """Achado 57: hint() usa MIN entre provedores pendentes e livres, não MAX dos bloqueados."""

    def setUp(self):
        patcher = patch.dict(os.environ, clean_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reports_now_when_any_pending_provider_is_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = queue.queue_path(tmp)
            data = queue.empty_state()
            now = queue._now()
            queue.add(data, "instagram", [REEL], at=now)
            queue.add(data, "tiktok", [TIKTOK], at=now)
            # instagram blocked far in the future; tiktok has no state at all (free).
            data["providers"]["instagram"] = {
                "last_action_at": None,
                "next_allowed_at": (now + timedelta(hours=4)).isoformat(),
                "cooldown_until": None,
                "cooldown_strikes": 0,
            }
            queue.save(path, data)
            result = queue.hint(tmp)
            assert result is not None
            self.assertIn("agora", result["line"])

    def test_uses_min_not_max_when_both_pending_providers_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = queue.queue_path(tmp)
            data = queue.empty_state()
            # hint() always computes "now" internally (no injectable `at`): anchor the
            # holds on the real clock, not on the fixed T0 fixture, or this flakes.
            now = queue._now()
            queue.add(data, "instagram", [REEL], at=now)
            queue.add(data, "tiktok", [TIKTOK], at=now)
            soon = now + timedelta(minutes=5)
            later = now + timedelta(hours=4)
            data["providers"]["instagram"] = {
                "last_action_at": None,
                "next_allowed_at": later.isoformat(),
                "cooldown_until": None,
                "cooldown_strikes": 0,
            }
            data["providers"]["tiktok"] = {
                "last_action_at": None,
                "next_allowed_at": soon.isoformat(),
                "cooldown_until": None,
                "cooldown_strikes": 0,
            }
            queue.save(path, data)
            result = queue.hint(tmp)
            assert result is not None
            self.assertIn(soon.isoformat(), result["line"])
            self.assertNotIn(later.isoformat(), result["line"])


class HintConsultsRulesTests(unittest.TestCase):
    """Achado 51: hint() consulta RULES.md (load_rules, somente leitura) para o ritmo."""

    def setUp(self):
        patcher = patch.dict(os.environ, clean_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_hint_passes_rules_pacing_into_report(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {**clean_env(), "GB_PACE_MIN_S": "0", "GB_PACE_MAX_S": "0"}, clear=True),
        ):
            (Path(tmp) / "RULES.md").write_text(VALID_RULES, encoding="utf-8")
            path = queue.queue_path(tmp)
            data = queue.empty_state()
            # 4 items so the RULES.md max_per_day=4 for instagram is exactly hit.
            urls = [f"https://www.instagram.com/reel/RUL{i:03d}/" for i in range(5)]
            queue.add(data, "instagram", urls, at=T0)
            at = T0
            for _ in range(4):
                got = queue.claim_next(data, at=at, rng=rng())
                self.assertIsNotNone(got["item"])
                queue.mark(data, got["item"]["id"], "done", at=at)
                at += timedelta(seconds=1)
            queue.save(path, data)
            result: Any = queue.hint(tmp, at=at)
            self.assertEqual("max_per_day", result["providers"]["instagram"]["hold"])


class QueueActionReadOnlyTests(unittest.TestCase):
    """Achado 61: `queue --action status` não deve tomar a trava exclusiva do projeto."""

    def test_queue_status_is_registered_as_read_only(self):
        self.assertIn(("queue", "status"), READ_ONLY_ACTIONS)

    def test_queue_status_succeeds_while_another_command_holds_the_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            from getbrolls.ledger import Ledger

            Ledger(tmp)
            with project_lock(tmp):
                args = queue_args(tmp, "status")
                result = audited(args, commands.execute)
                self.assertEqual("status", result["action"])

    def test_a_write_action_still_takes_the_lock_and_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp, project_lock(tmp):
            args = queue_args(tmp, "add", provider="instagram", urls=[REEL])
            with self.assertRaisesRegex(getattr(commands, "OperationError", Exception), "."):
                audited(args, commands.execute)


class RulesPacingReachesQueueTests(unittest.TestCase):
    """Achado 19/5: RULES.md inválido levanta; RULES.md válido chega ao `next`;
    RULES.md quebrado ao ler pelo comando `queue` cai para defaults com aviso."""

    def setUp(self):
        patcher = patch.dict(os.environ, clean_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_rules_pacing_reaches_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "RULES.md").write_text(VALID_RULES, encoding="utf-8")
            add_args = queue_args(tmp, "add", provider="instagram", urls=[REEL])
            audited(add_args, commands.execute)
            with patch.object(queue, "_rng", return_value=rng(0.0)):
                next_args = queue_args(tmp, "next")
                result = audited(next_args, commands.execute)
            self.assertIsNotNone(result["item"])
            # RULES.md pacing.instagram.max_s = 2s, far below the 45-120s default.
            state = queue.load(queue.queue_path(tmp))["providers"]["instagram"]
            gap = datetime.fromisoformat(state["next_allowed_at"]) - datetime.fromisoformat(state["last_action_at"])
            self.assertLessEqual(gap.total_seconds(), 2)

    def test_invalid_pacing_block_makes_load_rules_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            invalid = VALID_RULES.replace('"max_s": 2', '"max_s": "not-a-number"')
            (Path(tmp) / "RULES.md").write_text(invalid, encoding="utf-8")
            from getbrolls.rules import load_rules

            with self.assertRaises(ValueError):
                load_rules(tmp)

    def test_broken_rules_md_falls_back_to_defaults_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "RULES.md").write_text("garbage, not even a code block", encoding="utf-8")
            args = queue_args(tmp, "status")
            result = audited(args, commands.execute)
            self.assertTrue(any(w["code"] == "RULES_UNAVAILABLE" for w in result.get("warnings", [])))


if __name__ == "__main__":
    unittest.main()


class CooldownReasonSkillMessagesTests(unittest.TestCase):
    """As mensagens de bloqueio que a própria skill emite (social.py) abrem cooldown."""

    def test_skill_messages_trigger_and_local_errors_do_not(self):
        from getbrolls import queue

        positives = [
            "A fonte exige uma sessão de acesso. Use o navegador autorizado.",
            "A fonte bloqueou o IP desta rede para esse post; download não concluído.",
            "limite de requisições da fonte (429); aguarde e tente de novo",
            "rate-limit atingido",
            "HTTP 403",
        ]
        negatives = ["ffmpeg falhou: bitrate inválido", "inaccurate seek", "frame= 429 fps=30 is not here"]
        for text in positives:
            self.assertTrue(queue.is_cooldown_reason(text), text)
        for text in negatives[:2]:
            self.assertFalse(queue.is_cooldown_reason(text), text)

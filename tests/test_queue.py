"""Fila com ritmo: `queue` nunca dorme, respeita tetos por hora/dia e registra cooldown."""

import json
import os
import random
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import CLI, ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import queue
from getbrolls.cli import SUMMARIES, build_parser
from getbrolls.ledger import Ledger

T0 = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
REEL = "https://www.instagram.com/reel/ABC123xyz/"
REEL_VARIANT = "https://www.instagram.com/reel/ABC123xyz"
REEL_2 = "https://www.instagram.com/reel/DEF456uvw/"
TIKTOK = "https://www.tiktok.com/@user/video/1234567890"


def rng(value=0.0):
    """Sorteio determinístico: `uniform(a, b)` devolve a + value * (b - a)."""
    generator = random.Random(1)
    generator.uniform = lambda a, b: a + value * (b - a)
    return generator


def clean_env():
    return {key: value for key, value in os.environ.items() if not key.startswith(("GB_PACE_", "GB_MAX_PER_"))}


class PacingConfigTests(unittest.TestCase):
    def test_defaults_per_provider(self):
        with patch.dict(os.environ, clean_env(), clear=True):
            self.assertEqual(
                {"min_s": 45, "max_s": 120, "max_per_hour": 20, "max_per_day": 60},
                queue.pacing("instagram"),
            )
            for provider in ("tiktok", "youtube"):
                self.assertEqual((15, 40), (queue.pacing(provider)["min_s"], queue.pacing(provider)["max_s"]))

    def test_env_wins_over_rules_block(self):
        rules = {"pacing": {"instagram": {"min_s": 5, "max_s": 6, "max_per_hour": 2, "max_per_day": 3}}}
        with patch.dict(os.environ, clean_env(), clear=True):
            self.assertEqual(
                {"min_s": 5, "max_s": 6, "max_per_hour": 2, "max_per_day": 3},
                queue.pacing("instagram", rules),
            )
            self.assertEqual(15, queue.pacing("tiktok", rules)["min_s"])
        with patch.dict(
            os.environ,
            {**clean_env(), "GB_PACE_MIN_S": "1", "GB_PACE_MAX_S": "2", "GB_MAX_PER_HOUR": "9", "GB_MAX_PER_DAY": "10"},
            clear=True,
        ):
            self.assertEqual(
                {"min_s": 1, "max_s": 2, "max_per_hour": 9, "max_per_day": 10},
                queue.pacing("instagram", rules),
            )

    def test_invalid_values_are_rejected(self):
        with (
            patch.dict(os.environ, {**clean_env(), "GB_PACE_MIN_S": "abc"}, clear=True),
            self.assertRaises(ValueError),
        ):
            queue.pacing("instagram")
        with (
            patch.dict(os.environ, {**clean_env(), "GB_PACE_MIN_S": "50", "GB_PACE_MAX_S": "10"}, clear=True),
            self.assertRaises(ValueError),
        ):
            queue.pacing("instagram")


class QueueStateTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, clean_env(), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_add_dedupes_by_normalized_url(self):
        data = queue.empty_state()
        first = queue.add(data, "instagram", [REEL, REEL_VARIANT, REEL_2], at=T0)
        self.assertEqual(["instagram:ABC123xyz", "instagram:DEF456uvw"], first["added"])
        self.assertEqual(["instagram:ABC123xyz"], first["duplicates"])
        again = queue.add(data, "instagram", [REEL], at=T0)
        self.assertEqual([], again["added"])
        self.assertEqual(2, len(data["items"]))
        self.assertTrue(all(item["status"] == "pending" for item in data["items"]))
        with self.assertRaises(ValueError):
            queue.add(data, "instagram", [TIKTOK], at=T0)
        with self.assertRaises(ValueError):
            queue.add(data, "instagram", ["https://example.org/x"], at=T0)

    def test_save_is_private_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = queue.queue_path(tmp)
            self.assertEqual(Path(tmp).resolve() / "work" / "queue.json", path)
            data = queue.empty_state()
            queue.add(data, "instagram", [REEL], at=T0)
            queue.save(path, data)
            self.assertEqual(data, queue.load(path))
            if os.name != "nt":
                self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(["queue.json"], [p.name for p in path.parent.iterdir()])
            self.assertEqual(queue.empty_state(), queue.load(Path(tmp) / "absent.json"))

    def test_next_paces_from_the_returned_item(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL, REEL_2], at=T0)
        first = queue.next_item(data, at=T0, rng=rng(0.0))
        self.assertEqual("instagram:ABC123xyz", first["item"]["id"])
        self.assertEqual(0, first["wait_seconds"])
        self.assertEqual("active", first["item"]["status"])
        # Repetir sem marcar devolve o mesmo item ativo, sem reiniciar o relógio.
        same = queue.next_item(data, at=T0 + timedelta(seconds=1), rng=rng(0.0))
        self.assertEqual("instagram:ABC123xyz", same["item"]["id"])
        queue.mark(data, "instagram:ABC123xyz", "done", at=T0 + timedelta(seconds=10))
        early = queue.next_item(data, at=T0 + timedelta(seconds=10), rng=rng(0.0))
        self.assertIsNone(early["item"])
        self.assertEqual(35, early["wait_seconds"])
        self.assertEqual((T0 + timedelta(seconds=45)).isoformat(), early["resume_at"])
        later = queue.next_item(data, at=T0 + timedelta(seconds=45), rng=rng(1.0))
        self.assertEqual("instagram:DEF456uvw", later["item"]["id"])
        # O sorteio vale para o próximo intervalo: 120 s a partir deste retorno.
        state = data["providers"]["instagram"]
        self.assertEqual((T0 + timedelta(seconds=45)).isoformat(), state["last_action_at"])
        self.assertEqual((T0 + timedelta(seconds=165)).isoformat(), state["next_allowed_at"])
        queue.mark(data, "instagram:DEF456uvw", "done", at=T0 + timedelta(seconds=50))
        drained = queue.next_item(data, at=T0 + timedelta(hours=1), rng=rng())
        self.assertIsNone(drained["item"])
        self.assertEqual("empty", drained["reason"])

    def test_hourly_and_daily_caps_count_finished_items(self):
        data = queue.empty_state()
        urls = [f"https://www.instagram.com/reel/CAP{index:04d}/" for index in range(25)]
        queue.add(data, "instagram", urls, at=T0)
        with patch.dict(os.environ, {"GB_PACE_MIN_S": "0", "GB_PACE_MAX_S": "0"}):
            for index in range(20):
                at = T0 + timedelta(minutes=index)
                got = queue.next_item(data, at=at, rng=rng())
                self.assertIsNotNone(got["item"], index)
                queue.mark(data, got["item"]["id"], "done" if index % 2 else "failed", reason="ffmpeg", at=at)
            blocked = queue.next_item(data, at=T0 + timedelta(minutes=20), rng=rng())
            self.assertIsNone(blocked["item"])
            self.assertEqual("max_per_hour", blocked["reason"])
            self.assertEqual((T0 + timedelta(hours=1)).isoformat(), blocked["resume_at"])
            self.assertEqual(40 * 60, blocked["wait_seconds"])
            freed = queue.next_item(data, at=T0 + timedelta(hours=1), rng=rng())
            self.assertIsNotNone(freed["item"])
            queue.mark(data, freed["item"]["id"], "done", at=T0 + timedelta(hours=1))
        with patch.dict(os.environ, {"GB_PACE_MIN_S": "0", "GB_PACE_MAX_S": "0", "GB_MAX_PER_DAY": "21"}):
            daily = queue.next_item(data, at=T0 + timedelta(hours=2), rng=rng())
            self.assertEqual("max_per_day", daily["reason"])
            self.assertEqual((T0 + timedelta(hours=24)).isoformat(), daily["resume_at"])

    def test_cooldown_doubles_caps_and_resets_after_done(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [f"https://www.instagram.com/reel/CD{index:03d}/" for index in range(8)], at=T0)
        with patch.dict(
            os.environ, {"GB_PACE_MIN_S": "0", "GB_PACE_MAX_S": "0", "GB_MAX_PER_HOUR": "100", "GB_MAX_PER_DAY": "100"}
        ):
            expected = [1800, 3600, 7200, 14400, 14400]
            at = T0
            for seconds in expected:
                got = queue.next_item(data, at=at, rng=rng())
                marked = queue.mark(data, got["item"]["id"], "failed", reason="HTTP 429 Too Many Requests", at=at)
                self.assertEqual(seconds, marked["cooldown"]["seconds"])
                blocked = queue.next_item(data, at=at, rng=rng())
                self.assertIsNone(blocked["item"])
                self.assertEqual("cooldown", blocked["reason"])
                self.assertEqual(seconds, blocked["wait_seconds"])
                at = at + timedelta(seconds=seconds)
            got = queue.next_item(data, at=at, rng=rng())
            done = queue.mark(data, got["item"]["id"], "done", at=at)
            self.assertIsNone(done["cooldown"])
            self.assertEqual(0, data["providers"]["instagram"]["cooldown_strikes"])
            got = queue.next_item(data, at=at, rng=rng())
            plain = queue.mark(data, got["item"]["id"], "failed", reason="ffmpeg sem stream", at=at)
            self.assertIsNone(plain["cooldown"])
            got = queue.next_item(data, at=at, rng=rng())
            challenge = queue.mark(data, got["item"]["id"], "skipped", reason="login challenge", at=at)
            self.assertEqual(1800, challenge["cooldown"]["seconds"])
        with self.assertRaises(ValueError):
            queue.mark(data, "instagram:missing", "done", at=at)

    def test_record_cooldown_touches_only_an_existing_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(queue.record_cooldown(tmp, "instagram", "HTTP 403", at=T0))
            self.assertFalse((Path(tmp) / "work").exists())
            path = queue.queue_path(tmp)
            data = queue.empty_state()
            queue.add(data, "instagram", [REEL], at=T0)
            queue.save(path, data)
            cooldown = queue.record_cooldown(tmp, "instagram", "curl 22 HTTP 429", at=T0)
            assert cooldown is not None
            until = cooldown["until"]
            self.assertEqual((T0 + timedelta(seconds=1800)).isoformat(), until)
            self.assertEqual([], cooldown["items_failed"])
            saved = queue.load(path)
            self.assertEqual(until, saved["providers"]["instagram"]["cooldown_until"])
            report = queue.report(saved, at=T0)
            self.assertEqual({"pending": 1, "active": 0, "done": 0, "failed": 0, "skipped": 0}, report["counts"])
            self.assertEqual(1800, report["providers"]["instagram"]["wait_seconds"])
            self.assertEqual(until, report["providers"]["instagram"]["next_allowed_at"])

    def test_record_cooldown_stamps_active_items_at_now_not_at_cooldown_until(self):
        # Achado 56: gravar finished_at=cooldown["until"] (futuro) inflava as contagens
        # de hora/dia; o item falhou agora, não daqui a 30 min.
        with tempfile.TemporaryDirectory() as tmp:
            path = queue.queue_path(tmp)
            data = queue.empty_state()
            queue.add(data, "instagram", [REEL], at=T0)
            queue.next_item(data, at=T0, rng=rng(0.0))
            queue.save(path, data)
            cooldown = queue.record_cooldown(tmp, "instagram", "HTTP 403", at=T0)
            assert cooldown is not None
            self.assertEqual(["instagram:ABC123xyz"], cooldown["items_failed"])
            saved = queue.load(path)
            item = saved["items"][0]
            self.assertEqual("failed", item["status"])
            self.assertEqual(T0.isoformat(), item["finished_at"])
            self.assertNotEqual(cooldown["until"], item["finished_at"])


class QueueCliTests(unittest.TestCase):
    def run_cli(self, *args, ok=True):
        done = subprocess.run(
            [sys.executable, str(CLI), *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**clean_env(), "GB_PACE_MIN_S": "30", "GB_PACE_MAX_S": "30"},
            check=False,
        )
        self.assertEqual(0 if ok else 2, done.returncode, done.stderr)
        return json.loads(done.stdout if ok else done.stderr)

    def test_queue_is_a_documented_subcommand(self):
        self.assertIn("queue", SUMMARIES)
        parser = build_parser()
        args = parser.parse_args(["queue", "--project", "p", "--action", "add", "--provider", "instagram", REEL])
        self.assertEqual([REEL], args.urls)
        help_run = subprocess.run(
            [sys.executable, str(CLI), "queue", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(0, help_run.returncode)
        self.assertIn("--action", help_run.stdout)

    def test_add_next_mark_status_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            added = self.run_cli(
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
                "--url",
                REEL,
            )
            self.assertEqual(2, len(added["added"]))
            self.assertIn("Enfileirei", added["summary"]["line"])
            self.assertTrue((Path(tmp) / "work/queue.json").is_file())
            first = self.run_cli("queue", "--project", tmp, "--action", "next")
            self.assertEqual("instagram:ABC123xyz", first["item"]["id"])
            marked = self.run_cli("queue", "--project", tmp, "--action", "mark", "--id", first["item"]["id"], "--done")
            self.assertEqual("done", marked["item"]["status"])
            waiting = self.run_cli("queue", "--project", tmp, "--action", "next")
            self.assertIsNone(waiting["item"])
            self.assertGreater(waiting["wait_seconds"], 0)
            self.assertLessEqual(waiting["wait_seconds"], 30)
            self.assertTrue(waiting["resume_at"])
            self.assertIn("Aguarde", waiting["summary"]["line"])
            failed = self.run_cli(
                "queue",
                "--project",
                tmp,
                "--action",
                "mark",
                "--id",
                "instagram:DEF456uvw",
                "--failed",
                "--reason",
                "HTTP 429",
            )
            self.assertEqual(1800, failed["cooldown"]["seconds"])
            status = self.run_cli("queue", "--project", tmp, "--action", "status")
            self.assertEqual({"pending": 0, "active": 0, "done": 1, "failed": 1, "skipped": 0}, status["counts"])
            self.assertTrue(status["providers"]["instagram"]["cooldown_until"])
            failure = self.run_cli("queue", "--project", tmp, "--action", "mark", "--id", "x", ok=False)
            self.assertIn("--done", failure["message"])
            self.assertNotIn(".conf", json.dumps(status))

    def test_status_reports_queue_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            Ledger(tmp)
            self.run_cli("queue", "--project", tmp, "--action", "add", "--provider", "instagram", REEL)
            path = Path(tmp) / "work/queue.json"
            before = (path.stat().st_mtime_ns, path.read_bytes())
            payload = self.run_cli("status", "--project", tmp)
            self.assertEqual(1, payload["queue"]["counts"]["pending"])
            self.assertIn("Fila", payload["summary"]["line"])
            self.assertIn("1 pendente", payload["summary"]["line"])
            self.assertEqual(before, (path.stat().st_mtime_ns, path.read_bytes()))
            empty = tempfile.mkdtemp()
            Ledger(empty)
            self.assertIsNone(self.run_cli("status", "--project", empty)["queue"])


if __name__ == "__main__":
    unittest.main()

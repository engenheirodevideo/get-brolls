"""A limite de ritmo espera; login, desafio e recusa param a fila. Esperar não libera acesso."""

import json
import os
import random
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import

# efeito de import: insere scripts/ em sys.path
from _paths import CLI_ARGV  # pylint: disable=unused-import

from getbrolls import queue

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
REEL = "https://www.instagram.com/reel/ABC123xyz/"
REEL_2 = "https://www.instagram.com/reel/DEF456uvw/"
TIKTOK = "https://www.tiktok.com/@user/video/1234567890"

# Motivo escrito pelo agente -> o que a fila faz com ele.
REFUSALS = (
    "login",
    "login page",
    "Instagram pediu login",
    "login challenge",
    "challenge body in the response",
    "checkpoint required",
    "captcha",
    "Instagram pediu CAPTCHA",
    "IP bloqueado",
    "HTTP 403",
    "403 Forbidden",
    "curl 22: returned error: 403",
    "HTTP 401 Unauthorized",
    "access denied",
    "acesso negado",
    "A fonte exige uma sessão de acesso. Use o navegador autorizado.",
    "A fonte bloqueou o IP desta rede para esse post; download não concluído.",
)
RATE_LIMITS = (
    "429",
    "HTTP 429 Too Many Requests",
    "rate limit",
    "rate-limit atingido",
    "too many requests",
    "limite de requisições da fonte (429); aguarde e tente de novo",
)
PLAIN = (
    "ffmpeg sem stream",
    "ffmpeg falhou: bitrate inválido",
    "inaccurate seek",
    "URL assinada expirada (oe no passado); recapture pela mesma rota autorizada",
    "mídia vazia",
)


def rng(value=0.0):
    """Sorteio determinístico: `uniform(a, b)` devolve a + value * (b - a)."""
    generator = random.Random(1)
    generator.uniform = lambda a, b: a + value * (b - a)
    return generator


def free_pace():
    return patch.dict(
        os.environ, {"GB_PACE_MIN_S": "0", "GB_PACE_MAX_S": "0", "GB_MAX_PER_HOUR": "100", "GB_MAX_PER_DAY": "100"}
    )


def claimed(data, at=T0):
    got = queue.claim_next(data, at=at, rng=rng())
    assert got["item"] is not None, got
    return got["item"]["id"]


def line_of(result):
    """The human line of `summary_line`, which is a text for most actions and a dict with `do` for `resume`."""
    summary = queue.summary_line(result)
    return summary["line"] if isinstance(summary, dict) else summary


def queue_with(*urls, provider="instagram"):
    data = queue.empty_state()
    queue.add(data, provider, list(urls), at=T0)
    return data


class FailureKindTests(unittest.TestCase):
    def test_every_reason_lands_in_exactly_one_kind(self):
        for kind, reasons in (("access_refused", REFUSALS), ("rate_limit", RATE_LIMITS), (None, PLAIN)):
            for reason in reasons:
                with self.subTest(reason=reason):
                    self.assertEqual(kind, queue.failure_kind(reason))
        self.assertIsNone(queue.failure_kind(None))
        self.assertIsNone(queue.failure_kind(""))

    def test_a_refusal_wins_over_a_rate_limit_word_in_the_same_text(self):
        # Um 403 sem causa conhecida não vira "espere e volte" só porque o texto cita "rate limit".
        self.assertEqual("access_refused", queue.failure_kind("HTTP 403 (rate limit?)"))
        self.assertEqual("access_refused", queue.failure_kind("login challenge, too many requests"))

    def test_captcha_is_a_challenge_and_a_blocked_ip_is_an_ip_block(self):
        for reason, category in (
            ("captcha", "challenge"),
            ("Instagram pediu CAPTCHA", "challenge"),
            ("IP bloqueado", "ip_block"),
            ("A fonte bloqueou o IP desta rede", "ip_block"),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(category, queue._reason_class(reason))

    def test_is_cooldown_reason_is_true_only_for_a_rate_limit(self):
        for reason in RATE_LIMITS:
            self.assertTrue(queue.is_cooldown_reason(reason), reason)
        for reason in (*REFUSALS, *PLAIN):
            self.assertFalse(queue.is_cooldown_reason(reason), reason)


class RefusalStopsTheBatchTests(unittest.TestCase):
    def test_a_refusal_opens_no_cooldown_and_records_a_stop(self):
        for reason in REFUSALS:
            with self.subTest(reason=reason), free_pace():
                data = queue_with(REEL, REEL_2)
                marked = queue.mark(data, claimed(data), "failed", reason=reason, at=T0)
                self.assertIsNone(marked["cooldown"])
                self.assertEqual("access_refused", marked["stop"]["kind"])
                state = data["providers"]["instagram"]
                self.assertIsNone(state["cooldown_until"])
                self.assertEqual(0, state["cooldown_strikes"])
                self.assertEqual(T0.isoformat(), state["stopped"]["at"])
                self.assertEqual("instagram:ABC123xyz", state["stopped"]["item"])

    def test_a_skipped_item_with_a_refusal_reason_stops_too(self):
        # tests/test_queue.py pinned "login challenge" -> 1800 s of cooldown here.
        with free_pace():
            data = queue_with(REEL, REEL_2)
            marked = queue.mark(data, claimed(data), "skipped", reason="login challenge", at=T0)
        self.assertIsNone(marked["cooldown"])
        self.assertIn("stopped", data["providers"]["instagram"])

    def test_next_refuses_instead_of_waiting_and_never_offers_a_resume_time(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            queue.mark(data, claimed(data), "failed", reason="login challenge", at=T0)
            for later in (T0, T0 + timedelta(hours=1), T0 + timedelta(days=3)):
                with self.subTest(later=later), self.assertRaises(ValueError) as caught:
                    queue.claim_next(data, at=later, rng=rng())
                message = str(caught.exception)
                self.assertIn("instagram", message)
                self.assertIn("parada", message)
                self.assertIn("esperar", message)
                self.assertNotIn("resume_at", message)
        self.assertEqual(["pending"], [i["status"] for i in data["items"] if i["id"] == "instagram:DEF456uvw"])

    def test_the_stop_survives_a_save_and_a_load(self):
        with free_pace(), tempfile.TemporaryDirectory() as tmp:
            path = queue.queue_path(tmp)
            data = queue_with(REEL, REEL_2)
            queue.mark(data, claimed(data), "failed", reason="HTTP 403", at=T0)
            queue.save(path, data)
            with self.assertRaises(ValueError):
                queue.claim_next(queue.load(path), at=T0 + timedelta(days=1), rng=rng())

    def test_a_stop_holds_one_provider_and_leaves_the_others_running(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            queue.add(data, "tiktok", [TIKTOK], at=T0)
            first = next(i["id"] for i in data["items"] if i["provider"] == "instagram")
            self.assertEqual(first, queue.claim_next(data, provider="instagram", at=T0, rng=rng())["item"]["id"])
            queue.mark(data, first, "failed", reason="login", at=T0)
            got = queue.claim_next(data, at=T0, rng=rng())
            self.assertEqual("tiktok", got["item"]["provider"])
            with self.assertRaises(ValueError):
                queue.claim_next(data, provider="instagram", at=T0, rng=rng())

    def test_a_stopped_provider_with_nothing_pending_is_just_empty(self):
        with free_pace():
            data = queue_with(REEL)
            queue.mark(data, claimed(data), "failed", reason="login", at=T0)
            got = queue.claim_next(data, at=T0, rng=rng())
        self.assertEqual(("empty", None, 0), (got["reason"], got["resume_at"], got["wait_seconds"]))

    def test_a_done_item_does_not_clear_a_stop(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            queue.mark(data, claimed(data), "failed", reason="login", at=T0)
            queue.mark(data, "instagram:DEF456uvw", "done", at=T0)
        self.assertIn("stopped", data["providers"]["instagram"])

    def test_the_stop_is_reported_by_status_without_a_wait(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            queue.mark(data, claimed(data), "failed", reason="login challenge", at=T0)
            report = queue.report(data, at=T0 + timedelta(hours=5))
        provider = report["providers"]["instagram"]
        self.assertEqual("challenge", provider["stopped"]["class"])
        self.assertIsNone(provider["cooldown_until"])
        self.assertEqual(0, provider["wait_seconds"])

    def test_a_provider_that_never_stopped_keeps_the_report_shape(self):
        with free_pace():
            data = queue_with(REEL)
            item = claimed(data)
            marked = queue.mark(data, item, "done", at=T0)
        self.assertNotIn("stop", marked)
        self.assertNotIn("stopped", data["providers"]["instagram"])
        self.assertNotIn("stopped", queue.report(data, at=T0)["providers"]["instagram"])

    def test_a_corrupted_stop_is_an_invalid_queue_not_a_free_pass(self):
        with free_pace():
            data = queue_with(REEL)
            data["providers"]["instagram"] = {
                "last_action_at": None,
                "next_allowed_at": None,
                "cooldown_until": None,
                "cooldown_strikes": 0,
                "stopped": "sim",
            }
            with self.assertRaisesRegex(ValueError, "stopped"):
                queue.claim_next(data, at=T0, rng=rng())

    def test_record_refusal_fails_the_active_item_and_stops_without_a_cooldown(self):
        with free_pace(), tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(queue.record_refusal(tmp, "instagram", "HTTP 403", at=T0))
            self.assertFalse((Path(tmp) / "work").exists())
            path = queue.queue_path(tmp)
            data = queue_with(REEL, REEL_2)
            claimed(data)
            queue.save(path, data)
            result = queue.record_refusal(tmp, "instagram", "CDN recusou com HTTP 403", at=T0, reason_class="http_403")
            assert result is not None
            self.assertEqual(["instagram:ABC123xyz"], result["items_failed"])
            saved = queue.load(path)
            state = saved["providers"]["instagram"]
            self.assertIsNone(state["cooldown_until"])
            self.assertEqual("http_403", state["stopped"]["class"])
            self.assertEqual(T0.isoformat(), saved["items"][0]["finished_at"])
            with self.assertRaises(ValueError):
                queue.claim_next(saved, at=T0 + timedelta(hours=9), rng=rng())


ATTEST = {"by": "Bruno", "statement": "voltei a logar no Instagram, pode seguir"}


class ResumeClearsOnlyTheStopTests(unittest.TestCase):
    def stopped_queue(self):
        data = queue_with(REEL, REEL_2)
        queue.mark(data, claimed(data), "failed", reason="login challenge", at=T0)
        return data

    def resume(self, data, **overrides):
        args = {**ATTEST, "expect": data["providers"]["instagram"]["stopped"]["at"], "at": T0 + timedelta(hours=1)}
        args.update(overrides)
        return queue.resume(data, "instagram", **args)

    def test_resume_removes_the_stop_and_nothing_else(self):
        with free_pace():
            data = self.stopped_queue()
            state = data["providers"]["instagram"]
            state["cooldown_until"] = (T0 + timedelta(hours=2)).isoformat()
            state["cooldown_strikes"] = 3
            before = {key: value for key, value in state.items() if key not in ("stopped", "last_resume")}
            items_before = json.dumps(data["items"], sort_keys=True)
            result = self.resume(data)
            self.assertEqual("instagram", result["provider"])
            self.assertEqual("Bruno", result["by"])
            self.assertEqual("challenge", result["resumed"]["class"])
            self.assertNotIn("stopped", state)
            self.assertEqual(before, {k: v for k, v in state.items() if k != "last_resume"})
            self.assertEqual(items_before, json.dumps(data["items"], sort_keys=True))

    def test_resume_records_who_cleared_it_and_with_which_words(self):
        with free_pace():
            data = self.stopped_queue()
            stopped_at = data["providers"]["instagram"]["stopped"]["at"]
            self.resume(data)
        self.assertEqual(
            {
                "at": (T0 + timedelta(hours=1)).isoformat(),
                "by": "Bruno",
                "statement": ATTEST["statement"],
                "stopped_at": stopped_at,
                "class": "challenge",
            },
            data["providers"]["instagram"]["last_resume"],
        )

    def test_resume_lets_next_run_again_through_the_usual_pacing(self):
        with free_pace():
            data = self.stopped_queue()
            with self.assertRaises(ValueError):
                queue.claim_next(data, at=T0, rng=rng())
            self.resume(data)
            self.assertEqual("instagram:DEF456uvw", queue.claim_next(data, at=T0, rng=rng())["item"]["id"])

    def test_resume_keeps_an_open_cooldown_so_next_still_waits(self):
        with free_pace():
            data = self.stopped_queue()
            data["providers"]["instagram"]["cooldown_until"] = (T0 + timedelta(hours=2)).isoformat()
            self.resume(data)
            waiting = queue.claim_next(data, at=T0, rng=rng())
        self.assertEqual("cooldown", waiting["reason"])

    def test_every_missing_attestation_is_refused_and_the_stop_stays(self):
        for field, pattern in (("by", "--by"), ("statement", "--phrase"), ("expect", "--expect")):
            for value in (None, "", "   "):
                with self.subTest(field=field, value=value), free_pace():
                    data = self.stopped_queue()
                    with self.assertRaisesRegex(ValueError, f"queue resume.*{pattern}"):
                        self.resume(data, **{field: value})
                    self.assertIn("stopped", data["providers"]["instagram"])
                    self.assertNotIn("last_resume", data["providers"]["instagram"])

    def test_a_stale_expect_cannot_clear_a_newer_stop(self):
        with free_pace():
            data = self.stopped_queue()
            old_at = data["providers"]["instagram"]["stopped"]["at"]
            self.resume(data)
            queue.add(data, "instagram", ["https://www.instagram.com/reel/GHI789rst/"], at=T0)
            queue.mark(data, "instagram:DEF456uvw", "failed", reason="HTTP 403", at=T0 + timedelta(hours=2))
            with self.assertRaisesRegex(ValueError, "--expect não confere"):
                self.resume(data, expect=old_at)
            self.assertIn("stopped", data["providers"]["instagram"])

    def test_resume_of_a_provider_that_is_not_stopped_is_refused(self):
        data = queue_with(REEL)
        for provider in ("instagram", "tiktok"):
            with self.subTest(provider=provider), self.assertRaisesRegex(ValueError, "não está parada"):
                queue.resume(data, provider, expect="x", **ATTEST)
        with self.assertRaises(ValueError):
            queue.resume(data, "vimeo", expect="x", **ATTEST)
        self.assertEqual({}, data["providers"], "a refusal leaves the state as it was")

    def test_resume_only_clears_the_named_provider(self):
        with free_pace():
            data = self.stopped_queue()
            queue.add(data, "tiktok", [TIKTOK], at=T0)
            data["providers"]["tiktok"] = {
                "last_action_at": None,
                "next_allowed_at": None,
                "cooldown_until": None,
                "cooldown_strikes": 0,
                "stopped": {"at": T0.isoformat(), "class": "login", "item": None},
            }
            self.resume(data)
        self.assertIn("stopped", data["providers"]["tiktok"])

    def test_resume_logs_one_event_that_says_a_statement_exists_but_never_the_words(self):
        with free_pace():
            data = self.stopped_queue()
            with self.assertLogs("getbrolls.queue", "INFO") as logged:
                self.resume(data)
        joined = "\n".join(logged.output)
        self.assertIn("event=queue_resume", joined)
        self.assertIn("reason_class=challenge", joined)
        self.assertIn("statement_present=True", joined)
        self.assertNotIn(ATTEST["statement"], joined)
        self.assertNotIn("Bruno", joined)
        self.assertNotIn("login challenge", joined)

    def test_the_summary_says_the_next_step_and_never_a_wait(self):
        summary = queue.summary_line({"action": "resume", "provider": "instagram"})
        assert isinstance(summary, dict)
        self.assertIn("Retomei a fila de instagram", summary["line"])
        self.assertIn("só a parada saiu", summary["line"])
        do = summary["do"]
        self.assertIn("queue --action next --provider instagram --project PROJETO", do["command"])
        self.assertFalse(do["blocking_human"])

    def test_the_stop_message_gives_a_template_and_never_a_ready_to_run_command(self):
        with free_pace():
            data = self.stopped_queue()
            stopped_at = data["providers"]["instagram"]["stopped"]["at"]
            with self.assertRaises(ValueError) as caught:
                queue.claim_next(data, at=T0, rng=rng())
        message = str(caught.exception)
        self.assertIn("queue --action resume --provider instagram", message)
        for placeholder in ("--by NOME", '--phrase "FRASE EXATA"', "--expect AT_DA_PARADA"):
            self.assertIn(placeholder, message)
        self.assertNotIn(f"--expect {stopped_at}", message, "o instante real não vem no modelo")
        self.assertIn("confirmação", message)


class StopVersusOtherProvidersTests(unittest.TestCase):
    def test_a_wait_for_another_provider_says_which_provider_is_stopped(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            queue.add(data, "tiktok", [TIKTOK], at=T0)
            queue.mark(data, claimed(data), "failed", reason="login", at=T0)
            data["providers"]["tiktok"] = {
                "last_action_at": None,
                "next_allowed_at": None,
                "cooldown_until": (T0 + timedelta(hours=1)).isoformat(),
                "cooldown_strikes": 1,
                "stopped": None,
            }
            del data["providers"]["tiktok"]["stopped"]
            waiting = queue.claim_next(data, at=T0, rng=rng())
        self.assertEqual("cooldown", waiting["reason"])
        self.assertEqual(["instagram"], waiting["stopped"])
        line = queue.summary_line({"action": "next", **waiting})
        self.assertIn("A espera não vale para instagram", line)

    def test_a_wait_with_no_stopped_provider_keeps_its_shape(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            claimed(data)
            queue.claim_next(data, at=T0, rng=rng())
            data["items"][0]["status"] = "done"
            data["items"][0]["finished_at"] = T0.isoformat()
            waiting = queue.claim_next(data, at=T0, rng=rng())
        self.assertNotIn("stopped", waiting)

    def test_an_active_item_of_a_stopped_provider_is_not_handed_out_again(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            first = claimed(data)
            # The stop came from another item (a skipped one), so the first item is still active.
            queue.mark(data, "instagram:DEF456uvw", "skipped", reason="login", at=T0)
            self.assertEqual("active", next(i["status"] for i in data["items"] if i["id"] == first))
            got = queue.claim_next(data, at=T0, rng=rng())
        self.assertIsNone(got["item"])
        self.assertEqual("empty", got["reason"])


class RateLimitKeepsTheCooldownTests(unittest.TestCase):
    def test_a_rate_limit_opens_the_doubling_cooldown_and_no_stop(self):
        for reason in RATE_LIMITS:
            with self.subTest(reason=reason), free_pace():
                data = queue_with(REEL, REEL_2)
                marked = queue.mark(data, claimed(data), "failed", reason=reason, at=T0)
                self.assertEqual(1800, marked["cooldown"]["seconds"])
                self.assertNotIn("stop", marked)
                self.assertNotIn("stopped", data["providers"]["instagram"])
                waiting = queue.claim_next(data, at=T0, rng=rng())
                self.assertEqual(("cooldown", 1800), (waiting["reason"], waiting["wait_seconds"]))
                self.assertEqual((T0 + timedelta(seconds=1800)).isoformat(), waiting["resume_at"])

    def test_a_plain_failure_opens_neither(self):
        for reason in PLAIN:
            with self.subTest(reason=reason), free_pace():
                data = queue_with(REEL, REEL_2)
                marked = queue.mark(data, claimed(data), "failed", reason=reason, at=T0)
                self.assertIsNone(marked["cooldown"])
                self.assertNotIn("stop", marked)
                self.assertEqual("instagram:DEF456uvw", queue.claim_next(data, at=T0, rng=rng())["item"]["id"])


class MessagesTests(unittest.TestCase):
    def test_a_wait_never_reads_as_a_clearance(self):
        pace = line_of(
            {"action": "next", "item": None, "reason": "pace", "wait_seconds": 83, "resume_at": "2026-10-20T12:01:23"}
        )
        self.assertIn("Aguarde 83 s", pace)
        self.assertNotIn("retome", pace.lower())
        cooldown = line_of(
            {
                "action": "next",
                "item": None,
                "reason": "cooldown",
                "wait_seconds": 1800,
                "resume_at": "2026-10-20T12:30:00",
            }
        )
        self.assertIn("Aguarde 1800 s", cooldown)
        self.assertNotIn("retome", cooldown.lower())
        self.assertIn("ritmo", cooldown)
        self.assertIn("não libera", cooldown)

    def test_mark_says_stop_and_names_no_cooldown_for_a_refusal(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            marked = queue.mark(data, claimed(data), "failed", reason="login", at=T0)
        line = line_of({"action": "mark", **marked})
        self.assertIn("Pare o lote", line)
        self.assertIn("esperar não autoriza", line)
        self.assertNotIn("Cooldown", line)

    def test_mark_with_a_rate_limit_keeps_the_cooldown_line(self):
        with free_pace():
            data = queue_with(REEL, REEL_2)
            marked = queue.mark(data, claimed(data), "failed", reason="429", at=T0)
        line = line_of({"action": "mark", **marked})
        self.assertIn("Cooldown de 1800 s", line)
        self.assertNotIn("Pare o lote", line)

    def test_the_status_line_names_a_stopped_provider(self):
        with tempfile.TemporaryDirectory() as tmp, free_pace():
            path = queue.queue_path(tmp)
            data = queue_with(REEL, REEL_2)
            queue.mark(data, claimed(data), "failed", reason="login", at=T0)
            queue.save(path, data)
            hint = queue.hint(tmp, at=T0)
        assert hint is not None
        self.assertIn("instagram", hint["line"])
        self.assertIn("parada", hint["line"])
        self.assertNotIn("próximo permitido", hint["line"])


class QueueRefusalCliTests(unittest.TestCase):
    def run_cli(self, *args, code=0):
        done = subprocess.run(
            [*CLI_ARGV, *map(str, args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "GB_PACE_MIN_S": "0", "GB_PACE_MAX_S": "0"},
            check=False,
        )
        self.assertEqual(code, done.returncode, done.stderr)
        return json.loads(done.stdout if code == 0 else done.stderr)

    def test_a_login_wall_ends_the_batch_with_the_existing_refusal_and_no_resume_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_cli("queue", "--project", tmp, "--action", "add", "--provider", "instagram", REEL, REEL_2)
            first = self.run_cli("queue", "--project", tmp, "--action", "next")
            marked = self.run_cli(
                "queue",
                "--project",
                tmp,
                "--action",
                "mark",
                "--id",
                first["item"]["id"],
                "--failed",
                "--reason",
                "login page",
            )
            self.assertIsNone(marked["cooldown"])
            self.assertEqual("access_refused", marked["stop"]["kind"])
            self.assertIn("Pare o lote", marked["summary"]["line"])
            refused = self.run_cli("queue", "--project", tmp, "--action", "next", code=1)
            self.assertEqual("INVALID_DATA", refused["error_code"])
            self.assertNotIn("reason_code", refused)
            self.assertNotIn("resume_at", json.dumps(refused))
            status = self.run_cli("queue", "--project", tmp, "--action", "status")
            self.assertEqual("login", status["providers"]["instagram"]["stopped"]["class"])
            self.assertIsNone(status["providers"]["instagram"]["cooldown_until"])

    def test_resume_is_attested_and_prints_the_next_step(self):
        statement = "voltei a logar, pode seguir"
        with tempfile.TemporaryDirectory() as tmp:
            self.run_cli("queue", "--project", tmp, "--action", "add", "--provider", "instagram", REEL, REEL_2)
            first = self.run_cli("queue", "--project", tmp, "--action", "next")
            self.run_cli(
                "queue",
                "--project",
                tmp,
                "--action",
                "mark",
                "--id",
                first["item"]["id"],
                "--failed",
                "--reason",
                "login",
            )
            refused = self.run_cli("queue", "--project", tmp, "--action", "next", code=1)
            self.assertIn("--by NOME", refused["message"])
            stopped_at = self.run_cli("queue", "--project", tmp, "--action", "status")["providers"]["instagram"][
                "stopped"
            ]["at"]
            not_given = self.run_cli("queue", "--project", tmp, "--action", "resume", code=1)
            self.assertEqual("INVALID_DATA", not_given["error_code"])
            self.assertNotIn("reason_code", not_given)
            base = ["queue", "--project", tmp, "--action", "resume", "--provider", "instagram"]
            for extra in (
                [],
                ["--by", "Bruno"],
                ["--by", "Bruno", "--phrase", statement],
                ["--by", "Bruno", "--phrase", statement, "--expect", "2020-01-01T00:00:00+00:00"],
                ["--phrase", statement, "--expect", stopped_at],
            ):
                failed = self.run_cli(*base, *extra, code=1)
                self.assertEqual("INVALID_DATA", failed["error_code"], extra)
                self.assertNotIn("reason_code", failed)
            self.run_cli("queue", "--project", tmp, "--action", "next", code=1)
            resumed = self.run_cli(*base, "--by", "Bruno", "--phrase", statement, "--expect", stopped_at)
            self.assertEqual("resume", resumed["action"])
            self.assertEqual("instagram", resumed["provider"])
            self.assertEqual("Bruno", resumed["by"])
            self.assertIn("queue --action next --provider instagram", resumed["summary"]["do"]["command"])
            self.assertNotIn(tmp, json.dumps(resumed["summary"]))
            self.assertNotIn(statement, json.dumps(resumed), "a frase fica só no queue.json privado")
            saved = json.loads((Path(tmp) / "work" / "queue.json").read_text(encoding="utf-8"))
            self.assertEqual(statement, saved["providers"]["instagram"]["last_resume"]["statement"])
            again = self.run_cli(*base, "--by", "Bruno", "--phrase", statement, "--expect", stopped_at, code=1)
            self.assertEqual("INVALID_DATA", again["error_code"])
            self.assertNotIn("reason_code", again)
            nxt = self.run_cli("queue", "--project", tmp, "--action", "next")
            self.assertEqual("instagram:DEF456uvw", nxt["item"]["id"])

    def test_a_null_provider_state_is_no_stop_and_a_corrupted_one_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_cli("queue", "--project", tmp, "--action", "add", "--provider", "instagram", REEL)
            path = Path(tmp) / "work" / "queue.json"
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved["providers"]["tiktok"] = None
            path.write_text(json.dumps(saved), encoding="utf-8")
            self.assertEqual({}, queue.stopped_providers(queue.load(path)))
            self.assertIsNone(queue.stop_of(tmp, "instagram"))
            status = self.run_cli("queue", "--project", tmp, "--action", "status")
            self.assertNotIn("stopped", status["providers"]["tiktok"])
            nxt = self.run_cli("queue", "--project", tmp, "--action", "next", "--provider", "instagram")
            self.assertEqual("instagram:ABC123xyz", nxt["item"]["id"])
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved["providers"]["tiktok"] = "parado"
            path.write_text(json.dumps(saved), encoding="utf-8")
            refused = self.run_cli("queue", "--project", tmp, "--action", "next", "--provider", "instagram", code=1)
            self.assertEqual("INVALID_DATA", refused["error_code"])
            self.assertNotIn("reason_code", refused)
            self.assertIn("tiktok", refused["message"])
            with self.assertRaisesRegex(ValueError, "corrompido"):
                queue.stop_of(tmp, "instagram")

    def test_a_rate_limit_still_waits_and_says_the_wait_is_only_pacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_cli("queue", "--project", tmp, "--action", "add", "--provider", "instagram", REEL, REEL_2)
            first = self.run_cli("queue", "--project", tmp, "--action", "next")
            marked = self.run_cli(
                "queue",
                "--project",
                tmp,
                "--action",
                "mark",
                "--id",
                first["item"]["id"],
                "--failed",
                "--reason",
                "HTTP 429",
            )
            self.assertEqual(1800, marked["cooldown"]["seconds"])
            waiting = self.run_cli("queue", "--project", tmp, "--action", "next")
            self.assertEqual("cooldown", waiting["reason"])
            self.assertTrue(waiting["resume_at"])
            self.assertNotIn("retome", waiting["summary"]["line"].lower())


if __name__ == "__main__":
    unittest.main()

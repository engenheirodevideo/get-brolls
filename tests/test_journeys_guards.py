"""Jornadas das travas: projeto ocupado por outro comando e fila de lote parada por recusa da plataforma.

Em ambos a pessoa leva uma recusa sem perder nada: o estado fica como estava, o comando só leitura segue
respondendo e, quando a causa passa (a trava solta, a retomada atestada), repetir o mesmo comando funciona.
"""

import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _journeys import JourneyCase, write_brief
from _media import skip_unless_ffmpeg

from getbrolls.runtime import project_lock

FIRST = "demo:1:shot:abertura"
REEL = "https://www.instagram.com/reel/ABC123xyz/"
REEL_2 = "https://www.instagram.com/reel/DEF456uvw/"
TIKTOK = "https://www.tiktok.com/@user/video/1234567890"


@skip_unless_ffmpeg
class LockedProjectJourney(JourneyCase):
    def setUp(self):
        super().setUp()
        self.enable_demo()
        write_brief(self.project)
        self.search("mar calmo", "abertura")

    def test_every_writing_stage_is_refused_while_another_command_holds_the_project(self):
        snapshot = self.ledger_bytes()
        commands = (
            ("preview", "--candidate", FIRST, "--start", 0, "--end", 2),
            ("approve", "--candidate", FIRST, "--by", "Ana", "--statement", "aprovo"),
            ("permit", "--candidate", FIRST, "--evidence", "Material próprio."),
            ("fetch", "--candidate", FIRST),
            ("reject", "--candidate", FIRST),
            ("deliver",),
            ("search", "--provider", "demo", "--query", "mar", "--shot", "abertura"),
        )
        with project_lock(self.project):
            for command in commands:
                with self.subTest(command=command[0]):
                    error = self.gb(*command, expect=1)
                    self.assertEqual("LOCKED", error["error_code"])
                    self.assertIn("Outro comando está usando este projeto", error["error"])
                    self.assertFalse(error["state_committed"])
            self.assertEqual(snapshot, self.ledger_bytes())
            self.assertEqual([], self.calls_made(), "nenhuma recusa chegou a pedir mídia à fonte")
            self.assertFalse((self.project / "entrega").exists())

    def test_status_keeps_answering_during_the_lock_and_does_not_touch_the_ledger(self):
        snapshot = self.ledger_bytes()
        with project_lock(self.project):
            status = self.gb("status")
        self.assertEqual(
            2, len(next(r["items"] for r in status["summary"]["stages"] if r["stage"] == "candidatos encontrados"))
        )
        self.assertEqual(snapshot, self.ledger_bytes())

    def test_the_same_chain_runs_after_the_lock_is_released(self):
        with project_lock(self.project):
            self.gb("preview", "--candidate", FIRST, "--start", 0, "--end", 2, expect=1)
        self.assertEqual("pending", self.item(FIRST)["approval"]["status"])
        fetched = self.collect(FIRST)
        self.assertTrue(fetched["output"]["verified"])
        # Uma trava no meio da cadeia não desfaz o que já estava gravado.
        with project_lock(self.project):
            self.gb("deliver", expect=1)
        self.assertEqual("verified", self.item(FIRST)["state"])
        self.assertEqual(1, len(self.gb("deliver")["rows"]))


class StoppedQueueJourney(JourneyCase):
    """O lote social para na recusa da plataforma e só volta com a retomada atestada por uma pessoa."""

    def queue(self, action, *args, expect=0):
        return self.gb("queue", "--action", action, *args, expect=expect)

    def stop_the_batch(self):
        self.queue("add", "--provider", "instagram", REEL, REEL_2)
        first = self.queue("next")
        marked = self.queue("mark", "--id", first["item"]["id"], "--failed", "--reason", "login page")
        return first["item"]["id"], marked

    def test_a_login_wall_stops_the_batch_and_the_person_resumes_it_with_an_attestation(self):
        first_id, marked = self.stop_the_batch()
        self.assertEqual("instagram:ABC123xyz", first_id)
        self.assertIsNone(marked["cooldown"])
        self.assertEqual("access_refused", marked["stop"]["kind"])
        # Parada: nem o próximo item sai nem a fila sugere hora de voltar.
        refused = self.queue("next", expect=1)
        self.assertEqual("INVALID_DATA", refused["error_code"])
        self.assertIn("--by NOME", refused["message"])
        self.assertNotIn("resume_at", str(refused))
        status = self.queue("status")
        stopped = status["providers"]["instagram"]["stopped"]
        self.assertEqual("login", stopped["class"])
        # Retomada: sem nome, sem frase ou com carimbo velho, recusa; o certo libera só o próximo item.
        base = ("--provider", "instagram")
        phrase = "voltei a logar, pode seguir"
        for extra in (
            (),
            ("--by", "Bruno"),
            ("--by", "Bruno", "--phrase", phrase),
            ("--by", "Bruno", "--phrase", phrase, "--expect", "2020-01-01T00:00:00+00:00"),
        ):
            with self.subTest(extra=extra):
                failed = self.queue("resume", *base, *extra, expect=1)
                self.assertEqual("INVALID_DATA", failed["error_code"])
        self.queue("next", expect=1)
        resumed = self.queue("resume", *base, "--by", "Bruno", "--phrase", phrase, "--expect", stopped["at"])
        self.assertEqual(("resume", "instagram", "Bruno"), (resumed["action"], resumed["provider"], resumed["by"]))
        self.assertNotIn(phrase, str(resumed), "a frase fica só no queue.json privado")
        again = self.queue("resume", *base, "--by", "Bruno", "--phrase", phrase, "--expect", stopped["at"], expect=1)
        self.assertEqual("INVALID_DATA", again["error_code"])
        self.assertEqual("instagram:DEF456uvw", self.queue("next")["item"]["id"])

    def test_a_stopped_provider_does_not_hold_the_other_provider_nor_the_project_status(self):
        self.stop_the_batch()
        self.queue("add", "--provider", "tiktok", TIKTOK)
        self.queue("next", "--provider", "instagram", expect=1)
        self.assertEqual("tiktok:1234567890", self.queue("next")["item"]["id"])
        write_brief(self.project)
        self.assertIn("Resumi o projeto", self.gb("status")["summary"]["line"])

    def test_a_rate_limit_is_only_pacing_and_a_refusal_is_not(self):
        self.queue("add", "--provider", "instagram", REEL, REEL_2)
        first = self.queue("next")
        marked = self.queue("mark", "--id", first["item"]["id"], "--failed", "--reason", "HTTP 429")
        self.assertEqual(1800, marked["cooldown"]["seconds"])
        self.assertIsNone(marked.get("stop"))
        waiting = self.queue("next")
        self.assertEqual("cooldown", waiting["reason"])
        self.assertTrue(waiting["resume_at"])

    def test_a_url_from_the_wrong_provider_is_refused_and_the_queue_stays_empty(self):
        error = self.queue("add", "--provider", "instagram", TIKTOK, expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("URL pertence a tiktok", error["error"])
        self.assertEqual(
            {"pending": 0, "active": 0, "done": 0, "failed": 0, "skipped": 0}, self.queue("status")["counts"]
        )
        added = self.queue("add", "--provider", "instagram", REEL)
        self.assertEqual(["instagram:ABC123xyz"], added["added"])


if __name__ == "__main__":
    unittest.main()

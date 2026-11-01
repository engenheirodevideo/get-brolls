"""Jornadas de coleta pela CLI real: brief → busca → candidatos → prévia → aprovação → permit → fetch → entrega.

Rede nenhuma: a fonte é o plugin `demo` de `_journeys` (a rota copia um vídeo sintético) e o corte é
FFmpeg de verdade. Cada teste é um caminho da pessoa, do começo ao fim; as recusas e o conserto depois
delas ficam no mesmo teste, porque é a ordem em que a pessoa as vive. Rede de segurança da modularização.
"""

import hashlib
import json
import unittest

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _journeys import APPROVAL, EVIDENCE, JourneyCase, brief_with, write_brief
from _media import skip_unless_ffmpeg
from _paths import suggested_argv

ABERTURA = "demo:1:shot:abertura"
TEMPESTADE = "demo:1:shot:tempestade"


def stage_items(status, name):
    """Os ids da etapa `name` do resumo do `status`."""
    return next(row["items"] for row in status["summary"]["stages"] if row["stage"] == name)


@skip_unless_ffmpeg
class BriefToDeliveryJourney(JourneyCase):
    """O caminho feliz inteiro, seguindo os comandos que o próprio produto sugere."""

    def run_planned(self, command):
        """Roda o comando de um plano (`brief`/`status`) exatamente como veio, sem trocar nada."""
        return run_cli(*suggested_argv(command), env=self.env)

    def test_the_brief_plan_leads_a_person_from_nothing_to_a_verified_delivery(self):
        self.enable_demo()
        write_brief(self.project)
        self.plan_and_search()
        self.preview_stage()
        self.approval_stage()
        self.fetch_stage()
        self.delivery_stage()
        self.second_beat_stage()

    def plan_and_search(self):
        """BRIEF → plano de busca → candidatos: o comando do plano roda sem edição e liga os candidatos ao beat."""
        plan = self.gb("brief")
        self.assertEqual({"beats": 2, "covered": 0, "missing": 2, "blocked": 0}, plan["coverage"])
        first = plan["beats"][0]
        self.assertEqual("abertura", first["id"])
        self.assertEqual([], first["candidates"])
        for part in ("--provider demo", "--shot abertura", "mar calmo"):
            self.assertIn(part, first["commands"]["search"])
        self.assertIn("abertura", plan["summary"]["next"])
        found = self.run_planned(first["commands"]["search"])
        self.assertEqual([ABERTURA, "demo:2:shot:abertura"], [row["id"] for row in found["items"]])
        self.assertEqual({"abertura"}, {row["shot"] for row in found["items"]})
        self.assertEqual({"pending"}, {row["approval"]["status"] for row in found["items"]})
        covered = self.gb("brief")
        self.assertEqual(1, covered["coverage"]["covered"])
        self.assertEqual([ABERTURA, "demo:2:shot:abertura"], covered["beats"][0]["candidates"])
        self.assertIn("tempestade", covered["summary"]["next"])

    def preview_stage(self):
        """Candidato → prévia: GIF, contact sheet e poster saem na pasta do projeto; a rota rodou uma vez."""
        before = self.gb("status")
        self.assertEqual([ABERTURA, "demo:2:shot:abertura"], stage_items(before, "candidatos encontrados"))
        self.assertEqual([], stage_items(before, "prévias geradas"))
        previewed = self.gb("preview", "--candidate", ABERTURA, "--start", 0, "--end", 2)
        self.assertEqual("awaiting_approval", previewed["state"])
        self.assertEqual((0.0, 2.0), (previewed["segment"]["start_s"], previewed["segment"]["end_s"]))
        for key in ("gif_path", "contact_sheet_path", "poster_path"):
            self.assertTrue((self.project / "brolls" / previewed["preview"][key]).is_file(), key)
        self.assertEqual([ABERTURA], self.calls_made())
        self.assertEqual([ABERTURA], stage_items(self.gb("status"), "prévias geradas"))

    def approval_stage(self):
        """Aprovação humana registrada literalmente; ainda sem direitos, o fetch recusa e não grava."""
        approved = self.approve(ABERTURA)
        self.assertEqual(
            ("approved", "Ana", APPROVAL), tuple(approved["approval"][k] for k in ("status", "by", "statement"))
        )
        self.assertEqual("chat", approved["approval"]["channel"])
        snapshot = self.ledger_bytes()
        refused = self.gb("fetch", "--candidate", ABERTURA, expect=1)
        self.assertEqual("INVALID_DATA", refused["error_code"])
        self.assertIn("permit", refused["error"])
        self.assertFalse(refused["state_committed"])
        self.assertEqual(snapshot, self.ledger_bytes())
        clips = self.project / "brolls" / "clips"
        self.assertFalse(clips.exists() and any(clips.iterdir()))

    def fetch_stage(self):
        """Permit → fetch: o corte final existe, tem o hash registrado e o intervalo aprovado."""
        permitted = self.permit(ABERTURA)
        self.assertEqual("permitted", permitted["rights"]["status"])
        self.assertEqual([EVIDENCE], permitted["rights"]["evidence"])
        fetched = self.gb("fetch", "--candidate", ABERTURA)
        self.assertEqual("verified", fetched["state"])
        out = fetched["output"]
        clip = self.project / "brolls" / out["path"]
        self.assertTrue(out["verified"])
        self.assertEqual(hashlib.sha256(clip.read_bytes()).hexdigest(), out["sha256"])
        self.assertEqual(APPROVAL, fetched["approval"]["statement"])
        self.assertEqual(self.item(ABERTURA)["output"], out)

    def delivery_stage(self):
        """Storyboard e entrega: o ensaio não grava; a entrega cria uma pasta por beat com a origem ao lado."""
        review = self.gb("review")
        page = (self.project / "brolls" / "review.html").read_text(encoding="utf-8")
        self.assertTrue(review["review"].endswith("review.html"))
        self.assertIn(ABERTURA, page)
        self.assertTrue(self.gb("deliver", "--dry-run")["dry_run"])
        self.assertFalse((self.project / "entrega").exists())
        delivered = self.gb("deliver")
        row = delivered["rows"][0]
        self.assertEqual(["abertura"], [r["beat"] for r in delivered["rows"]])
        self.assertEqual(("verified", "permitted"), (row["state"], row["rights"]))
        self.assertTrue((self.project / "entrega" / row["file"]).is_file())
        origem = self.project / "entrega" / row["file"].split("/", 1)[0] / "ORIGEM.md"
        self.assertIn(EVIDENCE, origem.read_text(encoding="utf-8"))
        self.assertTrue((self.project / "entrega" / "README.md").is_file())
        status = self.gb("status")
        for stage in ("itens com permit registrado", "itens entregues", "itens verificados"):
            self.assertEqual([ABERTURA], stage_items(status, stage), stage)
        self.assertEqual("brief-search", status["summary"]["do"]["step"])
        self.assertIn("--shot tempestade", status["summary"]["do"]["command"])

    def second_beat_stage(self):
        """Segundo beat pelo mesmo caminho, seguindo o `do` do status; `verify` confere os dois cortes."""
        self.run_planned(self.gb("status")["summary"]["do"]["command"])
        self.collect(TEMPESTADE, start=1, end=3)
        delivered = self.gb("deliver")
        self.assertEqual(["abertura", "tempestade"], [row["beat"] for row in delivered["rows"]])
        final = self.gb("status")
        self.assertEqual([ABERTURA, TEMPESTADE], stage_items(final, "itens entregues"))
        self.assertEqual({"beats": 2, "covered": 2, "missing": 0, "blocked": 0}, self.gb("brief")["coverage"])
        self.assertNotEqual("brief-search", final["summary"]["do"]["step"])
        verified = self.gb("verify")
        self.assertEqual(2, verified["count"])
        self.assertEqual([ABERTURA, TEMPESTADE], [row["id"] for row in verified["verified"]])

    def test_deliver_again_changes_nothing_and_status_does_not_write(self):
        self.enable_demo()
        write_brief(self.project)
        self.search("mar calmo", "abertura")
        self.collect(ABERTURA)
        first = self.gb("deliver")
        delivered = self.project / "entrega" / first["rows"][0]["file"]
        content = delivered.read_bytes()
        manifest = self.ledger_bytes()
        second = self.gb("deliver")
        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual(content, delivered.read_bytes())
        self.assertEqual(manifest, self.ledger_bytes())
        # `status` é somente leitura: nem o manifest nem a entrega mudam.
        self.gb("status")
        self.assertEqual(manifest, self.ledger_bytes())
        self.assertEqual(content, delivered.read_bytes())


@skip_unless_ffmpeg
class CollectionRefusalJourney(JourneyCase):
    """A pessoa erra a ordem ou o dado; cada recusa explica o que falta, não grava, e o conserto segue."""

    def setUp(self):
        super().setUp()
        self.enable_demo()
        write_brief(self.project)
        self.search("mar calmo", "abertura")

    def refused(self, *args):
        """Roda `args` esperando uma recusa de dados: o manifest fica byte a byte igual."""
        snapshot = self.ledger_bytes()
        error = self.gb(*args, expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertFalse(error["state_committed"])
        self.assertEqual(snapshot, self.ledger_bytes())
        return error["error"]

    def test_stage_order_is_enforced_and_each_refusal_names_the_missing_step(self):
        self.assertIn(
            "preview", self.refused("approve", "--candidate", ABERTURA, "--by", "Ana", "--statement", APPROVAL)
        )
        self.assertIn("Aprovação humana ausente", self.refused("fetch", "--candidate", ABERTURA))
        self.gb("preview", "--candidate", ABERTURA, "--start", 0, "--end", 2)
        self.assertIn("Aprovação humana ausente", self.refused("fetch", "--candidate", ABERTURA))
        self.approve(ABERTURA)
        self.assertIn("permit", self.refused("fetch", "--candidate", ABERTURA))
        self.assertIn("--evidence", self.refused("permit", "--candidate", ABERTURA))
        self.permit(ABERTURA)
        # Recuperação: a mesma cadeia, agora na ordem certa, entrega o corte.
        self.assertTrue(self.gb("fetch", "--candidate", ABERTURA)["output"]["verified"])

    def test_invalid_ranges_and_unknown_candidates_are_refused_and_a_valid_rerun_works(self):
        self.assertIn("Intervalo inválido", self.refused("preview", "--candidate", ABERTURA, "--start", 3, "--end", 1))
        self.assertIn("excede a duração", self.refused("preview", "--candidate", ABERTURA, "--start", 0, "--end", 30))
        self.assertIn("não encontrado", self.refused("preview", "--candidate", "demo:zz", "--start", 0, "--end", 1))
        self.assertIn("não encontrado", self.refused("fetch", "--candidate", "demo:zz"))
        self.assertEqual([], self.calls_made(), "nenhuma recusa chegou a pedir mídia à fonte")
        self.assertEqual(
            "awaiting_approval", self.gb("preview", "--candidate", ABERTURA, "--start", 0, "--end", 2)["state"]
        )

    def test_changing_the_range_after_approval_voids_it_until_a_new_approval(self):
        self.gb("preview", "--candidate", ABERTURA, "--start", 0, "--end", 2)
        self.approve(ABERTURA)
        self.permit(ABERTURA)
        moved = self.gb("preview", "--candidate", ABERTURA, "--start", 1, "--end", 3)
        self.assertEqual(("pending", "awaiting_approval"), (moved["approval"]["status"], moved["state"]))
        self.assertIn("Aprovação humana ausente", self.refused("fetch", "--candidate", ABERTURA))
        self.approve(ABERTURA)
        fetched = self.gb("fetch", "--candidate", ABERTURA)
        self.assertEqual(1.0, fetched["segment"]["start_s"])
        self.assertTrue(fetched["output"]["verified"])
        self.assertEqual(2.0, self.gb("verify")["verified"][0]["media"]["duration_s"])

    def test_an_approval_whose_range_was_edited_behind_the_cli_is_not_honoured(self):
        self.gb("preview", "--candidate", ABERTURA, "--start", 0, "--end", 2)
        self.approve(ABERTURA)
        self.permit(ABERTURA)
        manifest = self.project / "brolls" / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["items"][0]["segment"]["end_s"] = 4.0
        manifest.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual("approved", self.item(ABERTURA)["approval"]["status"])
        self.assertIn("Aprovação humana ausente", self.refused("fetch", "--candidate", ABERTURA))
        self.assertEqual([], list((self.project / "brolls").glob("clips/*")))

    def test_a_rejected_candidate_cannot_be_fetched_until_the_person_approves_it_again(self):
        self.gb("preview", "--candidate", ABERTURA, "--start", 0, "--end", 2)
        self.approve(ABERTURA)
        self.permit(ABERTURA)
        rejected = self.gb("reject", "--candidate", ABERTURA, "--reason", "Não é o mar certo.")
        self.assertEqual("rejected", rejected["approval"]["status"])
        self.assertIn("Aprovação humana ausente", self.refused("fetch", "--candidate", ABERTURA))
        self.assertEqual(["Não é o mar certo."], [self.item(ABERTURA)["rejection"]["reason"]])
        self.approve(ABERTURA)
        self.assertNotIn("rejection", self.item(ABERTURA))
        self.assertTrue(self.gb("fetch", "--candidate", ABERTURA)["output"]["verified"])

    def test_a_tampered_clip_fails_verify_and_a_rerun_after_the_fix_passes(self):
        fetched = self.collect(ABERTURA)
        clip = self.project / "brolls" / fetched["output"]["path"]
        original = clip.read_bytes()
        damaged = bytearray(original)
        damaged[len(damaged) // 2] ^= 0xFF
        clip.chmod(0o644)
        clip.write_bytes(bytes(damaged))
        error = self.gb("verify", expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn(ABERTURA, error["message"])
        self.assertFalse(self.item(ABERTURA)["output"]["verified"])
        # `deliver` não leva um corte que deixou de estar verificado.
        delivered = self.gb("deliver")
        self.assertEqual([], delivered["rows"])
        clip.write_bytes(original)
        self.assertEqual(1, self.gb("verify")["count"])
        self.assertTrue(self.item(ABERTURA)["output"]["verified"])


class BriefAndSearchRefusalJourney(JourneyCase):
    """O plano de busca recusa o que o BRIEF.md não permite; corrigir o brief e repetir resolve."""

    def setUp(self):
        super().setUp()
        self.enable_demo()

    def test_a_source_outside_the_beat_is_refused_and_registers_nothing_until_the_brief_allows_it(self):
        write_brief(self.project)
        error = self.gb("search", "--provider", "youtube", "--query", "mar", "--shot", "abertura", expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("só aceita material de demo", error["error"])
        self.assertFalse((self.project / "brolls" / "manifest.json").exists() and self.manifest()["items"])
        # Recuperação 1: a fonte certa passa. Recuperação 2: o brief passa a permitir a outra fonte.
        self.assertEqual(2, len(self.search("mar calmo", "abertura")["items"]))
        data = brief_with()
        data["beats"][1]["allowed_sources"] = ["demo", "youtube"]
        write_brief(self.project, data)
        self.assertEqual(["demo", "youtube"], self.gb("brief")["beats"][1]["resolved"]["allowed_sources"])

    def test_a_search_without_results_is_an_answer_not_a_failure_and_leaves_the_beat_uncovered(self):
        write_brief(self.project)
        empty = self.search("nada aqui", "abertura")
        self.assertEqual([], empty["items"])
        self.assertIn("Nenhum candidato", empty["summary"]["line"])
        self.assertEqual(0, self.gb("brief")["coverage"]["covered"])
        self.assertEqual(2, len(self.search("mar calmo", "abertura")["items"]))
        self.assertEqual(1, self.gb("brief")["coverage"]["covered"])

    def test_searching_the_same_beat_twice_does_not_duplicate_candidates(self):
        write_brief(self.project)
        self.search("mar calmo", "abertura")
        again = self.search("mar calmo", "abertura")
        self.assertEqual(2, len(again["items"]))
        self.assertEqual([ABERTURA, "demo:2:shot:abertura"], [row["id"] for row in self.manifest()["items"]])

    def test_a_brief_from_a_newer_version_is_refused_and_a_valid_one_is_accepted(self):
        write_brief(self.project, {"version": 2})
        error = self.gb("brief", "--validate", expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("versão mais nova", error["error"])
        write_brief(self.project)
        validated = self.gb("brief", "--validate")
        self.assertTrue(validated["valid"])
        self.assertEqual(2, validated["beats"])

    def test_a_source_that_is_not_installed_is_refused_by_the_brief_until_its_plugin_is_enabled(self):
        self.gb("plugins", "--action", "disable", "--id", "demo", "--yes", project=False)
        write_brief(self.project)
        error = self.gb("brief", "--validate", expect=1)
        self.assertEqual("INVALID_DATA", error["error_code"])
        self.assertIn("defaults.allowed_sources", error["error"])
        self.assertIn("youtube", error["error"])
        self.gb("plugins", "--action", "enable", "--id", "demo", "--yes", project=False)
        self.assertTrue(self.gb("brief", "--validate")["valid"])


if __name__ == "__main__":
    unittest.main()

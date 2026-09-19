"""Regression tests for `scripts/getbrolls/delivery.py` safety fixes.

Covers: refusing a symlinked `entrega/` root, confining every sweep deletion to the
real `entrega/` directory, only treating a symlink as generator-owned when it both
matches the naming pattern and resolves into this project's `brolls/`, distinguishing
an I/O failure from a real content difference when comparing delivered files, surfacing
a failed read-only chmod through the project's warning channel instead of swallowing
it, and never delivering an item whose `output.verified` is explicitly `False` or whose
approval was rejected.
"""

import os
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import delivery, runtime
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment


def fetched(source_id, title, shot=None, clip: str | None = "clips/x.mp4", sheet: str | None = "previews/x.jpg"):
    """Same shape `tests/test_delivery.py` uses: an approved, verified, collected candidate."""
    c = candidate("local", source_id, title, source_url="https://example.org/" + source_id)
    set_segment(c, 0, 2)
    c["creator"]["name"] = "Autora Exemplo"
    c["preview"]["contact_sheet_path"] = sheet
    c["approval"] = {
        "status": "approved",
        "by": "Humano",
        "at": now(),
        "revision": 1,
        "channel": "chat",
        "statement": "aprovo",
    }
    c["rights"]["status"] = "permitted"
    c["rights"]["evidence"] = ["Condições conferidas na página da fonte"]
    c["output"] = {"path": clip, "sha256": "a" * 64, "verified": True}
    c["state"] = "verified"
    if shot:
        c["shot"] = shot
        c["id"] += ":shot:" + shot
    return c


def project(tmp, items):
    """Projeto sintético com os arquivos de `brolls/` realmente no disco."""
    ledger = Ledger(tmp)
    stored = [ledger.add(c) for c in items]
    ledger.save_many("fixture", stored)
    for c in stored:
        for rel in (c["output"].get("path"), c["preview"].get("contact_sheet_path")):
            if rel:
                path = ledger.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(b"conteudo de " + rel.encode())
    return ledger


def _force_rmtree(path):
    """Remove `path` even if a frozen (read-only) delivered file ended up inside it.

    Only needed for the negative-proof cleanup: a fixed `build_delivery` never writes
    here, but this keeps teardown robust regardless.
    """
    import shutil
    import stat as stat_module

    def _on_error(func, target, exc_info):
        os.chmod(target, stat_module.S_IWRITE | stat_module.S_IREAD)
        func(target)

    shutil.rmtree(path, onerror=_on_error)


def _symlinks_available(tmp):
    """True quando este ambiente consegue mesmo criar um symlink (falha limpa senão)."""
    probe_target = Path(tmp) / "probe-target"
    probe_target.write_bytes(b"x")
    probe_link = Path(tmp) / "probe-link"
    try:
        probe_link.symlink_to(probe_target)
    except (OSError, NotImplementedError):
        return False
    return True


@unittest.skipIf(os.name == "nt", "Symlinks exigem privilégio extra no Windows nativo.")
class SymlinkedDeliveryRoot(unittest.TestCase):
    """Finding: `entrega/` como symlink deixava a varredura apagar arquivos de outra pasta."""

    def test_deliver_refuses_when_entrega_itself_is_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            if not _symlinks_available(tmp):
                self.skipTest("symlinks indisponíveis neste ambiente")
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            victim = Path(tmp).parent / (Path(tmp).name + "-victima")
            victim.mkdir()
            (victim / "ORIGEM.md").write_text("nao mexer", encoding="utf-8")
            (victim / "arquivo-da-vitima.txt").write_text("nao mexer", encoding="utf-8")
            entrega = Path(tmp) / delivery.DELIVERY_DIR
            entrega.symlink_to(victim, target_is_directory=True)
            try:
                with self.assertRaises(ValueError) as caught:
                    delivery.build_delivery(tmp)
                self.assertIn("link simbólico", str(caught.exception))
                # Nada na pasta-alvo foi tocado: a recusa acontece antes de qualquer escrita.
                self.assertEqual(
                    {"ORIGEM.md", "arquivo-da-vitima.txt"},
                    {p.name for p in victim.iterdir()},
                )
                self.assertEqual("nao mexer", (victim / "ORIGEM.md").read_text(encoding="utf-8"))
            finally:
                entrega.unlink()
                _force_rmtree(victim)

    def test_deliver_refuses_even_in_dry_run(self):
        """Ensaio também recusa: só relatar já seguiria o link para fora do projeto."""
        with tempfile.TemporaryDirectory() as tmp:
            if not _symlinks_available(tmp):
                self.skipTest("symlinks indisponíveis neste ambiente")
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            victim = Path(tmp).parent / (Path(tmp).name + "-victima-dry")
            victim.mkdir()
            entrega = Path(tmp) / delivery.DELIVERY_DIR
            entrega.symlink_to(victim, target_is_directory=True)
            try:
                with self.assertRaises(ValueError):
                    delivery.build_delivery(tmp, dry_run=True)
            finally:
                entrega.unlink()
                _force_rmtree(victim)

    def test_sweep_never_deletes_through_a_symlinked_beat_directory(self):
        """Mesmo sem `entrega/` em si ser link, uma pasta de beat virada link não confina."""
        with tempfile.TemporaryDirectory() as tmp:
            if not _symlinks_available(tmp):
                self.skipTest("symlinks indisponíveis neste ambiente")
            root = Path(tmp) / "entrega"
            root.mkdir()
            victim = Path(tmp) / "victima"
            victim.mkdir()
            (victim / "ORIGEM.md").write_text("nao mexer", encoding="utf-8")
            beat_link = root / "01-abertura-alvo"
            beat_link.symlink_to(victim, target_is_directory=True)
            try:
                # `01-abertura-alvo` não está mais no plano (`expected` vazio): sem a
                # confinação, `_sweep` apagaria `ORIGEM.md` dentro do alvo do link.
                removed, _kept = delivery._sweep(root, expected=set(), dry_run=False, owned=(), brolls_root=None)
                self.assertEqual([], removed)
                self.assertTrue((victim / "ORIGEM.md").is_file())
                self.assertEqual("nao mexer", (victim / "ORIGEM.md").read_text(encoding="utf-8"))
            finally:
                beat_link.unlink()


@unittest.skipIf(os.name == "nt", "Symlinks exigem privilégio extra no Windows nativo.")
class ForeignSymlinksSurviveSweep(unittest.TestCase):
    """Finding: qualquer symlink em `entrega/` era tratado como órfão do gerador."""

    def test_a_symlink_the_user_made_to_something_outside_brolls_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            if not _symlinks_available(tmp):
                self.skipTest("symlinks indisponíveis neste ambiente")
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            delivery.build_delivery(tmp)
            beat_dir = next(p for p in (Path(tmp) / "entrega").iterdir() if p.is_dir() and p.name != delivery.NO_BEAT)
            outside = Path(tmp) / "minha-mídia-pessoal.mp4"
            outside.write_bytes(b"nao e do get-brolls")
            # Nome que bate com o padrão de mídia do beat (`<pasta>-2.<ext>`), mas o alvo
            # não fica dentro de `brolls/`: continua sendo um link da pessoa.
            user_link = beat_dir / f"{beat_dir.name}-2.mp4"
            user_link.symlink_to(outside)
            report = delivery.build_delivery(tmp)
            self.assertTrue(user_link.is_file() or user_link.is_symlink())
            self.assertIn(f"{beat_dir.name}/{user_link.name}", report["kept"])
            self.assertTrue(outside.is_file())

    def test_a_symlink_the_generator_would_make_into_brolls_is_still_swept_as_orphan(self):
        """Continua podando o que é seu: link para dentro de `brolls/` some quando vira órfão."""
        with tempfile.TemporaryDirectory() as tmp:
            if not _symlinks_available(tmp):
                self.skipTest("symlinks indisponíveis neste ambiente")
            ledger = project(tmp, [fetched("a", "Palco", shot="abertura")])
            delivery.build_delivery(tmp)
            beat_dir = next(p for p in (Path(tmp) / "entrega").iterdir() if p.is_dir() and p.name != delivery.NO_BEAT)
            inside_brolls = ledger.root / "clips" / "x.mp4"
            generator_like_link = beat_dir / f"{beat_dir.name}-2.mp4"
            generator_like_link.symlink_to(inside_brolls)
            # Renomear o beat torna a pasta inteira órfã na próxima rodada.
            ledger.data["items"][0]["shot"] = "fechamento"
            ledger.save_many("fixture", ledger.data["items"])
            delivery.build_delivery(tmp)
            self.assertFalse(beat_dir.exists())


class ComparisonIOFailureIsNotAnEditClaim(unittest.TestCase):
    """Finding: um `OSError` ao comparar virava "parece edição sua"."""

    def test_link_or_copy_reports_the_os_error_instead_of_blaming_an_edit(self):
        """Wiring test: `link_or_copy` must translate `_CompareError` into an IO message,
        never into the "parece edição sua" content-mismatch message."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            dest = Path(tmp) / "dest.mp4"
            src.write_bytes(b"mesmo conteudo")
            dest.write_bytes(b"mesmo conteudo")

            broken = delivery._CompareError(f"[Errno 13] Permission denied: '{dest}'")
            with patch.object(delivery, "_same_file", side_effect=broken), self.assertRaises(ValueError) as caught:
                delivery.link_or_copy(src, dest)
            message = str(caught.exception)
            self.assertNotIn("parece edição sua", message)
            self.assertIn(str(dest), message)
            self.assertIn("Permission denied", message)

    def test_same_file_raises_compare_error_instead_of_silently_returning_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nao-existe.mp4"
            other = Path(tmp) / "outro.mp4"
            other.write_bytes(b"x")
            with self.assertRaises(delivery._CompareError):
                delivery._same_file(missing, other)

    def test_same_bytes_raises_compare_error_instead_of_silently_returning_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nao-existe.mp4"
            other = Path(tmp) / "outro.mp4"
            other.write_bytes(b"x")
            with self.assertRaises(delivery._CompareError):
                delivery._same_bytes(missing, other)


class FreezeFailureIsSurfacedAsAWarning(unittest.TestCase):
    """Finding: `_freeze` engolia `OSError`, deixando a promessa de somente-leitura falsa."""

    def test_a_chmod_failure_records_a_warning_instead_of_vanishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "congelado.mp4"
            path.write_bytes(b"bytes")

            def refuse(*args, **kwargs):
                raise OSError(13, "Permission denied")

            original = os.chmod
            os.chmod = refuse
            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                delivery._freeze(path, "hardlink")
            finally:
                runtime.ACTIVE.reset(token)
                os.chmod = original
            self.assertTrue(any(w["code"] == "DELIVERY_FREEZE_FAILED" for w in event["warnings"]))
            warning = next(w for w in event["warnings"] if w["code"] == "DELIVERY_FREEZE_FAILED")
            self.assertIn(str(path), warning["message"])

    def test_a_successful_freeze_records_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "congelado.mp4"
            path.write_bytes(b"bytes")
            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                delivery._freeze(path, "hardlink")
            finally:
                runtime.ACTIVE.reset(token)
            self.assertEqual([], event["warnings"])


class UnverifiedAndRejectedItemsAreNeverDelivered(unittest.TestCase):
    """Finding: `_plan` entregava por `output.path` sozinho, sem olhar integridade/aprovação."""

    def test_an_item_with_verified_explicitly_false_is_skipped_with_a_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = fetched("a", "Palco", shot="abertura")
            bad["output"]["verified"] = False
            project(tmp, [bad])
            report = delivery.build_delivery(tmp)
            self.assertEqual([], report["items"])
            self.assertEqual(1, len(report["skipped"]))
            self.assertEqual(bad["id"], report["skipped"][0]["id"])
            self.assertTrue(report["skipped"][0]["reason"])
            # Não fica em `entrega/`: nada de pasta de beat, nada de mídia gravada.
            self.assertFalse(any((Path(tmp) / "entrega").rglob("*.mp4")))

    def test_a_rejected_item_is_skipped_even_if_output_still_has_a_path(self):
        """Defesa extra: mesmo que `reject` ainda não tenha limpado `output`, `_plan` recusa."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = fetched("a", "Palco", shot="abertura")
            bad["approval"]["status"] = "rejected"
            project(tmp, [bad])
            report = delivery.build_delivery(tmp)
            self.assertEqual([], report["items"])
            self.assertEqual(1, len(report["skipped"]))
            self.assertEqual(bad["id"], report["skipped"][0]["id"])

    def test_an_item_with_verified_unset_still_delivers_normally(self):
        """`verified: None` (nem `True` nem `False` explícito) não pode mudar o comportamento."""
        with tempfile.TemporaryDirectory() as tmp:
            fine = fetched("a", "Palco", shot="abertura")
            fine["output"]["verified"] = None
            project(tmp, [fine])
            report = delivery.build_delivery(tmp)
            self.assertEqual(1, len(report["items"]))
            self.assertEqual([], report["skipped"])

    def test_an_item_with_verified_true_still_delivers_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            fine = fetched("a", "Palco", shot="abertura")
            fine["output"]["verified"] = True
            project(tmp, [fine])
            report = delivery.build_delivery(tmp)
            self.assertEqual(1, len(report["items"]))
            self.assertEqual([], report["skipped"])

    def test_a_freshly_fetched_clip_with_no_shot_still_delivers_as_an_orphan(self):
        """`fetch` grava `verified: True`; a checagem não pode excluir clipes recém-buscados."""
        with tempfile.TemporaryDirectory() as tmp:
            fresh = fetched("a", "Sem beat")
            project(tmp, [fresh])
            report = delivery.build_delivery(tmp)
            self.assertEqual(1, len(report["items"]))
            self.assertEqual([], report["skipped"])


if __name__ == "__main__":
    unittest.main()

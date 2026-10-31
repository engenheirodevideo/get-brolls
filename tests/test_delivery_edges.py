"""Bordas de `deliver`: registro de entrega varrido, symlink da pessoa, ensaio que vê a pasta vazia, thaw em link."""

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import
from _portable import unlink_frozen
from _symlink import needs_symlink
from test_delivery_safety import fetched, project

from getbrolls import delivery, delivery_sweep
from getbrolls.ledger import Ledger


def media_of(tmp):
    """O arquivo de mídia do único beat entregue."""
    return next(p for p in (Path(tmp) / delivery.DELIVERY_DIR).rglob("*.mp4"))


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gb-dedges-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)


class SweptItemForgetsItsRecordTests(TempCase):
    def test_item_that_stops_being_deliverable_loses_its_record_with_its_files(self):
        ledger = project(self.tmp, [fetched("a", "Palco", shot="abertura"), fetched("b", "Mesa", shot="fecho")])
        delivery.build_delivery(self.tmp, ledger=ledger)
        self.assertTrue(all("delivery" in c for c in Ledger(self.tmp).data["items"]))
        gone = Ledger(self.tmp)
        victim = next(c for c in gone.data["items"] if c["shot"] == "abertura")
        victim["output"]["verified"] = False
        gone.save_many("test", [victim])
        report = delivery.build_delivery(self.tmp)
        self.assertTrue(any("abertura" in rel for rel in report["removed"]))
        items = {c["shot"]: c for c in Ledger(self.tmp).data["items"]}
        self.assertNotIn("delivery", items["abertura"])
        self.assertIn("delivery", items["fecho"])

    def test_item_whose_edited_file_is_kept_keeps_its_record(self):
        ledger = project(self.tmp, [fetched("a", "Palco", shot="abertura")])
        delivery.build_delivery(self.tmp, ledger=ledger)
        media = media_of(self.tmp)
        unlink_frozen(media)
        media.write_bytes(b"edicao da pessoa")
        again = Ledger(self.tmp)
        item = again.data["items"][0]
        item["output"]["verified"] = False
        again.save_many("test", [item])
        with self.assertRaises(ValueError):
            delivery.build_delivery(self.tmp)
        self.assertEqual(b"edicao da pessoa", media.read_bytes())
        self.assertIn("delivery", Ledger(self.tmp).data["items"][0])

    def test_dry_run_never_touches_the_record(self):
        ledger = project(self.tmp, [fetched("a", "Palco", shot="abertura")])
        delivery.build_delivery(self.tmp, ledger=ledger)
        again = Ledger(self.tmp)
        item = again.data["items"][0]
        item["output"]["verified"] = False
        again.save_many("test", [item])
        delivery.build_delivery(self.tmp, dry_run=True)
        self.assertIn("delivery", Ledger(self.tmp).data["items"][0])


class DryRunSeesTheEmptiedFolderTests(TempCase):
    def unverify_first(self):
        again = Ledger(self.tmp)
        item = next(c for c in again.data["items"] if c["shot"] == "abertura")
        item["output"]["verified"] = False
        again.save_many("test", [item])

    def test_dry_run_removed_equals_the_real_run_removed(self):
        ledger = project(self.tmp, [fetched("a", "Palco", shot="abertura"), fetched("b", "Mesa", shot="fecho")])
        delivery.build_delivery(self.tmp, ledger=ledger)
        self.unverify_first()
        planned = delivery.build_delivery(self.tmp, dry_run=True)
        real = delivery.build_delivery(self.tmp)
        self.assertEqual(sorted(real["removed"]), sorted(planned["removed"]))
        self.assertTrue(any("/" not in rel and "abertura" in rel for rel in real["removed"]), real["removed"])

    def test_dry_run_keeps_a_folder_that_still_holds_the_persons_file(self):
        ledger = project(self.tmp, [fetched("a", "Palco", shot="abertura")])
        delivery.build_delivery(self.tmp, ledger=ledger)
        folder = media_of(self.tmp).parent
        (folder / "anotacao.txt").write_text("minha", encoding="utf-8")
        self.unverify_first()
        planned = delivery.build_delivery(self.tmp, dry_run=True)
        real = delivery.build_delivery(self.tmp)
        self.assertNotIn(folder.name, planned["removed"])
        self.assertEqual(sorted(real["removed"]), sorted(planned["removed"]))


@needs_symlink
class UserSymlinkInAPlannedSlotTests(TempCase):
    def user_link(self):
        ledger = project(self.tmp, [fetched("a", "Palco", shot="abertura")])
        delivery.build_delivery(self.tmp, ledger=ledger)
        media = media_of(self.tmp)
        mine = Path(self.tmp) / "minha-edicao.mp4"
        mine.write_bytes(b"minha edicao")
        unlink_frozen(media)
        media.symlink_to(mine)
        return media, mine, ledger

    def test_deliver_refuses_to_replace_a_link_to_the_persons_own_file(self):
        media, mine, _ = self.user_link()
        with self.assertRaises(ValueError) as caught:
            delivery.build_delivery(self.tmp)
        self.assertIn("parece edição sua", str(caught.exception))
        self.assertTrue(media.is_symlink())
        self.assertEqual(mine.resolve(), media.resolve())
        self.assertEqual(b"minha edicao", mine.read_bytes())

    def test_dry_run_reports_the_same_conflict_without_touching_the_link(self):
        media, _, _ = self.user_link()
        planned = delivery.build_delivery(self.tmp, dry_run=True)
        self.assertTrue(any("parece edição sua" in c for c in planned["conflicts"]), planned.get("conflicts"))
        self.assertTrue(media.is_symlink())

    def test_a_link_to_an_older_clip_inside_brolls_is_still_replaced(self):
        media, _, ledger = self.user_link()
        older = ledger.root / "clips" / "antigo.mp4"
        older.write_bytes(b"revisao antiga")
        media.unlink()
        media.symlink_to(older)
        delivery.build_delivery(self.tmp)
        self.assertEqual(b"conteudo de clips/x.mp4", media.read_bytes())
        self.assertEqual(b"revisao antiga", older.read_bytes())

    def test_a_dangling_link_is_replaced(self):
        media, _, _ = self.user_link()
        media.unlink()
        media.symlink_to(Path(self.tmp) / "nao-existe.mp4")
        delivery.build_delivery(self.tmp)
        self.assertEqual(b"conteudo de clips/x.mp4", media.read_bytes())


@needs_symlink
@unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "precisa de pasta sem escrita")
class ThawUnlinkNeverFollowsALinkTests(TempCase):
    def test_a_failed_unlink_of_a_link_leaves_the_clip_mode_alone(self):
        clip = Path(self.tmp) / "clip.mp4"
        clip.write_bytes(b"clipe")
        clip.chmod(0o444)
        folder = Path(self.tmp) / "pasta"
        folder.mkdir()
        link = folder / "link.mp4"
        link.symlink_to(clip)
        folder.chmod(0o555)
        self.addCleanup(folder.chmod, 0o755)
        with self.assertRaises(PermissionError):
            delivery_sweep.thaw_unlink(link)
        self.assertEqual(0o444, stat.S_IMODE(clip.stat().st_mode))
        self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()

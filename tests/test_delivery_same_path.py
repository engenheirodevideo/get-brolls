"""O arquivo planejado de `entrega/` que já existe: revisão nova, contact sheet e link congelado.

O nome em `entrega/` não muda entre revisões do mesmo item. O que está ali dá lugar ao
novo só quando ainda é nosso (um clipe de `brolls/` pelo inode ou pelos bytes, o contact
sheet que o `deliver` deixou) e mora dentro da `entrega/` real; tirar um hardlink, ali ou
na varredura, nunca destrava o clipe de `brolls/`; e o que não sai vira conflito do item,
sem abortar a entrega.
"""

import contextlib
import os
import stat as stat_module
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _symlink import needs_symlink
from test_delivery_safety import (
    _brief_with_target,
    _change_item,
    _force_rmtree,
    _symlinks_available,
    _two_delivered,
    fetched,
    project,
)

from getbrolls import delivery
from getbrolls.commands import mark_rejected


def _new_revision(tmp, clip="clips/a.mp4"):
    """`fetch` de outro corte do mesmo item: um `-r2` novo em `clips/`, o antigo continua no disco."""
    new = Path(tmp) / "brolls" / "clips" / "a-r2.mp4"
    new.write_bytes(b"bytes da revisao 2")
    _change_item(tmp, clip, lambda c: c["output"].update(path="clips/a-r2.mp4"))
    return new


class SamePathKnowsEveryRevision(unittest.TestCase):
    """O nome em `entrega/` não muda entre revisões do mesmo item, no mesmo beat.

    Depois de um corte novo, o arquivo planejado ainda é o link (ou a cópia intocada) do
    clipe antigo, que continua em `brolls/`: ele dá lugar à revisão nova em vez de virar
    "parece edição sua". O contact sheet, que o `preview` regrava no mesmo caminho com
    inode novo, também é trocado. Só uma cópia que a pessoa editou continua em conflito.
    """

    def test_a_new_revision_replaces_the_untouched_file_in_the_same_beat(self):
        for env in ({}, {"GB_DELIVERY_COPY": "1"}):
            with self.subTest(env=env), tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env):
                delivered = _two_delivered(tmp)
                old = Path(tmp) / "brolls" / "clips" / "a.mp4"
                old_mode = stat_module.S_IMODE(old.stat().st_mode)
                new = _new_revision(tmp)
                media = next(path for path in delivered.values() if path.read_bytes() == old.read_bytes())
                dry = delivery.build_delivery(tmp, dry_run=True)
                self.assertNotIn("conflicts", dry)
                report = delivery.build_delivery(tmp)
                item = next(i for i in report["items"] if Path(tmp, i["path"]) == media)
                self.assertEqual(b"bytes da revisao 2", media.read_bytes())
                self.assertEqual("copy" if env else "hardlink", item["method"])
                self.assertEqual([], report["kept"])
                # O clipe antigo fica em brolls/, com o mesmo conteúdo e o mesmo modo.
                self.assertEqual(b"conteudo de clips/a.mp4", old.read_bytes())
                self.assertEqual(old_mode, stat_module.S_IMODE(old.stat().st_mode))
                self.assertTrue(new.is_file())

    def test_an_edited_copy_in_the_same_beat_still_conflicts_after_a_new_revision(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
            delivered = _two_delivered(tmp)
            media = next(path for path in delivered.values() if path.read_bytes() == b"conteudo de clips/a.mp4")
            media.write_bytes(b"MEU CORTE EDITADO")
            _new_revision(tmp)
            dry = delivery.build_delivery(tmp, dry_run=True)
            with self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertIn("parece edição sua", str(caught.exception))
            self.assertEqual([str(caught.exception)], dry["conflicts"])
            self.assertEqual(b"MEU CORTE EDITADO", media.read_bytes())

    def test_a_contact_sheet_regenerated_in_place_is_replaced_in_the_same_beat(self):
        with tempfile.TemporaryDirectory() as tmp:
            delivered = _two_delivered(tmp)
            sheets = sorted(path.parent / "contact-sheet.jpg" for path in delivered.values())
            staged = Path(tmp) / "brolls" / "previews" / "stage.jpg"
            staged.write_bytes(b"sheet regravado")
            staged.replace(Path(tmp) / "brolls" / "previews" / "a.jpg")
            self.assertNotIn("conflicts", delivery.build_delivery(tmp, dry_run=True))
            report = delivery.build_delivery(tmp)
            self.assertEqual(2, len(report["items"]))
            self.assertIn(b"sheet regravado", [sheet.read_bytes() for sheet in sheets])


def _regenerate_preview(tmp, name="a.jpg"):
    """O `preview` regrava o contact sheet no mesmo caminho, com inode novo."""
    staged = Path(tmp) / "brolls" / "previews" / "stage.jpg"
    staged.write_bytes(b"sheet regravado")
    staged.replace(Path(tmp) / "brolls" / "previews" / name)


class EditedContactSheetIsKept(unittest.TestCase):
    """O contact sheet que o `deliver` deixou dá lugar ao novo; o que a pessoa editou fica e é conflito.

    O manifesto guarda o sha256 do sheet entregue: só um arquivo que ainda tem esses bytes
    é a prévia velha. Com `GB_DELIVERY_COPY=1` o sheet é cópia, e o `COPY_NOTE` promete que
    o `deliver` não mexe numa cópia editada.
    """

    def _sheet_of_a(self, delivered):
        media = next(path for path in delivered.values() if path.read_bytes() == b"conteudo de clips/a.mp4")
        return media.parent / "contact-sheet.jpg"

    def _refused(self, tmp, sheet):
        dry = delivery.build_delivery(tmp, dry_run=True)
        with self.assertRaises(ValueError) as caught:
            delivery.build_delivery(tmp)
        message = str(caught.exception)
        self.assertIn("parece edição sua", message)
        self.assertIn(sheet.relative_to(Path(tmp) / "entrega").as_posix(), message)
        self.assertEqual([message], dry["conflicts"])
        return message

    def test_an_edited_sheet_copy_is_a_conflict_and_survives(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
            sheet = self._sheet_of_a(_two_delivered(tmp))
            self.assertEqual(1, sheet.stat().st_nlink)
            sheet.write_bytes(b"ANOTADO PELA PESSOA")
            self._refused(tmp, sheet)
            self.assertEqual(b"ANOTADO PELA PESSOA", sheet.read_bytes())
            # Nem a prévia regravada depois passa por cima da edição.
            _regenerate_preview(tmp)
            self._refused(tmp, sheet)
            self.assertEqual(b"ANOTADO PELA PESSOA", sheet.read_bytes())

    def test_an_edit_saved_over_the_hardlinked_sheet_is_a_conflict_and_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet = self._sheet_of_a(_two_delivered(tmp))
            # O editor salva num temporário e troca pelo nome: o hardlink com a prévia se desfaz.
            saved = sheet.with_name("salvando.tmp")
            saved.write_bytes(b"ANOTADO PELA PESSOA")
            saved.replace(sheet)
            self._refused(tmp, sheet)
            self.assertEqual(b"ANOTADO PELA PESSOA", sheet.read_bytes())

    def test_a_regenerated_preview_replaces_the_sheet_copy_deliver_left(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
            sheet = self._sheet_of_a(_two_delivered(tmp))
            _regenerate_preview(tmp)
            self.assertNotIn("conflicts", delivery.build_delivery(tmp, dry_run=True))
            report = delivery.build_delivery(tmp)
            self.assertEqual(2, len(report["items"]))
            self.assertEqual(b"sheet regravado", sheet.read_bytes())
            # O registro acompanha o sheet novo: a próxima prévia regravada também passa.
            _regenerate_preview(tmp)
            delivery.build_delivery(tmp)
            self.assertEqual(b"sheet regravado", sheet.read_bytes())

    def test_a_sheet_delivered_without_its_digest_on_record_still_conflicts(self):
        """Uma entrega de versão anterior não guardou o sha256: sem prova de que é a prévia velha, fica."""
        with tempfile.TemporaryDirectory() as tmp:
            sheet = self._sheet_of_a(_two_delivered(tmp))
            _change_item(tmp, "clips/a.mp4", lambda c: c["delivery"].pop("sheet_sha256"))
            _regenerate_preview(tmp)
            self._refused(tmp, sheet)
            self.assertEqual(b"conteudo de previews/a.jpg", sheet.read_bytes())


class UnlinkKeepsTheClipFrozen(unittest.TestCase):
    """Tirar de `entrega/` o nome de um clipe congelado nunca deixa o clipe de `brolls/` destravado.

    No Windows o `unlink` de um somente-leitura falha e o degelo devolve a escrita ao inode
    compartilhado; se o `unlink` falha de novo (o editor segura o arquivo aberto) ou um
    Ctrl+C cai no meio, o modo do clipe volta mesmo assim.
    """

    def _linked(self, tmp):
        clip = Path(tmp) / "clipe.mp4"
        clip.write_bytes(b"bytes do clipe")
        name = Path(tmp) / "entregue.mp4"
        os.link(clip, name)
        clip.chmod(0o444)
        return clip, name

    def _unlink(self, name, fail):
        """Troca `Path.unlink` por um que chama `fail(tentativa)` para `name`."""
        real_unlink = Path.unlink
        tries = []

        def unlink(path, missing_ok=False):
            if path == name:
                tries.append(path)
                fail(len(tries))
            return real_unlink(path, missing_ok=missing_ok)

        return patch.object(Path, "unlink", unlink), tries

    def test_an_unlink_that_fails_again_after_the_thaw_puts_the_mode_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip, name = self._linked(tmp)

            def locked(_try):
                raise PermissionError(13, "O arquivo está em uso", str(name))

            patched, tries = self._unlink(name, locked)
            with patched, self.assertRaises(PermissionError):
                delivery._unlink_keeping_mode(name, clip)
            self.assertEqual(2, len(tries))
            self.assertTrue(clip.samefile(name))
            self.assertEqual(0o444, stat_module.S_IMODE(clip.stat().st_mode))

    def test_an_interrupt_between_the_thaw_and_the_unlink_puts_the_mode_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip, name = self._linked(tmp)

            def read_only_then_interrupted(attempt):
                if attempt == 1:
                    raise PermissionError(13, "Acesso negado", str(name))
                raise KeyboardInterrupt

            patched, _tries = self._unlink(name, read_only_then_interrupted)
            with patched, self.assertRaises(KeyboardInterrupt):
                delivery._unlink_keeping_mode(name, clip)
            self.assertEqual(0o444, stat_module.S_IMODE(clip.stat().st_mode))


def _windows_unlink():
    """`Path.unlink` com a regra do Windows: um arquivo sem escrita não sai até devolverem a escrita."""
    real_unlink = Path.unlink

    def unlink(path, missing_ok=False):
        mode = 0
        with contextlib.suppress(OSError):
            mode = path.lstat().st_mode
        if stat_module.S_ISREG(mode) and not mode & stat_module.S_IWUSR:
            raise PermissionError(13, "Acesso negado", str(path))
        return real_unlink(path, missing_ok=missing_ok)

    return patch.object(Path, "unlink", unlink)


class SweepKeepsTheClipFrozen(unittest.TestCase):
    """A varredura que apaga o link órfão de um clipe congelado não destrava o clipe de `brolls/`.

    No Windows o somente-leitura é do arquivo, compartilhado por todos os hardlinks: devolver
    a escrita para apagar o nome velho destravava o clipe (e o link novo dele).
    """

    def test_a_renamed_beat_leaves_the_clip_and_its_new_link_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            beat = _brief_with_target(tmp, "Palco principal")
            project(tmp, [fetched("a", "Palco", shot=beat)])
            first = delivery.build_delivery(tmp)
            old = Path(tmp) / first["items"][0]["path"]
            clip = Path(tmp) / "brolls" / "clips" / "x.mp4"
            frozen = stat_module.S_IMODE(clip.stat().st_mode)
            self.assertFalse(frozen & 0o222)
            _brief_with_target(tmp, "Outro alvo diferente")
            with _windows_unlink():
                report = delivery.build_delivery(tmp)
            self.assertFalse(old.exists())
            self.assertIn(old.relative_to(Path(tmp) / "entrega").as_posix(), report["removed"])
            self.assertTrue((Path(tmp) / report["items"][0]["path"]).samefile(clip))
            self.assertEqual(frozen, stat_module.S_IMODE(clip.stat().st_mode))

    def test_a_rejected_item_leaves_its_clip_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            delivered = _two_delivered(tmp)
            c = _change_item(tmp, "clips/a.mp4", lambda c: mark_rejected(c, "não serve mais"))
            clip = Path(tmp) / "brolls" / "clips" / "a.mp4"
            frozen = stat_module.S_IMODE(clip.stat().st_mode)
            with _windows_unlink():
                delivery.build_delivery(tmp)
            self.assertFalse(delivered[c["id"]].exists())
            self.assertEqual(frozen, stat_module.S_IMODE(clip.stat().st_mode))


def _locked_unlink(*locked):
    """`Path.unlink` que recusa os caminhos `locked`, como o Windows recusa um arquivo aberto no editor."""
    real_unlink = Path.unlink
    refused = []

    def unlink(path, missing_ok=False):
        if path in locked:
            refused.append(path)
            raise PermissionError(13, "O arquivo está sendo usado por outro processo", str(path))
        return real_unlink(path, missing_ok=missing_ok)

    return patch.object(Path, "unlink", unlink), refused


class LockedPlannedFileIsAConflict(unittest.TestCase):
    """O arquivo planejado que não sai de `entrega/` (aberto no editor) é conflito do item, não aborta a entrega."""

    def test_a_locked_old_revision_is_a_conflict_and_the_other_beat_is_still_delivered(self):
        with tempfile.TemporaryDirectory() as tmp:
            delivered = _two_delivered(tmp)
            old = Path(tmp) / "brolls" / "clips" / "a.mp4"
            media_a = next(path for path in delivered.values() if path.read_bytes() == old.read_bytes())
            media_b = next(path for path in delivered.values() if path != media_a)
            _new_revision(tmp)
            (Path(tmp) / "brolls" / "clips" / "b-r2.mp4").write_bytes(b"bytes de b na revisao 2")
            _change_item(tmp, "clips/b.mp4", lambda c: c["output"].update(path="clips/b-r2.mp4"))
            patched, refused = _locked_unlink(media_a.resolve())
            with patched, self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertTrue(refused)
            message = str(caught.exception)
            self.assertIn(media_a.name, message)
            self.assertIn("feche", message)
            self.assertNotIn("parece edição sua", message)
            # O outro beat recebeu a revisão nova; o item preso fica como estava, e congelado.
            self.assertEqual(b"bytes de b na revisao 2", media_b.read_bytes())
            self.assertTrue(media_a.samefile(old))
            self.assertEqual(0o444, stat_module.S_IMODE(old.stat().st_mode))
            conflicts = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8").split("## Conflitos", 1)[1]
            self.assertIn(media_a.name, conflicts)
            # Com o arquivo fechado, o `deliver` seguinte troca pela revisão nova.
            delivery.build_delivery(tmp)
            self.assertEqual(b"bytes da revisao 2", media_a.read_bytes())

    def test_a_locked_old_contact_sheet_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            delivered = _two_delivered(tmp)
            media_a = next(path for path in delivered.values() if path.read_bytes() == b"conteudo de clips/a.mp4")
            sheet = media_a.parent / "contact-sheet.jpg"
            staged = Path(tmp) / "brolls" / "previews" / "stage.jpg"
            staged.write_bytes(b"sheet regravado")
            staged.replace(Path(tmp) / "brolls" / "previews" / "a.jpg")
            patched, refused = _locked_unlink(sheet.resolve())
            with patched, self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertTrue(refused)
            self.assertIn(sheet.relative_to(Path(tmp) / "entrega").as_posix(), str(caught.exception))
            self.assertIn("feche", str(caught.exception))
            self.assertEqual(b"conteudo de previews/a.jpg", sheet.read_bytes())


@needs_symlink
class SymlinkedBeatFolderKeepsWhatIsBehindIt(unittest.TestCase):
    """Uma pasta de beat trocada à mão por um link para fora de `entrega/`: nada de lá é apagado.

    O arquivo planejado só dá lugar ao novo dentro da `entrega/` real, como na varredura;
    atrás do link, ele fica e o item vira conflito.
    """

    def _behind_a_link(self, tmp, files):
        """Entrega `a` e `b`, troca a pasta de `a` por um link para uma pasta com `files`; devolve (mídia, pasta)."""
        if not _symlinks_available(tmp):
            self.skipTest("symlinks indisponíveis neste ambiente")
        delivered = _two_delivered(tmp)
        media = next(path for path in delivered.values() if path.read_bytes() == b"conteudo de clips/a.mp4")
        outside = Path(tmp) / "pasta-do-editor"
        outside.mkdir()
        for name, data in files(media).items():
            (outside / name).write_bytes(data)
        _force_rmtree(media.parent)
        media.parent.symlink_to(outside, target_is_directory=True)
        return media, outside

    def test_a_contact_sheet_behind_a_symlinked_beat_folder_is_never_deleted(self):
        for mine in (b"MEU SHEET PESSOAL", b"conteudo de previews/a.jpg"):
            with self.subTest(sheet=mine), tempfile.TemporaryDirectory() as tmp:
                _media, outside = self._behind_a_link(tmp, lambda _media, data=mine: {"contact-sheet.jpg": data})
                # O `preview` regravou o sheet: o de lá deixou de bater com a prévia atual.
                staged = Path(tmp) / "brolls" / "previews" / "stage.jpg"
                staged.write_bytes(b"sheet regravado")
                staged.replace(Path(tmp) / "brolls" / "previews" / "a.jpg")
                with self.assertRaises(ValueError) as caught:
                    delivery.build_delivery(tmp)
                self.assertIn("contact-sheet.jpg", str(caught.exception))
                self.assertEqual(mine, (outside / "contact-sheet.jpg").read_bytes())
                self.assertFalse((outside / "ORIGEM.md").exists())

    def test_an_old_revision_behind_a_symlinked_beat_folder_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            media, outside = self._behind_a_link(
                tmp,
                lambda media: {
                    media.name: b"conteudo de clips/a.mp4",
                    "contact-sheet.jpg": b"conteudo de previews/a.jpg",
                },
            )
            _new_revision(tmp)
            with self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertIn(media.name, str(caught.exception))
            self.assertEqual(b"conteudo de clips/a.mp4", (outside / media.name).read_bytes())


if __name__ == "__main__":
    unittest.main()

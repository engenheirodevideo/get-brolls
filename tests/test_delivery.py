"""Camada `entrega/`: a mesma coleta, organizada por beat, sem tocar no que é canônico.

`brolls/` continua sendo a verdade; `entrega/` é derivado e regenerável. Por isso os
testes cobrem: nome estável, repetir sem estragar, link órfão removido, queda para
cópia quando o sistema não deixa linkar, ensaio sem gravar, caminho perigoso recusado,
arquivo editado pela pessoa preservado e `verify` que avisa em vez de reprovar.
"""

import errno
import json
import os
import re
import shutil
import stat
import tempfile
import types
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT
from _symlink import needs_symlink

from getbrolls import delivery, delivery_files, delivery_sweep

# Reimport com apelido só pra isolar o setup do teste que precisa dele
# (mesmo motivo do redefined-outer-name em tests/pylintrc).
from getbrolls import delivery as module  # pylint: disable=reimported
from getbrolls.commands import execute
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, now, set_segment
from getbrolls.runtime import audited


def fetched(source_id, title, shot=None, clip: str | None = "clips/x.mp4", sheet: str | None = "previews/x.jpg"):
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


def with_brief(tmp):
    """BRIEF.md válido com um beat `abertura`, para a escada ter onde chegar."""

    raw = (ROOT / "docs" / "BRIEF.md").read_text(encoding="utf-8")
    block = re.findall(r"```json\s*\n(.*?)\n```", raw, re.DOTALL)[0]
    data = json.loads(block)
    data["beats"] = [data["beats"][0]]
    Path(tmp, "BRIEF.md").write_text(
        raw.replace(block, json.dumps(data, ensure_ascii=False, indent=2), 1),
        encoding="utf-8",
    )


def project(tmp, items):
    """Projeto sintético com os arquivos de `brolls/` realmente no disco."""
    ledger = Ledger(tmp)
    stored = [ledger.add(c) for c in items]
    ledger.save_many("fixture", stored)
    for c in stored:
        for rel in (c["output"]["path"], c["preview"].get("contact_sheet_path")):
            if rel:
                path = ledger.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(b"conteudo de " + rel.encode())
    return ledger


class DirectoryNames(unittest.TestCase):
    def test_slug_is_ascii_lowercase_and_bounded(self):
        name = delivery.beat_dir_name(1, "abertura", "Jensen Huang na GTC — palco, luzes & público")
        self.assertTrue(name.startswith("01-abertura-"))
        self.assertLessEqual(len(name), 60)
        self.assertRegex(name, r"^[a-z0-9-]+$")
        # Mesmo alvo, mesmo nome: a pasta não muda de lugar entre duas execuções.
        self.assertEqual(
            name,
            delivery.beat_dir_name(1, "abertura", "Jensen Huang na GTC — palco, luzes & público"),
        )

    def test_path_separators_and_parent_refs_are_refused(self):
        for bad in ("../fuga", "..", "a/b", "a\\b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                delivery.beat_dir_name(1, bad, "alvo")
        with self.assertRaises(ValueError):
            delivery.beat_dir_name(1, "beat", "../fuga")


class Build(unittest.TestCase):
    def test_creates_one_folder_per_beat_with_media_sheet_and_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(
                tmp,
                [
                    fetched("a", "Palco", shot="abertura", clip="clips/a.mp4", sheet="previews/a.jpg"),
                    fetched("b", "Sem beat", clip="clips/b.mp4", sheet="previews/b.jpg"),
                ],
            )
            report = delivery.build_delivery(tmp)
            root = Path(tmp) / "entrega"
            self.assertTrue((root / "README.md").is_file())
            folders = sorted(p.name for p in root.iterdir() if p.is_dir())
            self.assertEqual(2, len(folders))
            self.assertIn(delivery.NO_BEAT, folders)
            beat_dir = next(p for p in root.iterdir() if p.is_dir() and p.name != delivery.NO_BEAT)
            self.assertTrue((beat_dir / (beat_dir.name + ".mp4")).exists())
            self.assertTrue((beat_dir / "contact-sheet.jpg").exists())
            origin = (beat_dir / "ORIGEM.md").read_text(encoding="utf-8")
            self.assertIn("type: delivery-origin", origin)
            self.assertIn("https://example.org/a", origin)
            self.assertIn("a" * 64, origin)
            readme = (root / "README.md").read_text(encoding="utf-8")
            self.assertIn("Palco", readme)
            self.assertIn("| Beat |", readme)
            # O manifesto guarda onde o arquivo foi parar e como ele foi ligado.
            fresh = Ledger(tmp, recover=False).data["items"]
            for c in fresh:
                self.assertIn(c["delivery"]["method"], ("hardlink", "symlink", "copy"))
                self.assertTrue(c["delivery"]["path"].startswith("entrega/"))
            self.assertEqual(2, len(report["items"]))

    def test_running_twice_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            first = delivery.build_delivery(tmp)
            before = {
                str(p.relative_to(tmp)): (p.read_bytes() if p.is_file() else None)
                for p in sorted((Path(tmp) / "entrega").rglob("*"))
            }
            second = delivery.build_delivery(tmp)
            after = {
                str(p.relative_to(tmp)): (p.read_bytes() if p.is_file() else None)
                for p in sorted((Path(tmp) / "entrega").rglob("*"))
            }
            self.assertEqual(before, after)
            self.assertEqual([i["path"] for i in first["items"]], [i["path"] for i in second["items"]])
            self.assertEqual([], second["removed"])

    def test_orphan_links_are_removed_when_the_beat_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project(tmp, [fetched("a", "Palco", shot="abertura")])
            delivery.build_delivery(tmp)
            old = sorted(p.name for p in (Path(tmp) / "entrega").iterdir() if p.is_dir())
            ledger.data["items"][0]["shot"] = "fechamento"
            ledger.save_many("fixture", ledger.data["items"])
            report = delivery.build_delivery(tmp)
            new = sorted(p.name for p in (Path(tmp) / "entrega").iterdir() if p.is_dir())
            self.assertNotEqual(old, new)
            self.assertTrue(report["removed"])
            for name in old:
                self.assertFalse((Path(tmp) / "entrega" / name).exists())

    def test_copy_is_the_last_resort_when_the_system_refuses_to_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])

            def refuse(*args, **kwargs):
                raise OSError("sem privilégio para linkar")

            original = (os.link, os.symlink)
            os.link, os.symlink = refuse, refuse
            try:
                report = delivery.build_delivery(tmp)
            finally:
                os.link, os.symlink = original
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            media = Path(tmp) / report["items"][0]["path"]
            self.assertTrue(media.is_file() and not media.is_symlink())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            report = delivery.build_delivery(tmp, dry_run=True)
            self.assertTrue(report["dry_run"])
            self.assertEqual(1, len(report["items"]))
            self.assertFalse((Path(tmp) / "entrega").exists())
            self.assertNotIn("delivery", Ledger(tmp, recover=False).data["items"][0])

    def test_dry_run_leaves_the_manifest_byte_identical(self):
        # O ensaio passa pelo mesmo caminho de `deliver`, inclusive `sync_formats`:
        # nenhuma etapa dele pode reescrever o manifesto.

        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            Path(tmp, "RULES.md").write_text((ROOT / "docs" / "RULES.md").read_text(encoding="utf-8"), encoding="utf-8")
            manifest = Path(tmp) / "brolls" / "manifest.json"
            before = manifest.read_bytes()
            args = types.SimpleNamespace(
                command="deliver",
                project=tmp,
                env_file=None,
                dry_run=True,
                confirm_format_change=False,
            )
            report = audited(args, execute)
            self.assertEqual(before, manifest.read_bytes())
            self.assertFalse((Path(tmp) / "entrega").exists())
            self.assertEqual(["planned"], [i["method"] for i in report["items"]])

    def test_dry_run_reports_the_conflict_the_real_run_raises(self):
        """O ensaio enxerga o arquivo editado que a rodada real recusa."""
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            first = delivery.build_delivery(tmp)
            media = Path(tmp) / first["items"][0]["path"]
            media.chmod(0o644)
            media.unlink()
            media.write_bytes(b"edicao da pessoa")
            snapshot = {
                str(p.relative_to(tmp)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(tmp).rglob("*"))
            }
            dry = delivery.build_delivery(tmp, dry_run=True)
            after_dry = {
                str(p.relative_to(tmp)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(tmp).rglob("*"))
            }
            self.assertEqual(snapshot, after_dry)
            with self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertEqual([str(caught.exception)], dry.get("conflicts"))
            self.assertEqual(b"edicao da pessoa", media.read_bytes())

    def test_delivered_media_is_read_only_because_the_inode_is_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            report = delivery.build_delivery(tmp)
            self.assertEqual("hardlink", report["items"][0]["method"])
            media = Path(tmp) / report["items"][0]["path"]
            self.assertFalse(media.stat().st_mode & stat.S_IWUSR)
            index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
            self.assertIn("editar o original", index)
            origin = next((Path(tmp) / "entrega").rglob("ORIGEM.md")).read_text(encoding="utf-8")
            self.assertIn("editar o original", origin)

    def test_copies_are_forced_by_env_for_people_who_edit_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            os.environ["GB_DELIVERY_COPY"] = "1"
            try:
                report = delivery.build_delivery(tmp)
            finally:
                del os.environ["GB_DELIVERY_COPY"]
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            media = Path(tmp) / report["items"][0]["path"]
            source = Path(tmp) / "brolls" / "clips" / "x.mp4"
            self.assertFalse(os.path.samestat(media.stat(), source.stat()))
            # A promessa da variável é justamente poder editar dentro de `entrega/`.
            self.assertTrue(media.stat().st_mode & stat.S_IWUSR)
            media.write_bytes(b"editei aqui mesmo")
            origin = next((Path(tmp) / "entrega").rglob("ORIGEM.md")).read_text(encoding="utf-8")
            self.assertIn("cópia independente", origin)
            self.assertNotIn("editar o original", origin)
            index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
            self.assertIn("cópia independente", index)

    def test_forced_copy_replaces_a_hardlink_with_a_writable_copy(self):
        """`GB_DELIVERY_COPY=1` depois de uma entrega por hardlink vira cópia de verdade."""
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            first = delivery.build_delivery(tmp)
            self.assertEqual(["hardlink"], [i["method"] for i in first["items"]])
            media = Path(tmp) / first["items"][0]["path"]
            source = Path(tmp) / "brolls" / "clips" / "x.mp4"
            original = source.read_bytes()
            with patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
                report = delivery.build_delivery(tmp)
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            self.assertEqual(1, media.stat().st_nlink)
            self.assertFalse(os.path.samestat(media.stat(), source.stat()))
            # A cópia nasce editável, mesmo vindo de um clipe congelado pelo hardlink.
            self.assertTrue(media.stat().st_mode & stat.S_IWUSR)
            origin = (media.parent / "ORIGEM.md").read_text(encoding="utf-8")
            self.assertIn("cópia independente", origin)
            self.assertNotIn("editar o original", origin)
            # O clipe canônico continua lá, com os mesmos bytes.
            self.assertEqual(original, source.read_bytes())
            media.write_bytes(b"editei a copia")
            self.assertEqual(original, source.read_bytes())

    @needs_symlink
    def test_forced_copy_replaces_a_symlink_with_a_writable_copy(self):
        """O symlink para o clipe também é o original; com cópia forçada ele sai."""
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            with patch.object(os, "link", side_effect=OSError("outro volume")):
                first = delivery.build_delivery(tmp)
            if first["items"][0]["method"] != "symlink":
                self.skipTest("symlinks indisponíveis neste ambiente")
            media = Path(tmp) / first["items"][0]["path"]
            with patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
                report = delivery.build_delivery(tmp)
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            self.assertFalse(media.is_symlink())
            self.assertTrue(media.stat().st_mode & stat.S_IWUSR)

    def test_forced_copy_keeps_the_clip_frozen_when_unlinking_needs_the_write_bit(self):
        """No Windows, apagar o nome somente-leitura em `entrega/` não destrava o clipe.

        A troca do `.part` pelo nome recusa um destino somente-leitura no Windows, e o
        `unlink` dele também: `thaw_unlink` devolve a escrita ao inode — que é o mesmo do
        clipe em `brolls/`. O modo do clipe volta ao que era.
        """
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            first = delivery.build_delivery(tmp)
            # O mesmo caminho que `build_delivery` monta (raiz resolvida: /var → /private/var no macOS).
            media = Path(tmp).resolve() / first["items"][0]["path"]
            source = Path(tmp) / "brolls" / "clips" / "x.mp4"
            frozen = stat.S_IMODE(source.stat().st_mode)
            self.assertFalse(frozen & 0o222)
            real_unlink = Path.unlink
            real_replace = Path.replace
            refused = []

            def windows_unlink(path, missing_ok=False):
                # O primeiro `unlink` do nome somente-leitura falha, como no Windows.
                if path == media and not refused and not path.stat().st_mode & stat.S_IWUSR:
                    refused.append(path)
                    raise PermissionError(13, "Acesso negado", str(path))
                return real_unlink(path, missing_ok=missing_ok)

            def windows_replace(path, target):
                # `MoveFileEx` recusa trocar por cima de um destino somente-leitura.
                if Path(target) == media and media.exists() and not media.stat().st_mode & stat.S_IWUSR:
                    raise PermissionError(13, "Acesso negado", str(target))
                return real_replace(path, target)

            with (
                patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}),
                patch.object(Path, "unlink", windows_unlink),
                patch.object(Path, "replace", windows_replace),
            ):
                report = delivery.build_delivery(tmp)
            self.assertEqual([media], refused)
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            self.assertEqual(frozen, stat.S_IMODE(source.stat().st_mode))
            self.assertTrue(media.stat().st_mode & stat.S_IWUSR)

    def test_a_forced_copy_that_cannot_be_written_keeps_the_hardlink(self):
        """Disco cheio ou Ctrl+C no meio da cópia forçada: o hardlink que funcionava continua lá.

        A cópia nasce num `.part` e só toma o nome inteira; antes dela, nada sai. O disco
        cheio é conflito do item (o resto da entrega segue), e o registro de entrega
        continua apontando para um arquivo que existe.
        """
        for failure in (OSError(errno.ENOSPC, "No space left on device"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as tmp:
                project(tmp, [fetched("a", "Palco", shot="abertura")])
                first = delivery.build_delivery(tmp)
                media = Path(tmp) / first["items"][0]["path"]
                source = Path(tmp) / "brolls" / "clips" / "x.mp4"
                frozen = stat.S_IMODE(source.stat().st_mode)
                expected = ValueError if isinstance(failure, OSError) else KeyboardInterrupt
                with (
                    patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}),
                    patch.object(shutil, "copy2", side_effect=failure),
                    self.assertRaises(expected) as caught,
                ):
                    delivery.build_delivery(tmp)
                self.assertTrue(media.samefile(source))
                self.assertEqual(frozen, stat.S_IMODE(source.stat().st_mode))
                self.assertEqual([], list(media.parent.glob("*.part")))
                stored = Ledger(tmp, recover=False).data["items"][0]["delivery"]
                self.assertTrue((Path(tmp) / stored["path"]).samefile(source))
                if expected is ValueError:
                    self.assertIn(media.name, str(caught.exception))
                    index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
                    self.assertIn(media.name, index.split("## Conflitos", 1)[1])

    def test_forced_copy_makes_a_read_only_copy_from_an_older_run_editable(self):
        """A cópia 0444 que a versão antiga deixou (mesmos bytes) volta a ser editável."""
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            plan = delivery.build_delivery(tmp, dry_run=True)
            media = Path(tmp) / plan["items"][0]["path"]
            source = Path(tmp) / "brolls" / "clips" / "x.mp4"
            media.parent.mkdir(parents=True)
            shutil.copy2(source, media)
            media.chmod(0o444)
            with patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
                report = delivery.build_delivery(tmp)
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            self.assertTrue(media.stat().st_mode & stat.S_IWUSR)

    def test_a_same_bytes_hardlink_of_another_clip_stays_frozen(self):
        """Mesmos bytes, mas outro nome de um clipe congelado: dar escrita ali destravaria `brolls/`."""
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            plan = delivery.build_delivery(tmp, dry_run=True)
            media = Path(tmp) / plan["items"][0]["path"]
            twin = Path(tmp) / "brolls" / "clips" / "x-r0.mp4"
            shutil.copy2(Path(tmp) / "brolls" / "clips" / "x.mp4", twin)
            twin.chmod(0o444)
            media.parent.mkdir(parents=True)
            os.link(twin, media)
            with patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
                report = delivery.build_delivery(tmp)
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            self.assertEqual(0o444, stat.S_IMODE(twin.stat().st_mode))

    def test_the_index_points_at_what_comes_after_the_delivery(self):

        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            Path(tmp, "RULES.md").write_text((ROOT / "docs" / "RULES.md").read_text(encoding="utf-8"), encoding="utf-8")
            with_brief(tmp)
            args = types.SimpleNamespace(
                command="deliver",
                project=tmp,
                env_file=None,
                dry_run=False,
                confirm_format_change=False,
            )
            audited(args, execute)
            index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
            # Com o brief válido e tudo conferido, o degrau antes da entrega seria
            # justamente `deliver`: o índice precisa falar do estado DEPOIS dela.
            self.assertNotIn("organizar os trechos conferidos", index)
            self.assertIn("Fluxo completo", index)

    def test_the_index_never_points_at_a_candidate_the_human_never_saw(self):
        """Fricção 1 da rodada 2: o README nascia mandando inspecionar um descarte."""

        with tempfile.TemporaryDirectory() as tmp:
            leftover = candidate("youtube", "descartado", "Livestream 24/7")
            leftover["source_url"] = "https://www.youtube.com/watch?v=descartadoXY"
            project(tmp, [fetched("a", "Palco", shot="abertura"), leftover])
            Path(tmp, "RULES.md").write_text((ROOT / "docs" / "RULES.md").read_text(encoding="utf-8"), encoding="utf-8")
            with_brief(tmp)
            args = types.SimpleNamespace(
                command="deliver",
                project=tmp,
                env_file=None,
                dry_run=False,
                confirm_format_change=False,
            )
            audited(args, execute)
            index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
            self.assertIn("Fluxo completo", index)
            # O descarte vira aparte, com a saída (`reject`) dita — nunca o próximo passo.
            self.assertIn("1 candidato sem decisão", index)
            self.assertIn("reject", index)
            self.assertNotIn("Antes de gerar prévia", index)
            self.assertNotIn("inspect --project", index)

    def test_the_index_asks_for_the_pending_decision_instead_of_saying_it_is_done(self):
        """Entregar o que foi aprovado não fecha o fluxo enquanto houver prévia sem decisão."""

        with tempfile.TemporaryDirectory() as tmp:
            waiting = candidate("local", "espera", "Prévia esperando decisão")
            set_segment(waiting, 0, 2)
            waiting["preview"]["contact_sheet_path"] = "previews/espera.jpg"
            waiting["state"] = "awaiting_approval"
            project(tmp, [fetched("a", "Palco", shot="abertura"), waiting])
            Path(tmp, "RULES.md").write_text((ROOT / "docs" / "RULES.md").read_text(encoding="utf-8"), encoding="utf-8")
            with_brief(tmp)
            args = types.SimpleNamespace(
                command="deliver",
                project=tmp,
                env_file=None,
                dry_run=False,
                confirm_format_change=False,
            )
            audited(args, execute)
            index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
            self.assertNotIn("Fluxo completo", index)
            # A seção "Próximo passo" diz o que está pronto e o que ficou pendente.
            self.assertIn(
                "Entrega pronta (1 trecho). Há 1 prévia sem decisão no projeto — decida ou rejeite.",
                index,
            )
            # Quem lê este arquivo está na pasta, não na conversa: prometer que o
            # agente "vai subir o Storyboard" aqui é promessa que o README não cumpre.
            self.assertNotIn("vou subir o Storyboard", index)
            self.assertNotIn("Agora é com você", index)

    def test_a_stray_note_inside_a_beat_folder_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            report = delivery.build_delivery(tmp)
            folder = Path(tmp) / report["items"][0]["path"]
            note = folder.parent / "minhas-anotacoes.txt"
            note.write_text("lembrar de cortar no 3s", encoding="utf-8")
            again = delivery.build_delivery(tmp)
            self.assertTrue(note.is_file())
            self.assertIn(f"{folder.parent.name}/{note.name}", again["kept"])
            self.assertEqual("lembrar de cortar no 3s", note.read_text(encoding="utf-8"))

    def test_one_edited_file_does_not_abort_the_rest_of_the_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(
                tmp,
                [
                    fetched("a", "Palco", shot="abertura", clip="clips/a.mp4", sheet="previews/a.jpg"),
                    fetched("b", "Plateia", shot="fechamento", clip="clips/b.mp4", sheet="previews/b.jpg"),
                ],
            )
            # O ensaio diz onde cada arquivo cairia; plantamos um conflito antes da
            # primeira entrega, para o item nunca ter tido um registro válido.
            plan = delivery.build_delivery(tmp, dry_run=True)
            first = Path(tmp) / plan["items"][0]["path"]
            second = Path(tmp) / plan["items"][1]["path"]
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"corte que a pessoa mexeu na mao")
            with self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertIn(first.name, str(caught.exception))
            # O item saudável foi materializado mesmo com o conflito do outro.
            self.assertTrue(second.exists())
            self.assertEqual(b"corte que a pessoa mexeu na mao", first.read_bytes())
            # O item em conflito não conta como entregue e sai da tabela, para o
            # `status` continuar pedindo `deliver` em vez de dizer que terminou.
            stored = Ledger(tmp, recover=False).data["items"]
            delivered = [c["id"] for c in stored if (c.get("delivery") or {}).get("path")]
            conflicted = next(c for c in stored if c["output"]["path"] == "clips/a.mp4")
            self.assertNotIn(conflicted["id"], delivered)
            index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
            self.assertIn("## Conflitos", index)
            self.assertIn(first.name, index.split("## Conflitos", 1)[1])
            self.assertNotIn(first.name, index.split("## Conflitos", 1)[0])

    def test_a_file_the_person_edited_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            report = delivery.build_delivery(tmp)
            media = Path(tmp) / report["items"][0]["path"]
            media.chmod(0o644)
            media.unlink()
            media.write_bytes(b"corte que a pessoa mexeu na mao")
            with self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertIn(media.name, str(caught.exception))
            self.assertEqual(b"corte que a pessoa mexeu na mao", media.read_bytes())


def _json_block(path):
    raw = Path(path).read_text(encoding="utf-8")
    block = re.findall(r"```json\s*\n(.*?)\n```", raw, re.DOTALL)[0]
    return raw, block, json.loads(block)


class BriefOrder(unittest.TestCase):
    """A ordem e os nomes de `entrega/` vêm do BRIEF.md validado com o RULES.md."""

    def test_delivery_follows_the_brief_with_user_declaration_rules(self):
        raw, block, data = _json_block(ROOT / "docs" / "BRIEF.md")
        first = {**data["beats"][0], "id": "abertura", "target": "Ponte ao amanhecer"}
        last = {**first, "id": "fechamento", "target": "Cristo visto do mar"}
        data["beats"] = [first, last]
        data["rights"]["posture"] = "user_declaration"
        rules_raw, rules_block, rules = _json_block(ROOT / "docs" / "RULES.md")
        rules["copyright"].update(
            mode="user_declaration", responsible_person="Pessoa Exemplo", declaration="Assumo os direitos."
        )
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "BRIEF.md").write_text(
                raw.replace(block, json.dumps(data, ensure_ascii=False, indent=2), 1), encoding="utf-8"
            )
            Path(tmp, "RULES.md").write_text(
                rules_raw.replace(rules_block, json.dumps(rules, ensure_ascii=False, indent=2), 1), encoding="utf-8"
            )
            # Ordem do manifesto ao contrário da do brief.
            project(
                tmp,
                [
                    fetched("b", "Titulo do video B", shot="fechamento", clip="clips/b.mp4", sheet="previews/b.jpg"),
                    fetched("a", "Titulo do video A", shot="abertura", clip="clips/a.mp4", sheet="previews/a.jpg"),
                ],
            )
            report = delivery.build_delivery(tmp)
            self.assertEqual(["abertura", "fechamento"], [i["beat"] for i in report["items"]])
            self.assertEqual(
                ["entrega/01-abertura-ponte-ao-amanhecer", "entrega/02-fechamento-cristo-visto-do-mar"],
                [i["path"].rsplit("/", 1)[0] for i in report["items"]],
            )

    def test_broken_brief_refuses_and_keeps_entrega(self):
        with tempfile.TemporaryDirectory() as tmp:
            with_brief(tmp)
            project(tmp, [fetched("a", "Palco principal do evento", shot="abertura")])
            delivery.build_delivery(tmp)
            entrega = Path(tmp) / "entrega"
            before = sorted(p.relative_to(entrega).as_posix() for p in entrega.rglob("*"))
            brief = Path(tmp, "BRIEF.md")
            brief.write_text(
                brief.read_text(encoding="utf-8").replace('"version": 1,', '"version": 1,,', 1), encoding="utf-8"
            )
            for dry_run in (True, False):
                with self.subTest(dry_run=dry_run):
                    with self.assertRaises(ValueError) as caught:
                        delivery.build_delivery(tmp, dry_run=dry_run)
                    self.assertIn("BRIEF.md", str(caught.exception))
                    self.assertIn("entrega/", str(caught.exception))
                    # Nada foi renomeado nem varrido: a entrega anterior fica como estava.
                    self.assertEqual(before, sorted(p.relative_to(entrega).as_posix() for p in entrega.rglob("*")))

    def test_without_a_brief_the_manifest_order_still_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(
                tmp,
                [
                    fetched("b", "Plateia", shot="fechamento", clip="clips/b.mp4", sheet=None),
                    fetched("a", "Palco", shot="abertura", clip="clips/a.mp4", sheet=None),
                ],
            )
            report = delivery.build_delivery(tmp)
            self.assertEqual(["fechamento", "abertura"], [i["beat"] for i in report["items"]])


class FrozenFiles(unittest.TestCase):
    """O que `deliver` congela precisa continuar apagável — inclusive no Windows."""

    def test_thaw_unlink_removes_a_read_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "congelado.mp4"
            path.write_bytes(b"bytes")
            delivery_files._freeze(path, "hardlink")
            self.assertFalse(os.access(path, os.W_OK))
            delivery_sweep.thaw_unlink(path)
            self.assertFalse(path.exists())

    def test_thaw_unlink_gives_the_write_bit_back_when_unlink_is_refused(self):
        """Simula o Windows: o atributo somente-leitura barra o primeiro `unlink`."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "congelado.mp4"
            path.write_bytes(b"bytes")
            delivery_files._freeze(path, "hardlink")
            real = Path.unlink
            refused = []

            def windowsish(self, *args, **kwargs):
                if not refused and not os.access(self, os.W_OK):
                    refused.append(str(self))
                    raise PermissionError(13, "read-only file")
                return real(self, *args, **kwargs)

            Path.unlink = windowsish
            try:
                delivery_sweep.thaw_unlink(path)
            finally:
                Path.unlink = real
            self.assertEqual([str(path)], refused)
            self.assertFalse(path.exists())

    def test_thaw_unlink_is_used_when_a_frozen_link_becomes_an_orphan(self):
        """Renomear o beat apaga o link antigo mesmo que ele esteja somente-leitura."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = project(tmp, [fetched("a", "Palco", shot="abertura")])
            delivery.build_delivery(tmp)
            old = sorted(p.name for p in (Path(tmp) / "entrega").iterdir() if p.is_dir())
            frozen = list((Path(tmp) / "entrega").rglob("*.mp4"))
            self.assertTrue(frozen and not os.access(frozen[0], os.W_OK))
            ledger.data["items"][0]["shot"] = "fechamento"
            ledger.save_many("fixture", ledger.data["items"])
            delivery.build_delivery(tmp)
            for name in old:
                self.assertFalse((Path(tmp) / "entrega" / name).exists())


class OriginAuthor(unittest.TestCase):
    def test_channel_is_the_credit_when_the_provider_gave_no_creator_name(self):
        c = fetched("a", "Palco", shot="abertura")
        c["creator"]["name"] = None
        c["channel"] = "Canal do Exemplo"
        self.assertIn("- Autor: Canal do Exemplo", delivery.render_origin(c, "a.mp4"))

    def test_creator_name_still_wins_over_the_channel(self):
        c = fetched("a", "Palco", shot="abertura")
        c["channel"] = "Canal do Exemplo"
        self.assertIn("- Autor: Autora Exemplo", delivery.render_origin(c, "a.mp4"))

    def test_without_any_credit_the_line_stays_explicit(self):
        c = fetched("a", "Palco", shot="abertura")
        c["creator"]["name"] = None
        self.assertIn("- Autor: não informado", delivery.render_origin(c, "a.mp4"))


class VerifyHook(unittest.TestCase):
    def test_delivery_failure_only_warns_and_verify_still_succeeds(self):

        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura", clip=None)])
            Path(tmp, "RULES.md").write_text((ROOT / "docs" / "RULES.md").read_text(encoding="utf-8"), encoding="utf-8")
            original = module.build_delivery

            def boom(*args, **kwargs):
                raise OSError("disco cheio")

            module.build_delivery = boom
            try:
                args = types.SimpleNamespace(
                    command="verify",
                    project=tmp,
                    env_file=None,
                    confirm_format_change=False,
                )
                result = audited(args, execute)
            finally:
                module.build_delivery = original
            self.assertEqual(0, result["count"])
            self.assertIn("DELIVERY_LINK_FAILED", [w["code"] for w in result.get("warnings", [])])


class MixedDeliveryIndex(unittest.TestCase):
    """Parte link, parte cópia: nenhum aviso global é verdade para os dois."""

    ROWS: ClassVar[list] = [
        {
            "beat": "abertura",
            "narration": "Fala 1",
            "target": "Palco",
            "file": "01/a.mp4",
            "state": "verified",
            "rights": "permitted",
            "method": "hardlink",
        },
        {
            "beat": "meio",
            "narration": "Fala 2",
            "target": "Plateia",
            "file": "02/b.mp4",
            "state": "verified",
            "rights": "permitted",
            "method": "copy",
        },
    ]

    def test_the_column_answers_per_row_instead_of_one_global_warning(self):
        index = delivery.render_index(self.ROWS, "nada pendente")
        self.assertIn("| Beat | Narração | Alvo | Arquivo | Estado | Direitos | Edição |", index)
        self.assertIn("| 01/a.mp4 | verified | permitted | original compartilhado |", index)
        self.assertIn("| 02/b.mp4 | verified | permitted | editável |", index)
        self.assertIn("**Esta entrega tem os dois casos.**", index)
        # Os avisos globais mentiriam sobre metade dos arquivos.
        self.assertNotIn("**Editar aqui é editar o original.**", index)
        self.assertNotIn("**Esta é uma cópia independente**", index)

    def test_a_uniform_delivery_keeps_the_single_warning_and_no_column(self):
        links = delivery.render_index([self.ROWS[0]], "x")
        self.assertIn("**Editar aqui é editar o original.**", links)
        self.assertNotIn("Edição |", links)
        self.assertNotIn("original compartilhado", links)
        copies = delivery.render_index([self.ROWS[1]], "x", copies=True)
        self.assertIn("**Esta é uma cópia independente**", copies)
        self.assertNotIn("Edição |", copies)

    def test_a_real_mixed_delivery_produces_the_column(self):

        real = delivery.link_or_copy
        calls = {"n": 0}

        def alternating(src, dest, read_only=False):
            # O primeiro arquivo vira link, o segundo cai para cópia — o caso real é
            # o sistema recusar o link de um arquivo só (outro volume, por exemplo).
            calls["n"] += 1
            if calls["n"] == 2:  # noqa: PLR2004 - second file of the pair (see the comment above)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                return "copy"
            return real(src, dest, read_only=read_only)

        with tempfile.TemporaryDirectory() as tmp:
            project(
                tmp,
                [
                    fetched("a", "Palco", shot="abertura", clip="clips/a.mp4", sheet=None),
                    fetched("b", "Plateia", shot="meio", clip="clips/b.mp4", sheet=None),
                ],
            )
            with patch.object(delivery, "link_or_copy", alternating):
                report = delivery.build_delivery(tmp)
            self.assertEqual({"hardlink", "copy"}, {i["method"] for i in report["items"]})
            index = Path(report["readme"]).read_text(encoding="utf-8")
            self.assertIn("| Edição |", index)
            self.assertIn("original compartilhado", index)
            self.assertIn("editável", index)
            self.assertIn("**Esta entrega tem os dois casos.**", index)


class EmptyDeliveryIndex(unittest.TestCase):
    """Projeto sem nenhum clipe ainda: a tabela do README precisa continuar uma tabela."""

    def cells(self, line):
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    def test_the_empty_row_has_exactly_one_cell_per_header(self):
        index = delivery.render_index([], "nada pendente")
        table = [line for line in index.splitlines() if line.startswith("|")]
        header, divider, row = table
        self.assertEqual(len(self.cells(header)), len(self.cells(row)))
        self.assertEqual(len(self.cells(header)), len(self.cells(divider)))
        # A mensagem fica na coluna do arquivo, e as outras ficam em branco.
        self.assertEqual(["—", "—", "—", "nenhum trecho coletado ainda", "—", "—"], self.cells(row))

    def test_a_real_empty_project_writes_the_same_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [])
            report = delivery.build_delivery(tmp)
            self.assertEqual([], report["rows"])
            table = [
                line for line in Path(report["readme"]).read_text(encoding="utf-8").splitlines() if line.startswith("|")
            ]
            self.assertEqual(3, len(table))
            self.assertEqual({6}, {len(self.cells(line)) for line in table})


if __name__ == "__main__":
    unittest.main()

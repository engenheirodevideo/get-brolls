"""Regression tests for `scripts/getbrolls/delivery.py` safety fixes.

Covers: refusing a symlinked `entrega/` root, confining every sweep deletion to the
real `entrega/` directory, only treating a symlink as generator-owned when it both
matches the naming pattern and resolves into this project's `brolls/`, distinguishing
an I/O failure from a real content difference when comparing delivered files, surfacing
a failed read-only chmod through the project's warning channel instead of swallowing
it, and never delivering an item whose `output.verified` is explicitly `False` or whose
approval was rejected.
"""

import hashlib
import json
import os
import re
import shutil
import stat as stat_module
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # também insere scripts/ em sys.path
from _portable import unlink_frozen
from _symlink import needs_symlink

from getbrolls import delivery, delivery_files, runtime
from getbrolls.commands import mark_rejected
from getbrolls.ledger import Ledger
from getbrolls.models import candidate, invalidate_approval, now, set_segment


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

    def _on_error(func, target, exc_info):
        Path(target).chmod(stat_module.S_IWRITE | stat_module.S_IREAD)
        func(target)

    # onerror (não onexc) por compatibilidade com Python 3.11, como em runtime.retry().
    shutil.rmtree(path, onerror=_on_error)  # pylint: disable=deprecated-argument


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


@needs_symlink
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

    def test_a_real_empty_beat_directory_rmdir_failure_is_a_warning_not_a_crash(self):
        """The same non-fatal contract `_freeze` already has: an `OSError` while
        removing an empty, orphaned beat directory must not abort `_sweep`."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "entrega"
            root.mkdir()
            beat_dir = root / "01-abertura-alvo"
            beat_dir.mkdir()

            def refuse(*_args, **_kwargs):
                raise OSError(13, "Permission denied")

            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                with patch.object(Path, "rmdir", refuse):
                    removed, kept = delivery._sweep(root, expected=set(), dry_run=False, owned=(), brolls_root=None)
            finally:
                runtime.ACTIVE.reset(token)
            self.assertEqual([], removed)
            self.assertEqual([], kept)
            self.assertTrue(beat_dir.is_dir())
            self.assertTrue(any(w["code"] == "DELIVERY_SWEEP_RMDIR_FAILED" for w in event["warnings"]))

    def test_an_empty_symlinked_beat_directory_is_kept_not_crashed_on(self):
        """Finding: `path.is_dir()` follows symlinks, so a symlink named like a beat
        directory pointing at an EMPTY outside directory used to satisfy the
        `rmdir` gate and then raise `NotADirectoryError`, breaking `deliver` for
        good until someone found and removed the link by hand."""
        with tempfile.TemporaryDirectory() as tmp:
            if not _symlinks_available(tmp):
                self.skipTest("symlinks indisponíveis neste ambiente")
            root = Path(tmp) / "entrega"
            root.mkdir()
            empty_outside = Path(tmp) / "vazio-de-fora"
            empty_outside.mkdir()
            beat_link = root / "01-abertura-alvo"
            beat_link.symlink_to(empty_outside, target_is_directory=True)
            try:
                removed, kept = delivery._sweep(root, expected=set(), dry_run=False, owned=(), brolls_root=None)
                self.assertEqual([], removed)
                self.assertEqual(["01-abertura-alvo"], kept)
                self.assertTrue(beat_link.is_symlink())
                self.assertTrue(empty_outside.is_dir())
            finally:
                beat_link.unlink()


@needs_symlink
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

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            dest = Path(tmp) / "dest.mp4"
            src.write_bytes(b"mesmo conteudo")
            dest.write_bytes(b"mesmo conteudo")

            broken = delivery_files._CompareError(f"[Errno 13] Permission denied: '{dest}'")
            with (
                patch.object(delivery_files, "_same_file", side_effect=broken),
                self.assertRaises(ValueError) as caught,
            ):
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
            with self.assertRaises(delivery_files._CompareError):
                delivery_files._same_file(missing, other)

    def test_same_bytes_raises_compare_error_instead_of_silently_returning_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nao-existe.mp4"
            other = Path(tmp) / "outro.mp4"
            other.write_bytes(b"x")
            with self.assertRaises(delivery_files._CompareError):
                delivery_files._same_bytes(missing, other)


class InterruptedCopy(unittest.TestCase):
    """Uma cópia cortada no meio (Ctrl+C) não pode deixar arquivo truncado em `entrega/`."""

    def test_interrupted_copy_leaves_no_partial_file(self):
        real_copy = shutil.copy2

        def interrupted(src, dst, *_args, **_kwargs):
            with Path(src).open("rb") as source, Path(dst).open("wb") as target:
                target.write(source.read(5))
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            plan = delivery.build_delivery(tmp, dry_run=True)
            dest = Path(tmp) / plan["items"][0]["path"]
            with patch.object(shutil, "copy2", interrupted), self.assertRaises(KeyboardInterrupt):
                delivery.build_delivery(tmp)
            self.assertFalse(dest.exists())
            self.assertEqual([], [p.name for p in dest.parent.iterdir()] if dest.parent.is_dir() else [])
            self.assertIs(real_copy, shutil.copy2)
            # A rodada seguinte não acusa "edição sua": não sobrou nada pela metade.
            report = delivery.build_delivery(tmp)
            self.assertEqual(["copy"], [i["method"] for i in report["items"]])
            self.assertEqual((Path(tmp) / "brolls" / "clips" / "x.mp4").read_bytes(), dest.read_bytes())


class MissingClip(unittest.TestCase):
    """Clipe sumido de `clips/` com o manifesto dizendo `verified` não vira "entregue"."""

    def test_missing_clip_is_a_conflict_not_a_delivery(self):
        for delivered_before in (False, True):
            with self.subTest(delivered_before=delivered_before), tempfile.TemporaryDirectory() as tmp:
                ledger = project(
                    tmp,
                    [
                        fetched("a", "Palco", shot="abertura", clip="clips/a.mp4", sheet="previews/a.jpg"),
                        fetched("b", "Plateia", shot="fechamento", clip="clips/b.mp4", sheet="previews/b.jpg"),
                    ],
                )
                if delivered_before:
                    delivery.build_delivery(tmp)
                before = {c["id"]: c.get("delivery") for c in Ledger(tmp, recover=False).data["items"]}
                unlink_frozen(ledger.root / "clips" / "a.mp4")
                with self.assertRaises(ValueError) as caught:
                    delivery.build_delivery(tmp)
                message = str(caught.exception)
                self.assertIn("clips/a.mp4", message)
                self.assertIn("verify", message)
                stored = {c["output"]["path"]: c for c in Ledger(tmp, recover=False).data["items"]}
                missing = stored["clips/a.mp4"]
                self.assertEqual(before[missing["id"]], missing.get("delivery"))
                # O outro item segue entregue normalmente.
                self.assertTrue((stored["clips/b.mp4"].get("delivery") or {}).get("method"))
                index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
                self.assertIn("## Conflitos", index)
                conflicts = index.split("## Conflitos", 1)[1]
                self.assertIn("clips/a.mp4", conflicts)
                self.assertIn("verify", conflicts)
                # Fora da tabela: a linha do item em conflito não diz que ele foi entregue.
                self.assertNotIn("01-abertura", index.split("## Conflitos", 1)[0].split("|---|", 1)[1])
                self.assertIn("01-abertura", conflicts)
                if delivered_before:
                    # O que já estava em `entrega/` fica: a varredura não apaga o item em conflito.
                    self.assertTrue((Path(tmp) / before[missing["id"]]["path"]).is_file())


def _brief_with_target(tmp, target):
    """BRIEF.md válido com um beat só (o `abertura` do modelo), com o alvo dado."""
    raw = (ROOT / "docs" / "BRIEF.md").read_text(encoding="utf-8")
    block = re.findall(r"```json\s*\n(.*?)\n```", raw, re.DOTALL)[0]
    data = json.loads(block)
    data["beats"] = [{**data["beats"][0], "target": target}]
    Path(tmp, "BRIEF.md").write_text(
        raw.replace(block, json.dumps(data, ensure_ascii=False, indent=2), 1), encoding="utf-8"
    )
    return data["beats"][0]["id"]


class SweepKeepsEditedCopies(unittest.TestCase):
    """Com `GB_DELIVERY_COPY=1`, a cópia editada numa pasta que saiu do plano nunca é apagada."""

    def _delivered_copy(self, tmp):
        beat = _brief_with_target(tmp, "Palco principal")
        c = fetched("a", "Palco", shot=beat)
        # O sha registrado é o dos bytes que `project` grava no clipe.
        c["output"]["sha256"] = hashlib.sha256(b"conteudo de clips/x.mp4").hexdigest()
        project(tmp, [c])
        first = delivery.build_delivery(tmp)
        self.assertEqual(["copy"], [i["method"] for i in first["items"]])
        return Path(tmp) / first["items"][0]["path"]

    def test_sweep_never_deletes_an_edited_copy(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
            media = self._delivered_copy(tmp)
            # A cópia é independente: editar é o que o COPY_NOTE convida a fazer.
            media.write_bytes(b"MEU CORTE EDITADO")
            # O alvo do beat muda e a pasta da entrega muda de nome.
            _brief_with_target(tmp, "Outro alvo completamente diferente")
            with self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            self.assertIn(media.relative_to(Path(tmp) / "entrega").as_posix(), str(caught.exception))
            # Sem fonte que confira, a mensagem não sugere apagar nada.
            self.assertNotRegex(str(caught.exception), r"\bapague\b")
            self.assertEqual(b"MEU CORTE EDITADO", media.read_bytes())
            # O ORIGEM.md da cópia editada fica com ela: origem e condições de uso não somem.
            self.assertIn("Candidato:", (media.parent / "ORIGEM.md").read_text(encoding="utf-8"))
            stored = Ledger(tmp, recover=False).data["items"][0]["delivery"]["path"]
            new = Path(tmp) / stored
            self.assertNotEqual(media, new)
            self.assertEqual(b"conteudo de clips/x.mp4", new.read_bytes())
            index = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8")
            conflicts = index.split("## Conflitos", 1)[1]
            self.assertIn(media.name, conflicts)
            self.assertNotRegex(conflicts, r"\bapague\b")
            # O ensaio vê o mesmo conflito e também não apaga nada.
            dry = delivery.build_delivery(tmp, dry_run=True)
            self.assertEqual([str(caught.exception)], dry["conflicts"])
            self.assertEqual(b"MEU CORTE EDITADO", media.read_bytes())
            self.assertTrue((media.parent / "ORIGEM.md").is_file())

    def test_sweep_still_removes_an_untouched_copy(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
            media = self._delivered_copy(tmp)
            _brief_with_target(tmp, "Outro alvo completamente diferente")
            report = delivery.build_delivery(tmp)
            self.assertFalse(media.exists())
            self.assertFalse(media.parent.exists())
            self.assertIn(media.relative_to(Path(tmp) / "entrega").as_posix(), report["removed"])
            self.assertTrue((Path(tmp) / report["items"][0]["path"]).is_file())


def _two_delivered(tmp):
    """Dois itens entregues (`a` no beat abertura, `b` no fechamento): {id: arquivo em `entrega/`}."""
    project(
        tmp,
        [
            fetched("a", "Palco", shot="abertura", clip="clips/a.mp4", sheet="previews/a.jpg"),
            fetched("b", "Plateia", shot="fechamento", clip="clips/b.mp4", sheet="previews/b.jpg"),
        ],
    )
    first = delivery.build_delivery(tmp)
    return {i["id"]: Path(tmp) / i["path"] for i in first["items"]}


def _change_item(tmp, clip, change):
    """Aplica `change` ao item cujo clipe é `clip` e grava o manifesto; devolve o item."""
    ledger = Ledger(tmp, recover=False)
    c = next(x for x in ledger.data["items"] if x["output"]["path"] == clip)
    change(c)
    ledger.save_many("test", [c])
    return c


class OrphanMessageUsesSlashes(unittest.TestCase):
    """The text of a conflict names the file with `/` on every system (a Windows root is fed in by hand)."""

    ctx = SimpleNamespace(root=PureWindowsPath(r"C:\p\entrega"))

    def test_an_edited_orphan_with_no_clip_is_named_with_slashes(self):
        with patch.object(delivery, "_vanished_clip", return_value=None):
            message, _note = delivery._edited_orphan(self.ctx, "a/b.mp4", None)
        self.assertTrue(message.startswith("C:/p/entrega/a/b.mp4 saiu do plano"), message)
        self.assertNotIn("\\", message)

    def test_an_edited_orphan_whose_clip_vanished_is_named_with_slashes(self):
        with patch.object(delivery, "_vanished_clip", return_value="brolls/x.mp4"):
            message, _note = delivery._edited_orphan(self.ctx, "a/b.mp4", {})
        self.assertTrue(message.startswith("C:/p/entrega/a/b.mp4 saiu do plano"), message)
        self.assertNotIn("\\", message)


class SweepKnowsEveryRevision(unittest.TestCase):
    """O link (ou a cópia) intocado de um clipe que saiu do manifesto continua sendo nosso.

    `reject`, um corte novo (`set_segment`), a troca de formato (`invalidate_approval`) e um
    `fetch` de outra revisão tiram o caminho do clipe do manifesto, mas o arquivo continua
    em `brolls/clips/`: a varredura apaga o que sobrou dele em `entrega/` sem acusar edição.
    """

    CHANGES = (
        ("reject", lambda c: mark_rejected(c, "não serve mais")),
        ("set_segment", lambda c: set_segment(c, 0, 1.5)),
        ("invalidate_approval", lambda c: invalidate_approval(c, bump_revision=True)),
    )

    def test_deliver_after_the_clip_left_the_manifest_sweeps_the_untouched_files(self):
        for env in ({}, {"GB_DELIVERY_COPY": "1"}):
            for name, change in self.CHANGES:
                with (
                    self.subTest(change=name, env=env),
                    tempfile.TemporaryDirectory() as tmp,
                    patch.dict(os.environ, env),
                ):
                    delivered = _two_delivered(tmp)
                    c = _change_item(tmp, "clips/a.mp4", change)
                    media = delivered[c["id"]]
                    self.assertTrue(media.is_file())
                    report = delivery.build_delivery(tmp)
                    self.assertFalse(media.exists())
                    self.assertFalse(media.parent.exists())
                    self.assertIn(media.relative_to(Path(tmp) / "entrega").as_posix(), report["removed"])
                    self.assertEqual([], report["kept"])
                    # O clipe canônico continua em brolls/, intocado.
                    self.assertEqual(b"conteudo de clips/a.mp4", (Path(tmp) / "brolls/clips/a.mp4").read_bytes())

    def test_a_new_revision_and_a_renamed_beat_sweep_the_old_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            beat = _brief_with_target(tmp, "Palco principal")
            ledger = project(tmp, [fetched("a", "Palco", shot=beat, clip="clips/a-r1.mp4", sheet="previews/a.jpg")])
            first = delivery.build_delivery(tmp)
            old = Path(tmp) / first["items"][0]["path"]
            # `fetch` de outro corte grava um `-r2` novo; o `-r1` continua no disco.
            (ledger.root / "clips" / "a-r2.mp4").write_bytes(b"bytes da revisao 2")
            _change_item(tmp, "clips/a-r1.mp4", lambda c: c["output"].update(path="clips/a-r2.mp4"))
            _brief_with_target(tmp, "Outro alvo diferente")
            report = delivery.build_delivery(tmp)
            self.assertFalse(old.exists())
            self.assertIn(old.relative_to(Path(tmp) / "entrega").as_posix(), report["removed"])
            self.assertEqual(b"bytes da revisao 2", (Path(tmp) / report["items"][0]["path"]).read_bytes())

    def test_a_regenerated_contact_sheet_in_a_renamed_beat_is_swept(self):
        """O `preview` regrava o sheet no mesmo caminho (inode novo): o antigo em `entrega/` é só prévia velha."""
        with tempfile.TemporaryDirectory() as tmp:
            beat = _brief_with_target(tmp, "Palco principal")
            ledger = project(tmp, [fetched("a", "Palco", shot=beat)])
            first = delivery.build_delivery(tmp)
            old_sheet = (Path(tmp) / first["items"][0]["path"]).parent / "contact-sheet.jpg"
            self.assertTrue(old_sheet.is_file())
            staged = ledger.root / "previews" / "stage.jpg"
            staged.write_bytes(b"sheet regravado")
            staged.replace(ledger.root / "previews" / "x.jpg")
            _brief_with_target(tmp, "Outro alvo diferente")
            report = delivery.build_delivery(tmp)
            self.assertFalse(old_sheet.exists())
            self.assertFalse(old_sheet.parent.exists())
            self.assertIn(old_sheet.relative_to(Path(tmp) / "entrega").as_posix(), report["removed"])

    def test_the_last_copy_of_a_vanished_clip_is_kept_with_its_own_message(self):
        """O clipe sumiu e o `verify` o marcou como não conferido: o arquivo em `entrega/` pode ser a única cópia."""
        with tempfile.TemporaryDirectory() as tmp:
            delivered = _two_delivered(tmp)
            c = _change_item(tmp, "clips/a.mp4", lambda c: c["output"].update(verified=False))
            media = delivered[c["id"]]
            unlink_frozen(Path(tmp) / "brolls" / "clips" / "a.mp4")
            with self.assertRaises(ValueError) as caught:
                delivery.build_delivery(tmp)
            message = str(caught.exception)
            self.assertIn(media.relative_to(Path(tmp) / "entrega").as_posix(), message)
            self.assertIn("brolls/clips/a.mp4", message)
            self.assertIn("única cópia", message)
            self.assertNotIn("edição sua", message)
            self.assertNotRegex(message, r"\bapague\b")
            self.assertEqual(b"conteudo de clips/a.mp4", media.read_bytes())
            self.assertTrue((media.parent / "ORIGEM.md").is_file())
            conflicts = (Path(tmp) / "entrega" / "README.md").read_text(encoding="utf-8").split("## Conflitos", 1)[1]
            self.assertIn(media.name, conflicts)
            self.assertIn("única cópia", conflicts)
            dry = delivery.build_delivery(tmp, dry_run=True)
            self.assertEqual([message], dry["conflicts"])
            # Devolver o arquivo para brolls/ é o caminho de volta: o `deliver` seguinte passa.
            shutil.copy2(media, Path(tmp) / "brolls" / "clips" / "a.mp4")
            _change_item(tmp, "clips/a.mp4", lambda c: c["output"].update(verified=True))
            report = delivery.build_delivery(tmp)
            self.assertEqual(2, len(report["items"]))


class LeftoverPartFiles(unittest.TestCase):
    """O `.part` que um kill à força deixou é do gerador, não "o que você criou lá"."""

    def test_a_leftover_part_file_is_swept_inside_and_outside_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            beat = _brief_with_target(tmp, "Palco principal")
            project(tmp, [fetched("a", "Palco", shot=beat)])
            first = delivery.build_delivery(tmp)
            media = Path(tmp) / first["items"][0]["path"]
            planned = media.with_name(media.name + ".part")
            planned.write_bytes(b"metade")
            sheet_part = media.parent / "contact-sheet.jpg.part"
            sheet_part.write_bytes(b"metade")
            _brief_with_target(tmp, "Outro alvo diferente")
            report = delivery.build_delivery(tmp)
            self.assertFalse(planned.exists())
            self.assertFalse(sheet_part.exists())
            self.assertFalse(media.parent.exists())
            self.assertEqual([], report["kept"])
            # Dentro de uma pasta que continua no plano, a sobra também sai.
            current = Path(tmp) / report["items"][0]["path"]
            leftover = current.with_name(current.name + ".part")
            leftover.write_bytes(b"metade")
            again = delivery.build_delivery(tmp)
            self.assertFalse(leftover.exists())
            self.assertEqual([], again["kept"])

    def test_a_part_file_the_person_named_outside_the_generator_names_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            project(tmp, [fetched("a", "Palco", shot="abertura")])
            first = delivery.build_delivery(tmp)
            mine = (Path(tmp) / first["items"][0]["path"]).parent / "meu-video.mp4.part"
            mine.write_bytes(b"meu")
            report = delivery.build_delivery(tmp)
            self.assertTrue(mine.exists())
            self.assertIn(mine.relative_to(Path(tmp) / "entrega").as_posix(), report["kept"])


class CopyReadOnlyWarning(unittest.TestCase):
    """A cópia que não consegue virar editável vira aviso, com o nome final do arquivo."""

    def _frozen_clip(self, tmp):
        src = Path(tmp) / "clipe.mp4"
        src.write_bytes(b"bytes do clipe")
        src.chmod(0o444)
        return src

    def test_a_chmod_failure_on_the_copy_is_a_warning_naming_the_final_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = self._frozen_clip(tmp)
            dest = Path(tmp) / "entrega" / "01-a" / "01-a.mp4"
            dest.parent.mkdir(parents=True)

            def refuse(*_args, **_kwargs):
                raise OSError(13, "Permission denied")

            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                with patch.object(Path, "chmod", refuse):
                    delivery_files._copy_whole(src, dest)
            finally:
                runtime.ACTIVE.reset(token)
            warning = next(w for w in event["warnings"] if w["code"] == "DELIVERY_COPY_READ_ONLY")
            # The final file by its label (`labels.shown`), never the machine path nor the `.part`.
            self.assertIn("/entrega/01-a/01-a.mp4 editável", warning["message"])
            self.assertNotIn(str(dest.parent), warning["message"])
            self.assertNotIn(".part", warning["message"])
            self.assertEqual(b"bytes do clipe", dest.read_bytes())

    def test_a_copy_outside_entrega_keeps_the_mode_and_records_nothing(self):
        """O `export` usa a mesma escada sem chmod: a cópia de um clipe congelado sai como ele."""
        with tempfile.TemporaryDirectory() as tmp:
            src = self._frozen_clip(tmp)
            dest = Path(tmp) / "export" / "clipe.mp4"
            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                with patch.object(os, "link", side_effect=OSError("outro volume")):
                    method = delivery.link_or_copy(src, dest, allow_symlink=False, editable_copy=False)
            finally:
                runtime.ACTIVE.reset(token)
            self.assertEqual("copy", method)
            self.assertEqual(0o444, stat_module.S_IMODE(dest.stat().st_mode))
            self.assertEqual([], event["warnings"])
            dest.chmod(0o644)


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
                delivery_files._freeze(path, "hardlink")
            finally:
                runtime.ACTIVE.reset(token)
                os.chmod = original
            self.assertTrue(any(w["code"] == "DELIVERY_FREEZE_FAILED" for w in event["warnings"]))
            warning = next(w for w in event["warnings"] if w["code"] == "DELIVERY_FREEZE_FAILED")
            self.assertIn(f"/{path.name} somente-leitura", warning["message"])
            self.assertNotIn(str(path.parent), warning["message"])

    def test_a_successful_freeze_records_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "congelado.mp4"
            path.write_bytes(b"bytes")
            event = {"warnings": [], "state_committed": False}
            token = runtime.ACTIVE.set(event)
            try:
                delivery_files._freeze(path, "hardlink")
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

    def test_falsy_but_not_the_false_singleton_is_also_skipped(self):
        """Finding: the old guard used `is False`, so `0`/`None` (falsy, but not the
        `False` singleton) slipped through and were delivered anyway, while
        `STAGE_TESTS["verified"]` in summaries.py (which uses `bool(...)`) already
        reported the same item as unverified — an inconsistent reader."""
        for falsy in (0, None, 0.0, ""):
            with self.subTest(verified=falsy), tempfile.TemporaryDirectory() as tmp:
                bad = fetched("a", "Palco", shot="abertura")
                bad["output"]["verified"] = falsy
                project(tmp, [bad])
                report = delivery.build_delivery(tmp)
                self.assertEqual([], report["items"])
                self.assertEqual(1, len(report["skipped"]))
                self.assertEqual(bad["id"], report["skipped"][0]["id"])

    def test_an_item_with_verified_true_still_delivers_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            fine = fetched("a", "Palco", shot="abertura")
            fine["output"]["verified"] = True
            project(tmp, [fine])
            report = delivery.build_delivery(tmp)
            self.assertEqual(1, len(report["items"]))
            self.assertEqual([], report["skipped"])

    def test_an_item_whose_output_has_no_verified_key_still_delivers(self):
        """Older manifests written before `verified` existed must keep delivering:
        the rule is about a falsy value that IS present, not about absence."""
        with tempfile.TemporaryDirectory() as tmp:
            fine = fetched("a", "Palco", shot="abertura")
            del fine["output"]["verified"]
            # `validate_manifest` requires the key, so this bypasses the ledger and
            # calls `_plan` directly, the way a hand-edited old manifest would look.
            groups, skipped = delivery._plan(tmp, [fine])
            self.assertEqual([], skipped)
            self.assertEqual(1, sum(len(g["items"]) for g in groups))

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

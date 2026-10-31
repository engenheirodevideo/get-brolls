"""O journal do `fetch` leva o nome final do clipe: um SIGKILL, em qualquer ponto, deixa o projeto recuperável.

O processo filho morre com `os._exit` (sem `finally` nem `except`, como um SIGKILL) num ponto do
`save_many`; o teste abre o projeto de novo e confere o que sobrou.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT

from getbrolls.ledger import Ledger
from getbrolls.models import id_stem

KILLED = 137
STATEMENT = "Aprovo este trecho para o vídeo."
EVIDENCE = "Material próprio de teste sintético."

# Roda o `fetch` e mata o processo num ponto: `after_journal` (journal no disco, nada mais) ou
# `after_rename` (clipe com o nome final, manifesto ainda sem gravar).
CHILD = """
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from getbrolls import cli, ledger

where = sys.argv[2]
real_write = ledger.atomic_write


def die(*_args, **_kwargs):
    os._exit(137)


if where == "after_journal":
    ledger.Ledger._finish_transaction = die
else:

    def write(path, text, *, unique=False):
        if Path(path).name == "manifest.json":
            die()
        return real_write(path, text, unique=unique)

    ledger.atomic_write = write
cli.main(sys.argv[3:])
"""


def _prepared(tmp):
    """Projeto com um corte aprovado e liberado, pronto para `fetch`: (pasta, id, argumentos)."""
    root = Path(tmp)
    src = root / "original.mp4"
    synth_video(src, size="160x90", duration=3, rate=10)
    cid = run_cli("resolve", "--file", src, project=root)["id"]
    base = ["--candidate", cid, "--project", str(root)]
    run_cli("preview", *base, "--start", "0", "--end", "1")
    run_cli("approve", *base, "--start", "0", "--end", "1", "--by", "Ana", "--statement", STATEMENT)
    run_cli("permit", *base, "--evidence", EVIDENCE)
    return root, cid, base


def _fetch_killed_at(base, where):
    done = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT / "scripts"), where, "fetch", *base],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert done.returncode == KILLED, done.stderr


def _item(root, cid):
    manifest = json.loads((root / "brolls" / "manifest.json").read_text(encoding="utf-8"))
    return next(c for c in manifest["items"] if c["id"] == cid)


def _clip_names(root):
    return sorted(path.name for path in (root / "brolls" / "clips").iterdir())


@skip_unless_ffmpeg
class KilledFetchTests(unittest.TestCase):
    def test_a_kill_after_the_journal_is_finished_by_the_next_open(self):
        """Journal no disco e o corte ainda provisório: abrir o projeto dá o nome final e grava o manifesto."""
        with tempfile.TemporaryDirectory() as tmp:
            root, cid, base = _prepared(tmp)
            stem = f"{id_stem(cid)}-r{_item(root, cid)['segment']['revision']}"
            _fetch_killed_at(base, "after_journal")
            self.assertTrue((root / "brolls" / ".pending-transaction.json").exists())
            self.assertEqual([f"{stem}.staged.mp4"], _clip_names(root))
            Ledger(root)  # o próximo comando que grava conclui o journal
            item = _item(root, cid)
            self.assertTrue(item["output"]["verified"])
            self.assertEqual([f"{stem}.mp4"], _clip_names(root))
            self.assertEqual(f"clips/{stem}.mp4", item["output"]["path"])
            self.assertEqual(1, run_cli("verify", project=root)["count"])

    def test_a_kill_after_the_rename_only_writes_the_manifest(self):
        """O corte já tem o nome final quando o manifesto é gravado: nenhum manifesto nomeia um clipe ausente."""
        with tempfile.TemporaryDirectory() as tmp:
            root, cid, base = _prepared(tmp)
            stem = f"{id_stem(cid)}-r{_item(root, cid)['segment']['revision']}"
            _fetch_killed_at(base, "after_rename")
            self.assertIsNone(_item(root, cid)["output"]["path"])
            self.assertEqual([f"{stem}.mp4"], _clip_names(root))
            Ledger(root)
            self.assertEqual(f"clips/{stem}.mp4", _item(root, cid)["output"]["path"])
            self.assertEqual(1, run_cli("verify", project=root)["count"])


class PublishJournalTests(unittest.TestCase):
    def _journal(self, root, publish):
        (root / "brolls").mkdir()
        journal = {"data": {"schema_version": 1, "items": []}, "events": [], "publish": publish}
        (root / "brolls" / ".pending-transaction.json").write_text(json.dumps(journal), encoding="utf-8")

    def test_a_journal_that_names_something_else_than_a_clip_is_refused_and_kept(self):
        for bad in (
            "../fora.mp4", "clips/../fora.mp4", "clips/..", "clips/a/b.mp4", "clips/a\\b.mp4", "clips/x.staged.mp4",
            "clips/", "fora.mp4", 3,
        ):  # fmt: skip
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._journal(root, [bad])
                with self.assertRaisesRegex(ValueError, "Journal de recuperação inválido"):
                    Ledger(root)
                self.assertTrue((root / "brolls" / ".pending-transaction.json").exists())

    def test_a_clip_that_is_neither_provisional_nor_final_does_not_block_the_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._journal(root, ["clips/sumiu.mp4"])
            Ledger(root)
            self.assertFalse((root / "brolls" / ".pending-transaction.json").exists())
            self.assertEqual([], _clip_names(root))

    def test_a_provisional_clip_swapped_for_a_link_is_refused_and_the_journal_kept(self):
        """Link vivo ou quebrado no lugar do provisório: nada é renomeado, o journal fica, sem caminho na mensagem."""
        for dangling in (False, True):
            with self.subTest(dangling=dangling), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._journal(root, ["clips/c.mp4"])
                clips = root / "brolls" / "clips"
                clips.mkdir()
                target = clips / "alvo.bin"
                if not dangling:
                    target.write_bytes(b"x")
                link = clips / "c.staged.mp4"
                try:
                    link.symlink_to(target)
                except (OSError, NotImplementedError):
                    self.skipTest("sem suporte a links simbólicos")
                with self.assertRaises(ValueError) as caught:
                    Ledger(root)
                self.assertIn("não é um arquivo comum", str(caught.exception))
                self.assertNotIn(tmp, str(caught.exception))
                self.assertTrue((root / "brolls" / ".pending-transaction.json").exists())
                self.assertTrue(link.is_symlink())
                self.assertFalse((clips / "c.mp4").exists())
                self.assertFalse((clips / "c.mp4").is_symlink())

    def test_a_provisional_clip_that_is_a_folder_is_refused_and_the_journal_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._journal(root, ["clips/c.mp4"])
            (root / "brolls" / "clips" / "c.staged.mp4").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "não é um arquivo comum"):
                Ledger(root)
            self.assertTrue((root / "brolls" / ".pending-transaction.json").exists())

    def test_a_journal_without_publish_keeps_the_old_shape(self):
        seen = []
        real = Ledger._finish_transaction

        def spy(ledger):
            seen.append(json.loads(ledger.pending.read_text(encoding="utf-8")))
            return real(ledger)

        with tempfile.TemporaryDirectory() as tmp, patch.object(Ledger, "_finish_transaction", spy):
            Ledger(tmp).save("fixture")
        self.assertEqual({"schema_version", "data", "events"}, set(seen[0]))


if __name__ == "__main__":
    unittest.main()

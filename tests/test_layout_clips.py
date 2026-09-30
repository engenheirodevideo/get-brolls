"""Onde mora o clipe final: `broll/` no layout 1, `brolls/clips/` no layout 0.

O manifesto guarda sempre o caminho lógico `clips/<arquivo>`; só `layout` sabe em que
pasta ele está. No layout 1 a leitura procura em `broll/` e, se existir, na pasta
antiga `brolls/clips/` (projeto adotado sem mover nada). O mesmo nome nas duas pastas
nunca vira escolha calada: ou o `sha256` registrado desempata, ou é um problema.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _cli import run_cli
from _media import skip_unless_ffmpeg, synth_video
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import layout
from getbrolls.ledger import Ledger, digest
from getbrolls.models import id_stem


def make_layout_one(project):
    """Projeto de layout 1 como o `init` deixa (só o que importa aqui: `project.json` e `broll/`)."""
    project.mkdir(parents=True, exist_ok=True)
    layout.write_project(project, layout.new_project_doc())
    (project / "broll").mkdir(exist_ok=True)


def symlink_or_skip(test, link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        test.skipTest("este sistema não cria symlink")


class TempProject(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-clips-")).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def legacy(self):
        return self.project / "brolls" / "clips"


class LayoutZeroTests(TempProject):
    def test_every_clip_lives_under_brolls_clips(self):
        self.assertEqual(self.legacy(), layout.clips_dir(self.project))
        self.assertEqual((self.legacy(),), layout.clip_roots(self.project))
        self.assertEqual(self.legacy() / "a.mp4", layout.clip_file(self.project, "clips/a.mp4"))
        self.assertEqual(self.legacy() / "a.mp4", layout.clip_file(self.project, "clips/a.mp4", for_write=True))

    def test_a_broll_folder_of_the_person_is_never_read(self):
        """Pasta `broll/` de quem ainda está no layout 0 é da pessoa: nunca vira raiz de clipe."""
        (self.project / "broll").mkdir()
        (self.project / "broll" / "a.mp4").write_bytes(b"da pessoa")
        self.assertEqual(self.legacy() / "a.mp4", layout.clip_file(self.project, "clips/a.mp4"))
        self.assertEqual((self.legacy(),), layout.clip_roots(self.project))

    def test_ledger_still_creates_brolls_clips(self):
        Ledger(self.project)
        self.assertTrue(self.legacy().is_dir())
        self.assertFalse((self.project / "broll").exists())

    def test_output_file_keeps_any_other_logical_path_under_brolls(self):
        self.assertEqual(
            self.project / "brolls" / "previews" / "x.jpg", layout.output_file(self.project, "previews/x.jpg")
        )


class LayoutOneTests(TempProject):
    def setUp(self):
        super().setUp()
        make_layout_one(self.project)

    def test_writes_go_to_broll_and_reads_fall_back_to_the_legacy_folder(self):
        broll = self.project / "broll"
        self.assertEqual(broll, layout.clips_dir(self.project))
        self.assertEqual((broll,), layout.clip_roots(self.project))
        self.legacy().mkdir(parents=True)
        self.assertEqual((broll, self.legacy()), layout.clip_roots(self.project))
        (self.legacy() / "old.mp4").write_bytes(b"old")
        self.assertEqual(self.legacy() / "old.mp4", layout.clip_file(self.project, "clips/old.mp4"))
        self.assertEqual(broll / "old.mp4", layout.clip_file(self.project, "clips/old.mp4", for_write=True))
        self.assertEqual(broll / "new.mp4", layout.clip_file(self.project, "clips/new.mp4"))

    def test_same_name_in_both_folders_is_a_problem_unless_the_hash_decides(self):
        self.legacy().mkdir(parents=True)
        (self.project / "broll" / "x.mp4").write_bytes(b"novo")
        (self.legacy() / "x.mp4").write_bytes(b"antigo")
        with self.assertRaises(layout.ClipConflictError) as ctx:
            layout.clip_file(self.project, "clips/x.mp4")
        self.assertIn("broll/", str(ctx.exception))
        self.assertIn("brolls/clips/", str(ctx.exception))
        legacy_sha = digest(self.legacy() / "x.mp4")
        self.assertEqual(self.legacy() / "x.mp4", layout.clip_file(self.project, "clips/x.mp4", sha256=legacy_sha))
        with self.assertRaises(layout.ClipConflictError):
            layout.clip_file(self.project, "clips/x.mp4", sha256="0" * 64)

    def test_identical_copies_in_both_folders_are_not_a_conflict(self):
        self.legacy().mkdir(parents=True)
        (self.project / "broll" / "x.mp4").write_bytes(b"igual")
        (self.legacy() / "x.mp4").write_bytes(b"igual")
        sha = digest(self.legacy() / "x.mp4")
        self.assertEqual(self.project / "broll" / "x.mp4", layout.clip_file(self.project, "clips/x.mp4", sha256=sha))

    def test_existing_clips_lists_both_folders(self):
        self.legacy().mkdir(parents=True)
        (self.project / "broll" / "a-r0.jpg").write_bytes(b"a")
        (self.legacy() / "a-r0.png").write_bytes(b"b")
        (self.legacy() / "b-r0.png").write_bytes(b"c")
        self.assertEqual(["clips/a-r0.jpg", "clips/a-r0.png"], layout.existing_clips(self.project, "a-r0.*"))

    def test_a_linked_broll_folder_is_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="gb-clips-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (self.project / "broll").rmdir()
        symlink_or_skip(self, self.project / "broll", outside)
        for call in (
            lambda: layout.clips_dir(self.project),
            lambda: layout.clip_roots(self.project),
            lambda: layout.clip_file(self.project, "clips/a.mp4"),
        ):
            with self.assertRaises(ValueError) as ctx:
                call()
            self.assertIn("broll/ é um link", str(ctx.exception))

    def test_ledger_creates_broll_and_not_brolls_clips(self):
        Ledger(self.project)
        self.assertTrue((self.project / "broll").is_dir())
        self.assertTrue((self.project / "brolls" / "candidates").is_dir())
        self.assertTrue((self.project / "brolls" / "previews").is_dir())
        self.assertFalse(self.legacy().exists())


class BadPathTests(TempProject):
    def test_only_a_plain_clips_path_is_accepted(self):
        for rel in ("previews/a.mp4", "clips/../manifest.json", "clips\\a.mp4", "clips/", "a.mp4", None):
            with self.subTest(rel=rel), self.assertRaises(ValueError):
                layout.clip_file(self.project, rel)


class BrokenProjectFileTests(TempProject):
    def test_a_broken_project_json_without_broll_behaves_like_layout_zero(self):
        (self.project / layout.PROJECT_FILE).write_text("{", encoding="utf-8")
        self.assertEqual(self.legacy(), layout.clips_dir(self.project))

    def test_a_broken_project_json_next_to_broll_refuses_to_write(self):
        (self.project / layout.PROJECT_FILE).write_text("{", encoding="utf-8")
        (self.project / "broll").mkdir()
        with self.assertRaises(ValueError) as ctx:
            layout.clips_dir(self.project)
        self.assertIn(layout.PROJECT_FILE, str(ctx.exception))


def collect_local(test, project, *, fetch_expect=0):
    """Resolve, prévia, aprovação, permit e fetch de um vídeo sintético; devolve (candidato, fetch)."""
    src = project.parent / (project.name + "-original.mp4")
    synth_video(src, size="320x180", duration=3, rate=10)
    test.addCleanup(src.unlink, missing_ok=True)
    c = run_cli("resolve", "--file", src, "--shot", "abertura", project=project)
    base = ["--candidate", c["id"]]
    run_cli("preview", *base, "--start", 0.5, "--end", 1.5, "--narration", "Linha um", project=project)
    run_cli(
        "approve",
        *base,
        "--start",
        0.5,
        "--end",
        1.5,
        "--by",
        "Fixture",
        "--statement",
        "Aprovo.",
        project=project,
    )
    run_cli("permit", *base, "--evidence", "Vídeo sintético de teste", project=project)
    return c, run_cli("fetch", *base, project=project, expect=fetch_expect)


def manifest_item(project, ident):
    data = json.loads((project / "brolls" / "manifest.json").read_text(encoding="utf-8"))
    return next(item for item in data["items"] if item["id"] == ident)


@skip_unless_ffmpeg
class FetchTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp(prefix="gb-clips-cli-")).resolve()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.project = base / "video"

    def test_layout_one_fetch_lands_in_broll(self):
        make_layout_one(self.project)
        c, _ = collect_local(self, self.project)
        item = manifest_item(self.project, c["id"])
        rel = item["output"]["path"]
        self.assertTrue(rel.startswith("clips/"), rel)
        final = self.project / "broll" / rel.removeprefix("clips/")
        self.assertTrue(final.is_file())
        self.assertEqual(item["output"]["sha256"], digest(final))
        self.assertFalse((self.project / "brolls" / "clips").exists())

    def test_layout_one_refuses_a_revision_already_in_the_legacy_folder(self):
        make_layout_one(self.project)
        legacy = self.project / "brolls" / "clips"
        # O nome planejado depende do id do candidato: resolve primeiro, depois planta a cópia antiga.
        src = self.project.parent / "original.mp4"
        synth_video(src, size="320x180", duration=3, rate=10)
        c = run_cli("resolve", "--file", src, "--shot", "abertura", project=self.project)
        base = ["--candidate", c["id"]]
        run_cli("preview", *base, "--start", 0.5, "--end", 1.5, project=self.project)
        run_cli("approve", *base, "--start", 0.5, "--end", 1.5, "--by", "F", "--statement", "Ok.", project=self.project)
        run_cli("permit", *base, "--evidence", "Sintético", project=self.project)
        revision = manifest_item(self.project, c["id"])["segment"]["revision"]
        stem = id_stem(c["id"]) + f"-r{revision}.mp4"
        legacy.mkdir(parents=True)
        (legacy / stem).write_bytes(b"coletado na 2.5")
        refused = run_cli("fetch", *base, project=self.project, expect=1)
        self.assertIn("clips/" + stem, json.dumps(refused, ensure_ascii=False))
        self.assertFalse((self.project / "broll" / stem).exists())
        self.assertEqual(b"coletado na 2.5", (legacy / stem).read_bytes())

    def test_layout_one_with_a_linked_broll_refuses_to_fetch(self):
        make_layout_one(self.project)
        outside = Path(tempfile.mkdtemp(prefix="gb-clips-outside-"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (self.project / "broll").rmdir()
        symlink_or_skip(self, self.project / "broll", outside)
        _, refused = collect_local(self, self.project, fetch_expect=1)
        self.assertIn("broll/ é um link", json.dumps(refused, ensure_ascii=False))
        self.assertEqual([], list(outside.iterdir()))

    def test_layout_zero_fetch_is_unchanged(self):
        c, _ = collect_local(self, self.project)
        rel = manifest_item(self.project, c["id"])["output"]["path"]
        self.assertTrue((self.project / "brolls" / rel).is_file())
        self.assertFalse((self.project / "broll").exists())


if __name__ == "__main__":
    unittest.main()

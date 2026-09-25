"""Revisão humana do roteiro: hash canônico, status e registro append-only."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import roteiro, roteiro_review

DOC = (
    '---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n\n'
    "## Problema <!-- c01 -->\n[BROLL: timeline cheia]\n[SFX: whoosh]\nTodo mundo trava.\nNa mesma parte.\n"
)


def read(text):
    return roteiro.parse(text, plugins=frozenset())


def digest(text):
    return roteiro_review.review_hash(read(text))


class ReviewHashTests(unittest.TestCase):
    def test_formatting_only_edits_keep_the_hash(self):
        base = digest(DOC)
        same = {
            "sem id": DOC.replace(" <!-- c01 -->", ""),
            "status": roteiro_review.set_status(DOC, "revisado"),
            "linhas em branco": DOC.replace("Todo mundo trava.\n", "\n\nTodo mundo trava.\n\n"),
            "espaços": DOC.replace("Todo mundo trava.", "Todo   mundo  trava.   "),
            "fala reflowed": DOC.replace("trava.\nNa mesma", "trava. Na mesma"),
            "default explícito": DOC.replace('tema: "t"', 'tema: "t"\nlegenda: true\naspecto: "9:16"'),
            "título com espaço": DOC.replace("## Problema", "##   Problema  "),
        }
        for label, text in same.items():
            with self.subTest(label=label):
                self.assertEqual(base, digest(text))

    def test_content_edits_change_the_hash(self):
        base = digest(DOC)
        changed = {
            "fala": DOC.replace("Todo mundo trava.", "Todo mundo para."),
            "alvo": DOC.replace("timeline cheia", "mesa de edição"),
            "camada movida": DOC.replace("[SFX: whoosh]\nTodo mundo trava.\n", "Todo mundo trava.\n[SFX: whoosh]\n"),
            "nota de linha": DOC.replace("Na mesma parte.", "[pausa]\nNa mesma parte."),
            "título": DOC.replace("## Problema", "## Dor"),
            "tema": DOC.replace('tema: "t"', 'tema: "outro"'),
        }
        for label, text in changed.items():
            with self.subTest(label=label):
                self.assertNotEqual(base, digest(text))


class StatusTests(unittest.TestCase):
    def test_set_status_inserts_then_replaces_inside_the_frontmatter(self):
        once = roteiro_review.set_status(DOC, "revisado")
        self.assertIn('tema: "t"\nstatus: revisado\n---', once)
        twice = roteiro_review.set_status(once, "draft")
        self.assertEqual(twice.count("status:"), 1)
        self.assertEqual(read(twice).meta["status"], "draft")
        body_status = DOC + "status: isso é fala, não frontmatter\n"
        self.assertTrue(
            roteiro_review.set_status(body_status, "revisado").endswith("status: isso é fala, não frontmatter\n")
        )

    def test_set_status_tolerates_trailing_spaces_on_the_fence(self):
        text = DOC.replace('tema: "t"\n---\n', 'tema: "t"\n---   \n')
        self.assertIn("status: revisado\n---   \n", roteiro_review.set_status(text, "revisado"))

    def test_set_status_refuses_broken_frontmatter(self):
        with self.assertRaises(ValueError):
            roteiro_review.set_status("## sem frontmatter\n", "revisado")


class ReviewRecordTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-rev-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_review_requires_name_chat_and_statement(self):
        doc = read(DOC)
        for by, channel, statement in (
            (" ", "chat", "ok"),
            ("Bruno Moreira", "storyboard", "ok"),
            ("Bruno Moreira", "chat", ""),
            ("Bruno Moreira", "chat", None),
        ):
            with self.subTest(by=by, channel=channel, statement=statement), self.assertRaises(ValueError):
                roteiro_review.record_review(self.project, doc, by, channel, statement)
        self.assertFalse((self.project / "brolls").exists())

    def test_review_is_bound_to_content(self):
        doc = read(DOC)
        self.assertFalse(roteiro_review.review_state(self.project, doc)["reviewed"])
        self.assertFalse((self.project / "brolls").exists())
        roteiro_review.record_review(self.project, doc, "Bruno Moreira", "chat", "pode seguir")
        state = roteiro_review.review_state(self.project, read(DOC.replace(" <!-- c01 -->", "")))
        self.assertEqual((state["reviewed"], state["by"]), (True, "Bruno Moreira"))
        self.assertFalse(roteiro_review.review_state(self.project, read(DOC.replace("trava.", "para.")))["reviewed"])
        path = self.project / "brolls" / roteiro_review.REVIEWS_FILE
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["statement"], "pode seguir")
        path.write_text("lixo\n" + lines[0] + "\n", encoding="utf-8")
        self.assertTrue(roteiro_review.review_state(self.project, doc)["reviewed"])


if __name__ == "__main__":
    unittest.main()

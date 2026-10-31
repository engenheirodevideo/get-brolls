"""Bordas do roteiro: BRIEF.md com formato torto, separadores Unicode na revisão, duração gigante, CRLF."""

import json
import unittest

from test_roteiro_cli import BRIEF, REVIEW, CliCase

from getbrolls import export_gates, roteiro, roteiro_review

SEPARATORS = (" ", "\u0085", "\x0b", "\x0c", " ", "\x1c")


class MalformedBriefShapeTests(CliCase):
    def brief_with(self, **changes):
        data = {**BRIEF, **changes}
        (self.project / "BRIEF.md").write_text("# Brief\n\n```json\n" + json.dumps(data) + "\n```\n", encoding="utf-8")

    def test_check_survives_a_video_block_that_is_not_an_object(self):
        self.ready()
        for video in ("x", ["a"], {"delivery": "x"}, {"delivery": ["a"]}):
            with self.subTest(video=video):
                self.brief_with(video=video)
                out = self.cli("roteiro", "--action", "check")
                self.assertTrue(any("BRIEF.md" in w for w in out["warnings"]), out["warnings"])

    def test_export_gate_refuses_a_malformed_brief_as_invalid_data_not_a_crash(self):
        self.ready()
        for video in ("x", {"delivery": "x"}):
            with self.subTest(video=video):
                self.brief_with(video=video)
                with self.assertRaises(ValueError) as caught:
                    export_gates.check(self.project, plugins=frozenset())
                self.assertIn("video", str(caught.exception))

    def test_a_well_formed_brief_still_reports_the_format_divergence(self):
        self.ready()
        self.brief_with(video={**BRIEF["video"], "delivery": {**BRIEF["video"]["delivery"], "format": "stories"}})
        out = self.cli("roteiro", "--action", "check", expect=1)
        self.assertIn("BRIEF.md entrega", out["error"])


class ReviewSeparatorTests(CliCase):
    def test_a_statement_with_a_unicode_line_separator_is_read_back(self):
        for sep in SEPARATORS:
            with self.subTest(sep=repr(sep)):
                self.setUp()
                self.cli("init-rules", "--format", "reels")
                self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
                self.fill_skeleton()
                self.write_brief()
                sha = self.cli("roteiro", "--action", "check")["review"]["sha256"]
                self.cli(
                    "roteiro",
                    "--action",
                    "review",
                    "--by",
                    "Bruno Moreira",
                    "--statement",
                    f"aprovo{sep}este",
                    "--expect",
                    sha,
                )
                doc = roteiro.parse(roteiro.load_text(self.project))
                self.assertTrue(roteiro_review.review_state(self.project, doc)["reviewed"])
                self.assertTrue(self.cli("roteiro", "--action", "check")["review"]["reviewed"])

    def test_review_without_special_characters_is_unchanged(self):
        self.reviewed()
        out = self.cli("roteiro", "--action", "check")
        self.assertTrue(out["review"]["reviewed"], REVIEW)


class LineEndingTests(CliCase):
    """`sync` e `review` regravam o arquivo da pessoa: fim de linha (CRLF) e BOM ficam como estavam."""

    def to_crlf(self, name, bom=False):
        path = self.project / name
        text = path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
        return path

    def prepared(self, bom):
        self.cli("init-rules", "--format", "reels")
        self.cli("roteiro", "--action", "new", "--genero", "reels", "--tema", "IA")
        self.fill_skeleton()
        self.write_brief()
        self.to_crlf("BRIEF.md")
        return self.to_crlf("ROTEIRO.md", bom=bom)

    def assert_form(self, path, bom):
        raw = path.read_bytes()
        self.assertEqual(bom, raw.startswith(b"\xef\xbb\xbf"), path.name)
        body = raw.removeprefix(b"\xef\xbb\xbf")
        self.assertIn(b"\r\n", body, path.name)
        self.assertNotRegex(body, rb"(?<!\r)\n", path.name)

    def test_review_keeps_crlf_and_bom_of_the_roteiro(self):
        for bom in (False, True):
            with self.subTest(bom=bom):
                self.setUp()
                path = self.prepared(bom)
                self.review()
                self.assertIn("status: revisado", path.read_bytes().decode("utf-8-sig"))
                self.assert_form(path, bom)

    def test_sync_keeps_crlf_and_bom_of_roteiro_and_brief(self):
        for bom in (False, True):
            with self.subTest(bom=bom):
                self.setUp()
                path = self.prepared(bom)
                self.review()
                brief_before = (self.project / "BRIEF.md").read_bytes()
                done = self.cli("roteiro", "--action", "sync")
                self.assertIn("BRIEF.md", done["written"])
                self.assertIn("ROTEIRO.md", done["written"])
                self.assertNotEqual(brief_before, (self.project / "BRIEF.md").read_bytes())
                self.assert_form(path, bom)
                self.assert_form(self.project / "BRIEF.md", False)
                self.assertTrue(self.cli("roteiro", "--action", "check")["review"]["reviewed"])

    def test_lf_files_stay_lf(self):
        self.reviewed()
        self.cli("roteiro", "--action", "sync")
        for name in ("ROTEIRO.md", "BRIEF.md"):
            self.assertNotIn(b"\r", (self.project / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()

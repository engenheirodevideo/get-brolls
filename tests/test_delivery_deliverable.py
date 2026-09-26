"""`delivery.deliverable`: o predicado de clipe entregável, com o evento de log escolhido por quem chama."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import delivery


def item(ident, shot, **output):
    out = {"path": f"clips/{ident}.mp4", **output}
    return {"id": ident, "shot": shot, "title": ident, "output": out, "approval": {"status": "approved"}}


def write_roteiro_brief(project, beats):
    brief = {"version": 1, "beats": beats}
    (project / "BRIEF.md").write_text("```json\n" + json.dumps(brief) + "\n```\n", encoding="utf-8")
    (project / "ROTEIRO.md").write_text('---\ntype: roteiro\ngenero: reels\ntema: "t"\n---\n', encoding="utf-8")


class DeliverableTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp(prefix="gb-deliverable-"))
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)

    def test_keeps_manifest_order_and_explains_each_skip(self):
        rejected = item("r", "c02")
        rejected["approval"] = {"status": "rejected"}
        items = [
            item("b", "c03"),
            {"id": "sem-arquivo", "shot": "c01", "output": {}},
            item("u", "c01", verified=False),
            rejected,
            item("a", "c01", verified=True),
        ]
        collected, skipped = delivery.deliverable(self.project, items)
        self.assertEqual(["b", "a"], [c["id"] for c in collected])
        self.assertEqual(["u", "r"], [s["id"] for s in skipped])
        self.assertIn("verify", skipped[0]["reason"])
        self.assertIn("rejeitado", skipped[1]["reason"])

    def test_retired_beat_is_dropped_and_listed(self):
        write_roteiro_brief(self.project, [{"id": "c01", "target": "x"}, {"id": "c02", "target": "y", "retired": True}])
        retired = []
        collected, _ = delivery.deliverable(self.project, [item("a", "c01"), item("b", "c02")], retired)
        self.assertEqual(["a"], [c["id"] for c in collected])
        self.assertEqual([{"id": "b", "shot": "c02", "reason": delivery.RETIRED_REASON}], retired)

    def test_log_event_name_is_chosen_by_the_caller(self):
        write_roteiro_brief(self.project, [{"id": "c02", "target": "y", "retired": True}])
        items = [item("u", "c01", verified=False), item("b", "c02")]
        for name in ("deliver_skipped", "export_skipped"):
            with self.subTest(name=name), mock.patch.object(delivery.logs, "event") as event:
                if name == "deliver_skipped":
                    delivery.deliverable(self.project, items)
                else:
                    delivery.deliverable(self.project, items, log_event=name)
                self.assertEqual([name, name], [call.args[2] for call in event.call_args_list])
                self.assertEqual(["unverified", "retired_beat"], [c.kwargs["reason"] for c in event.call_args_list])

    def test_plan_uses_the_same_predicate(self):
        items = [item("a", "c01"), item("u", "c01", verified=False)]
        groups, skipped = delivery._plan(self.project, items)
        self.assertEqual([["a"]], [[c["id"] for c in g["items"]] for g in groups])
        self.assertEqual(["u"], [s["id"] for s in skipped])


class LinkOrCopyWithoutSymlinkTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-link-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.src = self.root / "src.mp4"
        self.src.write_bytes(b"video")

    def test_default_still_falls_back_to_symlink(self):
        with (
            mock.patch.dict(os.environ, {"GB_DELIVERY_COPY": ""}),
            mock.patch.object(delivery.os, "link", side_effect=OSError("sem hardlink")),
            mock.patch.object(delivery.os, "symlink") as symlink,
        ):
            self.assertEqual("symlink", delivery.link_or_copy(self.src, self.root / "a.mp4"))
            symlink.assert_called_once()

    def test_without_symlink_the_fallback_is_a_copy(self):
        dest = self.root / "b.mp4"
        with (
            mock.patch.dict(os.environ, {"GB_DELIVERY_COPY": ""}),
            mock.patch.object(delivery.os, "link", side_effect=OSError("sem hardlink")),
            mock.patch.object(delivery.os, "symlink") as symlink,
        ):
            self.assertEqual("copy", delivery.link_or_copy(self.src, dest, allow_symlink=False))
            symlink.assert_not_called()
        self.assertFalse(dest.is_symlink())
        self.assertEqual(b"video", dest.read_bytes())

    def test_without_symlink_hardlink_still_comes_first(self):
        dest = self.root / "c.mp4"
        with mock.patch.dict(os.environ, {"GB_DELIVERY_COPY": ""}):
            self.assertEqual("hardlink", delivery.link_or_copy(self.src, dest, allow_symlink=False))
        self.assertTrue(self.src.samefile(dest))
        self.assertEqual(self.src.stat().st_mode, dest.stat().st_mode)


if __name__ == "__main__":
    unittest.main()

import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from getbrolls.ledger import Ledger
from getbrolls.models import candidate
from getbrolls.rules import load_rules


class StabilityTests(unittest.TestCase):
    def test_windows_lock_backend_locks_and_unlocks_one_byte(self):
        from getbrolls.runtime import _acquire_lock, _release_lock

        calls = []

        class WindowsLock:
            LK_NBLCK = 1
            LK_UNLCK = 2

            @staticmethod
            def locking(descriptor, mode, size):
                calls.append((descriptor, mode, size))

        with tempfile.TemporaryFile(mode="w+") as stream:
            _acquire_lock(stream, platform="nt", windows=WindowsLock)
            _release_lock(stream, platform="nt", windows=WindowsLock)
            stream.seek(0, os.SEEK_END)
            self.assertEqual(stream.tell(), 1)
        self.assertEqual([WindowsLock.LK_NBLCK, WindowsLock.LK_UNLCK], [c[1] for c in calls])
        self.assertEqual([1, 1], [c[2] for c in calls])

    def test_invalid_rules_have_actionable_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = load_rules(tmp)
            for change in (
                {"asset_types": [{}]},
                {"preferred_providers": {"literal": [{}], "illustrative": []}},
                {"version": True},
                {"blocked_domains": ["..example.com"]},
            ):
                rule = {**base, **change}
                Path(tmp, "RULES.md").write_text("```json\n" + json.dumps(rule) + "\n```", encoding="utf-8")
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_rules(tmp)

    def test_invalid_manifest_does_not_start_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "brolls")
            root.mkdir()
            (root / "manifest.json").write_text('{"items": [1]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest"):
                Ledger(tmp)

    def test_failed_snapshot_commit_recovers_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            c = candidate("local", "one", "One")
            ledger.add(c)
            import getbrolls.ledger as module

            real = module.os.replace

            def interrupted(src, dst):
                if Path(dst).parent.name == "candidates":
                    raise OSError("simulated disk failure")
                return real(src, dst)

            with patch.object(module.os, "replace", side_effect=interrupted):
                from argparse import Namespace
                from getbrolls.runtime import audited, OperationError

                with self.assertRaises(OperationError) as failure:
                    audited(
                        Namespace(command="resolve", project=tmp),
                        lambda _: ledger.save("resolve", c),
                    )
                self.assertTrue(failure.exception.payload["state_committed"])
                self.assertTrue(failure.exception.payload["recovery_pending"])
            recovered = Ledger(tmp)
            self.assertEqual(recovered.get(c["id"])["title"], "One")
            events = [
                json.loads(line) for line in (recovered.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(events), 1)
            Ledger(tmp)
            self.assertEqual(
                len((recovered.root / "events.jsonl").read_text(encoding="utf-8").splitlines()),
                1,
            )

    def test_log_redaction_and_single_json_warning(self):
        from argparse import Namespace
        from getbrolls.runtime import audited, OperationError, record_warning

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"PEXELS_API_KEY": "fixture-private-key"}),
        ):
            args = Namespace(command="preview", project=tmp)

            def fail(_):
                raise ValueError("fixture-private-key https://example.org/?key=secret")

            with self.assertRaises(OperationError) as failure:
                audited(args, fail)
            log = Path(tmp, "brolls/diagnostics.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("fixture-private-key", log)
            self.assertNotIn("?key=", log)
            self.assertEqual(json.loads(log)["status"], "error")

            def warn(_):
                record_warning("STATIC_FALLBACK", "Use estático")
                return {"done": True}

            result = audited(args, warn)
            self.assertEqual(result["warnings"][0]["code"], "STATIC_FALLBACK")
            real = Path.open

            def deny(path, *a, **kw):
                if path.name == "diagnostics.jsonl":
                    raise PermissionError("fixture")
                return real(path, *a, **kw)

            with patch.object(Path, "open", deny):
                result = audited(args, lambda _: {"done": True})
                self.assertEqual(result["warnings"][0]["code"], "LOG_UNAVAILABLE")

    def test_preview_scope_keeps_context_static(self):
        from getbrolls.previewing import prepare_preview
        from getbrolls.config import settings
        from getbrolls.models import set_segment

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp)
            c = candidate("local", "scope", "Scope")
            c.update(
                local_path="broll.mp4",
                local_sha256="sha",
                context_image_path="person.png",
                context_image_sha256="sha",
                full_preview_path="composition.mp4",
                full_preview_sha256="sha",
                full_preview_media={"duration_s": 2},
            )
            set_segment(c, 5, 7)
            cfg = settings()
            cfg["scope"] = "broll"
            with (
                patch("getbrolls.previewing.digest", return_value="sha"),
                patch(
                    "getbrolls.previewing.review_preview",
                    return_value={
                        "poster_path": "previews/poster.jpg",
                        "gif_path": "previews/broll.gif",
                    },
                ) as video,
                patch(
                    "getbrolls.previewing.image_preview",
                    return_value={"poster_path": "previews/person.jpg"},
                ) as still,
            ):
                prepare_preview(ledger, c, 5, 7, cfg)
                self.assertEqual(video.call_args.args[0], "broll.mp4")
                self.assertEqual(c["preview"]["context_path"], "previews/person.jpg")
                cfg["scope"] = "full"
                prepare_preview(ledger, c, 5, 7, cfg)
                self.assertEqual(video.call_args.args[0], "composition.mp4")
                self.assertEqual(video.call_args.args[3:5], (0, 2))
                del c["full_preview_path"]
                with self.assertRaisesRegex(ValueError, "full exige"):
                    prepare_preview(ledger, c, 5, 7, cfg)


if __name__ == "__main__":
    unittest.main()

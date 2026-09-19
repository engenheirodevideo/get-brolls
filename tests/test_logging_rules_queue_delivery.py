"""Structured logging call sites added to rules.py, queue.py, acquisition.py and
delivery.py: one event per documented decision, with the fields the audit asked for,
and never a URL, a full domain list, a title/creator string, or other free text.
"""

import hashlib
import json
import logging
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# A pasta pessoal da skill vai para um temporário: nenhum teste toca ~/.getbrolls.
import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import acquisition, delivery, queue
from getbrolls.ledger import Ledger
from getbrolls.models import approve, candidate, id_stem, now, set_segment
from getbrolls.rules import allowed, load_rules, sync_formats

T0 = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
REEL = "https://www.instagram.com/reel/ABC123xyz/"

SECRET_TITLE = "Roteiro Secreto Do Episodio"
SECRET_CREATOR = "Fulano De Tal Autor"
SECRET_URL_TAIL = "?token=SUPER-SECRET-TOKEN-9Q7"


def rng(value=0.0):
    import random

    generator = random.Random(1)
    generator.uniform = lambda a, b: a + value * (b - a)
    return generator


def _joined(cm):
    return "\n".join(cm.output)


class RulesLayersLoggingTests(unittest.TestCase):
    """rules.py: `rules_layers` logs once per load; `_strip_never_inherited` per key."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir(parents=True)
        for key in ("GB_HOME", "GB_RULES_FILE"):
            old = os.environ.get(key)
            self.addCleanup(
                lambda k=key, v=old: os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
            )
            os.environ.pop(key, None)
        os.environ["GB_HOME"] = str(self.home)

    def _write_block(self, path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("```json\n" + json.dumps(data, ensure_ascii=False) + "\n```\n", encoding="utf-8")
        return path

    def test_rules_layers_logs_once_at_debug_with_layer_booleans(self):
        with self.assertLogs("getbrolls.rules", "DEBUG") as cm:
            load_rules(self.project)
        joined = _joined(cm)
        self.assertIn("event=rules_layers", joined)
        self.assertIn("global=False", joined)
        self.assertIn("env_file=False", joined)
        self.assertIn("project=False", joined)
        self.assertIn("template_base=True", joined)

    def test_rules_layers_reflects_a_real_project_file(self):
        base = load_rules(self.project)
        self._write_block(self.project / "RULES.md", dict(base, video_format="horizontal"))
        with self.assertLogs("getbrolls.rules", "DEBUG") as cm:
            load_rules(self.project)
        joined = _joined(cm)
        self.assertIn("event=rules_layers", joined)
        self.assertIn("project=True", joined)
        self.assertIn("template_base=False", joined)

    def test_strip_never_inherited_logs_info_with_key_and_layer(self):
        self._write_block(
            self.home / "RULES.md",
            {
                "copyright": {
                    "mode": "user_declaration",
                    "responsible_person": "Alguem",
                    "declaration": "Assumo tudo.",
                }
            },
        )
        with self.assertLogs("getbrolls.rules", "INFO") as cm:
            load_rules(self.project)
        joined = _joined(cm)
        self.assertIn("event=rules_key_not_inherited", joined)
        self.assertIn("key=copyright", joined)
        self.assertIn("layer=global", joined)


class RulesAllowedLoggingTests(unittest.TestCase):
    """rules.py: `allowed()` logs the blocking branch at INFO and never changes its bool."""

    def _rules(self):
        return {"asset_types": ["video"], "blocked_domains": ["blocked.example"]}

    def _candidate(self, asset_type="video", url="https://blocked.example/watch?v=abc" + SECRET_URL_TAIL):
        c = candidate("youtube", "abc123", SECRET_TITLE, source_url=url)
        c["creator"]["name"] = SECRET_CREATOR
        c["asset_type"] = asset_type
        return c

    def test_asset_type_block_logs_rule_asset_type(self):
        c = self._candidate(asset_type="image", url="https://ok.example/x")
        with self.assertLogs("getbrolls.rules", "INFO") as cm:
            result = allowed(c, self._rules())
        self.assertFalse(result)
        joined = _joined(cm)
        self.assertIn("event=rule_block", joined)
        self.assertIn("rule=asset_type", joined)
        self.assertIn("candidate=" + c["id"], joined)

    def test_blocked_domain_logs_rule_blocked_domain_and_host_only(self):
        c = self._candidate()
        with self.assertLogs("getbrolls.rules", "INFO") as cm:
            result = allowed(c, self._rules())
        self.assertFalse(result)
        joined = _joined(cm)
        self.assertIn("event=rule_block", joined)
        self.assertIn("rule=blocked_domain", joined)
        self.assertIn("host=blocked.example", joined)
        # Host only: the full URL (query string, token) never reaches the log.
        self.assertNotIn(SECRET_URL_TAIL, joined)
        self.assertNotIn(SECRET_TITLE, joined)
        self.assertNotIn(SECRET_CREATOR, joined)

    def test_allowed_item_does_not_log_a_block_event(self):
        c = self._candidate(url="https://ok.example/x")
        with self.assertNoLogs("getbrolls.rules", "INFO"):
            result = allowed(c, self._rules())
        self.assertTrue(result)

    def test_bool_is_unchanged_across_a_scenario_table_with_logging_enabled_and_disabled(self):
        rules = self._rules()
        scenarios = [
            (self._candidate(asset_type="video", url="https://ok.example/x"), True),
            (self._candidate(asset_type="image", url="https://ok.example/x"), False),
            (self._candidate(asset_type="video"), False),  # blocked domain
        ]
        for c, expected in scenarios:
            self.assertEqual(expected, allowed(c, rules), c["id"])
            logging.disable(logging.CRITICAL)
            try:
                self.assertEqual(expected, allowed(c, rules), c["id"])
            finally:
                logging.disable(logging.NOTSET)


class RulesFormatInvalidationLoggingTests(unittest.TestCase):
    """rules.py: `sync_formats` logs one `format_invalidation` per changed target."""

    def _project_with_approved_item(self, folder):
        ledger = Ledger(folder)
        rules = load_rules(folder)
        c = candidate("local", "x", SECRET_TITLE, source_url="https://example.org/a")
        set_segment(c, 0, 1)
        ledger.add(c)
        approve(c, "Humano")
        ledger.save("fixture")
        return ledger, rules, c

    def test_approved_item_logs_confirmed_true(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger, rules, c = self._project_with_approved_item(folder)
            rules["video_format"] = "reels"
            with self.assertLogs("getbrolls.rules", "INFO") as cm:
                sync_formats(ledger, rules, confirm=True)
            joined = _joined(cm)
            self.assertIn("event=format_invalidation", joined)
            self.assertIn("candidate=" + c["id"], joined)
            self.assertIn("from=native", joined)
            self.assertIn("to=reels", joined)
            self.assertIn("confirmed=True", joined)
            self.assertNotIn(SECRET_TITLE, joined)

    def test_never_approved_item_logs_confirmed_false(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(folder)
            rules = load_rules(folder)
            c = candidate("local", "y", SECRET_TITLE, source_url="https://example.org/b")
            set_segment(c, 0, 1)
            ledger.add(c)
            ledger.save("fixture")
            rules["video_format"] = "reels"
            with self.assertLogs("getbrolls.rules", "INFO") as cm:
                sync_formats(ledger, rules, confirm=False)
            joined = _joined(cm)
            self.assertIn("event=format_invalidation", joined)
            self.assertIn("confirmed=False", joined)


class QueueClaimLoggingTests(unittest.TestCase):
    """queue.py: `claim_next` logs `queue_claim` when it hands out an item, `queue_wait`
    with the holding reason otherwise."""

    def setUp(self):
        patcher = patch.dict(
            os.environ,
            {k: v for k, v in os.environ.items() if not k.startswith(("GB_PACE_", "GB_MAX_PER_"))},
            clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_claim_logs_provider_and_item(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        with self.assertLogs("getbrolls.queue", "INFO") as cm:
            result = queue.claim_next(data, at=T0, rng=rng(0.0))
        joined = _joined(cm)
        self.assertIn("event=queue_claim", joined)
        self.assertIn("provider=instagram", joined)
        self.assertIn("item=" + result["item"]["id"], joined)

    def test_wait_logs_hold_and_next_allowed_in_s(self):
        # rng=1.0 arms the provider's pace hold at its longest interval (max_s); marking
        # the first item done frees the "active" slot but the pace hold on the provider
        # itself still stands, so a second pending item at the same instant is blocked.
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        first = queue.claim_next(data, at=T0, rng=rng(1.0))
        queue.mark(data, first["item"]["id"], "done", at=T0)
        queue.add(data, "instagram", [REEL.replace("ABC123xyz", "DEF456uvw")], at=T0)
        with self.assertLogs("getbrolls.queue", "INFO") as cm:
            waited = queue.claim_next(data, provider="instagram", at=T0, rng=rng(0.0))
        self.assertIsNone(waited["item"])
        self.assertGreater(waited["wait_seconds"], 0)
        joined = _joined(cm)
        self.assertIn("event=queue_wait", joined)
        self.assertIn("provider=instagram", joined)
        self.assertIn("hold=pace", joined)
        self.assertIn(f"next_allowed_in_s={waited['wait_seconds']}", joined)


class QueueMarkAndCooldownLoggingTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(
            os.environ,
            {k: v for k, v in os.environ.items() if not k.startswith(("GB_PACE_", "GB_MAX_PER_"))},
            clear=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mark_logs_transition(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        claimed = queue.claim_next(data, at=T0, rng=rng(0.0))
        item_id = claimed["item"]["id"]
        with self.assertLogs("getbrolls.queue", "INFO") as cm:
            queue.mark(data, item_id, "done", at=T0)
        joined = _joined(cm)
        self.assertIn("event=queue_mark", joined)
        self.assertIn("item=" + item_id, joined)
        self.assertIn("from=active", joined)
        self.assertIn("to=done", joined)

    def test_cooldown_reason_opens_then_extends_then_closes(self):
        data = queue.empty_state()
        queue.add(data, "instagram", [REEL], at=T0)
        claimed = queue.claim_next(data, at=T0, rng=rng(0.0))
        item_id = claimed["item"]["id"]
        free_text_reason = "HTTP 429 Too Many Requests: sessao bloqueada, tente mais tarde"
        with self.assertLogs("getbrolls.queue", "INFO") as cm:
            queue.mark(data, item_id, "failed", reason=free_text_reason, at=T0)
        joined = _joined(cm)
        self.assertIn("event=cooldown", joined)
        self.assertIn("provider=instagram", joined)
        self.assertIn("action=open", joined)
        self.assertIn("reason_class=http_429", joined)
        # The category reaches the log; the free-text reason never does.
        self.assertNotIn(free_text_reason, joined)
        self.assertNotIn("sessao bloqueada", joined)

        queue.add(data, "instagram", [REEL.replace("ABC123xyz", "DEF456uvw")], at=T0)
        claimed2 = queue.claim_next(data, at=T0 + timedelta(hours=1), rng=rng(0.0))
        item_id2 = claimed2["item"]["id"]
        with self.assertLogs("getbrolls.queue", "INFO") as cm2:
            queue.mark(data, item_id2, "failed", reason="429 again", at=T0 + timedelta(hours=1))
        joined2 = _joined(cm2)
        self.assertIn("action=extend", joined2)

        queue.add(data, "instagram", [REEL.replace("ABC123xyz", "GHI789rst")], at=T0)
        claimed3 = queue.claim_next(data, at=T0 + timedelta(hours=3), rng=rng(0.0))
        item_id3 = claimed3["item"]["id"]
        with self.assertLogs("getbrolls.queue", "INFO") as cm3:
            queue.mark(data, item_id3, "done", at=T0 + timedelta(hours=3))
        joined3 = _joined(cm3)
        self.assertIn("action=close", joined3)


class QueuePacingOverrideLoggingTests(unittest.TestCase):
    def test_env_override_logs_at_debug(self):
        for key in ("GB_PACE_MIN_S", "GB_PACE_MAX_S", "GB_MAX_PER_HOUR", "GB_MAX_PER_DAY"):
            os.environ.pop(key, None)
        with patch.dict(os.environ, {"GB_PACE_MIN_S": "5", "GB_PACE_MAX_S": "10"}):
            with self.assertLogs("getbrolls.queue", "DEBUG") as cm:
                queue.pacing("instagram")
        joined = _joined(cm)
        self.assertIn("event=pacing_override", joined)
        self.assertIn("provider=instagram", joined)
        self.assertIn("min_s=env", joined)
        self.assertIn("max_s=env", joined)

    def test_no_override_logs_nothing(self):
        for key in ("GB_PACE_MIN_S", "GB_PACE_MAX_S", "GB_MAX_PER_HOUR", "GB_MAX_PER_DAY"):
            os.environ.pop(key, None)
        with self.assertNoLogs("getbrolls.queue", "DEBUG"):
            queue.pacing("instagram")


class AcquisitionSourceCacheLoggingTests(unittest.TestCase):
    """acquisition.py: `_reuse_from_index` logs hit/miss/stale cache decisions."""

    def test_miss_when_index_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            with self.assertLogs("getbrolls.acquisition", "INFO") as cm:
                result = acquisition._reuse_from_index(cache, "cand-1", 0, 3)
        self.assertIsNone(result)
        joined = _joined(cm)
        self.assertIn("event=source_cache", joined)
        self.assertIn("candidate=cand-1", joined)
        self.assertIn("result=miss", joined)

    def test_hit_when_an_entry_covers_the_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            media = cache / "clip.mp4"
            media.write_bytes(b"conteudo")
            sha = hashlib.sha256(b"conteudo").hexdigest()
            index = {"cand-1": [{"path": str(media), "sha": sha, "start": 0, "duration": 10}]}
            (cache / acquisition.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
            with self.assertLogs("getbrolls.acquisition", "INFO") as cm:
                result = acquisition._reuse_from_index(cache, "cand-1", 0, 5)
        self.assertIsNotNone(result)
        joined = _joined(cm)
        self.assertIn("result=hit", joined)

    def test_stale_when_digest_no_longer_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            media = cache / "clip.mp4"
            media.write_bytes(b"conteudo-novo")
            index = {"cand-1": [{"path": str(media), "sha": "sha-antigo-errado", "start": 0, "duration": 10}]}
            (cache / acquisition.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
            with self.assertLogs("getbrolls.acquisition", "INFO") as cm:
                result = acquisition._reuse_from_index(cache, "cand-1", 0, 5)
        self.assertIsNone(result)
        joined = _joined(cm)
        self.assertIn("result=stale", joined)
        self.assertIn("reason=sha_mismatch", joined)


class AcquisitionMaterializationLoggingTests(unittest.TestCase):
    """acquisition.py: `cache_direct_media` logs `source_materialized` (remote, then a
    reused local hit) and the digest-mismatch refusal at WARNING."""

    def test_remote_then_local_materialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            ledger = Ledger(project)
            c = candidate("pexels", "vid1", SECRET_TITLE, source_url="https://pexels.com/vid1")
            c["media_url"] = "https://cdn.pexels.com/vid1.mp4" + SECRET_URL_TAIL
            content = b"conteudo do video de teste"

            def fake_download(url, target):
                Path(target).write_bytes(content)

            with (
                patch("getbrolls.http.download", side_effect=fake_download),
                patch(
                    "getbrolls.acquisition.probe",
                    return_value={"duration_s": 5.0, "width": 100, "height": 100, "fps": 30},
                ),
                self.assertLogs("getbrolls.acquisition", "INFO") as cm,
            ):
                acquisition.cache_direct_media(ledger, c, refresh=False)
            joined = _joined(cm)
            self.assertIn("event=source_materialized", joined)
            self.assertIn("kind=remote", joined)
            self.assertIn(f"bytes={len(content)}", joined)
            self.assertNotIn(SECRET_URL_TAIL, joined)
            self.assertNotIn(SECRET_TITLE, joined)

            with self.assertLogs("getbrolls.acquisition", "INFO") as cm2:
                acquisition.cache_direct_media(ledger, c, refresh=False)
            joined2 = _joined(cm2)
            self.assertIn("event=source_materialized", joined2)
            self.assertIn("kind=local", joined2)

    def test_digest_mismatch_logs_warning_and_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            ledger = Ledger(project)
            c = candidate("pexels", "vid2", SECRET_TITLE, source_url="https://pexels.com/vid2")
            c["media_url"] = "https://cdn.pexels.com/vid2.mp4"
            cache = ledger.root.parent / ".getbrolls-sources"
            cache.mkdir(parents=True, exist_ok=True)
            content = b"conteudo esperado"
            sha = hashlib.sha256(content).hexdigest()
            stem = id_stem(c["id"])
            final = cache / f"{stem}-{sha}.mp4"
            final.write_bytes(b"bytes completamente diferentes")

            def fake_download(url, target):
                Path(target).write_bytes(content)

            with (
                patch("getbrolls.http.download", side_effect=fake_download),
                patch(
                    "getbrolls.acquisition.probe",
                    return_value={"duration_s": 5.0, "width": 100, "height": 100, "fps": 30},
                ),
            ):
                with self.assertLogs("getbrolls.acquisition", "WARNING") as cm, self.assertRaises(ValueError):
                    acquisition.cache_direct_media(ledger, c, refresh=False)
            joined = _joined(cm)
            self.assertIn("event=source_cache", joined)
            self.assertIn("result=stale", joined)
            self.assertIn("reason=digest_mismatch", joined)


def _fetched(source_id, title, shot=None, clip="clips/x.mp4", sheet=None, creator=None):
    c = candidate("local", source_id, title, source_url="https://example.org/" + source_id)
    set_segment(c, 0, 2)
    c["creator"]["name"] = creator or "Autora Exemplo"
    if sheet:
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
    c["rights"]["evidence"] = ["Condicoes conferidas na pagina da fonte"]
    c["output"] = {"path": clip, "sha256": "a" * 64, "verified": True}
    c["state"] = "verified"
    if shot:
        c["shot"] = shot
        c["id"] += ":shot:" + shot
    return c


def _project(tmp, items):
    ledger = Ledger(tmp)
    stored = [ledger.add(c) for c in items]
    ledger.save_many("fixture", stored)
    for c in stored:
        rel = c["output"].get("path")
        if rel:
            path = ledger.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"conteudo de " + rel.encode())
    return ledger


class DeliveryItemLoggingTests(unittest.TestCase):
    def test_delivered_item_logs_beat_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            _project(tmp, [_fetched("a", SECRET_TITLE, shot="abertura", creator=SECRET_CREATOR)])
            with self.assertLogs("getbrolls.delivery", "INFO") as cm:
                delivery.build_delivery(tmp)
            joined = _joined(cm)
            self.assertIn("event=deliver_item", joined)
            self.assertIn("beat=abertura", joined)
            self.assertIn("mode=hardlink", joined)
            self.assertIn("event=sweep", joined)
            self.assertNotIn(SECRET_TITLE, joined)
            self.assertNotIn(SECRET_CREATOR, joined)

    def test_env_copy_logs_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            _project(tmp, [_fetched("a", "T", shot="abertura")])
            with patch.dict(os.environ, {"GB_DELIVERY_COPY": "1"}):
                with self.assertLogs("getbrolls.delivery", "INFO") as cm:
                    delivery.build_delivery(tmp)
            joined = _joined(cm)
            self.assertIn("mode=copy", joined)
            self.assertIn("reason=env_copy", joined)


class DeliverySkippedLoggingTests(unittest.TestCase):
    def test_unverified_item_logs_skipped_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = _fetched("a", "T", shot="abertura")
            c["output"]["verified"] = False
            _project(tmp, [c])
            with self.assertLogs("getbrolls.delivery", "INFO") as cm:
                delivery.build_delivery(tmp)
            joined = _joined(cm)
            self.assertIn("event=deliver_skipped", joined)
            self.assertIn("candidate=" + c["id"], joined)
            self.assertIn("reason=unverified", joined)

    def test_rejected_item_logs_skipped_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = _fetched("a", "T", shot="abertura")
            c["approval"]["status"] = "rejected"
            _project(tmp, [c])
            with self.assertLogs("getbrolls.delivery", "INFO") as cm:
                delivery.build_delivery(tmp)
            joined = _joined(cm)
            self.assertIn("event=deliver_skipped", joined)
            self.assertIn("reason=rejected", joined)


class DeliveryConflictLoggingTests(unittest.TestCase):
    def test_foreign_file_conflict_logs_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            _project(tmp, [_fetched("a", "T", shot="abertura")])
            beat_dir = Path(tmp) / delivery.DELIVERY_DIR / "01-abertura-t"
            beat_dir.mkdir(parents=True)
            (beat_dir / "01-abertura-t.mp4").write_bytes(b"edicao da pessoa, bytes diferentes")
            with self.assertLogs("getbrolls.delivery", "WARNING") as cm:
                with self.assertRaises(ValueError):
                    delivery.build_delivery(tmp)
            joined = _joined(cm)
            self.assertIn("event=deliver_conflict", joined)
            self.assertIn("kind=foreign_file", joined)

    def test_io_error_conflict_logs_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            _project(tmp, [_fetched("a", "T", shot="abertura")])
            broken = delivery._CompareError("[Errno 13] Permission denied")
            with patch.object(delivery, "_same_file", side_effect=broken):
                beat_dir = Path(tmp) / delivery.DELIVERY_DIR / "01-abertura-t"
                beat_dir.mkdir(parents=True)
                (beat_dir / "01-abertura-t.mp4").write_bytes(b"qualquer coisa")
                with self.assertLogs("getbrolls.delivery", "WARNING") as cm:
                    with self.assertRaises(ValueError):
                        delivery.build_delivery(tmp)
            joined = _joined(cm)
            self.assertIn("event=deliver_conflict", joined)
            self.assertIn("kind=io_error", joined)


class DeliveryRefusalLoggingTests(unittest.TestCase):
    def test_symlinked_entrega_logs_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            _project(tmp, [_fetched("a", "T", shot="abertura")])
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (Path(tmp) / delivery.DELIVERY_DIR).symlink_to(outside)
            with self.assertLogs("getbrolls.delivery", "WARNING") as cm:
                with self.assertRaises(ValueError):
                    delivery.build_delivery(tmp)
            joined = _joined(cm)
            self.assertIn("event=deliver_refusal", joined)
            self.assertIn("reason=entrega_is_symlink", joined)


if __name__ == "__main__":
    unittest.main()

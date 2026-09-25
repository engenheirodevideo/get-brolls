"""A I1: fontes embutidas voltam ao filtro de URL do 2.5.0; o filtro amplo de query
secreta vale só para URL que veio de plugin. B-06: `source_id` de plugin só com
caracteres seguros, e o id de candidato de plugin vai citado em comando sugerido."""

import unittest

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)

from getbrolls import acquisition, http
from getbrolls.http import ProviderError
from getbrolls.models import candidate
from getbrolls.sdk import guard

BUILTIN_SHAPES = (
    "https://v16-webapp.tiktok.com/v/abc.mp4?x-signature=AbC123&x-expires=9",
    "https://scontent.cdninstagram.com/v/t51/p.jpg?ig_cache_key=MzA5ODc%3D&stp=dst",
    "https://example.org/lista?page_token=2",
    "https://example.org/lista?sort_key=date",
    "https://example.org/doc?doc_key=1&share_token=abc",
    "https://example.org/p?policy=privacy&pwd=1",
)


class PublicUrlScopeTests(unittest.TestCase):
    def test_builtin_paths_keep_the_250_filter(self):
        for url in BUILTIN_SHAPES:
            with self.subTest(url=url):
                self.assertEqual(url, http.public_url(url))

    def test_strict_filter_still_drops_them_for_plugins(self):
        for url in BUILTIN_SHAPES:
            with self.subTest(url=url):
                self.assertIsNone(http.public_url(url, strict=True))
                self.assertIsNone(guard.public_url(url))

    def test_exact_secret_names_are_dropped_everywhere(self):
        for url in (
            "https://x.example/v?token=1",
            "https://x.example/v?X-Amz-Signature=1",
            "https://x.example/v?sig=a",
        ):
            with self.subTest(url=url):
                self.assertIsNone(http.public_url(url))
                self.assertIsNone(http.public_url(url, strict=True))


def raw(source_id):
    item = candidate("demo", "1", "Demo", "https://demo.example/v/1")
    item["source_id"] = source_id
    item["id"] = f"demo:{source_id}"
    return item


class SourceIdTests(unittest.TestCase):
    def test_unsafe_source_ids_are_refused_and_logged(self):
        for bad in ("a1$(touch${IFS}/x)", "com espaço", "a;b", "x" * 129, "", "a/b", "ação"):
            with self.subTest(bad=bad):
                with (
                    self.assertLogs("getbrolls.sdk", level="WARNING") as cm,
                    self.assertRaises(ProviderError) as caught,
                ):
                    guard.plugin_candidate(raw(bad), "demo", "demo")
                self.assertIn("source_id inválido", str(caught.exception))
                self.assertIn("event=plugin_candidate_refused", "\n".join(cm.output))

    def test_safe_source_ids_pass(self):
        for good in ("abc-1.2:x_y", "0" * 128, "a1b2c3d4e5f6a7b8"):
            with self.subTest(good=good):
                self.assertEqual(f"demo:{good}", guard.plugin_candidate(raw(good), "demo", "demo")["id"])


class SuggestedCommandQuotingTests(unittest.TestCase):
    def test_plugin_candidate_id_is_quoted_when_unsafe(self):
        legacy = {"id": "inj:a1$(touch x)", "provider": "inj", "media": {"kind": "video"}}
        message = acquisition.fetch_stage_message(legacy)
        self.assertIn("preview --candidate 'inj:a1$(touch x)' --start", message)

    def test_builtin_and_safe_ids_are_unchanged(self):
        nasa = {"id": "nasa:Ultimate Saturn V:shot:espaco", "provider": "nasa"}
        self.assertEqual(nasa["id"], acquisition.candidate_arg(nasa))
        safe = {"id": "demo:abc-1", "provider": "demo", "media": {"kind": "image"}}
        self.assertIn("preview --candidate demo:abc-1 --reference-only", acquisition.fetch_stage_message(safe))


if __name__ == "__main__":
    unittest.main()

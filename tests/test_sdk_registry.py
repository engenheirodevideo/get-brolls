"""Registro tipado: nomes, donos, colisões e rollback por dono."""

import unittest
from unittest.mock import patch

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT  # noqa: F401  (efeito de import: insere scripts/ em sys.path)  # pylint: disable=unused-import

from getbrolls import presets, providers
from getbrolls.sdk.contracts import CORE, ProviderCapabilities
from getbrolls.sdk.registry import Registry, RegistryError, get_registry, reset_registry


class FakeProvider:
    def __init__(self, name, **caps):
        self.name = name
        self.capabilities = ProviderCapabilities(**caps)

    def search(self, query, limit, media):
        return []

    def resolve(self, url):
        return None

    def refresh(self, item):
        return item


class RegistryTests(unittest.TestCase):
    def test_providers_keep_registration_order_and_owner(self):
        reg = Registry()
        reg.add_provider(FakeProvider("youtube", search=True))
        reg.add_provider(FakeProvider("acme_drive"), owner="acme_drive")
        self.assertEqual(("youtube", "acme_drive"), reg.provider_names())
        self.assertEqual(CORE, reg.owner("provider", "youtube"))
        self.assertEqual("acme_drive", reg.owner("provider", "acme_drive"))

    def test_second_owner_of_a_name_is_refused(self):
        reg = Registry()
        reg.add_provider(FakeProvider("youtube"))
        with self.assertRaises(RegistryError) as caught:
            reg.add_provider(FakeProvider("youtube"), owner="intruso")
        self.assertIn("core", str(caught.exception))
        self.assertEqual(CORE, reg.owner("provider", "youtube"))

    def test_host_collision_is_refused_without_partial_state(self):
        reg = Registry()
        reg.add_provider(FakeProvider("youtube", url_hosts=("youtu.be",)))
        with self.assertRaises(RegistryError):
            reg.add_provider(FakeProvider("outro", url_hosts=("youtu.be",)), owner="outro")
        self.assertIsNone(reg.provider("outro"))
        found = reg.provider_for_host("youtu.be")
        assert found is not None
        self.assertEqual("youtube", found.name)

    def test_invalid_names_and_capabilities_are_refused(self):
        reg = Registry()
        for bad in ("YouTube", "x", "a-b", "1abc"):
            with self.assertRaises(RegistryError):
                reg.add_provider(FakeProvider(bad))
        broken = FakeProvider("sem_caps")
        broken.capabilities = {"search": True}  # type: ignore[assignment]  # propositalmente fora do contrato
        with self.assertRaises(RegistryError):
            reg.add_provider(broken)

    def test_presets_must_point_to_the_source_page(self):
        reg = Registry()
        reg.add_preset(
            "acme",
            "https://acme.example/licenca",
            "Condições — verifique a página da fonte: https://acme.example/licenca",
        )
        preset = reg.preset("acme")
        assert preset is not None
        self.assertEqual("https://acme.example/licenca", preset.url)
        with self.assertRaises(RegistryError):
            reg.add_preset("ruim", "https://x.example", "Pode usar à vontade.")

    def test_remove_owner_rolls_back_everything_of_that_owner(self):
        reg = Registry()
        reg.add_provider(FakeProvider("youtube"))
        reg.add_provider(FakeProvider("acme_drive", url_hosts=("drive.acme.example",)), owner="acme_drive")
        reg.add_preset(
            "acme_drive",
            "https://acme.example",
            "x — verifique a página da fonte: https://acme.example",
            owner="acme_drive",
        )
        reg.remove_owner("acme_drive")
        self.assertEqual(("youtube",), reg.provider_names())
        self.assertIsNone(reg.provider_for_host("drive.acme.example"))
        self.assertEqual((), reg.preset_names())


class BuiltinProvidersTests(unittest.TestCase):
    def setUp(self):

        reset_registry()
        self.addCleanup(reset_registry)

    def test_builtins_are_registered_in_canonical_order_owned_by_core(self):

        reg = get_registry()
        expected = ("youtube", "instagram", "tiktok", "pexels", "pixabay", "commons", "nasa", "local")
        self.assertEqual(expected, reg.provider_names())
        self.assertTrue(all(reg.owner("provider", n) == CORE for n in expected))
        self.assertEqual("youtube", reg.provider_for_host("youtu.be").name)  # type: ignore[union-attr]

    def test_capabilities_output_is_unchanged(self):

        caps = providers.capabilities()
        self.assertEqual(
            {
                "search": True,
                "resolve_url": True,
                "account_library": False,
                "embed": False,
                "seek": "unsupported",
                "download": True,
                "transport": "yt-dlp",
                "configured": True,
                "env_key": None,
            },
            caps["youtube"],
        )
        self.assertEqual("browser-cdn-pairs / yt-dlp", caps["instagram"]["transport"])
        self.assertEqual("local", caps["local"]["seek"])
        self.assertFalse(caps["commons"]["resolve_url"])
        self.assertEqual("PEXELS_API_KEY", caps["pexels"]["env_key"])
        self.assertNotIn("plugin", caps["pexels"])

    def test_search_still_honours_patched_module_functions(self):

        with patch.object(providers, "_youtube", return_value=[]) as fake:
            self.assertEqual([], providers.search("youtube", "earth", 2))
        fake.assert_called_once_with("earth", 2)


class BuiltinPresetsTests(unittest.TestCase):
    def setUp(self):

        reset_registry()
        self.addCleanup(reset_registry)

    def test_every_builtin_preset_is_in_the_registry(self):

        reg = get_registry()
        self.assertEqual(sorted(presets.PERMIT_PRESETS), sorted(reg.preset_names()))
        self.assertEqual(sorted(presets.PERMIT_PRESETS), presets.names())
        self.assertEqual(presets.PERMIT_PRESETS["nasa"]["text"], presets.get("nasa")["text"])

    def test_unknown_preset_is_a_clear_error(self):

        with self.assertRaises(ValueError) as caught:
            presets.get("inexistente")
        self.assertIn("inexistente", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

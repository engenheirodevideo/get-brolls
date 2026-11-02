"""Tipo `environments`: só a origem oficial o contribui, e a saída da ponte é conferida (`sdk.bridges`)."""

import ast
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from test_sdk_manifest import BASE, write_plugin

from getbrolls.sdk import bridges, contracts, loader
from getbrolls.sdk.manifest import ManifestError, read_manifest
from getbrolls.sdk.registry import Registry

# Ponte de teste: só existe aqui. Ecoa o que recebe para os testes conferirem as cópias.
BRIDGE = {
    **BASE,
    "id": "ponte_teste",
    "name": "Ponte de teste",
    "contributes": {"environments": ["ponte_teste"]},
    "permissions": {"network": [], "env": [], "paths": [], "project_write": []},
}
BRIDGE_CODE = """
def package(handoff):
    handoff["mexido"] = True
    return {"files": {"ponte_teste/LEIA-ME.md": "# Pedido\\n" + handoff["id"]}, "notes": ["linha\\nnova"]}


def readback(view):
    state = "project" if view["entries"] else "waiting"
    return {"state": state, "artifacts": [], "renders": [], "scenes": {"total": 0, "seen": None, "method": "count"},
            "problems": [], "env_detail": {"vistos": len(view["entries"])}}


def register(api):
    api.environment("ponte_teste", package, readback, "Ponte de teste")
"""
# Devolve um dict de subclasse cujo `items()` levanta o que o teste escolher (aqui `RAISE_HERE`).
HOSTILE_CODE = """
class Hostile(dict):
    def items(self):
        RAISE_HERE
"""
READBACK = {
    "state": "waiting",
    "artifacts": [],
    "renders": [],
    "scenes": {"total": 0, "seen": None, "method": "count"},
    "problems": [],
    "env_detail": {},
}


def bridge_registry(code=BRIDGE_CODE):
    """Registro com a ponte de teste carregada pela porta oficial (`official=True`) e marcada habilitada."""
    tmp = tempfile.mkdtemp(prefix="gb-bridge-")
    folder = write_plugin(tmp, BRIDGE)
    (folder / "plugin.py").write_text(code, encoding="utf-8")
    registry = Registry()
    loader.register_plugin(folder, read_manifest(folder, official=True), registry)
    registry.plugins["ponte_teste"] = {"id": "ponte_teste", "status": "enabled"}
    return registry, tmp


class EnvironmentKindTests(unittest.TestCase):
    def test_a_third_party_manifest_cannot_contribute_environments(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ManifestError) as caught:
            read_manifest(write_plugin(tmp, BRIDGE))
        self.assertIn("contributes.environments é reservado a plugin oficial nesta versão", str(caught.exception))

    def test_only_the_official_door_accepts_an_official_id(self):
        official = {**BRIDGE, "id": "hyperframes", "contributes": {"environments": ["hyperframes"]}}
        with tempfile.TemporaryDirectory() as tmp:
            folder = write_plugin(tmp, official)
            self.assertEqual("hyperframes", read_manifest(folder, official=True)["id"])
            with self.assertRaises(ManifestError) as caught:
                read_manifest(folder)
        self.assertIn("reservado para plugin oficial", str(caught.exception))

    def test_only_the_loader_opens_the_official_door(self):
        root = Path(loader.__file__).resolve().parents[1]
        callers = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", None)) == "read_manifest"
            and any(kw.arg == "official" and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
                    for kw in node.keywords)
        )  # fmt: skip
        self.assertEqual(["sdk/official.py"], callers)

    def test_shipped_environments_are_official(self):
        self.assertLessEqual(set(contracts.SHIPPED_ENVIRONMENTS), set(contracts.OFFICIAL_ENVIRONMENTS))

    def test_a_manifest_with_bom_or_nan_says_why(self):
        for raw, phrase in ((b"\xef\xbb\xbf" + json.dumps(BASE).encode(), "sem BOM"), (b'{"id": NaN}', "NaN")):
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as tmp:
                folder = write_plugin(tmp, BASE)
                (folder / "getbrolls-plugin.json").write_bytes(raw)
                with self.assertRaises(ManifestError) as caught:
                    read_manifest(folder)
                self.assertIn(phrase, str(caught.exception))


class BridgeRunTests(unittest.TestCase):
    def setUp(self):
        self.registry, tmp = bridge_registry()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

    def test_package_gets_a_copy_and_its_output_is_checked(self):
        handoff = {"id": "002"}
        result = bridges.run_package(self.registry, "ponte_teste", handoff)
        self.assertEqual({"ponte_teste/LEIA-ME.md": "# Pedido\n002"}, result["files"])
        self.assertEqual(("linha nova",), result["notes"])
        self.assertEqual({"id": "002"}, handoff)

    def test_readback_output_follows_the_return_schema(self):
        result = bridges.run_readback(self.registry, "ponte_teste", {"entries": [{"rel": "index.html"}]})
        self.assertEqual(("project", {"vistos": 1}), (result["state"], result["env_detail"]))

    def test_an_unknown_or_disabled_environment_is_refused(self):
        with self.assertRaises(ValueError):
            bridges.run_package(self.registry, "outro", {})
        self.registry.plugins["ponte_teste"]["status"] = "disabled"
        with self.assertRaises(ValueError):
            bridges.run_package(self.registry, "ponte_teste", {})
        with self.assertRaises(ValueError):
            bridges.run_package(self.registry, "../x", {})

    def test_a_failing_bridge_is_isolated(self):
        registry, tmp = bridge_registry(BRIDGE_CODE.replace('handoff["mexido"] = True', "raise SystemExit(3)"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(ValueError) as caught:
            bridges.run_package(registry, "ponte_teste", {"id": "x"})
        self.assertIn("Plugin ponte_teste: package falhou (SystemExit)", str(caught.exception))
        registry, tmp = bridge_registry(BRIDGE_CODE.replace('"state": state', '"state": "pronto"'))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(ValueError) as caught:
            bridges.run_readback(registry, "ponte_teste", {"entries": []})
        self.assertIn("readback devolveu um resultado fora das regras", str(caught.exception))

    def test_a_hostile_return_value_is_isolated(self):
        hostile = BRIDGE_CODE.replace(
            'return {"files": {"ponte_teste/LEIA-ME.md": "# Pedido\\n" + handoff["id"]}, "notes": ["linha\\nnova"]}',
            "return Hostile(files={})",
        ).replace("def package", HOSTILE_CODE + "\n\ndef package")
        for body, phrase in (
            ("raise SystemExit(3)", "Plugin ponte_teste: package falhou (SystemExit)"),
            (
                'raise ValueError("x")',
                "package devolveu um resultado fora das regras: O resultado de package não é JSON válido",
            ),
        ):
            with self.subTest(phrase=phrase):
                registry, tmp = bridge_registry(hostile.replace("RAISE_HERE", body))
                self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
                with self.assertRaises(ValueError) as caught:
                    bridges.run_package(registry, "ponte_teste", {"id": "x"})
                self.assertIs(ValueError, type(caught.exception))
                self.assertIn(phrase, str(caught.exception))
        registry, tmp = bridge_registry(BRIDGE_CODE.replace('"problems": []', '"problems": [float("nan")]'))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(ValueError) as caught:
            bridges.run_readback(registry, "ponte_teste", {"entries": []})
        self.assertIn("O resultado de readback não é JSON válido", str(caught.exception))


class PackageRulesTests(unittest.TestCase):
    def refused(self, result, phrase):
        with self.assertRaises(bridges.BridgeResultError) as caught:
            bridges.check_package(result, "hyperframes")
        self.assertIn(phrase, str(caught.exception))

    def test_only_intent_text_inside_the_environment_folder(self):
        ok = {"files": {"hyperframes/BRIEF.md": "x", "hyperframes/a/b.json": "{}"}, "notes": []}
        self.assertEqual(ok["files"], bridges.check_package(ok, "hyperframes")["files"])
        self.refused({"files": {"hyperframes/index.html": "<x>"}}, "não é .md, .json, .txt")
        self.refused({"files": {"outro/BRIEF.md": "x"}}, "tem que ficar em hyperframes/")
        self.refused({"files": {"hyperframes/../BRIEF.md": "x"}}, "nome inválido")
        self.refused({"files": {"hyperframes/a/b/c/d/e/f.md": "x"}}, "tem que ficar em hyperframes/")
        self.refused({"files": {"hyperframes/A.md": "x", "hyperframes/a.md": "y"}}, "só diferem na caixa")
        self.refused({"files": {"hyperframes/a.md": "a\x00"}}, "NUL")
        self.refused({"files": {"hyperframes/a.md": "\ud800"}}, "não é UTF-8 válido")
        self.refused({"files": {"hyperframes/a.md": 3}}, "dict de caminho")
        self.refused({"files": [], "notes": []}, 'package devolve {"files"')
        self.refused({"files": {}, "extra": 1}, 'package devolve {"files"')
        self.refused({"files": {}, "notes": [1]}, "notes é uma lista")

    def test_the_ceilings(self):
        many = {f"hyperframes/{n}.md": "x" for n in range(bridges.FILES_MAX + 1)}
        self.refused({"files": many}, "passa de 50 arquivos")
        self.refused({"files": {"hyperframes/a.md": "x" * (bridges.FILE_MAX_BYTES + 1)}}, "passa de 256 KiB")
        big = {f"hyperframes/{n}.md": "x" * bridges.FILE_MAX_BYTES for n in range(5)}
        self.refused({"files": big}, "1024 KiB no total")


class ReadbackRulesTests(unittest.TestCase):
    def test_the_state_is_closed_and_the_detail_is_bounded(self):
        self.assertEqual(READBACK, bridges.check_readback(READBACK))
        for broken in ({**READBACK, "state": "feito"}, {**READBACK, "extra": 1}, {"state": "waiting"}):
            with self.subTest(broken=broken), self.assertRaises(bridges.BridgeResultError):
                bridges.check_readback(broken)
        with self.assertRaises(bridges.BridgeResultError):
            bridges.check_readback({**READBACK, "env_detail": {"x": "y" * bridges.ENV_DETAIL_MAX_BYTES}})
        with self.assertRaises(bridges.BridgeResultError):
            bridges.check_readback({**READBACK, "problems": [float("nan")]})


if __name__ == "__main__":
    unittest.main()

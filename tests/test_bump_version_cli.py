"""`scripts/bump_version.py` contra uma cópia temporária das fontes de versão.

Nunca toca a árvore real: toda escrita acontece numa raiz criada por `setUp`.
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

# isort: split
import bump_version

OLD = "1.0.0"
NEW = "2.3.4"
DATE = "2026-05-06"

SKILL_TEXT = (
    "---\nname: demo\nmetadata:\n  version: "
    f'"{OLD}"\n  updated: "2026-01-01"\n---\n'
    "<!-- espelho: edite os dois -->\nCorpo do skill.\n"
)


def _dump(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


class BumpFixture(unittest.TestCase):
    """Raiz temporária com todas as fontes de versão em `OLD`."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gb-bump-cli-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.put("scripts/getbrolls/__init__.py", f'"""x."""\n__version__ = "{OLD}"\n')
        self.put("package.json", _dump({"name": "x", "version": OLD}))
        self.put(
            "package-lock.json",
            _dump(
                {"name": "x", "version": OLD, "packages": {"": {"version": OLD}, "node_modules/a": {"version": "9"}}}
            ),
        )
        self.put(".claude-plugin/plugin.json", _dump({"name": "x", "version": OLD}))
        self.put(".claude-plugin/marketplace.json", _dump({"plugins": [{"name": "x", "version": OLD}]}))
        self.put("SKILL.md", SKILL_TEXT)
        for name, word in (("README.md", "Versão"), ("README.en.md", "Version")):
            self.put(name, f'![badge](https://img/badge/version-{OLD}-blue) <img alt="{word} {OLD}">\n')
        self.put("docs/QUALITY.md", f"# Qualidade e evidências — GET B-ROLLS {OLD}\n\ntexto\n")
        self.put("docs/MANUAL.md", f"# Manual\n\nVersão {OLD}.\n")
        self.put("CHANGELOG.md", "# Changelog\n\n## Unreleased\n\n## 1.0.0 — 2026-01-01\n")
        self.put("schemas/a.schema.json", '{\n  "$id": "https://old.example/x/schemas/a.schema.json"\n}\n')
        shutil.copy(ROOT / "scripts" / "gen_skill_mirror.py", self.root / "scripts" / "gen_skill_mirror.py")

    def put(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def get(self, relative: str) -> str:
        return (self.root / relative).read_text(encoding="utf-8")

    def snapshot(self) -> dict[str, str]:
        return {
            str(p.relative_to(self.root)): p.read_text(encoding="utf-8")
            for p in sorted(self.root.rglob("*"))
            if p.is_file()
        }

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bump_version.main([*argv, "--root", str(self.root)])
        return code, out.getvalue(), err.getvalue()


class BumpWriteTests(BumpFixture):
    def test_write_rewrites_every_source_and_the_mirror(self):
        code, out, _ = self.run_main(NEW, "--date", DATE)
        self.assertEqual(0, code)
        self.assertEqual(f"versão atualizada para {NEW}.\n", out)
        self.assertIn(f'__version__ = "{NEW}"', self.get("scripts/getbrolls/__init__.py"))
        self.assertEqual({"name": "x", "version": NEW}, json.loads(self.get("package.json")))
        lock = json.loads(self.get("package-lock.json"))
        self.assertEqual(
            (NEW, NEW, "9"),
            (lock["version"], lock["packages"][""]["version"], lock["packages"]["node_modules/a"]["version"]),
        )
        self.assertEqual(NEW, json.loads(self.get(".claude-plugin/plugin.json"))["version"])
        self.assertEqual(NEW, json.loads(self.get(".claude-plugin/marketplace.json"))["plugins"][0]["version"])
        skill = self.get("SKILL.md")
        self.assertIn(f'version: "{NEW}"', skill)
        self.assertIn(f'updated: "{DATE}"', skill)
        self.assertIn(f"badge/version-{NEW}-blue", self.get("README.md"))
        self.assertIn(f'alt="Versão {NEW}"', self.get("README.md"))
        self.assertIn(f'alt="Version {NEW}"', self.get("README.en.md"))
        self.assertIn(f"GET B-ROLLS {NEW}\n", self.get("docs/QUALITY.md"))
        self.assertIn(f"\nVersão {NEW}.\n", self.get("docs/MANUAL.md"))
        self.assertEqual(
            f"# Changelog\n\n## Unreleased\n\n## {NEW} — {DATE}\n\n## 1.0.0 — 2026-01-01\n", self.get("CHANGELOG.md")
        )
        schema_id = f"{bump_version.SCHEMA_ID_BASE}/v{NEW}/schemas/a.schema.json"
        self.assertEqual('{\n  "$id": "' + schema_id + '"\n}\n', self.get("schemas/a.schema.json"))
        # o espelho foi gerado pelo script copiado: prefixo e comentário fixo
        mirror = self.get("skills/get-brolls/SKILL.md")
        self.assertIn("<!-- Gerado a partir do SKILL.md da raiz", mirror)
        self.assertIn(f'version: "{NEW}"', mirror)

    def test_the_official_plugin_version_and_its_pin_move_together(self):
        source = ROOT / "plugins_official" / "hyperframes"
        manifest = (source / "getbrolls-plugin.json").read_text(encoding="utf-8")
        self.put(
            "plugins_official/hyperframes/getbrolls-plugin.json",
            manifest.replace('"version": "2.6.0"', f'"version": "{OLD}"'),
        )
        for name in ("plugin.py", "README.md"):
            self.put(f"plugins_official/hyperframes/{name}", (source / name).read_text(encoding="utf-8"))
        self.put("scripts/getbrolls/sdk/official_pins.py", "PINS = {}\n")
        self.assertEqual(1, self.run_main(NEW, "--check", "--root", str(self.root))[0])
        self.assertEqual(0, self.run_main(NEW, "--date", DATE, "--root", str(self.root))[0])
        self.assertIn(f'"version": "{NEW}"', self.get("plugins_official/hyperframes/getbrolls-plugin.json"))
        self.assertIn('"hyperframes": "', self.get("scripts/getbrolls/sdk/official_pins.py"))
        self.assertEqual(0, self.run_main(NEW, "--check", "--root", str(self.root))[0])

    def test_write_then_check_passes_and_second_write_is_a_noop(self):
        self.assertEqual(0, self.run_main(NEW, "--date", DATE)[0])
        after_first = self.snapshot()
        code, out, _ = self.run_main(NEW, "--check")
        self.assertEqual((0, f"todas as fontes coerentes com a versão {NEW}.\n"), (code, out))
        self.assertEqual(0, self.run_main(NEW, "--date", DATE)[0])
        self.assertEqual(after_first, self.snapshot())  # CHANGELOG sem stub duplicado

    def test_default_date_is_today(self):
        class FakeDate:
            @staticmethod
            def today():
                return mock.Mock(isoformat=lambda: "2031-02-03")

        with mock.patch.object(bump_version, "_date", FakeDate):
            self.assertEqual(0, self.run_main(NEW)[0])
        self.assertIn("## 2.3.4 — 2031-02-03", self.get("CHANGELOG.md"))
        self.assertIn('updated: "2031-02-03"', self.get("SKILL.md"))

    def test_package_lock_without_root_entry_only_bumps_the_top_level(self):
        self.put("package-lock.json", _dump({"version": OLD, "packages": {"node_modules/a": {"version": "9"}}}))
        bump_version.targets(self.root)[2].write(self.root, NEW, DATE)
        lock = json.loads(self.get("package-lock.json"))
        self.assertEqual(NEW, lock["version"])
        self.assertEqual({"node_modules/a": {"version": "9"}}, lock["packages"])


class BumpCheckTests(BumpFixture):
    def test_check_lists_every_divergent_source_and_writes_nothing(self):
        before = self.snapshot()
        code, out, err = self.run_main(NEW, "--check")
        self.assertEqual(1, code)
        self.assertEqual("", out)
        self.assertTrue(err.startswith(f"fora de sincronia com a versão {NEW}:\n"))
        for name in (
            "scripts/getbrolls/__init__.py",
            "package.json",
            "package-lock.json",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            "SKILL.md",
            "README.md",
            "README.en.md",
            "docs/QUALITY.md",
            "docs/MANUAL.md",
            "CHANGELOG.md",
            "schemas/a.schema.json",
        ):
            self.assertIn(f"  - {name}\n", err)
        self.assertEqual(before, self.snapshot())

    def test_check_flags_a_single_stale_source_only(self):
        self.assertEqual(0, self.run_main(NEW, "--date", DATE)[0])
        self.put("docs/MANUAL.md", "# Manual\n\nVersão 1.0.0.\n")
        code, _, err = self.run_main(NEW, "--check")
        self.assertEqual(1, code)
        self.assertEqual(f"fora de sincronia com a versão {NEW}:\n  - docs/MANUAL.md\n", err)

    def test_check_flags_a_stale_mirror(self):
        self.assertEqual(0, self.run_main(NEW, "--date", DATE)[0])
        self.put("skills/get-brolls/SKILL.md", "editado a mão\n")
        code, _, err = self.run_main(NEW, "--check")
        self.assertEqual(1, code)
        self.assertIn("  - skills/get-brolls/SKILL.md\n", err)

    def test_check_treats_missing_markers_as_divergence(self):
        self.put("scripts/getbrolls/__init__.py", "sem versão\n")
        self.put("SKILL.md", "---\nname: x\n---\n<!-- c -->\n")
        self.put("README.md", "sem badge\n")
        self.put("docs/QUALITY.md", "# outro título\n")
        self.put("docs/MANUAL.md", "sem linha\n")
        self.put(".claude-plugin/marketplace.json", _dump({"plugins": []}))
        self.put("schemas/a.schema.json", "{ não é json")
        self.put("schemas/list.schema.json", "[1]")
        code, _, err = self.run_main(OLD, "--check")
        self.assertEqual(1, code)
        for name in (
            "scripts/getbrolls/__init__.py",
            "SKILL.md",
            "README.md",
            ".claude-plugin/marketplace.json",
            "docs/QUALITY.md",
            "docs/MANUAL.md",
            "schemas/a.schema.json",
            "schemas/list.schema.json",
        ):
            self.assertIn(f"  - {name}\n", err)
        self.assertNotIn("  - package.json\n", err)

    def test_check_schema_that_is_unreadable_is_divergent(self):
        target = bump_version.schema_targets(self.root)[0]
        (self.root / "schemas" / "a.schema.json").unlink()
        self.assertFalse(target.check(self.root, OLD))


class BumpRefusalTests(BumpFixture):
    def test_invalid_version_is_refused_with_exit_2_and_no_write(self):
        before = self.snapshot()
        for bad in ("1.2", "v1.2.3", "1.2.3-rc1", ""):
            code, out, err = self.run_main(bad, "--date", DATE)
            self.assertEqual((2, ""), (code, out), bad)
            self.assertEqual(f"versão inválida: {bad!r} (esperado X.Y.Z)\n", err)
        self.assertEqual(before, self.snapshot())

    def test_invalid_date_is_refused_with_exit_2_also_in_check_mode(self):
        before = self.snapshot()
        for extra in ((), ("--check",)):
            code, _, err = self.run_main(NEW, "--date", "06/05/2026", *extra)
            self.assertEqual(2, code)
            self.assertEqual("data inválida: '06/05/2026' (esperado AAAA-MM-DD)\n", err)
        self.assertEqual(before, self.snapshot())

    def assert_write_refused(self, relative: str, text: str, message: str):
        self.put(relative, text)
        with self.assertRaisesRegex(ValueError, message):
            bump_version.run(self.root, NEW, DATE, check=False)

    def test_init_without_version(self):
        self.assert_write_refused("scripts/getbrolls/__init__.py", "x = 1\n", "__version__ não encontrado")

    def test_skill_without_metadata_version(self):
        self.assert_write_refused("SKILL.md", "---\nname: x\n---\ncorpo\n", r"metadata\.version não encontrado")

    def test_skill_without_updated(self):
        self.assert_write_refused(
            "SKILL.md", f'---\nmetadata:\n  version: "{OLD}"\n---\ncorpo\n', r"metadata\.updated não encontrado"
        )

    def test_readme_without_badge_url(self):
        self.assert_write_refused("README.md", 'alt="Versão 1.0.0"\n', "badge de versão não encontrado")

    def test_readme_without_alt(self):
        self.assert_write_refused("README.en.md", f"badge/version-{OLD}-blue\n", "alt=.Version X.Y.Z. não encontrado")

    def test_quality_without_title(self):
        self.assert_write_refused("docs/QUALITY.md", "# outro\n", "título de versão não encontrado")

    def test_manual_without_version_line(self):
        self.assert_write_refused("docs/MANUAL.md", "# Manual\n", "linha de versão não encontrada")

    def test_changelog_without_unreleased(self):
        self.assert_write_refused("CHANGELOG.md", "# Changelog\n", "'## Unreleased' não encontrado")

    def test_schema_without_id(self):
        self.assert_write_refused("schemas/a.schema.json", '{"type": "object"}\n', r"\$id não encontrado")

    def test_mirror_generation_failure_surfaces_the_generator_error(self):
        self.put("SKILL.md", SKILL_TEXT.replace("<!-- espelho: edite os dois -->\n", ""))
        with self.assertRaisesRegex(ValueError, "(?s)gen_skill_mirror.py falhou: .*achou 0"):
            bump_version.run(self.root, NEW, DATE, check=False)

    def test_mirror_generation_failure_without_stderr_falls_back_to_stdout(self):
        fake = subprocess.CompletedProcess([], 1, stdout="saída\n", stderr="")
        with (
            mock.patch.object(bump_version.subprocess, "run", return_value=fake),
            self.assertRaisesRegex(ValueError, "falhou: saída"),
        ):
            bump_version.MIRROR_TARGET.write(self.root, NEW, DATE)


class BumpEntryPointTests(BumpFixture):
    def test_default_root_is_the_repository(self):
        self.assertEqual(ROOT, bump_version._default_root())  # pylint: disable=protected-access

    def test_main_without_root_checks_the_real_repository_read_only(self):
        with mock.patch.object(bump_version, "_default_root", return_value=self.root):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = bump_version.main([NEW, "--check"])
        self.assertEqual(1, code)
        self.assertIn("  - package.json\n", err.getvalue())

    def test_script_runs_as_a_program(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bump_version.py"), NEW, "--check", "--root", str(self.root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("fora de sincronia com a versão 2.3.4", result.stderr)

    def test_utf8_output_reconfigures_streams_and_tolerates_plain_ones(self):
        fake = mock.Mock()
        with mock.patch.object(sys, "stdout", fake), mock.patch.object(sys, "stderr", io.StringIO()):
            bump_version._utf8_output()  # pylint: disable=protected-access
        fake.reconfigure.assert_called_once_with(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

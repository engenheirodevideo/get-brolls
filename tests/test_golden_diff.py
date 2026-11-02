"""A diferença entre duas gravações do ouro e a classificação pela lista fechada, com registros sintéticos."""

import unittest

import _golden_diff


def _record(exit_code=0, stdout=None, stderr=None, files=None, appends=None):
    return {
        "case": "x",
        "argv": [],
        "exit": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "files": files or {},
        "appends": appends or {},
    }


OLD = {
    "fluxo/a": _record(2, stderr={"json": {"error": "e", "hint": "h", "traceback": "<TRACEBACK>"}}),
    "fluxo/b": _record(0, stdout={"json": {"summary": {"line": "x"}}}, files={"work/p/R.md": {"lines": ["um"]}}),
}
NEW = {
    "fluxo/a": _record(1, stderr={"json": {"error": "e", "error_code": "INVALID_DATA"}}),
    "fluxo/b": _record(0, stdout={"json": {"summary": {"line": "y"}}}, files={"work/p/R.md": {"lines": ["dois"]}}),
}
ACCEPTED = {
    "items": [
        {
            "id": "saidas",
            "lista": "saidas",
            "changelog": "**Erro de operação ou de dados sai com `1`, não mais `2`.**",
            "ratificado": "2026-10-05",
            "match": [{"case": "*", "where": "exit", "old": 2, "new": 1}],
        },
        {
            "id": "hint",
            "lista": "hint",
            "changelog": "**`hint` só quando há o que fazer.**",
            "ratificado": "2026-10-05",
            "match": [{"case": "*", "where": "stderr/json/hint", "kind": "removed"}],
        },
        {
            "id": "sem-uso",
            "lista": "acrescimo",
            "changelog": "nada",
            "ratificado": None,
            "match": [{"case": "fluxo/z", "where": "exit"}],
        },
    ]
}


class DiffTests(unittest.TestCase):
    def test_finds_added_removed_and_changed_leaves(self):
        found = {(item["case"], item["where"], item["kind"]) for item in _golden_diff.diff(OLD, NEW)}
        self.assertEqual(
            {
                ("fluxo/a", "exit", "changed"),
                ("fluxo/a", "stderr/json/hint", "removed"),
                ("fluxo/a", "stderr/json/traceback", "removed"),
                ("fluxo/a", "stderr/json/error_code", "added"),
                ("fluxo/b", "stdout/json/summary/line", "changed"),
                ("fluxo/b", "files/work/p/R.md", "changed"),
            },
            found,
        )

    def test_a_case_present_on_one_side_only_is_a_difference(self):
        found = _golden_diff.diff({"fluxo/a": OLD["fluxo/a"]}, {})
        self.assertEqual([{"case": "fluxo/a", "where": "case", "kind": "removed"}], found)

    def test_identical_recordings_have_no_difference(self):
        self.assertEqual([], _golden_diff.diff(OLD, OLD))

    def test_a_jsonl_diary_line_is_compared_key_by_key(self):
        before = '{"status": "success", "warnings": []}'
        after = '{"status": "error", "recovered": false, "warnings": []}'
        old = {"fluxo/a": _record(appends={"p/diagnostics.jsonl": [before]})}
        new = {"fluxo/a": _record(appends={"p/diagnostics.jsonl": [after]})}
        found = {(item["where"], item["kind"], item.get("new")) for item in _golden_diff.diff(old, new)}
        self.assertEqual(
            {
                ("appends/p/diagnostics.jsonl/0/status", "changed", "error"),
                ("appends/p/diagnostics.jsonl/0/recovered", "added", False),
            },
            found,
        )

    def test_a_log_and_a_line_that_is_not_json_stay_whole(self):
        old = {"fluxo/a": _record(appends={"p/getbrolls.log": ["a"], "p/x.jsonl": ["{quebrado"]})}
        new = {"fluxo/a": _record(appends={"p/getbrolls.log": ["b"], "p/x.jsonl": ["{quebrado!"]})}
        found = {(item["where"], item["kind"]) for item in _golden_diff.diff(old, new)}
        self.assertEqual({("appends/p/getbrolls.log", "changed"), ("appends/p/x.jsonl/0", "changed")}, found)


class ClassifyTests(unittest.TestCase):
    def test_first_rule_takes_the_difference_and_the_rest_is_loose(self):
        taken, loose, stale = _golden_diff.classify(_golden_diff.diff(OLD, NEW), ACCEPTED)
        self.assertEqual([("fluxo/a", "exit")], [(item["case"], item["where"]) for item in taken["saidas"]])
        self.assertEqual(["stderr/json/hint"], [item["where"] for item in taken["hint"]])
        self.assertEqual(["sem-uso"], stale)
        self.assertEqual(
            {"stderr/json/traceback", "stderr/json/error_code", "stdout/json/summary/line", "files/work/p/R.md"},
            {item["where"] for item in loose},
        )

    def test_a_rule_may_name_a_list_of_cases(self):
        rule = {"case": ["fluxo/b", "fluxo/c"], "where": "exit"}
        self.assertTrue(_golden_diff._matches(rule, {"case": "fluxo/b", "where": "exit", "kind": "changed"}))
        self.assertFalse(_golden_diff._matches(rule, {"case": "fluxo/a", "where": "exit", "kind": "changed"}))

    def test_old_and_new_values_must_match_when_the_rule_names_them(self):
        other = {"items": [{**ACCEPTED["items"][0], "match": [{"case": "*", "where": "exit", "old": 2, "new": 4}]}]}
        _, loose, stale = _golden_diff.classify(_golden_diff.diff(OLD, NEW), other)
        self.assertIn("exit", {item["where"] for item in loose})
        self.assertEqual(["saidas"], stale)


class DriftTests(unittest.TestCase):
    def test_an_item_pins_the_differences_it_takes(self):
        taken, _, _ = _golden_diff.classify(_golden_diff.diff(OLD, NEW), ACCEPTED)
        pinned = {
            "items": [
                {**entry, "diferencas_sha256": _golden_diff.digest(taken[entry["id"]])} for entry in ACCEPTED["items"]
            ]
        }
        self.assertEqual([], _golden_diff.drifted(taken, pinned))
        self.assertEqual(["saidas", "hint", "sem-uso"], _golden_diff.drifted(taken, ACCEPTED))

    def test_another_value_in_the_same_place_moves_the_digest(self):
        found = _golden_diff.diff(OLD, NEW)
        other = _golden_diff.diff(OLD, {**NEW, "fluxo/a": {**NEW["fluxo/a"], "exit": 1, "stderr": {"json": {}}}})
        self.assertEqual(_golden_diff.digest(found), _golden_diff.digest(list(reversed(found))))
        self.assertNotEqual(_golden_diff.digest(found), _golden_diff.digest(other))


class ProblemsTests(unittest.TestCase):
    CHANGELOG = (
        "## 2.6.0\n\n### Migração\n\n- **Erro de operação ou de dados sai com `1`, não mais `2`.** x\n"
        "- **`hint` só quando há o que fazer.** y\n\n### Limites honestos\n\n- nada\n"
    )

    def test_needs_closed_list_date_and_changelog_line(self):
        found = _golden_diff.problems(ACCEPTED, self.CHANGELOG)
        self.assertEqual(
            [
                "sem-uso: falta a data em que o mantenedor ratificou",
                "sem-uso: a frase não está na Migração do CHANGELOG: 'nada'",
            ],
            found,
        )

    def test_a_pending_item_has_no_date_and_says_why(self):
        base = {**ACCEPTED["items"][0], "ratificado": None}
        silent = {"items": [base]}
        self.assertEqual(
            ["saidas: falta a data em que o mantenedor ratificou"], _golden_diff.problems(silent, self.CHANGELOG)
        )
        waiting = {"items": [{**base, "pendente": "aguarda o ok do mantenedor (regravação de 2026-10-10)"}]}
        self.assertEqual([], _golden_diff.problems(waiting, self.CHANGELOG))
        self.assertEqual(["saidas"], _golden_diff.pending(waiting))
        dated = {"items": [{**ACCEPTED["items"][0], "pendente": "x"}]}
        self.assertEqual(
            ["saidas: tem data e pendência ao mesmo tempo; tire o pendente"],
            _golden_diff.problems(dated, self.CHANGELOG),
        )
        self.assertEqual([], _golden_diff.pending(dated))

    def test_a_list_name_outside_the_closed_list_is_refused(self):
        bad = {"items": [{**ACCEPTED["items"][0], "lista": "inventada"}]}
        self.assertEqual(["saidas: lista 'inventada' não existe"], _golden_diff.problems(bad, self.CHANGELOG))

    def test_a_phrase_outside_the_migration_section_does_not_count(self):
        late = {"items": [{**ACCEPTED["items"][0], "changelog": "nada"}]}
        self.assertEqual(1, len(_golden_diff.problems(late, self.CHANGELOG)))


class MigrationSectionTests(unittest.TestCase):
    CHANGELOG = (
        "# Changelog\n\n## 2.6.0 — 2026-09-26\n\n### Migração\n\n- **Principal.** a\n\n"
        "### Limites honestos\n\n- **Limite.** b\n\n### Correções da revisão de 2026-10-07\n\n"
        "#### Migração\n\n- **Da revisão.** c\n\n#### Erros, diagnóstico\n\n- **Correção.** d\n\n"
        "### Migração antiga\n\n- **Título que não é só Migração.** e\n\n"
        "Veja a ### Migração no topo: **Prosa.** f\n\n"
        "## 2.5.0 — 2026-09-19\n\n### Migração\n\n- **Versão velha.** g\n"
    )

    def test_reads_every_migration_heading_of_the_newest_version(self):
        section = _golden_diff.migration_section(self.CHANGELOG)
        self.assertIn("**Principal.**", section)
        self.assertIn("**Da revisão.**", section)
        for outside in ("**Limite.**", "**Correção.**", "**Versão velha.**"):
            with self.subTest(outside=outside):
                self.assertNotIn(outside, section)

    def test_the_heading_must_be_the_whole_line(self):
        section = _golden_diff.migration_section(self.CHANGELOG)
        self.assertNotIn("**Título que não é só Migração.**", section)
        self.assertNotIn("**Prosa.**", section)

    def test_the_fix0_line_of_summary_next_counts_in_the_real_changelog(self):
        section = _golden_diff.migration_section(_golden_diff.CHANGELOG.read_text(encoding="utf-8"))
        self.assertIn("**Texto de `summary.next`.**", section)
        self.assertIn("**Saídas renomeadas.**", section)
        self.assertNotIn("Um erro inesperado de qualquer tipo", section)


class ReportTests(unittest.TestCase):
    def test_report_lists_the_closed_list_then_the_loose_groups(self):
        text = _golden_diff.report(_golden_diff.diff(OLD, NEW), ACCEPTED, {"base": "árvore de teste"})
        self.assertIn("- base: árvore de teste", text)
        self.assertIn("### saidas — Saídas e error_code", text)
        self.assertIn("## Fora da lista", text)
        self.assertIn("`stderr/json/traceback` (removed, 1 caso(s))", text)
        self.assertIn("```diff", text)
        self.assertIn("## Regras sem diferença", text)
        self.assertIn("## Itens que mudaram depois do ok", text)


if __name__ == "__main__":
    unittest.main()

"""Todo efeito colateral de `scripts/getbrolls` passa por `getbrolls.effects` (`_effects_guard`).

A varredura é estrutural (AST): nenhuma linha nem mensagem é conferida, só que a primitiva
(subprocesso, rede, gravação, troca, remoção, pasta, código carregado) está dentro de um
helper ou de um bloco `with effects.op(...)`, ou leva `# effect-exempt: <motivo>`.
"""

import ast
import textwrap
import unittest

import _effects_guard
import _paths as _test_paths  # também põe `scripts/` no sys.path

from getbrolls import effects

PACKAGE = _test_paths.ROOT / "scripts" / "getbrolls"
OFFICIAL = _test_paths.ROOT / "plugins_official"


def scanned():
    """`(nome, caminho)` de cada `.py` que a varredura lê: o pacote e os plugins oficiais (que rodam como o core)."""
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path.relative_to(PACKAGE).as_posix(), path
    for path in sorted(OFFICIAL.rglob("*.py")):
        yield path.relative_to(_test_paths.ROOT).as_posix(), path


REASON = "only a membership test, nothing is called or written here"


def _operation_literals(tree):
    """Nomes de operação escritos no código: 1º argumento de `effects.op`/`batch` e `operation=`."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        is_effects = isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "effects"
        if is_effects and name in {"op", "batch"} and node.args and isinstance(node.args[0], ast.Constant):
            found.append((node.lineno, node.args[0].value))
        if is_effects:
            found.extend(
                (node.lineno, keyword.value.value)
                for keyword in node.keywords
                if keyword.arg == "operation" and isinstance(keyword.value, ast.Constant)
            )
    return found


class PackageTests(unittest.TestCase):
    def test_no_module_calls_a_side_effect_primitive_outside_effects(self):
        problems = []
        for name, path in scanned():
            if name in {"effects.py", "effects_salt.py"}:
                continue
            found = _effects_guard.bare_effects(path.read_text(encoding="utf-8"))
            problems += [f"{name}:{row}: {problem}: {code}" for row, code, problem in found]
        self.assertEqual(
            [],
            problems,
            "Use um helper de `getbrolls.effects` (ou um bloco `with effects.op(...)`): ele grava a linha "
            "`event=op` no getbrolls.log. Se o lugar não pode logar (recursão no próprio log ou nas travas), "
            "explique com `# effect-exempt: <motivo>` na linha ou logo acima.",
        )

    def test_every_operation_named_in_the_package_is_a_known_low_cardinality_name(self):
        unknown = []
        for where, path in scanned():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            unknown += [
                f"{where}:{row}: {name}" for row, name in _operation_literals(tree) if name not in effects.OPERATIONS
            ]
        self.assertEqual([], unknown)

    def test_the_exemptions_are_few_and_each_has_a_reason(self):
        markers = {}
        for name, path in scanned():
            for row, (_count, reason) in _effects_guard.markers(path.read_text(encoding="utf-8")).items():
                markers[f"{name}:{row}"] = reason
        # O log e as travas não podem passar pelo effects (recursão); o resto é leitura ou rede de dentro
        # de um opener que já loga. Um número que cresce pede revisão, não um teto maior.
        self.assertLessEqual(len(markers), 30, markers)
        self.assertTrue(all(not _effects_guard.weak(reason) for reason in markers.values()), markers)


class GuardSelfTests(unittest.TestCase):
    def guard(self, body):
        source = textwrap.dedent(body).replace("REASON", REASON)
        return [(row, problem) for row, _code, problem in _effects_guard.bare_effects(source)]

    def test_direct_primitives_are_flagged_and_helpers_are_not(self):
        source = """\
            import os
            import shutil
            import subprocess
            from . import effects
            def f(path, other):
                os.replace(path, other)
                shutil.rmtree(path)
                subprocess.run(["x"])
                path.unlink()
                path.write_text("x")
                effects.replace(path, other)
                effects.unlink(path, missing_ok=True)
                effects.mkdir(path, exist_ok=True)
            """
        self.assertEqual([(row, "sem effects") for row in range(6, 11)], self.guard(source))

    def test_aliases_are_flagged(self):
        source = """\
            import shutil as sh
            import subprocess as sp
            from os import replace, unlink as rm
            from shutil import copy2
            from importlib import import_module
            def f(a, b):
                sh.copytree(a, b)
                sp.Popen(["x"])
                replace(a, b)
                rm(a)
                copy2(a, b)
                import_module(a)
            """
        self.assertEqual([(row, "sem effects") for row in range(7, 13)], self.guard(source))

    def test_getattr_forms_and_values_are_flagged(self):
        source = """\
            import os
            import shutil
            def f(path, name):
                getattr(os, "replace")(path, path)
                getattr(shutil, name)(path)
                getattr(path, "unlink")()
                callbacks = (os.unlink, shutil.rmtree)
                return map(os.remove, [path])
            """
        self.assertEqual([(row, "sem effects") for row in range(4, 9)], self.guard(source))

    def test_a_primitive_inside_an_effects_block_is_covered_even_in_a_multi_item_with(self):
        source = """\
            import os
            from . import effects
            def f(path, data):
                with effects.op("fs.write", path):
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
                    os.replace(path, path)
                with effects.op("fs.copy", path) as copying, path.open("xb") as out:
                    out.write(data)
                with path.open("wb") as out:
                    out.write(data)
            """
        self.assertEqual([(9, "sem effects")], self.guard(source))

    def test_writes_are_told_apart_from_reads(self):
        source = """\
            import os
            READ = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            WRITE = os.O_WRONLY | os.O_CREAT
            def f(path, mode, text):
                open(path).read()
                open(path, "rb").read()
                path.open(encoding="utf-8").read()
                os.open(path, READ)
                os.open(path, os.O_RDONLY)
                text.replace("a", "b")
                os.kill(1, 0)
                open(path, "a")
                path.open(mode)
                os.open(path, WRITE)
                path.replace(path)
            """
        self.assertEqual([(row, "sem effects") for row in range(12, 16)], self.guard(source))

    def test_network_server_code_and_signals_are_flagged(self):
        source = """\
            import http.client
            import os
            import socket
            import urllib.request
            def f(server, proc, opener, request, spec):
                urllib.request.urlopen(request)
                http.client.HTTPSConnection("h")
                socket.socket()
                opener.open(request)
                server.serve_forever()
                exec("x")
                spec.loader.exec_module(proc)
                os.kill(proc.pid, 9)
                proc.terminate()
                proc.kill()
            """
        self.assertEqual([(row, "sem effects") for row in range(6, 16)], self.guard(source))

    def test_annotations_are_not_calls(self):
        source = """\
            import subprocess
            def f(proc: subprocess.Popen) -> subprocess.CompletedProcess:
                value: subprocess.Popen = proc
                return value
            """
        self.assertEqual([], self.guard(source))

    def test_a_marker_covers_its_line_or_the_line_below_and_names_its_count(self):
        source = """\
            import os
            def f(a, b):
                os.unlink(a)  # effect-exempt: REASON
                # effect-exempt: REASON
                os.unlink(b)
                os.replace(a, b) or os.unlink(a)  # effect-exempt: REASON
                os.replace(a, b) or os.unlink(a)  # effect-exempt(2): REASON
                os.rmdir(
                    a,
                )  # effect-exempt: REASON
            """
        self.assertEqual([(6, "marcador cobre 1 de 2")], self.guard(source))

    def test_a_vague_reason_or_a_marker_with_nothing_to_cover_does_not_count(self):
        source = """\
            import os
            def f(a):
                os.unlink(a)  # effect-exempt: ok
                os.unlink(a)  # effect-exempt: best effort
                os.unlink(a)  # effect-exempt: padding padding padding padding padding padding
                return a  # effect-exempt: REASON
            """
        self.assertEqual(
            [(3, "motivo vago"), (4, "motivo vago"), (5, "motivo vago"), (6, "marcador sem primitiva")],
            self.guard(source),
        )

    def test_only_op_batch_and_load_plugin_grant_their_block(self):
        source = """\
            import os
            from . import effects
            def f(p):
                with effects.op("fs.write", p):
                    os.unlink(p)
                with effects.batch("fs.rmtree", p):
                    os.unlink(p)
                with effects.load_plugin("x"):
                    exec("x")
                with effects.anything_at_all(p):
                    os.unlink(p)
            """
        self.assertEqual([(11, "sem effects")], self.guard(source))

    def test_a_block_grants_files_and_only_its_own_area(self):
        source = """\
            import os
            import subprocess
            import urllib.request
            from . import effects
            def f(p, opener, request):
                with effects.op("fs.write", p):
                    p.write_text("x")
                    subprocess.run(["rm", "-rf", "/x"])
                    urllib.request.urlopen(request)
                with effects.op("proc.run", ["x"]):
                    subprocess.run(["x"])
                    p.unlink()
                with effects.op("net.download", request), opener.open(request) as response:
                    p.open("xb").write(response.read())
                    exec("x")
            """
        self.assertEqual([(8, "sem effects"), (9, "sem effects"), (15, "sem effects")], self.guard(source))

    def test_an_operation_that_is_not_a_literal_name_is_refused(self):
        source = """\
            import os
            from . import effects
            def f(p, name):
                with effects.op(name, p):
                    os.unlink(p)
                with effects.batch("fs.rmtree", p):
                    pass
                with effects.op(operation=name):
                    pass
            """
        self.assertEqual(
            [(4, "operação não literal"), (5, "sem effects"), (8, "operação não literal")], self.guard(source)
        )

    def test_a_function_defined_inside_a_block_is_not_granted(self):
        source = """\
            import os
            import shutil
            from . import effects
            def f(p):
                with effects.op("fs.write", p):
                    def later():
                        os.unlink(p)
                    cb = lambda: shutil.rmtree(p)
                    os.replace(p, p)
                return later, cb
            """
        self.assertEqual([(7, "sem effects"), (8, "sem effects")], self.guard(source))

    def test_o_noctty_is_a_read_flag_but_does_not_hide_a_write(self):
        source = """\
            import os
            READ = os.O_RDONLY | getattr(os, "O_NOCTTY", 0)
            WRITE = os.O_RDONLY | os.O_NOCTTY | os.O_WRONLY
            CREATE = os.O_RDONLY | getattr(os, "O_NOCTTY", 0) | os.O_CREAT
            def f(path):
                os.open(path, os.O_RDONLY | os.O_NOCTTY)
                os.open(path, READ)
                os.open(path, os.O_NOCTTY | os.O_WRONLY)
                os.open(path, os.O_NOCTTY | os.O_CREAT)
                os.open(path, WRITE)
                os.open(path, CREATE)
            """
        self.assertEqual([(row, "sem effects") for row in range(8, 12)], self.guard(source))

    def test_flags_widened_with_an_augmented_or_count_as_a_write(self):
        source = """\
            import os
            def f(p):
                flags = os.O_RDONLY
                flags |= os.O_WRONLY | os.O_CREAT
                return os.open(p, flags)
            def g(p):
                flags = os.O_RDONLY
                return os.open(p, flags)
            """
        self.assertEqual([(5, "sem effects")], self.guard(source))

    def test_a_module_or_an_opener_bound_to_another_name_is_followed(self):
        source = """\
            import subprocess
            import urllib.request
            sp = subprocess
            def f(u):
                sp.run(["x"])
                o = urllib.request.build_opener()
                return o.open(u)
            """
        self.assertEqual([(5, "sem effects"), (7, "sem effects")], self.guard(source))

    def test_process_temp_and_code_primitives_are_flagged(self):
        source = """\
            import io
            import os
            import tempfile
            def f(p, q, src):
                os.system("x")
                os.popen("x")
                os.execv("/bin/sh", ["sh"])
                os.spawnv(os.P_WAIT, "/bin/sh", ["sh"])
                os.posix_spawn("/bin/sh", ["sh"], {})
                with tempfile.TemporaryDirectory() as d:
                    pass
                tempfile.NamedTemporaryFile(delete=False)
                tempfile.mkstemp()
                tempfile.mkdtemp()
                p.rename(target=q)
                p.replace(target=q)
                eval(compile(src, "x", "exec"))
                io.open(p, "w")
                os.fdopen(3, "wb")
            """
        rows = [5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17, 18, 19]
        self.assertEqual([(row, "sem effects") for row in rows], self.guard(source))

    def test_a_marker_inside_a_string_does_not_count(self):
        source = """\
            import os
            def f(a):
                return os.unlink(a), "# effect-exempt: REASON"
            """
        self.assertEqual([(3, "sem effects")], self.guard(source))


if __name__ == "__main__":
    unittest.main()

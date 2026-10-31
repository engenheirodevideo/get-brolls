"""Guarda das regras de import de `scripts/getbrolls` (camadas, ciclos, pares, superfície de plugin).

O teste monta o grafo de imports por AST e confere seis regras (a tabela de camadas e os dados ficam em
`tests/import_rules/`):

- R1: o grafo de imports de módulo, sem as arestas da allowlist, não tem ciclo.
- R2a: todo import de módulo aponta para a mesma camada ou para uma abaixo (exceção: allowlist `M`).
- R2b: um import dentro de função que sobe de camada é um adiamento deliberado e fica na allowlist `F`,
  com motivo. Import de função na mesma camada ou para baixo é livre.
- R3: `import getbrolls.sdk.X` também carrega `sdk/__init__`; o grafo tem essa aresta de pacote.
- R4: `examples/` e `plugins_official/` importam só `getbrolls.sdk`; o pacote nunca importa de `tests/`.
- R5: todo módulo existente está na tabela (módulo novo sem camada falha), módulo `planned` ausente é
  tolerado, entrada velha da allowlist falha e o catraca só desce.
- R6: dois módulos que se importam (qualquer tipo de aresta) precisam estar em `pairs_accepted.txt`.

Convenção do resolvedor: `from . import X` é aresta para o submódulo X quando `X.py` existe (senão para o
pacote); dentro de um subpacote ela também é uma aresta para o pacote (o `__init__` roda antes). `if
TYPE_CHECKING:` não conta. Despacho dinâmico entra pela constante `DYNAMIC`, conferida contra o código.
"""

import ast
import sys
import tempfile
import unittest
from collections.abc import Iterable, Mapping
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

PACKAGE = ROOT / "scripts" / "getbrolls"
DATA = Path(__file__).resolve().parent / "import_rules"
ROOT_PACKAGE = "getbrolls"
ROOT_INIT = "__init__"

# Módulos carregados por nome em tempo de execução: origem -> destinos (arestas de função).
DYNAMIC: dict[str, tuple[str, ...]] = {
    "commands": (
        "bootstrap",
        "clients",
        "profile",
        "recipes",
        "sdk.cli",
        "sdk.environments",
        "sdk.plugin_commands",
        "templates",
    ),
}
LAYER_FIELDS = 2  # `módulo camada`; um terceiro campo `planned` marca módulo futuro
ALLOW_FIELDS = 4  # `M|F origem -> destino`
PAIR_FIELDS = 2
SUBPACKAGE_DEPTH = 2  # `getbrolls.sdk.X`: nome + subpacote + módulo
RATCHET_KEYS = ("allow_module", "allow_function", "pairs")

Edge = tuple[str, str]
EdgeKinds = dict[Edge, set[str]]


# --- Grafo -----------------------------------------------------------------------------------------------


def module_name(path: Path) -> str:
    """`foo.py` -> `foo`, `sdk/foo.py` -> `sdk.foo`, `sdk/__init__.py` -> `sdk`, raiz `__init__.py` -> `__init__`."""
    parts = list(path.relative_to(PACKAGE).with_suffix("").parts)
    if len(parts) > 1 and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def package_modules() -> frozenset[str]:
    """Nomes de todos os módulos do pacote que existem em disco."""
    return frozenset(module_name(path) for path in PACKAGE.rglob("*.py"))


def _package_of(module: str, known: Iterable[str]) -> str:
    """Pacote onde o módulo mora: `""` na raiz, `sdk` para `sdk.x` e para o próprio `sdk/__init__`."""
    if module == ROOT_INIT:
        return ""
    if module in {name.split(".")[0] for name in known if "." in name}:
        return module
    return module.rpartition(".")[0]


def _is_type_checking(test: ast.expr) -> bool:
    """`TYPE_CHECKING` ou `typing.TYPE_CHECKING` como condição de um `if`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


class _Imports(ast.NodeVisitor):
    """Coleta os imports de um módulo como `(nó, dentro_de_função)`, sem os blocos `TYPE_CHECKING`."""

    # pylint: disable=invalid-name  # os nomes `visit_*` são a API do ast.NodeVisitor

    def __init__(self) -> None:
        self.found: list[tuple[ast.Import | ast.ImportFrom, bool]] = []
        self._depth = 0

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def _function(self, node: ast.AST) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._function(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.found.append((node, self._depth > 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.found.append((node, self._depth > 0))


def _resolve_path(parts: list[str], known: frozenset[str]) -> str | None:
    """Nome do módulo para o caminho pontilhado sob `getbrolls` (`[]` é a raiz); `None` se não existe."""
    if not parts:
        return ROOT_INIT
    name = ".".join(parts)
    return name if name in known else None


def _from_targets(node: ast.ImportFrom, source: str, known: frozenset[str], unknown: list[str]) -> list[str]:
    """Módulos que um `from ... import ...` alcança, pelas regras de resolução do cabeçalho."""
    package = _package_of(source, known)
    package_parts = package.split(".") if package else []
    if node.level:
        base = package_parts[: len(package_parts) - (node.level - 1)] + (node.module.split(".") if node.module else [])
    else:
        text = node.module or ""
        if text != ROOT_PACKAGE and not text.startswith(ROOT_PACKAGE + "."):
            return []
        base = text.split(".")[1:]
    target = _resolve_path(base, known)
    if target is None:
        unknown.append(f"{source}: {'.' * node.level}{node.module or ''}")
        return []
    targets: list[str] = []
    is_package = target == ROOT_INIT or any(name.startswith(target + ".") for name in known)
    if is_package:
        prefix = [] if target == ROOT_INIT else [target]
        for alias in node.names:
            sub = _resolve_path([*prefix, alias.name], known)
            targets.append(sub if sub and alias.name != "*" else target)
    else:
        targets.append(target)
    if node.level == 1 and not node.module and package:
        targets.append(package)  # `from . import X` dentro de um subpacote: o `__init__` roda antes
    return targets


def _import_targets(node: ast.Import, known: frozenset[str], unknown: list[str], source: str) -> list[str]:
    """Módulos de um `import getbrolls.a.b`: o módulo e o pacote que o contém (R3)."""
    targets: list[str] = []
    for alias in node.names:
        parts = alias.name.split(".")
        if parts[0] != ROOT_PACKAGE or len(parts) == 1:
            continue
        name = ".".join(parts[1:])
        if name not in known:
            unknown.append(f"{source}: import {alias.name}")
            continue
        targets.append(name)
        if len(parts) > SUBPACKAGE_DEPTH:
            targets.append(parts[1])
    return targets


def build_edges(
    sources: Mapping[str, str] | None = None, dynamic: Mapping[str, tuple[str, ...]] | None = None
) -> tuple[EdgeKinds, list[str]]:
    """Grafo `(origem, destino) -> {"M", "F"}` e a lista de imports do pacote que não resolvem."""
    if sources is None:
        sources = {module_name(p): p.read_text(encoding="utf-8") for p in sorted(PACKAGE.rglob("*.py"))}
    known = frozenset(sources)
    edges: EdgeKinds = {}
    unknown: list[str] = []
    for source, text in sources.items():
        collector = _Imports()
        collector.visit(ast.parse(text))
        for node, in_function in collector.found:
            if isinstance(node, ast.Import):
                targets = _import_targets(node, known, unknown, source)
            else:
                targets = _from_targets(node, source, known, unknown)
            for target in targets:
                if target != source:
                    edges.setdefault((source, target), set()).add("F" if in_function else "M")
    for source, targets in (dynamic or {}).items():
        for target in targets:
            edges.setdefault((source, target), set()).add("F")
    return edges, unknown


# --- Dados -----------------------------------------------------------------------------------------------


def _lines(path: Path) -> Iterable[tuple[int, str]]:
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield number, line


def parse_layers(name: str, text: str) -> dict[str, tuple[int, bool]]:
    """`módulo camada [planned]` por linha -> `{módulo: (camada, planejado)}`."""
    found: dict[str, tuple[int, bool]] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if (
            len(fields) not in (LAYER_FIELDS, LAYER_FIELDS + 1)
            or not fields[1].isdigit()
            or (len(fields) > LAYER_FIELDS and fields[2] != "planned")
        ):
            raise AssertionError(f"{name}:{number}: linha de camada inválida: {line!r}")
        found[fields[0]] = (int(fields[1]), len(fields) > LAYER_FIELDS)
    return found


def load_layers() -> tuple[dict[str, tuple[int, bool]], list[str]]:
    """Todos os `layers*.txt`; devolve o mapa e as duplicatas entre arquivos."""
    merged: dict[str, tuple[int, bool]] = {}
    duplicates: list[str] = []
    for path in sorted(DATA.glob("layers*.txt")):
        for module, value in parse_layers(path.name, path.read_text(encoding="utf-8")).items():
            if module in merged:
                duplicates.append(f"{path.name}: {module} já está em outro arquivo de camadas")
            merged[module] = value
    return merged, duplicates


def parse_allow(name: str, text: str) -> dict[tuple[str, str, str], str]:
    """`M|F origem -> destino | motivo` por linha -> `{(tipo, origem, destino): motivo}`."""
    found: dict[tuple[str, str, str], str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, reason = line.partition("|")
        fields = head.split()
        if len(fields) != ALLOW_FIELDS or fields[0] not in ("M", "F") or fields[2] != "->" or not reason.strip():
            raise AssertionError(f"{name}:{number}: esperado 'M|F origem -> destino | motivo': {line!r}")
        found[(fields[0], fields[1], fields[3])] = reason.strip()
    return found


def parse_pairs(name: str, text: str) -> dict[tuple[str, str], str]:
    """`a b | motivo` (a < b) por linha -> `{(a, b): motivo}`."""
    found: dict[tuple[str, str], str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, reason = line.partition("|")
        fields = head.split()
        if len(fields) != PAIR_FIELDS or fields[0] >= fields[1] or not reason.strip():
            raise AssertionError(f"{name}:{number}: esperado 'a b | motivo' com a < b: {line!r}")
        found[(fields[0], fields[1])] = reason.strip()
    return found


def parse_ratchet(text: str) -> dict[str, int]:
    """`chave N` por linha; as chaves são exatamente `RATCHET_KEYS`."""
    found = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition(" ")
            found[key] = int(value)
    if sorted(found) != sorted(RATCHET_KEYS):
        raise AssertionError(f"ratchet.txt precisa das chaves {RATCHET_KEYS}")
    return found


def load_allow() -> tuple[dict[tuple[str, str, str], str], list[str]]:
    """Todos os `allow_*.txt` da pasta; duplicatas entre arquivos são devolvidas."""
    merged: dict[tuple[str, str, str], str] = {}
    duplicates: list[str] = []
    for path in sorted(DATA.glob("allow_*.txt")):
        for key, reason in parse_allow(path.name, path.read_text(encoding="utf-8")).items():
            if key in merged:
                duplicates.append(f"{path.name}: {key[0]} {key[1]} -> {key[2]} já está em outro arquivo")
            merged[key] = reason
    return merged, duplicates


def load_pairs() -> dict[tuple[str, str], str]:
    return parse_pairs("pairs_accepted.txt", (DATA / "pairs_accepted.txt").read_text(encoding="utf-8"))


# --- Regras (puras: recebem o grafo e os dados) ----------------------------------------------------------


class _Tarjan:
    """Componentes fortemente conexos (algoritmo de Tarjan) de um grafo dado como lista de adjacência."""

    def __init__(self, graph: dict[str, list[str]]) -> None:
        self.graph = graph
        self.index: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.components: list[list[str]] = []

    def run(self) -> list[list[str]]:
        for node in sorted(self.graph):
            if node not in self.index:
                self._visit(node)
        return self.components

    def _visit(self, node: str) -> None:
        self.index[node] = self.low[node] = len(self.index)
        self.stack.append(node)
        for child in sorted(self.graph[node]):
            if child not in self.index:
                self._visit(child)
                self.low[node] = min(self.low[node], self.low[child])
            elif child in self.stack:
                self.low[node] = min(self.low[node], self.index[child])
        if self.low[node] == self.index[node]:
            members = self.stack[self.stack.index(node) :]
            del self.stack[self.stack.index(node) :]
            if len(members) > 1:
                self.components.append(sorted(members))


def strongly_connected(edges: Iterable[Edge]) -> list[list[str]]:
    """Componentes fortemente conexos com mais de um módulo."""
    graph: dict[str, list[str]] = {}
    for src, dst in edges:
        graph.setdefault(src, []).append(dst)
        graph.setdefault(dst, [])
    return _Tarjan(graph).run()


def _module_edges(edges: EdgeKinds) -> set[Edge]:
    return {edge for edge, kinds in edges.items() if "M" in kinds}


def _in_cycle(edges: set[Edge], edge: Edge) -> bool:
    """A aresta `edge` fecha ciclo no grafo `edges` (os dois pontos no mesmo componente)."""
    return any(edge[0] in comp and edge[1] in comp for comp in strongly_connected(edges | {edge}))


def _stale_entries(
    edges: EdgeKinds,
    layers: Mapping[str, tuple[int, bool]],
    allow_m: set[Edge],
    allow_f: set[Edge],
) -> list[str]:
    """Entradas da allowlist que não valem mais: a aresta sumiu ou deixou de violar a regra."""
    module_edges = _module_edges(edges)
    stale = []
    for src, dst in sorted(allow_m):
        upward = (src, dst) in module_edges and layers[dst][0] > layers[src][0]
        if (src, dst) not in module_edges:
            stale.append(f"M {src} -> {dst}: a aresta de módulo não existe mais")
        elif not upward and not _in_cycle(module_edges - (allow_m - {(src, dst)}) - {(src, dst)}, (src, dst)):
            stale.append(f"M {src} -> {dst}: nem sobe de camada nem fecha ciclo; apague a entrada")
    for src, dst in sorted(allow_f):
        kinds = edges.get((src, dst), set())
        if "F" not in kinds or "M" in kinds or layers[dst][0] <= layers[src][0]:
            stale.append(f"F {src} -> {dst}: não é mais um import de função que sobe; apague a entrada")
    return stale


def layer_rules(
    edges: EdgeKinds,
    layers: Mapping[str, tuple[int, bool]],
    allow: Mapping[tuple[str, str, str], str],
) -> dict[str, list[str]]:
    """R1, R2a, R2b e as entradas velhas da allowlist; cada chave devolve as mensagens da regra."""
    out: dict[str, list[str]] = {"R1": [], "R2a": [], "R2b": [], "stale": []}
    module_edges = _module_edges(edges)
    allow_m = {(src, dst) for kind, src, dst in allow if kind == "M"}
    allow_f = {(src, dst) for kind, src, dst in allow if kind == "F"}
    for comp in strongly_connected(module_edges - allow_m):
        out["R1"].append("ciclo de import de módulo: " + " <-> ".join(comp))
    for src, dst in sorted(module_edges - allow_m):
        if layers[dst][0] > layers[src][0]:
            out["R2a"].append(f"{src} (L{layers[src][0]}) importa {dst} (L{layers[dst][0]}) no módulo, para cima")
    for (src, dst), kinds in sorted(edges.items()):
        if kinds == {"F"} and layers[dst][0] > layers[src][0] and (src, dst) not in allow_f:
            out["R2b"].append(f"{src} (L{layers[src][0]}) importa {dst} (L{layers[dst][0]}) em função, para cima")
    out["stale"] += _stale_entries(edges, layers, allow_m, allow_f)
    return out


def mutual_pairs(edges: EdgeKinds) -> set[tuple[str, str]]:
    """Pares `(a, b)` com a < b em que um importa o outro, seja qual for o tipo de aresta."""
    return {(a, b) for (a, b) in edges if a < b and (b, a) in edges}


def pair_rules(
    edges: EdgeKinds,
    pairs: Mapping[tuple[str, str], str],
    allow: Mapping[tuple[str, str, str], str],
) -> list[str]:
    """R6: par mútuo sem aceite, aceite velho e par com aresta de módulo nos dois sentidos."""
    mutual = mutual_pairs(edges)
    allow_m = {(src, dst) for kind, src, dst in allow if kind == "M"}
    messages = [f"par mútuo sem aceite em pairs_accepted.txt: {a} {b}" for a, b in sorted(mutual - set(pairs))]
    messages += [f"aceite velho (o par não se importa mais): {a} {b}" for a, b in sorted(set(pairs) - mutual)]
    for a, b in sorted(set(pairs) & mutual):
        both = {(a, b), (b, a)} <= _module_edges(edges) and not ({(a, b), (b, a)} & allow_m)
        if both:
            messages.append(f"{a} {b}: import de módulo nos dois sentidos é violação de R1 e não pode ser aceito")
    return messages


def hygiene_rules(modules: Iterable[str], layers: Mapping[str, tuple[int, bool]]) -> list[str]:
    """R5: módulo existente sem camada falha; camada de módulo ausente só passa se for `planned`."""
    present = set(modules)
    messages = [f"{name}: módulo sem camada em tests/import_rules/layers.txt" for name in sorted(present - set(layers))]
    for name, (_, planned) in sorted(layers.items()):
        if name not in present and not planned:
            messages.append(f"{name}: a camada existe mas o módulo não (marque `planned` ou apague a linha)")
    return messages


def ratchet_rules(counts: Mapping[str, int], ratchet: Mapping[str, int]) -> list[str]:
    """R5: o catraca só desce; contagem acima do teto falha."""
    return [
        f"{key}: {counts[key]} entradas, teto do catraca {ratchet[key]} (o catraca só desce)"
        for key in RATCHET_KEYS
        if counts[key] > ratchet[key]
    ]


def plugin_surface_violations(root: Path, names: Iterable[str] = ("examples", "plugins_official")) -> list[str]:
    """R4: arquivos de plugin que importam algo de `getbrolls` além de `getbrolls.sdk`."""
    bad: list[str] = []
    for name in names:
        for path in sorted((root / name).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                bad += [
                    f"{path.relative_to(root).as_posix()}: importa {target}"
                    for target in _absolute_getbrolls(node)
                    if target != "getbrolls.sdk"
                ]
    return bad


def _absolute_getbrolls(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names if a.name.split(".")[0] == ROOT_PACKAGE]
    if isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] == ROOT_PACKAGE:
        if node.module == ROOT_PACKAGE:
            return [f"getbrolls.{a.name}" for a in node.names]
        return [node.module or ""]
    return []


def imports_from_tests(sources: Mapping[str, str], test_modules: Iterable[str]) -> list[str]:
    """R4: módulos do pacote que importam (por nome absoluto) um módulo de `tests/`."""
    names = set(test_modules) - set(sys.stdlib_module_names)
    bad: list[str] = []
    for source, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            tops: list[str] = []
            if isinstance(node, ast.Import):
                tops = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                tops = [node.module.split(".")[0]]
            bad += [f"{source}: importa {top} de tests/" for top in tops if top in names]
    return bad


# --- Testes sobre o código real --------------------------------------------------------------------------


class RealGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = {module_name(p): p.read_text(encoding="utf-8") for p in sorted(PACKAGE.rglob("*.py"))}
        cls.edges, cls.unknown = build_edges(cls.sources)
        cls.layers, cls.layer_duplicates = load_layers()
        cls.allow, cls.allow_duplicates = load_allow()
        cls.pairs = load_pairs()

    def test_every_package_import_resolves_to_a_module(self):
        self.assertEqual([], self.unknown)

    def test_the_dynamic_table_matches_the_code(self):
        tree = ast.parse(self.sources["commands"])
        runners: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_TOOLCHAIN_RUNNERS" for t in node.targets):
                values = node.value.values if isinstance(node.value, ast.Dict) else []
                runners = {
                    v.value.removeprefix("getbrolls.")
                    for v in values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                }
        self.assertEqual(runners, set(DYNAMIC["commands"]))
        for target in runners:
            self.assertIn(target, self.sources)
            self.assertIn("run", self.sources[target])
        providers = ast.parse(self.sources["providers"])
        defined = {n.name for n in providers.body if isinstance(n, ast.FunctionDef)}
        builtin = next(
            n.value
            for n in providers.body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "BUILTIN_SEARCH"
        )
        self.assertIsInstance(builtin, ast.Dict)
        values = builtin.values if isinstance(builtin, ast.Dict) else []
        searches = {v.value for v in values if isinstance(v, ast.Constant)}
        self.assertLessEqual(searches, defined)

    def test_the_layer_map_is_consistent_and_covers_every_module(self):
        self.assertEqual([], self.layer_duplicates)
        self.assertEqual([], hygiene_rules(self.sources, self.layers))

    def test_r1_r2a_r2b_the_graph_follows_the_layers_with_only_the_listed_exceptions(self):
        result = layer_rules(self.edges, self.layers, self.allow)
        self.assertEqual([], self.allow_duplicates)
        self.assertEqual(
            {"R1": [], "R2a": [], "R2b": [], "stale": []}, result, "corrija o import; allowlist só com motivo"
        )

    def test_r6_every_mutual_pair_is_accepted_with_a_reason(self):
        self.assertEqual([], pair_rules(self.edges, self.pairs, self.allow))

    def test_the_ratchet_only_goes_down(self):
        ratchet = parse_ratchet((DATA / "ratchet.txt").read_text(encoding="utf-8"))
        counts = {
            "allow_module": sum(1 for key in self.allow if key[0] == "M"),
            "allow_function": sum(1 for key in self.allow if key[0] == "F"),
            "pairs": len(self.pairs),
        }
        self.assertEqual([], ratchet_rules(counts, ratchet))

    def test_r4_plugins_import_only_the_sdk_and_the_package_never_imports_tests(self):
        self.assertEqual([], plugin_surface_violations(ROOT))
        test_modules = [p.stem for p in (ROOT / "tests").glob("*.py")]
        self.assertEqual([], imports_from_tests(self.sources, test_modules))

    def test_r3_the_sdk_package_edges_are_in_the_graph(self):
        self.assertIn("M", self.edges[("sdk.contracts", "sdk")])
        self.assertIn("M", self.edges[("sdk.registry", "sdk")])


# --- A guarda não pode ser vazia: grafos sintéticos ------------------------------------------------------


def synthetic(**modules: str):
    sources = {name.replace("__", "."): text for name, text in modules.items()}
    return build_edges(sources)


LAYERS = {"a": (0, False), "b": (1, False), "c": (2, False), "sdk": (3, False), "sdk.x": (0, False)}


class ResolverTests(unittest.TestCase):
    def test_relative_and_absolute_imports_become_edges(self):
        edges, unknown = synthetic(a="", b="from .a import x\nimport getbrolls.a\n", c="from . import a, b\n")
        self.assertEqual([], unknown)
        self.assertEqual({("b", "a"), ("c", "a"), ("c", "b")}, set(edges))

    def test_function_level_and_module_level_kinds_are_kept_apart(self):
        edges, _ = synthetic(
            a="", b="import getbrolls.a\ndef f():\n    from .a import y\n", c="def g():\n    from . import a\n"
        )
        self.assertEqual({"M", "F"}, edges[("b", "a")])
        self.assertEqual({"F"}, edges[("c", "a")])

    def test_type_checking_blocks_are_skipped(self):
        edges, _ = synthetic(
            a="",
            b="from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .a import X\nelse:\n    from . import c\n",
            c="",
        )
        self.assertEqual({("b", "c")}, set(edges))

    def test_sdk_package_edges(self):
        edges, _ = synthetic(
            sdk="from .x import y\n", sdk__x="from . import z\n", sdk__z="", a="import getbrolls.sdk.z\n"
        )
        self.assertIn(("sdk.x", "sdk.z"), edges)
        self.assertIn(("sdk.x", "sdk"), edges)  # `from . import X` dentro do subpacote
        self.assertIn(("a", "sdk.z"), edges)
        self.assertIn(("a", "sdk"), edges)  # `import getbrolls.sdk.z` carrega o `__init__`

    def test_an_import_of_a_missing_module_is_reported(self):
        _, unknown = synthetic(a="from .ghost import x\n")
        self.assertEqual(["a: .ghost"], unknown)


class RuleTests(unittest.TestCase):
    def rules(self, edges, allow=None):
        return layer_rules(edges, LAYERS, allow or {})

    def test_an_upward_module_import_fails_until_it_is_allowlisted(self):
        edges = {("a", "b"): {"M"}}
        self.assertEqual(1, len(self.rules(edges)["R2a"]))
        allow = {("M", "a", "b"): "reason"}
        self.assertEqual({"R1": [], "R2a": [], "R2b": [], "stale": []}, self.rules(edges, allow))

    def test_a_cycle_fails_and_an_allowlisted_edge_breaks_it(self):
        edges = {("a", "b"): {"M"}, ("b", "a"): {"M"}}
        self.assertEqual(1, len(self.rules(edges)["R1"]))
        self.assertEqual([], self.rules(edges, {("M", "a", "b"): "reason"})["R1"])

    def test_an_upward_function_import_needs_a_deferred_seam_entry(self):
        edges = {("a", "c"): {"F"}, ("c", "a"): {"F"}}
        self.assertEqual(1, len(self.rules(edges)["R2b"]))
        self.assertEqual([], self.rules(edges, {("F", "a", "c"): "lazy lookup"})["R2b"])

    def test_a_downward_function_import_is_free(self):
        self.assertEqual([], self.rules({("c", "a"): {"F"}})["R2b"])

    def test_stale_allowlist_entries_fail(self):
        edges = {("c", "a"): {"M"}}
        stale = self.rules(edges, {("M", "c", "a"): "r", ("M", "a", "b"): "r", ("F", "c", "a"): "r"})["stale"]
        self.assertEqual(3, len(stale))

    def test_a_pair_needs_acceptance_and_a_stale_acceptance_fails(self):
        edges = {("a", "c"): {"F"}, ("c", "a"): {"M"}}
        self.assertEqual(["par mútuo sem aceite em pairs_accepted.txt: a c"], pair_rules(edges, {}, {}))
        self.assertEqual([], pair_rules(edges, {("a", "c"): "reason"}, {}))
        self.assertEqual(1, len(pair_rules({}, {("a", "c"): "reason"}, {})))

    def test_a_pair_with_module_imports_both_ways_cannot_be_accepted(self):
        edges = {("a", "b"): {"M"}, ("b", "a"): {"M"}}
        self.assertEqual(1, len(pair_rules(edges, {("a", "b"): "reason"}, {})))
        self.assertEqual([], pair_rules(edges, {("a", "b"): "reason"}, {("M", "a", "b"): "r"}))

    def test_an_unmapped_module_fails_and_a_planned_absent_one_does_not(self):
        layers = {"a": (0, False), "later": (1, True), "gone": (1, False)}
        messages = hygiene_rules({"a", "new"}, layers)
        self.assertEqual(2, len(messages))
        self.assertTrue(any(m.startswith("new:") for m in messages))
        self.assertTrue(any(m.startswith("gone:") for m in messages))

    def test_a_planned_module_that_appears_is_checked_like_any_other(self):
        layers = {"a": (0, False), "later": (1, True)}
        self.assertEqual([], hygiene_rules({"a", "later"}, layers))
        edges, _ = synthetic(a="from . import later\n", later="")
        self.assertEqual(1, len(layer_rules(edges, layers, {})["R2a"]))

    def test_the_ratchet_refuses_growth(self):
        counts = {"allow_module": 3, "allow_function": 2, "pairs": 1}
        self.assertEqual([], ratchet_rules(counts, {"allow_module": 3, "allow_function": 2, "pairs": 1}))
        self.assertEqual(1, len(ratchet_rules(counts, {"allow_module": 2, "allow_function": 2, "pairs": 1})))

    def test_tarjan_finds_only_real_components(self):
        found = strongly_connected({("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")})
        self.assertEqual([["a", "b", "c"]], found)

    def test_a_plugin_that_imports_the_core_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "examples"
            plugin.mkdir()
            (plugin / "ok.py").write_text("from getbrolls.sdk import PluginError\nimport json\n", encoding="utf-8")
            (plugin / "bad.py").write_text(
                "from getbrolls.ledger import Ledger\nfrom getbrolls import runtime\n", encoding="utf-8"
            )
            found = plugin_surface_violations(Path(tmp), ("examples",))
        self.assertEqual(
            ["examples/bad.py: importa getbrolls.ledger", "examples/bad.py: importa getbrolls.runtime"], found
        )

    def test_the_package_importing_a_test_helper_is_refused(self):
        found = imports_from_tests({"x": "import _isolation\nfrom os import path\n"}, ["_isolation", "test_x"])
        self.assertEqual(["x: importa _isolation de tests/"], found)

    def test_the_data_parsers_refuse_malformed_lines(self):
        for parse, line in (
            (parse_layers, "mod x"),
            (parse_allow, "M a -> b"),
            (parse_allow, "X a -> b | r"),
            (parse_pairs, "b a | r"),
        ):
            with self.subTest(line=line), self.assertRaises(AssertionError):
                parse("t", line)


if __name__ == "__main__":
    unittest.main()

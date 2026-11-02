"""Pureza dos plugins oficiais: a ponte roda sem caixa de areia, então um teste torna a promessa verdadeira.

Um plugin oficial (`plugins_official/**/*.py`) só importa os módulos de `MODULE_API` e, de cada um, só os
nomes listados ali (por `import m` + `m.nome` ou por `from m import nome`); um módulo nunca aparece solto
(`t = typing`) nem com apelido (`import json as j`), porque `typing.sys.modules['os']` ou `json.codecs.open`
alcançam o que o módulo importou por dentro. Do SDK só entram os nomes públicos que não montam registro
(`getbrolls.sdk.__all__` menos `get_registry`). Nunca há import relativo, nome que abre arquivo, roda código ou
alcança objetos por nome (`FORBIDDEN_NAMES`), nome ou atributo dunder (`().__class__.__bases__`), nem
`.format`/`.format_map` (que alcançam atributos por texto). Também nunca há atributo de frame ou de código
(`gi_frame`, `f_builtins`, `tb_frame`, `co_*`, `with_traceback`), que devolve os `open` e `__import__` reais, nem
atributo que comece por `_` (`api._registry`, `api._resolve_roots()`), seja como `x.nome`, `case C(nome=...)` ou
`getattr`/`hasattr`/`setattr`/`delattr` com nome constante; esses chamados com nome calculado também são recusados.
O objeto `api` de `register` (e todo apelido dele que a AST enxerga) é uma LISTA DE PERMISSÃO, não uma lista de
proibição: só os atributos de `API_ALLOWLIST` (os que `plugin.py` usa hoje) podem ser lidos nele, e ele nunca é
passado adiante (argumento, lista, retorno), porque aí o que o recebe leria o que quisesse (`api.data_dir` devolve um
`Path` que escreve). O código é lido pelos mesmos bytes que o loader compila (`ast.parse(path.read_bytes())`), e todo
BOM ou linha de codificação PEP 263 é recusado, porque mudaria o texto que roda. `register` só pode ser um
`def`/`async def` simples (sem decorador, com um único parâmetro posicional) e nenhuma outra coisa liga esse nome,
porque é o que o loader chama com o `api` e o que o checador sabe rastrear. O teste de si mesmo prova que cada fuga
conhecida é pega e que o `plugin.py` real passa.
"""

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

import getbrolls.sdk
from getbrolls.sdk.official_pins import PINS

OFFICIAL = ROOT / "plugins_official"
MANIFEST = "getbrolls-plugin.json"
MODULE_API = {
    "json": frozenset({"dumps", "loads", "JSONDecodeError"}),
    "re": frozenset(
        {
            "compile",
            "match",
            "search",
            "fullmatch",
            "sub",
            "subn",
            "split",
            "findall",
            "finditer",
            "escape",
            "error",
            "Pattern",
            "Match",
            "A",
            "ASCII",
            "I",
            "IGNORECASE",
            "M",
            "MULTILINE",
            "S",
            "DOTALL",
            "U",
            "UNICODE",
            "X",
            "VERBOSE",
        }
    ),
    "textwrap": frozenset({"dedent", "indent", "fill", "wrap", "shorten"}),
    "unicodedata": frozenset({"normalize", "is_normalized", "category", "combining", "name"}),
    "typing": frozenset(
        {
            "Any",
            "Callable",
            "ClassVar",
            "Dict",
            "Final",
            "Iterable",
            "Iterator",
            "List",
            "Literal",
            "Mapping",
            "NamedTuple",
            "Optional",
            "Sequence",
            "Set",
            "Tuple",
            "TypedDict",
            "Union",
            "cast",
        }
    ),
    "dataclasses": frozenset(
        {
            "dataclass",
            "field",
            "fields",
            "asdict",
            "astuple",
            "replace",
            "is_dataclass",
            "FrozenInstanceError",
            "MISSING",
        }
    ),
}
ALLOWED_MODULES = frozenset(MODULE_API)
# a literal on purpose: `getbrolls.sdk.__all__` growing must not widen what a bridge may import without a review
# (`test_the_sdk_names_are_a_reviewed_list` ties the two together). `get_registry` is the one name it never gets
SDK_NAMES = frozenset(
    {
        "CommandContext",
        "CommandSpec",
        "ExportResult",
        "Exporter",
        "ExporterSpec",
        "MediaRequest",
        "PluginError",
        "Preset",
        "Provider",
        "ProviderCapabilities",
        "RESOLVER_KINDS",
        "Resolver",
        "ResolverHit",
        "ResolverSpec",
        "Route",
        "RouteResult",
        "SDK_API",
    }
)
FORBIDDEN_NAMES = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
        "help",
    }
)
FORBIDDEN_ATTRIBUTES = frozenset({"format", "format_map"})

# a generator, coroutine, async generator or traceback hands out its frame, and the frame hands out the real
# builtins and globals (`__import__("os")`, `open`), so no name that starts a frame or code attribute is allowed
FRAME_ATTRIBUTE = re.compile(r"(f|gi|cr|ag|tb|co)_")
FRAME_ATTRIBUTES = frozenset({"with_traceback"})
# a BOM of any codec, and a PEP 263 coding line (the regex of the PEP) on either of the first two lines
BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
CODING_LINE = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*[-_.a-zA-Z0-9]+")
# what the manifest door accepts as `entry` (`manifest.ENTRY_RE`): a `.py` file at the root of the plugin folder
ENTRY = re.compile(r"[A-Za-z0-9_]{1,64}\.py")
# the allowlist of what the bridge may read on the `api` it receives (and on any alias of it). It is exactly what
# the official plugins use today (`test_the_allowlist_is_exactly_what_the_bridge_reads` derives it from every pinned
# entry, not from one file); every other member of `PluginApi` reads disk, network or the environment, or returns an
# object (`data_dir` is a `Path`, `_registry` is the whole registry) whose methods do
API_ALLOWLIST = frozenset({"environment"})
# the name `api` is always treated as the object; so is the first parameter of any `register` function
API_NAME = "api"
REGISTER_NAME = "register"
# these reach an attribute by a text, so a computed text (`'_' + 'registry'`) can never be proven harmless
GETATTR_LIKE = frozenset({"getattr", "hasattr", "setattr", "delattr"})
FRAME_ESCAPES = (
    "def g():\n    yield 1\ng().gi_frame.f_builtins['open']",
    "def g():\n    yield 1\ng().gi_frame.f_builtins['__import' + '__']('os')",
    "def g():\n    yield 1\ng().gi_frame.f_globals",
    "def g():\n    yield 1\ng().gi_frame.f_back",
    "def g():\n    yield 1\ng().gi_frame.f_locals",
    "def g():\n    yield 1\ng().gi_code",
    "async def c():\n    pass\nc().cr_frame.f_builtins",
    "async def c():\n    pass\nc().cr_code.co_consts",
    "async def a():\n    yield 1\na().ag_frame.f_builtins",
    "async def a():\n    yield 1\na().ag_code",
    "try:\n    1 / 0\nexcept ZeroDivisionError as e:\n    e.with_traceback(None)",
    "try:\n    1 / 0\nexcept ZeroDivisionError as e:\n    e.__traceback__.tb_frame.f_builtins",
    "try:\n    1 / 0\nexcept ZeroDivisionError as e:\n    e.__traceback__.tb_next",
    "match g():\n    case object(gi_frame=f):\n        pass",
    "match g():\n    case object(__class__=c):\n        pass",
)
API_ESCAPES = (
    "api.data_dir.parent.parent.joinpath('x').write_text('y')",
    "api.data_dir.joinpath('x').open('w')",
    "api.data_dir",
    "api.config()",
    "api.env('HOME')",
    "api.get_json('https://example.invalid/')",
    "api.download('https://example.invalid/', 'x')",
    "api.local_file('x')",
    "api.cli_argv()",
    "match api:\n    case object(data_dir=d):\n        pass",
    "match api:\n    case object(environment=e):\n        pass",
)
PRIVATE_ESCAPES = (
    "api._registry.provider('youtube').search('x')",
    "api._registry.add_provider(None)",
    "api._registry.remove_owner('x')",
    "api._registry.route('x').prepare",
    "api._manifest['permissions']",
    "api._resolve_roots()",
    "api._local_source('/etc/hosts')",
    "api._own('environments', 'x')",
    "api._data_parts",
    "a = api\na._registry",
    "a = api\nb = a\nb._manifest",
    "a, b = api, api\na._registry",
    "def register(p):\n    p._registry.remove_owner('x')",
    "def register(p):\n    q = p\n    q._resolve_roots()",
    "getattr(api, '_registry')",
    "getattr(api, '_' + 'registry')",
    "getattr(api, name)",
    "hasattr(api, '_registry')",
    "hasattr(api, name)",
    "hasattr(obj, name)",
    "hasattr(obj, '_x')",
    "hasattr(obj)",
    "setattr(api, '_x', 1)",
    "delattr(api, '_registry')",
    "x._private",
    "x._private = 1",
    "x.__private",
    "match g():\n    case object(_registry=r):\n        pass",
    "match g():\n    case object(__dict__=d):\n        pass",
)
OFF_LIST_API_ESCAPES = (
    "def register(p):\n    p.provider(None)",
    "def register(p):\n    p.permissions",
    "def register(p):\n    q = p\n    q.config()",
    "api.provider(None)",
    "api.anything_else",
    "a = api\na.anything_else",
    "def register(p):\n    helper(p)",
    "def register(p):\n    return [p]",
    "api.environment('x', 1, 2, 'd')\nh = [api]",
)
REGISTER_REBINDINGS = (
    "register = lambda p: p.environment('x', 1, 2, 'd')",
    "def helper(p):\n    p.environment('x', 1, 2, 'd')\nregister = helper",
    "register: int = 1",
    "register += 1",
    "register, other = 1, 2",
    "del register",
    "(register := 1)",
    "for register in []:\n    pass",
    "with open_it as register:\n    pass",
    "async def f():\n    async with open_it as register:\n        pass",
    "try:\n    pass\nexcept Exception as register:\n    pass",
    "from json import loads as register",
    "import json as register",
    "class register:\n    pass",
    "def f(register):\n    pass",
    "match x:\n    case register:\n        pass",
    "match x:\n    case [1, *register]:\n        pass",
    "match x:\n    case {'a': 1, **register}:\n        pass",
    "[1 for register in []]",
    "@wrap\ndef register(api):\n    api.environment('x', 1, 2, 'd')",
    "@wrap\nasync def register(api):\n    api.environment('x', 1, 2, 'd')",
    "@wrap(1)\n@other\ndef register(api):\n    pass",
    "def register(*a):\n    a[0]._registry",
    "def register(**k):\n    pass",
    "def register(*, api):\n    api.environment('x', 1, 2, 'd')",
    "def register():\n    pass",
    "async def register(*a):\n    pass",
    "def register(api, other):\n    pass",
    "def register(api, *rest):\n    pass",
    "def register(api, **rest):\n    pass",
    "def register(api, *, key=1):\n    pass",
)
# the loader compiles the BYTES of the entry, so a BOM or a PEP 263 line can make the checker and the loader read
# different text; the shipped plugin has neither, and none is allowed
ENCODING_ESCAPES = (
    b"\xef\xbb\xbfx = 1\n",
    b"\xff\xfex\x00 \x00=\x00 \x001\x00\n\x00",
    b"\xfe\xff\x00x\x00 \x00=\x00 \x001\x00\n",
    b"# -*- coding: latin-1 -*-\nx = 1\n",
    b"# coding=cp037\nx = 1\n",
    b"#!/usr/bin/env python\n# vim: set fileencoding=utf-8 :\nx = 1\n",
    b"# coding: utf-8\nx = 1\n",
    b"\n# coding: ascii\nx = 1\n",
    b"  \t# coding: latin-1\rx = 1\n",
    "# coding: utf-8\nx = 1\n",
)
ALLOWED_ENCODING_USES = (
    b"x = 1\n",
    b"# nothing special here\nx = 1\n",
    b"x = 1\ny = 2\n# coding: utf-8\n",
    b"\n\n# coding: utf-8\nx = 1\n",
    "x = 'cafe\u0301'\n".encode("utf-8"),
    b'"""A docstring that says coding: latin-1."""\nx = 1\n',
)
ALLOWED_API_USES = (
    "def register(api):\n    api.environment('hyperframes', package, readback, 'd')",
    "def register(p):\n    p.environment('x', 1, 2, 'd')\n    p.environment('y', 1, 2, 'd')",
    "def register(p):\n    q = p\n    q.environment('x', 1, 2, 'd')",
    "def package(_handoff):\n    return {'files': {}}",
    "x = object()\nx.name",
    "def register(api):\n    api.environment('x', 1, 2, 'd')",
    "async def register(api):\n    api.environment('x', 1, 2, 'd')",
)


def _dunder(name):
    return name.startswith("__") and name.endswith("__")


def _register_bindings(node):
    """The names `node` binds when it is not a `def`: assignment, loop, `with`, `except`, `import`, walrus, pattern."""
    if isinstance(node, ast.Name):
        return [node.id] if isinstance(node.ctx, (ast.Store, ast.Del)) else []
    if isinstance(node, (ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return [node.name]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [alias.asname or alias.name.split(".")[0] for alias in node.names]
    if isinstance(node, ast.MatchMapping):
        return [node.rest]
    return []


def _register_definition_problems(node):
    """A `def register` must stay the function the loader calls with the api: no decorator (it would rebind the
    name to an untracked function) and exactly one positional parameter (`_register_parameters` tracks only that)."""
    if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == REGISTER_NAME):
        return []
    args = node.args
    problems = [f"{REGISTER_NAME} com decorador"] if node.decorator_list else []
    if len(args.posonlyargs) + len(args.args) != 1 or args.vararg or args.kwarg or args.kwonlyargs:
        problems.append(f"{REGISTER_NAME} sem exatamente um parâmetro posicional")
    return problems


def _register_problems(node):
    """`register` is the one name the core calls, and `_register_parameters` only models a plain `def register`."""
    return _register_definition_problems(node) + [
        f"{REGISTER_NAME} religado por outra coisa que não def"
        for name in _register_bindings(node)
        if name == REGISTER_NAME
    ]


def _import_problems(node):
    if isinstance(node, ast.Import):
        return [
            f"import {alias.name}" if alias.name not in ALLOWED_MODULES else f"import {alias.name} as {alias.asname}"
            for alias in node.names
            if alias.name not in ALLOWED_MODULES or alias.asname
        ]
    if node.level:
        return [f"import relativo (nível {node.level})"]
    if node.module == "getbrolls.sdk":
        return [f"from getbrolls.sdk import {alias.name}" for alias in node.names if alias.name not in SDK_NAMES]
    if node.module not in ALLOWED_MODULES:
        return [f"from {node.module} import ..."]
    return [
        f"from {node.module} import {alias.name}" for alias in node.names if alias.name not in MODULE_API[node.module]
    ]


def _attribute_problems(name):
    """Why the attribute `name` is refused, whether it is read as `x.name` or matched as `case C(name=...)`."""
    if _dunder(name) or name in FORBIDDEN_ATTRIBUTES:
        return [f"atributo .{name}"]
    if name.startswith("_"):
        return [f"atributo .{name} (nome privado)"]
    if FRAME_ATTRIBUTE.match(name) or name in FRAME_ATTRIBUTES:
        return [f"atributo .{name} (alcança o frame e os builtins reais)"]
    return []


def _module_attribute_problem(node):
    """O atributo de `m.nome` quando `m` é um módulo permitido e `nome` não está na lista dele."""
    if isinstance(node.value, ast.Name) and node.value.id in MODULE_API and node.attr not in MODULE_API[node.value.id]:
        return [f"atributo {node.value.id}.{node.attr} fora da lista do módulo"]
    return []


def _register_parameters(tree):
    """The name of the first parameter of every function called `register`: the object the core hands in."""
    return {
        (node.args.posonlyargs + node.args.args)[0].arg
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "register"
        and (node.args.posonlyargs or node.args.args)
    }


def _alias_of(node, names):
    """The names that `node` binds to one of `names` (`a = api`, `a = b = api`, `(a := api)`), else nothing."""
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in names:
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Name) and node.value.id in names:
        return {node.target.id} if isinstance(node.target, ast.Name) else set()
    if isinstance(node, ast.NamedExpr) and isinstance(node.value, ast.Name) and node.value.id in names:
        return {node.target.id}
    return set()


def _api_names(tree):
    """`api`, the `register` parameter and every alias of them the tree binds, until no new alias shows up."""
    names = {API_NAME} | _register_parameters(tree)
    while True:
        more = {alias for node in ast.walk(tree) for alias in _alias_of(node, names)} - names
        if not more:
            return names
        names |= more


def _api_problems(tree):
    """`[(linha, problema)]` for every use of the `api` object (or an alias) other than `api.<allowed name>`."""
    names = _api_names(tree)
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id in names and isinstance(node.ctx, ast.Load)):
            continue
        parent = parents.get(id(node))
        if isinstance(parent, ast.Attribute) and parent.value is node:
            if parent.attr not in API_ALLOWLIST:
                found.append((node.lineno, f"atributo {node.id}.{parent.attr} fora da lista do api"))
        elif not _alias_of(parent, names):  # the only Name children of an assignment are its targets and its value
            found.append((node.lineno, f"{node.id} (o api) passado adiante ou usado solto"))
    return found


def _getattr_like_problems(node):
    """`getattr(x, name)` and its kin: refused unless the name is a constant text that does not start with `_`."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in GETATTR_LIKE):
        return []
    name = node.args[1] if len(node.args) > 1 else None
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return [f"{node.func.id} com nome calculado"]
    if name.value.startswith("_"):
        return [f"{node.func.id} {name.value!r} (nome privado)"]
    return []


def encoding_problems(source):
    """`[(linha, problema)]` when `source` (bytes or text) has a BOM or a PEP 263 coding line; else `[]`.

    The loader compiles BYTES, so either one changes how they are decoded and the checker could parse other text
    than the loader runs. The coding line counts on either of the first two lines, like the PEP."""
    raw = source.encode("utf-8") if isinstance(source, str) else source
    if raw.startswith(BOMS):
        return [(1, "BOM (o loader decodifica os bytes por ele)")]
    return [
        (number, "linha de codificação PEP 263 (o loader decodifica os bytes por ela)")
        for number, line in enumerate(raw.splitlines()[:2], 1)
        if CODING_LINE.match(line)
    ]


def official_sources(root, pins):
    """`({id: [arquivos]}, [problemas])`: what the loader can run for each pinned plugin, never a bare glob.

    The loader runs `folder / manifest["entry"]` whatever its name, so each pinned folder contributes that entry
    and every `.py` under it; a pinned id that contributes nothing, an unreadable manifest and an entry that is
    not a `.py` file at the root of the folder are problems (the manifest door refuses such entries too)."""
    files, problems = {}, []
    for ident in sorted(pins):
        folder = root / ident
        if not folder.is_dir():
            problems.append(f"{ident}: sem pasta em plugins_official/")
            continue
        found = set(folder.rglob("*.py"))
        try:
            entry = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))["entry"]
        except (OSError, ValueError, KeyError, TypeError):
            problems.append(f"{ident}: manifesto ilegível")
            entry = None
        if entry is not None:
            if not (isinstance(entry, str) and ENTRY.fullmatch(entry)):
                problems.append(f"{ident}: entry {entry!r} não é um arquivo .py na raiz da pasta")
            if isinstance(entry, str) and "/" not in entry and "\\" not in entry and (folder / entry).is_file():
                found.add(folder / entry)  # whatever its name, it is what would run, so it is scanned
            else:
                problems.append(f"{ident}: entry {entry!r} não é um arquivo")
        if found:
            files[ident] = sorted(found)
        else:
            problems.append(f"{ident}: nada para escanear")
    return files, problems


def check_file(path):
    """`impurities` of the exact bytes of `path`, the ones the loader compiles."""
    return impurities(path.read_bytes())


def impurities(source):
    """`[(linha, problema)]` do código `source`; vazio quando ele é puro."""
    raw = source.encode("utf-8") if isinstance(source, str) else source
    found = encoding_problems(raw)
    if found:  # refused whole: the text the loader would compile may not be the text parsed here
        return found
    tree = ast.parse(raw)
    # a module name is only ever the base of `m.nome`; any other use hands the module itself around
    based = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.MatchClass):  # `case C(name=x)` reads the attribute `name` without an Attribute node
            found += [(node.lineno, problem) for name in node.kwd_attrs for problem in _attribute_problems(name)]
        found += [(getattr(node, "lineno", 1), problem) for problem in _register_problems(node)]
        if not isinstance(node, (ast.stmt, ast.expr)):
            continue
        problems = []
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            problems = _import_problems(node)
        elif isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or _dunder(node.id)):
            problems = [f"nome {node.id}"]
        elif isinstance(node, ast.Name) and node.id in MODULE_API and id(node) not in based:
            problems = [f"módulo {node.id} solto"]
        elif isinstance(node, ast.Attribute):
            problems = _attribute_problems(node.attr) + _module_attribute_problem(node)
        problems += _getattr_like_problems(node)
        found += [(node.lineno, problem) for problem in problems]
    return sorted(found + _api_problems(tree))


class PurityTests(unittest.TestCase):
    def test_official_plugins_are_pure(self):
        files, problems = official_sources(OFFICIAL, PINS)
        self.assertEqual([], problems)
        self.assertEqual(set(PINS), set(files))
        sources = [path for paths in files.values() for path in paths]
        self.assertTrue(sources)
        found = {path.relative_to(ROOT).as_posix(): check_file(path) for path in sources}
        self.assertEqual({}, {name: problems for name, problems in found.items() if problems})

    def test_the_scan_follows_the_pins_and_the_manifest_entries(self):
        """The set of scanned files is what the loader can run: the entry of each pinned plugin, plus any `.py`."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(({}, ["ghost: sem pasta em plugins_official/"]), official_sources(root, {"ghost": "x"}))
            ghost = root / "ghost"
            ghost.mkdir()
            unreadable = ["ghost: manifesto ilegível", "ghost: nada para escanear"]
            self.assertEqual(unreadable, official_sources(root, {"ghost": "x"})[1])
            (ghost / MANIFEST).write_text("{not json", encoding="utf-8")
            self.assertEqual(unreadable, official_sources(root, {"ghost": "x"})[1])
            (ghost / MANIFEST).write_text(json.dumps({"entry": "plugin.txt"}), encoding="utf-8")
            (ghost / "plugin.txt").write_text("x = 1\n", encoding="utf-8")
            files, problems = official_sources(root, {"ghost": "x"})
            self.assertEqual(["ghost: entry 'plugin.txt' não é um arquivo .py na raiz da pasta"], problems)
            self.assertEqual([ghost / "plugin.txt"], files["ghost"])  # still scanned: it is what would run
            for entry in ("../plugin.py", "sub/plugin.py", 3, None):
                (ghost / MANIFEST).write_text(json.dumps({"entry": entry}), encoding="utf-8")
                self.assertTrue(official_sources(root, {"ghost": "x"})[1], entry)
            (ghost / MANIFEST).write_text(json.dumps({"entry": "plugin.py"}), encoding="utf-8")
            missing = ["ghost: entry 'plugin.py' não é um arquivo", "ghost: nada para escanear"]
            self.assertEqual(missing, official_sources(root, {"ghost": "x"})[1])
            (ghost / "plugin.py").write_text("x = 1\n", encoding="utf-8")
            (ghost / "sub").mkdir()
            (ghost / "sub" / "extra.py").write_text("x = 2\n", encoding="utf-8")
            files, problems = official_sources(root, {"ghost": "x"})
            self.assertEqual([], problems)
            self.assertEqual([ghost / "plugin.py", ghost / "sub" / "extra.py"], files["ghost"])
            files, problems = official_sources(root, {"ghost": "x", "other": "y"})
            self.assertEqual(["other: sem pasta em plugins_official/"], problems)
            self.assertEqual({"ghost"}, set(files))

    def test_every_pinned_plugin_contributes_a_scanned_entry(self):
        files, problems = official_sources(OFFICIAL, PINS)
        self.assertEqual([], problems)
        for ident in PINS:
            with self.subTest(plugin=ident):
                entry = json.loads((OFFICIAL / ident / MANIFEST).read_text(encoding="utf-8"))["entry"]
                self.assertIn(OFFICIAL / ident / entry, files[ident])

    def test_the_shipped_sources_have_no_bom_and_no_coding_line(self):
        files, _problems = official_sources(OFFICIAL, PINS)
        for path in (path for paths in files.values() for path in paths):
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual([], encoding_problems(path.read_bytes()))

    def test_the_checker_parses_the_bytes_the_loader_compiles(self):
        for raw in ENCODING_ESCAPES:
            with self.subTest(raw=raw):
                self.assertTrue(impurities(raw), raw)
        for raw in ALLOWED_ENCODING_USES:
            with self.subTest(allowed=raw):
                self.assertEqual([], impurities(raw), raw)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plugin.py"
            path.write_bytes(b"# coding: latin-1\nx = '\xe9'\n")  # not UTF-8 text: only the bytes tell what runs
            self.assertTrue(check_file(path))
            path.write_bytes(b"x = 1\n")
            self.assertEqual([], check_file(path))

    def test_register_is_only_ever_defined_by_def(self):
        for snippet in REGISTER_REBINDINGS:
            with self.subTest(snippet=snippet):
                self.assertTrue(impurities(snippet), snippet)

    def test_the_sdk_names_are_a_reviewed_list(self):
        """A new public name of `getbrolls.sdk` reaches the bridge only when this literal is edited and reviewed."""
        self.assertEqual(SDK_NAMES | {"get_registry"}, frozenset(getbrolls.sdk.__all__))

    def test_the_allowlist_is_exactly_what_the_bridge_reads(self):
        """Neither a name the bridge does not use stays allowed, nor does it use a name outside the list."""
        files, _problems = official_sources(OFFICIAL, PINS)
        used = set()
        for path in (path for paths in files.values() for path in paths):
            tree = ast.parse(path.read_bytes())
            names = _api_names(tree)
            used |= {
                node.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in names
            }
        self.assertEqual(API_ALLOWLIST, used)

    def test_the_checker_catches_every_known_escape(self):
        hostile = (
            "import os",
            "import os.path",
            "import subprocess",
            "import builtins",
            "import importlib",
            "from pathlib import Path",
            "from os import path",
            "from . import vizinho",
            "from .vizinho import x",
            "import getbrolls.sdk.loader",
            "from getbrolls.sdk import get_registry",
            "from getbrolls.sdk import loader",
            "from getbrolls import effects",
            "open('x')",
            "f = open",
            "__import__('os')",
            "exec('1')",
            "eval('1')",
            "compile('1', 'x', 'exec')",
            "getattr(object, 'x')",
            "vars()",
            "globals()",
            "().__class__.__bases__[0].__subclasses__()",
            "__builtins__",
            "'{0.__class__}'.format(1)",
            "'{a}'.format_map({})",
            "breakpoint()",
            "typing.sys.modules['os'].system('x')",
            "json.codecs.open('x')",
            "re.enum.sys.modules['os']",
            "textwrap.re.sys",
            "from typing import sys",
            "from json import codecs",
            "from json import *",
            "import json as j",
            "import json\nt = json\nt.codecs",
            "import json\nx = [json]",
            "import typing\ntyping.io",
            *FRAME_ESCAPES,
            *API_ESCAPES,
            *PRIVATE_ESCAPES,
            *OFF_LIST_API_ESCAPES,
        )
        for snippet in hostile:
            with self.subTest(snippet=snippet):
                self.assertTrue(impurities(snippet), snippet)
        allowed = (
            "import json\nimport re\nimport textwrap\nimport unicodedata\n"
            "from typing import Any, Optional\nfrom dataclasses import dataclass\n"
            "from getbrolls.sdk import PluginError\n"
            "x = f'{json.dumps([1])}'\ny = re.compile('a').match('a')\n"
            "z = textwrap.dedent(unicodedata.normalize('NFC', 'a'))\n"
        )
        self.assertEqual([], impurities(allowed))
        for snippet in ALLOWED_API_USES:
            with self.subTest(allowed=snippet):
                self.assertEqual([], impurities(snippet), snippet)


if __name__ == "__main__":
    unittest.main()

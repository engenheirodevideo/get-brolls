"""Guarda do registro de schemas: todo JSON persistido tem schema e a versão vem dele.

O que o teste confere sobre o código de `scripts/getbrolls`:

1. Todo *produtor* de JSON (função com `json.dump(s)`, chamada `stamp*` ou dicionário com chave de
   versão) está em `WRITERS` (com o schema) ou em `EXCLUDED` (com o motivo), nos dois sentidos: chave
   que não existe mais no código também falha.
2. Nenhum literal numérico de versão no código (`SCHEMA_VERSION = 1`, `"schema_version": 1`,
   `stamp(dados)` sem versão, `getbrolls.x/1`...) fora das listas permitidas. Os literais de hoje ficam
   em `tests/_schema_pending_<dono>.py`, um módulo por família dona; quem troca o literal por
   `schemas.version(...)` apaga a entrada, e quem apaga a entrada sem trocar o literal falha com o
   arquivo, o símbolo e o dono.
3. O valor que cada literal pendente grava é igual ao que o schema declara (os esqueletos novos têm os
   valores de hoje, então um valor errado falha aqui).
4. Os literais de inicialização (`BOOTSTRAP_LITERALS`) são iguais aos do schema.
5. Todo schema passa em `jsonschema.check_schema`.
"""

import ast
import functools
import importlib
import re
import unittest
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import TypeGuard

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
import _schema_latency
from _paths import ROOT

from getbrolls import _paths as core_paths
from getbrolls import versioning
from getbrolls.sdk import contracts, jsonschema, schemas

PACKAGE = ROOT / "scripts" / "getbrolls"

# Dono de cada módulo: a família de módulos que troca o literal pela leitura do schema.
OWNER_BY_MODULE = {
    **dict.fromkeys(("ledger", "models", "memory", "roteiro_ids", "queue", "library", "acquisition"), "collection"),
    **dict.fromkeys(
        (
            "export_plan",
            "roteiro_plan",
            "export",
            "export_folder",
            "review",
            "serve",
            "instagram_pairs",
            "layout",
            "analysis",
            "analysis_contract",
            "recipe_init",
            "brief",
            "roteiro_sync",
            "commands",
        ),
        "delivery",
    ),
    **dict.fromkeys(
        (
            "profile",
            "capabilities",
            "_paths",
            "clients",
            "recipes",
            "rules",
            "sdk.loader",
            "sdk.marketplace",
            "sdk.marketplace_index",
            "sdk.manifest",
            "sdk.contracts",
            "sdk.scaffold",
            "sdk.guard",
            "sdk.marketplace_install",
        ),
        "home",
    ),
}
# Exceção por símbolo: o módulo `commands` tem um ponto de cada lado (BRIEF e RULES).
OWNER_BY_KEY = {"commands:rules_from_flags": "home"}
OWNERS = ("collection", "delivery", "home")

# Chaves que marcam um dicionário como documento versionado (produtor) e as que, com inteiro, são literal.
PRODUCER_KEYS = frozenset({"schema_version", "schema", "export_version", "plan_version", "templateVersion", "sdk_api"})
LITERAL_KEYS = PRODUCER_KEYS | {"version", "layout"}
FROMKEYS_ARGS = 2  # dict.fromkeys(chaves, valor)
VERSION_NAME = re.compile(r"(^|_)(VERSION|SUPPORTED)$|^SDK_API$")
FAMILY_STRING = re.compile(r"getbrolls\.[a-z][a-z0-9_]*/[1-9][0-9]*")
# Ajudantes de `versioning`: (nome do parâmetro de versão, posição) de cada um.
HELPERS = {
    "read_version": ("supported", 2),
    "read_schema": ("supported", 2),
    "stamp": ("version", 1),
    "stamp_schema": ("version", 2),
    "schema_name": ("version", 1),
}

# --- Produtores: cada função que produz JSON está aqui ou em EXCLUDED, nunca nos dois -------------------

WRITERS: dict[str, str | tuple[str, ...]] = {
    "runtime_parts:write_marker": "runtime_marker",
    "acquisition:_save_index": "source_index",
    "analysis:_new_index": "analysis_index",
    "analysis:_media_doc": "media",
    "analysis:write_component": ("transcript", "scenes", "silence", "speakers", "visual"),
    "analysis:write_markers": "markers",
    "analysis:_write_json": (
        "analysis_index",
        "media",
        "transcript",
        "scenes",
        "silence",
        "speakers",
        "visual",
        "markers",
    ),
    "capabilities:describe": "capabilities",
    "clients:load_registry": "clients",
    "clients:_write_registry": "clients",
    "clients:_prepare_folder": ("client", "clients"),
    "commands:rules_from_flags": "rules_block",
    "commands:_execute_init_brief": "brief",
    "export:_phase_validate": "export_plan",
    "export:_plan_text": "export_plan",
    "export_folder:_marker_bytes": "export_marker",
    "export_plan:build": "export_plan",
    "instagram_pairs:_build_summary": "instagram_summary",
    "instagram_pairs:write_summary": "instagram_summary",
    "layout:new_project_doc": "project",
    "layout:write_project": "project",
    "ledger:Ledger.__init__": "manifest",
    "ledger:Ledger.save_many": "pending_transaction",
    "ledger:Ledger._finish_transaction": ("manifest", "candidate"),
    "library:empty_index": "library_index",
    "library:save_index": "library_index",
    "memory:remember": "references",
    "models:candidate": "candidate",
    "profile_trust:read_store": "trusted_profiles",
    "profile_trust:_write_store": "trusted_profiles",
    "queue:empty_state": "queue",
    "queue:save": "queue",
    "recipe_init:_write_lock": "recipe_lock",
    "recipes:_write_components": "components",
    "recipes:_draft": "recipe",
    "recipes:_dump": ("recipe", "components"),
    "review:enhance": "review_decisions",
    "roteiro_ids:write_state": "roteiro_state",
    "roteiro_plan:scene_plan": "export_plan",
    "roteiro_sync:_replace_block": "brief",
    "rules:seed_project_rules": "rules_block",
    "sdk.guard:plugin_candidate": "candidate",
    "sdk.plugin_state:write_state": "plugins_state",
    "sdk.manifest:read_manifest": "plugin_manifest",
    "sdk.marketplace:_empty_state": "marketplaces",
    "sdk.marketplace:_write_state": "marketplaces",
    "sdk.marketplace_index:validate_entry": "marketplace_index",
    "sdk.marketplace_index:validate_index": "marketplace_index",
    "sdk.marketplace_index:entry_from_manifest": "marketplace_index",
    "sdk.marketplace_index:_manifest_view": "marketplace_index",
    "sdk.marketplace_install:check_compatible": "marketplace_index",
    "sdk.scaffold:_manifest": "plugin_manifest",
    "sdk.scaffold:new": "plugin_manifest",
    "serve:save_review": "review_decisions",
}

_ENVELOPE = "saída de erro ou de comando em stdout/stderr: o contrato está em CLI_CONTRACT.md, não é arquivo"
_HASH = "domínio de hash ou chave de comparação: o JSON nunca é gravado"
_LOG = "log de linhas JSONL, sem versão por linha (documentado em SCHEMAS.md)"
_HTTP = "resposta ou corpo HTTP, não é persistido"
EXCLUDED: dict[str, str] = {
    "_paths:_forms": "escape de texto de caminho (aspas JSON), não é documento",
    "analysis:_plain": "sonda de NaN de um documento que já tem família própria, não grava",
    "analysis:_progress": "linha de progresso em stderr",
    "cli_parser:GbArgumentParser.error": _ENVELOPE,
    "cli:main": _ENVELOPE,
    "cli:_print_error": _ENVELOPE,
    "cli:entrypoint": _ENVELOPE,
    "http:get_json": "corpo de resposta HTTP de terceiros",
    "instagram_pairs:_main": _ENVELOPE,
    "library:asset_id": _HASH,
    "logs:_format_value": "formatação de linha de log",
    "media:drawtext_available": "cache de sonda sem contrato de dados",
    "models:signature": _HASH,
    "previewing:prepare_preview": _HASH,
    "review:review_epoch": _HASH,
    "review:legacy_review_epoch": _HASH,
    "roteiro_plan:digest": _HASH,
    "roteiro_review:record_review": _LOG,
    "diagnostics:diagnostic_line": _LOG,
    "runtime:_write_audit_log": _LOG,
    "sdk.bridges:_json_copy": "cópia de conferência do que o plugin devolveu, não é persistida",
    "sdk.bridges:check_readback": "limite de tamanho do que o plugin devolveu, não é persistido",
    "sdk.bridges:_copied": "cópia de conferência do que o plugin devolveu, não é persistida",
    "sdk.exporters:_json_copy": "cópia de conferência do que o exporter devolveu, não é persistida",
    "sdk.guard:_normalize": _HASH,
    "sdk.jsonschema:_check_array": "chave de comparação de uniqueItems",
    "sdk.marketplace_index:_differences": _HASH,
    "sdk.marketplace_install:_permissions_token": _HASH,
    "sdk.plugin_commands:_within_result_limits": "limite de tamanho do resultado de plugin, não é persistido",
    "sdk.plugin_commands:run": _ENVELOPE,
    "serve:_NoCacheHandler._refuse": _HTTP,
    "serve:_NoCacheHandler._head_error": _HTTP,
    "serve:_NoCacheHandler._save_and_answer": _HTTP,
    "serve:_NoCacheHandler.send_head": _HTTP,
    "serve:_inject_token": "injeta o token na página HTML, não grava JSON de dados",
    "serve:run": _ENVELOPE,
    "serve:start_background": ".serve.pid: estado do processo sem contrato de dados",
}

# Schemas que o core não grava por conta própria (a pessoa ou o plugin produz o arquivo).
NO_CORE_WRITER = {
    "draft": "o plugin produz; o core valida na volta",
    "handoff": "o plugin produz; o core valida na volta",
    "handoff_return": "o plugin produz; o core valida na volta",
    "profile_toml": "a pessoa escreve o getbrolls.toml",
}

# --- Literais permitidos para sempre ------------------------------------------------------------------

# Constantes que precisam existir antes de a pasta de dados resolver (marcador de runtime e a API pública
# do SDK): cada uma tem um teste de igualdade com o schema.
BOOTSTRAP_LITERALS: dict[str, tuple[str, int]] = {
    "_paths:<module>:const:MARKER_SCHEMA_VERSION=1": ("runtime_marker", core_paths.MARKER_SCHEMA_VERSION),
    "sdk.contracts:<module>:const:SDK_API=1": ("plugin_manifest", contracts.SDK_API),
}
# Domínios de hash e versões de hash: não são versão de arquivo.
HASH_DOMAINS = {
    "roteiro_plan:<module>:const:HASH_VERSION=1": "versão do hash do plano, não do arquivo",
    "sdk.marketplace_install:<module>:family:getbrolls.permissions_increase/1": "domínio do sha256 da prévia",
}
# Fora do registro por decisão: arquivo do usuário ou número que não é versão de formato.
OUT_OF_REGISTRY = {
    "assets:<module>:const:LICENSE_SUPPORTED=1": ".licenca.json é arquivo da pessoa, versão opcional",
    "recipes:_draft:dictint:version=1": "número da versão da receita (pasta <N>), não o formato do arquivo",
    "versioning:<module>:const:FIRST_VERSION=1": (
        "regra única de campo ausente (vale a primeira versão), não a versão de um formato"
    ),
}


# --- Varredura ----------------------------------------------------------------------------------------


def _is_int(node: object) -> TypeGuard[ast.Constant]:
    return isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)


def _docstring(body: list[ast.stmt]) -> ast.Constant | None:
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        return first.value
    return None


class _Scan(ast.NodeVisitor):
    """Produtores e literais de um módulo: chave `módulo:símbolo[:tipo:detalhe]`."""

    # Os visit_<Nó> são o nome que o ast procura.
    # pylint: disable=invalid-name

    def __init__(self, module: str):
        self.module = module
        self.stack: list[str] = []
        self.producers: set[str] = set()
        self.literals: set[str] = set()
        self.docstrings: set[int] = set()

    def _scope(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _literal(self, kind: str, detail: str):
        self.literals.add(f"{self.module}:{self._scope()}:{kind}:{detail}")

    def _note_docstring(self, body):
        doc = _docstring(body)
        if doc is not None:
            self.docstrings.add(id(doc))

    def visit_Module(self, node):
        self._note_docstring(node.body)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._note_docstring(node.body)
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        self._note_docstring(node.body)
        self.stack.append(node.name)
        args = node.args
        positional = args.posonlyargs + args.args
        pairs = list(zip(positional[len(positional) - len(args.defaults) :], args.defaults, strict=True))
        pairs += list(zip(args.kwonlyargs, args.kw_defaults, strict=True))
        for arg, default in pairs:
            if arg.arg in ("version", "supported") and _is_int(default):
                self._literal("default", f"{arg.arg}={default.value}")
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.visit_FunctionDef(node)

    def _assign(self, targets, value):
        for target in targets:
            if not (isinstance(target, ast.Name) and VERSION_NAME.search(target.id) and value is not None):
                continue
            if _is_int(value):
                number = value.value
            elif (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "fromkeys"
                and len(value.args) == FROMKEYS_ARGS
                and _is_int(value.args[1])
            ):
                number = value.args[1].value
            else:
                continue
            self._literal("const", f"{target.id}={number}")

    def visit_Assign(self, node):
        self._assign(node.targets, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        self._assign([node.target], node.value)
        self.generic_visit(node)

    def visit_Dict(self, node):
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value in LITERAL_KEYS:
                if key.value in PRODUCER_KEYS:
                    self.producers.add(f"{self.module}:{self._scope()}")
                if _is_int(value):
                    self._literal("dictint", f"{key.value}={value.value}")
        self.generic_visit(node)

    def visit_Compare(self, node):
        parts = [node.left, *node.comparators]
        keys = [
            sub.value
            for part in parts
            for sub in ast.walk(part)
            if isinstance(sub, ast.Constant) and sub.value in LITERAL_KEYS
        ]
        numbers = [part.value for part in parts if _is_int(part)]
        if keys and numbers:
            self._literal("compare", f"{keys[0]}~{numbers[0]}")
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and id(node) not in self.docstrings and FAMILY_STRING.search(node.value):
            self._literal("family", node.value)

    def visit_JoinedStr(self, node):
        for part in node.values:
            if isinstance(part, ast.Constant) and FAMILY_STRING.search(str(part.value)):
                self._literal("family", str(part.value))
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if (
            name in ("dump", "dumps")
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "json"
        ):
            self.producers.add(f"{self.module}:{self._scope()}")
        if name in HELPERS:
            if name.startswith("stamp"):
                self.producers.add(f"{self.module}:{self._scope()}")
            keyword, position = HELPERS[name]
            argument = next((kw.value for kw in node.keywords if kw.arg == keyword), None)
            if argument is None and len(node.args) > position:
                argument = node.args[position]
            if argument is None:
                self._literal("helper", f"{name}:missing")
            elif _is_int(argument):
                self._literal("helper", f"{name}:{argument.value}")
        self.generic_visit(node)


def scan_source(text: str, module: str = "mod") -> _Scan:
    scan = _Scan(module)
    scan.visit(ast.parse(text))
    return scan


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(PACKAGE).with_suffix("").parts)


# --- Overlay de movimentos: `tests/schema_registry_moves/*.txt` -----------------------------------------
# Uma refatoração que move um produtor para outro módulo pode, antes de atualizar este arquivo, escrever uma
# linha `novo_modulo:simbolo <- velho_modulo:simbolo` num arquivo do overlay. O scan mapeia as chaves do
# código de volta para a chave antiga antes de comparar; `WRITERS`, `EXCLUDED`, `OWNER_BY_MODULE` e os
# módulos pendentes continuam falando a chave antiga. Quando este arquivo passa a falar a chave nova, a
# linha do overlay sai.

MOVES_DIR = Path(__file__).resolve().parent / "schema_registry_moves"


def parse_moves(name: str, text: str) -> dict[str, str]:
    """`novo <- velho` por linha (comentário com `#`) -> `{novo: velho}`; linha mal formada falha."""
    found: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        new, arrow, old = (part.strip() for part in line.partition("<-"))
        if not arrow or new.count(":") != 1 or old.count(":") != 1 or new == old:
            raise AssertionError(f"{name}:{number}: esperado 'novo_modulo:simbolo <- velho_modulo:simbolo': {line!r}")
        if new in found:
            raise AssertionError(f"{name}:{number}: {new} aparece duas vezes")
        found[new] = old
    return found


def merge_moves(files: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Junta os arquivos do overlay; devolve `{novo: velho}` e as duplicatas entre arquivos (novo ou velho)."""
    merged: dict[str, str] = {}
    duplicates: list[str] = []
    for name in sorted(files):
        for new, old in parse_moves(name, files[name]).items():
            if new in merged or old in merged.values():
                duplicates.append(f"{name}: {new} <- {old} já aparece em outro arquivo do overlay")
            merged[new] = old
    return merged, duplicates


def load_moves() -> tuple[dict[str, str], list[str]]:
    files = {path.name: path.read_text(encoding="utf-8") for path in sorted(MOVES_DIR.glob("*.txt"))}
    return merge_moves(files)


def symbol_of(key: str) -> str:
    """`módulo:símbolo` de uma chave de produtor (`módulo:símbolo`) ou de literal (`módulo:símbolo:tipo:...`)."""
    return ":".join(key.split(":")[:2])


def apply_moves(keys: AbstractSet[str], moves: dict[str, str]) -> frozenset[str]:
    """Troca o `módulo:símbolo` novo pelo antigo em cada chave; as chaves sem movimento ficam como estão."""
    mapped = set()
    for key in keys:
        old = moves.get(symbol_of(key))
        mapped.add(key if old is None else old + key[len(symbol_of(key)) :])
    return frozenset(mapped)


def move_problems(moves: dict[str, str], raw_keys: AbstractSet[str], registry_symbols: AbstractSet[str]) -> list[str]:
    """Linhas velhas do overlay: o código não produz a chave nova, o registro não tem a antiga ou ela ainda existe."""
    raw_symbols = {symbol_of(key) for key in raw_keys}
    problems = []
    for new, old in sorted(moves.items()):
        if new not in raw_symbols:
            problems.append(f"{new} <- {old}: o código não produz {new}; apague a linha ou corrija o nome")
        if old not in registry_symbols:
            problems.append(f"{new} <- {old}: {old} não está no registro principal; apague a linha")
        if old in raw_symbols:
            problems.append(f"{new} <- {old}: {old} ainda existe no código; o movimento é ambíguo")
    return problems


@functools.cache
def scan_package_raw() -> tuple[frozenset[str], frozenset[str]]:
    """Produtores e literais do código, com as chaves como estão hoje (antes do overlay)."""
    producers: set[str] = set()
    literals: set[str] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        scan = scan_source(path.read_text(encoding="utf-8"), module_name(path))
        producers |= scan.producers
        literals |= scan.literals
    return frozenset(producers), frozenset(literals)


def scan_package() -> tuple[frozenset[str], frozenset[str]]:
    """Produtores e literais do código com o overlay de movimentos aplicado (chaves antigas)."""
    moves, _ = load_moves()
    producers, literals = scan_package_raw()
    return apply_moves(producers, moves), apply_moves(literals, moves)


def registry_symbols() -> frozenset[str]:
    """Todo `módulo:símbolo` que o registro principal conhece: escritores, exclusões, pendentes e permitidos."""
    unmapped = {key for module in pending_modules().values() for key in module.UNMAPPED_WRITERS}
    keys = {*WRITERS, *EXCLUDED, *unmapped, *pending_literals(), *BOOTSTRAP_LITERALS, *HASH_DOMAINS, *OUT_OF_REGISTRY}
    return frozenset(symbol_of(key) for key in keys)


def owner_of(key: str) -> str:
    """Família dona da chave `módulo:símbolo[...]`; falha quando o módulo não tem dono."""
    module, scope = key.split(":")[:2]
    symbol = f"{module}:{scope}"
    owner = OWNER_BY_KEY.get(symbol) or OWNER_BY_MODULE.get(module)
    if owner is None:
        raise AssertionError(f"{module}: módulo sem dono em OWNER_BY_MODULE (tests/test_schema_registry.py)")
    return owner


def file_of(key: str) -> str:
    return "scripts/getbrolls/" + key.split(":", 1)[0].replace(".", "/") + ".py"


def pending_modules():
    return {owner: importlib.import_module(f"_schema_pending_{owner}") for owner in OWNERS}


def pending_literals() -> dict[str, tuple[str, str | tuple[str, ...]]]:
    """Entradas de todos os módulos pendentes: chave -> (dono, schema alvo)."""
    found: dict = {}
    for owner, module in pending_modules().items():
        for key, target in module.LITERALS.items():
            found[key] = (owner, target)
    return found


def split_target(target: str) -> tuple[str, str | None]:
    """`schema` ou `schema.campo` -> (schema, campo ou None)."""
    name, _, field = target.partition(".")
    return name, field or None


def unlisted_messages(literals: AbstractSet[str], pending: AbstractSet[str]) -> list[str]:
    """Uma frase por literal que não é pendente nem permitido: arquivo, símbolo, dono."""
    allowed = {*BOOTSTRAP_LITERALS, *HASH_DOMAINS, *OUT_OF_REGISTRY}
    return [
        f"{file_of(key)} {key.split(':')[1]} ({owner_of(key)}): literal de versão {key.split(':', 2)[2]}; "
        "leia de getbrolls.sdk.schemas.version(...)"
        for key in sorted(literals - allowed - pending)
    ]


def literal_parts(key: str) -> tuple[str, str]:
    """(tipo, detalhe) de uma chave de literal `módulo:símbolo:tipo:detalhe`."""
    _, _, kind, detail = key.split(":", 3)
    return kind, detail


def literal_value(key: str) -> int:
    """O número que o literal grava; 1 quando falta (o padrão de `versioning`)."""
    kind, detail = literal_parts(key)
    if kind in ("dictint", "const", "default"):
        return int(detail.rsplit("=", 1)[1])
    if kind == "compare":
        return int(detail.rsplit("~", 1)[1])
    if kind == "family":
        return int(detail.rsplit("/", 1)[1])
    last = detail.rsplit(":", 1)[1]
    return 1 if last == "missing" else int(last)


def literal_field(key: str) -> str | None:
    """O campo de versão que a chave de literal nomeia (`schema_version=1`), quando nomeia."""
    kind, detail = literal_parts(key)
    if kind == "dictint":
        return detail.split("=")[0]
    if kind == "compare":
        return detail.split("~")[0]
    return None


# --- Testes do detector (a guarda não pode ser vazia) ---------------------------------------------------


class DetectorTests(unittest.TestCase):
    def literals(self, source):
        return sorted(key.split(":", 1)[1] for key in scan_source(source).literals)

    def test_a_version_constant_is_found_by_name_and_a_hash_constant_is_not(self):
        found = self.literals("SCHEMA_VERSION = 2\nSUPPORTED: int = 1\nSDK_API = 1\nPAGE_LIMIT = 7\nX_VERSION = 3\n")
        self.assertEqual(
            [
                "<module>:const:SCHEMA_VERSION=2",
                "<module>:const:SDK_API=1",
                "<module>:const:SUPPORTED=1",
                "<module>:const:X_VERSION=3",
            ],
            found,
        )

    def test_a_dict_of_versions_is_found(self):
        self.assertEqual(["<module>:const:SUPPORTED=1"], self.literals("SUPPORTED = dict.fromkeys(NAMES, 1)\n"))
        self.assertEqual([], self.literals("OTHER = dict.fromkeys(NAMES, None)\n"))

    def test_a_version_key_with_an_integer_is_found_and_a_name_is_not(self):
        found = self.literals(
            'def f():\n    a = {"schema_version": 1, "version": 3, "layout": 1}\n    b = {"schema_version": N}\n'
        )
        self.assertEqual(["f:dictint:layout=1", "f:dictint:schema_version=1", "f:dictint:version=3"], found)

    def test_a_comparison_of_a_version_key_with_an_integer_is_found(self):
        found = self.literals('def f(d):\n    return d["schema_version"] != 1 or d.get("templateVersion") == 2\n')
        self.assertEqual(["f:compare:schema_version~1", "f:compare:templateVersion~2"], found)
        self.assertEqual([], self.literals('def f(d):\n    return d["schema_version"] != LIMIT\n'))

    def test_a_versioning_helper_without_a_version_or_with_an_integer_is_found(self):
        source = (
            "def f(d):\n"
            "    versioning.read_version(d, 'x')\n"
            "    versioning.read_version(d, 'x', SUPPORTED)\n"
            "    versioning.stamp(d)\n"
            "    versioning.stamp(d, 2)\n"
            "    versioning.stamp(d, version=schemas.version('queue'))\n"
            "    stamp_schema(d, 'project')\n"
            "    schema_name('x', 1)\n"
            "    read_schema(d, 'x', supported=3)\n"
        )
        self.assertEqual(
            [
                "f:helper:read_schema:3",
                "f:helper:read_version:missing",
                "f:helper:schema_name:1",
                "f:helper:stamp:2",
                "f:helper:stamp:missing",
                "f:helper:stamp_schema:missing",
            ],
            self.literals(source),
        )

    def test_an_integer_default_named_version_or_supported_is_found(self):
        found = self.literals("def f(a, version=1, *, supported=2, other=3):\n    pass\n")
        self.assertEqual(["f:default:supported=2", "f:default:version=1"], found)

    def test_a_family_string_is_found_outside_docstrings_only(self):
        source = '"""getbrolls.module/1"""\ndef f():\n    """getbrolls.project/1"""\n    return "getbrolls.project/1"\n'
        self.assertEqual(["f:family:getbrolls.project/1"], self.literals(source))
        fstring = 'def f(n):\n    return f"getbrolls.project/1{n}"\n'
        self.assertEqual(["f:family:getbrolls.project/1"], self.literals(fstring))

    def test_producers_are_json_dumps_stamps_and_versioned_dictionaries(self):
        source = (
            "class C:\n"
            "    def a(self):\n        json.dumps(x)\n"
            "    def b(self):\n        return stamp(x, 1)\n"
            '    def c(self):\n        return {"schema": "getbrolls.x/1"}\n'
            '    def d(self):\n        return {"version": "1.2"}\n'
            "    def e(self):\n        return other.dumps(x)\n"
        )
        self.assertEqual({"mod:C.a", "mod:C.b", "mod:C.c"}, scan_source(source).producers)


class MovesOverlayTests(unittest.TestCase):
    def test_a_move_maps_the_new_key_back_to_the_old_one_for_producers_and_literals(self):
        moves = {"runtime_parts:write_marker": "_paths:write_marker"}
        keys = {"runtime_parts:write_marker", "runtime_parts:write_marker:dictint:schema_version=1", "queue:save"}
        self.assertEqual(
            {"_paths:write_marker", "_paths:write_marker:dictint:schema_version=1", "queue:save"},
            apply_moves(keys, moves),
        )

    def test_without_moves_the_keys_are_unchanged(self):
        self.assertEqual(frozenset({"queue:save"}), apply_moves({"queue:save"}, {}))

    def test_a_malformed_or_repeated_line_is_refused(self):
        for text in ("a:b", "a:b <- a:b", "a <- b:c", "a:b <- c:d\na:b <- e:f"):
            with self.subTest(text=text), self.assertRaises(AssertionError):
                parse_moves("t.txt", text)
        self.assertEqual({"n:s": "o:s"}, parse_moves("t.txt", "# nota\n\nn:s <- o:s\n"))

    def test_the_same_move_in_two_files_is_a_duplicate(self):
        _, duplicates = merge_moves({"a.txt": "n:s <- o:s", "b.txt": "n:s <- o:s"})
        self.assertEqual(1, len(duplicates))
        _, duplicates = merge_moves({"a.txt": "n:s <- o:s", "b.txt": "m:s <- o:s"})
        self.assertEqual(1, len(duplicates))

    def test_stale_lines_are_reported(self):
        moves = {"new:fn": "old:fn"}
        self.assertEqual([], move_problems(moves, {"new:fn"}, {"old:fn"}))
        self.assertEqual(1, len(move_problems(moves, {"other:fn"}, {"old:fn"})))  # o código não produz o novo
        self.assertEqual(1, len(move_problems(moves, {"new:fn"}, {"x:fn"})))  # a chave antiga não está no registro
        self.assertEqual(1, len(move_problems(moves, {"new:fn", "old:fn"}, {"old:fn"})))  # a antiga ainda existe

    def test_the_overlay_files_on_disk_are_valid_and_not_stale(self):
        moves, duplicates = load_moves()
        self.assertEqual([], duplicates)
        producers, literals = scan_package_raw()
        self.assertEqual([], move_problems(moves, producers | literals, registry_symbols()))


# --- A guarda sobre o código -----------------------------------------------------------------------------


class ProducersMapToSchemas(unittest.TestCase):
    def test_every_json_producer_is_mapped_or_excluded_and_nothing_stale_is_listed(self):
        producers, _ = scan_package()
        unmapped = {key for module in pending_modules().values() for key in module.UNMAPPED_WRITERS}
        known = set(WRITERS) | set(EXCLUDED) | unmapped
        missing = sorted(producers - known)
        stale = sorted(known - producers)
        self.assertEqual(
            [],
            [f"{file_of(key)} {key.split(':')[1]} ({owner_of(key)}): produz JSON sem schema" for key in missing],
            "mapeie em WRITERS (com o schema) ou em EXCLUDED (com o motivo)",
        )
        self.assertEqual([], stale, "chave que não existe mais no código: apague de WRITERS/EXCLUDED/UNMAPPED_WRITERS")

    def test_a_producer_is_never_both_written_and_excluded(self):
        unmapped = {key for module in pending_modules().values() for key in module.UNMAPPED_WRITERS}
        self.assertEqual(set(), set(WRITERS) & set(EXCLUDED))
        self.assertEqual(set(), unmapped & (set(WRITERS) | set(EXCLUDED)))

    def test_every_writer_points_to_real_schemas_and_every_exclusion_has_a_reason(self):
        names = set(schemas.names())
        for key, target in WRITERS.items():
            for name in (target,) if isinstance(target, str) else target:
                with self.subTest(key=key, schema=name):
                    self.assertIn(name, names)
        for key, reason in EXCLUDED.items():
            with self.subTest(key=key):
                self.assertGreater(len(reason.strip()), 10)

    def test_every_schema_has_a_core_writer_or_a_reason_why_not(self):
        written = {name for target in WRITERS.values() for name in ((target,) if isinstance(target, str) else target)}
        self.assertEqual(set(), set(NO_CORE_WRITER) & written, "tem escritor: tire de NO_CORE_WRITER")
        self.assertEqual([], sorted(set(schemas.names()) - written - set(NO_CORE_WRITER)))
        self.assertEqual([], sorted(set(NO_CORE_WRITER) - set(schemas.names())))


class NoNumericVersionInCode(unittest.TestCase):
    def test_every_literal_is_pending_with_its_owner_or_permanently_allowed(self):
        _, literals = scan_package()
        self.assertEqual([], unlisted_messages(literals, set(pending_literals())))

    def test_every_pending_list_is_empty(self):
        # Every owner fixed its files: a version literal or an unmapped writer added from now on fails above
        # with file, symbol and owner, instead of waiting in a pending list.
        self.assertEqual({}, dict(pending_literals()))

    def test_a_new_literal_in_any_module_is_refused_with_its_owner(self):
        _, literals = scan_package()
        invented = {"queue:save:dictint:schema_version=2", "serve:_other:helper:stamp:missing"}
        messages = unlisted_messages(literals | invented, set(pending_literals()))
        self.assertEqual(2, len(messages))
        self.assertTrue(any("queue.py save (collection)" in message for message in messages))
        self.assertTrue(any("serve.py _other (delivery)" in message for message in messages))

    def test_every_owner_is_a_module_family_named_like_its_pending_module(self):
        for owner, module in pending_modules().items():
            with self.subTest(owner=owner):
                self.assertRegex(owner, r"^[a-z]+$", "dono é o nome da família de módulos, em minúsculas")
                self.assertEqual(owner, module.OWNER)
                self.assertEqual(f"_schema_pending_{owner}.py", Path(module.__file__ or "").name)

    def test_a_pending_entry_whose_literal_is_gone_is_removed(self):
        _, literals = scan_package()
        stale = sorted(key for key in pending_literals() if key not in literals)
        self.assertEqual([], stale, "o literal já saiu do código: apague a entrada do módulo pendente")

    def test_the_permanent_allowances_still_match_the_code(self):
        _, literals = scan_package()
        for table in (BOOTSTRAP_LITERALS, HASH_DOMAINS, OUT_OF_REGISTRY):
            for key in table:
                with self.subTest(key=key):
                    self.assertIn(key, literals)

    def test_a_pending_entry_lives_in_the_module_of_its_owner(self):
        for key, (owner, _) in pending_literals().items():
            with self.subTest(key=key):
                self.assertEqual(owner_of(key), owner, f"{file_of(key)} {key.split(':')[1]}")

    def test_no_entry_is_pending_and_allowed_at_once(self):
        allowed = {*BOOTSTRAP_LITERALS, *HASH_DOMAINS, *OUT_OF_REGISTRY}
        self.assertEqual(set(), allowed & set(pending_literals()))

    def test_every_unmapped_writer_is_a_real_producer_of_its_owner(self):
        producers, _ = scan_package()
        for owner, module in pending_modules().items():
            for key in module.UNMAPPED_WRITERS:
                with self.subTest(key=key):
                    self.assertIn(key, producers)
                    self.assertEqual(owner, owner_of(key))


class LiteralsEqualTheirSchema(unittest.TestCase):
    def test_the_value_each_pending_literal_writes_is_the_one_its_schema_declares(self):
        checked = 0
        for key, (_, target) in sorted(pending_literals().items()):
            value = literal_value(key)
            detail_key = literal_field(key)
            for item in (target,) if isinstance(target, str) else target:
                name, field = split_target(item)
                if field is None and detail_key in (
                    schemas.version_field(name),
                    *schemas.SECONDARY_FIELDS.get(name, ()),
                ):
                    field = detail_key
                with self.subTest(key=key, schema=item):
                    self.assertEqual(value, schemas.version(name, field), f"{file_of(key)} {key.split(':')[1]}")
                    checked += 1
        if pending_literals():
            self.assertGreater(checked, 0)

    def test_every_pending_target_is_a_real_schema(self):
        for key, (_, target) in pending_literals().items():
            for item in (target,) if isinstance(target, str) else target:
                with self.subTest(key=key, schema=item):
                    self.assertIn(split_target(item)[0], schemas.names())

    def test_the_bootstrap_constants_equal_the_schema(self):
        for key, (name, current) in BOOTSTRAP_LITERALS.items():
            with self.subTest(key=key):
                self.assertEqual(schemas.version(name), current)

    def test_the_trusted_profiles_and_the_marketplace_constants_follow_the_schema_today(self):
        self.assertEqual(schemas.version("runtime_marker"), core_paths.MARKER_SCHEMA_VERSION)
        self.assertEqual(schemas.version("plugin_manifest"), contracts.SDK_API)


class EverySchemaIsWellFormed(unittest.TestCase):
    def test_every_schema_passes_the_core_validator_check_and_declares_its_version_field(self):
        for name in schemas.names():
            with self.subTest(name=name):
                schema = schemas.load(name)
                jsonschema.check_schema(schema)
                self.assertIn(schemas.version_field(name), schema["properties"])
                self.assertIn(f"/{name}.schema.json", schema["$id"])

    def test_every_new_schema_is_packaged(self):
        manifest = (ROOT / "packaging" / "data_manifest.txt").read_text(encoding="utf-8").splitlines()
        entries = [line for line in manifest if line and not line.startswith("#")]
        self.assertEqual(sorted(entries), entries)
        self.assertEqual(list(core_paths.REQUIRED_DATA), entries)
        for name in schemas.names():
            with self.subTest(name=name):
                self.assertIn(f"schemas/{name}.schema.json", entries)

    def test_the_versioning_helpers_still_take_the_number_from_the_caller(self):
        self.assertEqual(2, versioning.read_version({"schema_version": 2}, "x", supported=schemas.version("queue")))
        self.assertEqual({"schema_version": 2}, versioning.stamp({}, schemas.version("queue")))
        with self.assertRaisesRegex(ValueError, "mais nova"):
            versioning.read_version({"schema_version": 3}, "x", supported=schemas.version("queue"))


class ValidateOnReadIsCheap(unittest.TestCase):
    def test_the_stdlib_validator_handles_a_large_document_within_the_budget(self):
        schema = {
            "type": "object",
            "properties": {
                "schema_version": {"type": "integer", "minimum": 1, "maximum": 2},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "state"],
                        "properties": {
                            "id": {"type": "string", "pattern": "^[a-z0-9_-]+$"},
                            "state": {"enum": ["pending", "done"]},
                            "tries": {"type": "integer", "minimum": 0},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
        }
        doc = _schema_latency.large_document(
            {"schema_version": 2}, "items", {"id": "a-b_1", "state": "done", "tries": 3, "tags": ["x", "y"]}
        )
        self.assertEqual(_schema_latency.DEFAULT_ITEMS, len(doc["items"]))
        best = _schema_latency.assert_validates_within(self, doc, schema)
        self.assertGreater(best, 0)

    def test_the_helper_fails_when_the_document_is_slower_than_the_limit(self):
        schema = {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}}}}
        doc = [{"id": "x"}] * 20000
        with self.assertRaises(AssertionError):
            _schema_latency.assert_validates_within(self, doc, schema, limit_s=0.0)

    def test_the_helper_fails_when_the_document_does_not_follow_the_schema(self):
        with self.assertRaises(AssertionError):
            _schema_latency.assert_validates_within(self, {"a": 1}, {"type": "object", "required": ["b"]})


if __name__ == "__main__":
    unittest.main()

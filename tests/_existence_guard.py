"""Varredura das conferências de existência de `scripts/getbrolls` (`test_py314_strict_paths`).

Toda conferência passa por `_paths.strict_*` (uma pasta sem permissão vira IO_ERROR em qualquer
Python) ou leva o marcador `# lenient: <motivo>`: no fim da própria linha, ou num comentário
sozinho na linha logo acima. Um marcador vale só para a linha a que se refere e para UMA
conferência; uma linha com mais de uma usa `# lenient(N): <motivo>`, com o número exato. O
marcador viaja com o código (um allowlist por arquivo:linha quebra a cada mudança) e deixa o
motivo à vista de quem revisa. Só comentários de verdade contam (o `tokenize` separa um `#`
dentro de uma string), e o motivo precisa dizer alguma coisa: nada de placeholder nem de
enchimento.

Conta como conferência:

- método de `Path` (`exists`, `is_dir`, `is_file`, `is_symlink`, `is_mount`, `is_junction`,
  `is_fifo`, `is_socket`, `is_block_device`, `is_char_device`), chamado ou passado como valor;
- função de `os.path` por qualquer nome (`os.path`, `import os.path as x`, `from os import
  path`, `from os.path import isdir`, `posixpath`/`ntpath`/`genericpath`) e as privadas do
  `nt`/`posix` (`_path_isdir`, `_path_exists`…);
- `getattr(x, "exists")` e qualquer `getattr` com nome que não é texto fixo (pode ser uma
  conferência; só passa com marcador), e `operator.methodcaller("exists")`;
- `__import__(...)` e `importlib.import_module` de `os`/`os.path` (desviam dos nomes acima);
- um `try` que chama `stat`/`lstat` e cujo `except` de `OSError` (ou mais largo) só devolve
  uma resposta calada (`return False`, `x = None`, `continue`): a conferência feita à mão. A
  linha que conta é a do `except`.
"""

import ast
import io
import re
import tokenize

PATH_PROBES = frozenset(
    {
        "exists",
        "is_dir",
        "is_file",
        "is_symlink",
        "is_mount",
        "is_junction",
        "is_fifo",
        "is_socket",
        "is_block_device",
        "is_char_device",
    }
)
OS_PATH_PROBES = frozenset({"exists", "isdir", "isfile", "islink", "lexists", "ismount", "isjunction"})
OS_PATH_MODULES = frozenset({"os.path", "posixpath", "ntpath", "genericpath"})
OS_MODULES = OS_PATH_MODULES | {"os", "nt", "posix"}
PRIVATE_PROBE = re.compile(r"^_path_(is\w+|exists|lexists)$")
STAT_CALLS = frozenset({"stat", "lstat"})
BROAD_ERRORS = frozenset(
    {
        "OSError",
        "IOError",
        "EnvironmentError",
        "PermissionError",
        "Exception",
        "BaseException",
    }
)
MARKER = re.compile(r"#\s*lenient(?:\((?P<count>\d+)\))?:(?P<reason>.*)$")
MIN_REASON = 20
LONG_REASON = 40
MIN_WORDS = 4
VOCABULARY = re.compile(
    r"\b(cache|cleanup|sweep|probe|raise|fallback|optional|diagnostic|absent|leftover|discovery)",
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(
    r"(todo|tbd|fixme|xxx+|n/?a|ok|okay|ignore|reason|motivo|because|lenient|probe|best effort|fine|safe)\W*",
    re.IGNORECASE,
)


def _os_path_names(tree):
    """Nomes que valem `os` (`os_names`), `os.path` (`modules`) e uma função de `os.path`."""
    os_names, modules, functions = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or (alias.name == "os.path" and not alias.asname):
                    os_names.add(alias.asname or "os")
                elif alias.name in OS_PATH_MODULES:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for alias in node.names:
                if node.module == "os" and alias.name == "path":
                    modules.add(alias.asname or "path")
                elif node.module in OS_PATH_MODULES and alias.name in OS_PATH_PROBES:
                    functions.add(alias.asname or alias.name)
    return os_names, modules, functions


def _is_os_path(node, os_names, modules):
    if isinstance(node, ast.Name):
        return node.id in modules
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id in os_names
    )


def _call_name(node):
    """Nome da função chamada (`x.y(...)` → `y`, `y(...)` → `y`), ou `None`."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return node.func.id if isinstance(node.func, ast.Name) else None


def _text_arg(node, index):
    """O argumento `index` como texto fixo; `None` se é outra coisa; `False` se não existe."""
    if len(node.args) <= index:
        return False
    arg = node.args[index]
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None


def _call_probe(node):
    """Conferência feita por chamada indireta: `getattr`, `methodcaller`, `__import__`."""
    name = _call_name(node)
    if name == "getattr":
        attr = _text_arg(node, 1)
        return attr is None or (attr is not False and attr in PATH_PROBES | OS_PATH_PROBES)
    if name == "methodcaller":
        method = _text_arg(node, 0)
        return method is None or (method is not False and method in PATH_PROBES | OS_PATH_PROBES)
    if name == "__import__":
        return True
    if name == "import_module":
        module = _text_arg(node, 0)
        return module is None or module in OS_MODULES
    return False


def _probe_line(node, os_names, modules, functions):
    """Linha da conferência que `node` é, ou `None`."""
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        os_path = node.attr in OS_PATH_PROBES and _is_os_path(node.value, os_names, modules)
        hit = node.attr in PATH_PROBES or os_path or PRIVATE_PROBE.match(node.attr)
        return node.end_lineno or node.lineno if hit else None
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.lineno if node.id in functions or PRIVATE_PROBE.match(node.id) else None
    return node.lineno if _call_probe(node) else None


def _catches_broadly(handler):
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(name, ast.Name) and name.id in BROAD_ERRORS for name in names)


def _quiet(statement):
    """`return False`/`None`/`True`, `x = <constante>`, `pass` ou `continue`: a falha vira resposta."""
    if isinstance(statement, (ast.Pass, ast.Continue)):
        return True
    if isinstance(statement, ast.Return):
        return statement.value is None or isinstance(statement.value, ast.Constant)
    return isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)


def _manual_check_lines(node):
    """Linhas `except` de um `try` que faz `stat`/`lstat` e troca o `OSError` por uma resposta
    calada (`return False`, `x = None`, `continue`…). Quem só pega `FileNotFoundError`/
    `NotADirectoryError` (ausência de verdade), relança ou relata o erro não conta."""
    if not isinstance(node, ast.Try):
        return []
    if not any(_call_name(inner) in STAT_CALLS for stmt in node.body for inner in ast.walk(stmt)):
        return []
    return [handler.lineno for handler in node.handlers if _catches_broadly(handler) and all(map(_quiet, handler.body))]


def probe_counts(tree):
    """{linha: quantas conferências} do módulo."""
    os_names, modules, functions = _os_path_names(tree)
    counts = {}
    for node in ast.walk(tree):
        lines = _manual_check_lines(node)
        line = _probe_line(node, os_names, modules, functions)
        for row in [*lines, *([line] if line is not None else [])]:
            counts[row] = counts.get(row, 0) + 1
    return counts


def markers(source):
    """{linha coberta: (quantas, motivo)} de cada `# lenient:`: a própria linha do comentário
    que fecha uma linha de código, ou a linha logo abaixo de um comentário sozinho."""
    covered = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        found = MARKER.match(token.string)
        if not found:
            continue
        row = token.start[0]
        alone = not token.line[: token.start[1]].strip()
        covered[row + 1 if alone else row] = (int(found.group("count") or 1), found.group("reason").strip())
    return covered


def weak(reason):
    """O motivo não diz nada: curto, placeholder, enchimento ou sem palavra do domínio."""
    words = {word.lower() for word in re.findall(r"[^\W\d_]{3,}", reason)}
    if len(reason) < MIN_REASON or PLACEHOLDER.fullmatch(reason) or len(words) < MIN_WORDS:
        return True
    return not VOCABULARY.search(reason) and len(reason) < LONG_REASON


def bare_probes(source):
    """(linha, código, problema) de cada conferência sem marcador válido e de cada marcador
    que não cobre conferência nenhuma."""
    lines = source.splitlines()
    counts = probe_counts(ast.parse(source))
    covered = markers(source)
    found = []
    for row in sorted(counts):
        code = lines[row - 1].strip()
        if row not in covered:
            found.append((row, code, "sem marcador"))
            continue
        count, reason = covered[row]
        if count != counts[row]:
            found.append((row, code, f"marcador cobre {count} de {counts[row]}"))
        elif weak(reason):
            found.append((row, code, "motivo vago"))
    found.extend(
        (row, lines[row - 1].strip() if row <= len(lines) else "", "marcador sem conferência")
        for row in sorted(set(covered) - set(counts))
    )
    return found

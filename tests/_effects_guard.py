"""Varredura dos efeitos colaterais de `scripts/getbrolls` (`test_effects_guard`).

Todo efeito colateral (subprocesso, rede, servidor HTTP, código carregado, arquivo gravado,
trocado, apagado ou pasta criada) passa por `getbrolls.effects`: um helper (`effects.run`,
`effects.replace`, `effects.unlink`…) ou, quando a operação precisa de mais de uma chamada
(temporário + fsync + troca), um bloco `with effects.op(...)` em volta dela. Cada um grava UMA
linha `event=op` no getbrolls.log. Fora de `effects.py`, a chamada direta à primitiva só passa
dentro de um bloco `with effects.op(...)` ou com o marcador `# effect-exempt: <motivo>`, no
fim da própria linha ou num comentário sozinho logo acima; `# effect-exempt(N): <motivo>` cobre
N primitivas na mesma linha. Só `effects.op`, `effects.batch` e `effects.load_plugin`
abrem um bloco assim, e o bloco cobre arquivos e a área da própria operação: um `fs.write` não
cobre um `subprocess.run` que esteja dentro dele (processo só num `proc.*`, rede num `net.*` ou
`server.*`, código num `plugin.*`, `code.*` ou `load_plugin`). Uma função ou um `lambda`
definidos dentro do bloco rodam depois, fora dele: não herdam nada. O nome da operação de um `effects.op`/`batch`
tem de ser texto fixo (`effects.op(nome)` com variável é recusado): é ele que diz a área do
bloco e mantém os nomes do log poucos e conhecidos. As regras do motivo são as
do `# lenient:` (`_existence_guard`): 20 caracteres, palavras distintas, nada de placeholder.

Conta como primitiva:

- `subprocess.*` (`run`, `Popen`, `call`, `check_call`, `check_output`…), `os.system`,
  `os.popen`, `os.exec*`, `os.spawn*`, `os.posix_spawn*`, `os.startfile`;
- `os.replace`/`rename`/`renames`/`unlink`/`remove`/`rmdir`/`removedirs`/`mkdir`/`makedirs`/
  `link`/`symlink`, e `os.open` com flag de escrita (ou flags que não dá para ler);
- `shutil.rmtree`/`move`/`copy`/`copy2`/`copyfile`/`copytree`;
- `tempfile.mkstemp`/`mkdtemp`/`TemporaryDirectory`/`NamedTemporaryFile`/`TemporaryFile`,
  `webbrowser.open*`;
- método de `Path`: `unlink`, `mkdir`, `write_text`, `write_bytes`, `rmdir`, `touch`,
  `symlink_to`, `hardlink_to`, e `rename`/`replace` com um argumento só ou com `target=` (o
  `str.replace` tem dois); `open(...)`/`io.open(...)`/`os.fdopen(...)`/`.open(...)` com modo
  de escrita (`w`, `a`, `x`, `+`) ou modo que não é texto fixo; as flags de `os.open` são lidas
  também numa constante do módulo ou da função, inclusive alargada com `|=`;
- rede: `urllib.request.urlopen`, `http.client.HTTPSConnection`/`HTTPConnection`,
  `socket.socket`/`create_connection` e o `.open(...)` de um opener do `urllib`;
- `.serve_forever()`, `importlib.import_module`, `__import__`, `exec`, `eval` e `.exec_module(...)`;
- sinal para um processo: `os.kill` (menos `os.kill(pid, 0)`, que só pergunta se ele vive),
  `Popen.terminate()`/`.kill()` sem argumento e `.send_signal(...)`.

Pega o nome por qualquer apelido (`import shutil as sh`, `from os import replace`, `sp = subprocess`,
um opener guardado num nome), a primitiva
passada como valor (`callback=os.unlink`) e o `getattr(os, "replace")` (ou `getattr` de um
módulo desses com nome que não é texto fixo).
"""

# pylint: disable=invalid-name
# `invalid-name`: os métodos `visit_<Nó>` (`visit_Call`, `visit_With`…) são a API do `ast.NodeVisitor`.

import ast
import io
import re
import tokenize

MODULE_PRIMITIVES = {
    "subprocess": frozenset({"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}),
    "os": frozenset(
        {
            "replace",
            "rename",
            "renames",
            "unlink",
            "remove",
            "rmdir",
            "removedirs",
            "mkdir",
            "makedirs",
            "link",
            "symlink",
            "kill",
            "killpg",
            "system",
            "popen",
            "startfile",
            "posix_spawn",
            "posix_spawnp",
            *(f"exec{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
            *(f"spawn{suffix}" for suffix in ("l", "le", "lp", "lpe", "v", "ve", "vp", "vpe")),
        }
    ),
    "shutil": frozenset({"rmtree", "move", "copy", "copy2", "copyfile", "copytree"}),
    "tempfile": frozenset(
        {"mkstemp", "mkdtemp", "TemporaryDirectory", "NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"}
    ),
    "webbrowser": frozenset({"open", "open_new", "open_new_tab"}),
    "urllib.request": frozenset({"urlopen"}),
    "http.client": frozenset({"HTTPSConnection", "HTTPConnection"}),
    "socket": frozenset({"socket", "create_connection"}),
    "importlib": frozenset({"import_module"}),
}
# `os.open` só conta com flag de escrita; estas flags, sozinhas, só leem.
READ_FLAGS = frozenset(
    {"O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_BINARY", "O_NONBLOCK", "O_NOATIME", "O_NOCTTY"}
)
# Métodos de `Path` sem homônimo comum: contam chamados ou passados como valor.
PATH_METHODS = frozenset(
    {"unlink", "mkdir", "write_text", "write_bytes", "rmdir", "touch", "symlink_to", "hardlink_to"}
)
# `str.replace(a, b)` e `dict`/`Enum` não têm um argumento só: `x.replace(y)` é o de `Path`.
PATH_ONE_ARG = frozenset({"rename", "replace"})
BUILTINS = frozenset({"__import__", "exec", "eval"})
# Só estes abrem um bloco que vale como efeito registrado: `with effects.op(...)`, `batch`, `load_plugin`.
GRANTING = frozenset({"op", "batch", "load_plugin"})
# Sinal para um processo filho (`Popen.terminate()`, `.kill()`, `.send_signal(sig)`).
PROCESS_METHODS = frozenset({"terminate", "kill", "send_signal"})
WRITE_MODE = re.compile(r"[wax+]")
MARKER = re.compile(r"#\s*effect-exempt(?:\((?P<count>\d+)\))?:(?P<reason>.*)$")
MIN_REASON = 20
LONG_REASON = 40
MIN_WORDS = 4
VOCABULARY = re.compile(
    r"\b(recurs|log|lock|rotat|diagnostic|audit|emitter|child|wrapper|test|fixture|stdlib|read)",
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(
    r"(todo|tbd|fixme|xxx+|n/?a|ok|okay|ignore|reason|motivo|because|exempt|effect|best effort|fine|safe)\W*",
    re.IGNORECASE,
)


def _bind_import(node, modules):
    """`import x`, `import x.y` (liga `x`; o atributo completo é lido em `_dotted`) e `import x as y`."""
    for alias in node.names:
        head = alias.name.split(".")[0]
        modules[alias.asname or head] = alias.name if alias.asname else head


def _bind_from(node, modules, names, effects, granting):
    """`from x import y [as z]`: módulo de primitivas, primitiva solta, `effects` ou uma função dele."""
    module = node.module or ""
    for alias in node.names:
        bound = alias.asname or alias.name
        if alias.name == "effects" and node.level:
            effects.add(bound)
        elif module.endswith("effects"):
            granting[bound] = alias.name
        elif module in MODULE_PRIMITIVES and alias.name in MODULE_PRIMITIVES[module]:
            names[bound] = (module, alias.name)
        elif f"{module}.{alias.name}" in MODULE_PRIMITIVES:
            modules[bound] = f"{module}.{alias.name}"


def _aliases(tree):
    """({apelido: módulo}, {nome importado: (módulo, primitiva)}, apelidos de `effects`, {nome: função
    de `effects`}); `sp = subprocess` também liga um apelido."""
    modules, names, effects, granting = {}, {}, set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _bind_import(node, modules)
        elif isinstance(node, ast.ImportFrom):
            _bind_from(node, modules, names, effects, granting)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            module = _dotted(node.value, modules)
            if module is not None:
                modules[node.targets[0].id] = module
    return modules, names, effects, granting


def _dotted(node, modules):
    """O módulo (`os`, `urllib.request`…) que `node` nomeia, ou `None`."""
    if isinstance(node, ast.Name):
        return modules.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, modules)
        return f"{base}.{node.attr}" if base else None
    return None


def _text(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _flag_names(node, constants, seen=frozenset()):
    """Nomes das flags de `os.open` (`os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)`, ou uma constante
    do módulo que vale isso), ou `None` se não dá para ler."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _flag_names(node.left, constants, seen), _flag_names(node.right, constants, seen)
        return None if left is None or right is None else left | right
    if isinstance(node, ast.Attribute) and node.attr.startswith("O_"):
        return {node.attr}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and node.args[1:]:
        name = _text(node.args[1])
        return {name} if name else None
    if isinstance(node, ast.Name) and node.id in constants and node.id not in seen:
        return _constant_flags(node.id, constants, seen)
    if isinstance(node, ast.Constant) and node.value == 0:
        return set()
    return None


# Área de cada bloco além de `fs`: `proc.run` cobre processo, `net.get`/`server.*` rede, `plugin.*` código.
AREAS = {"proc": ("proc",), "net": ("net",), "server": ("net",), "plugin": ("code",), "code": ("code",)}
PROC_NAMES = frozenset({"terminate", "kill", "send_signal", "killpg", "system", "popen", "startfile"})
NET_NAMES = frozenset({"urlopen", "HTTPSConnection", "HTTPConnection", "socket", "create_connection", "serve_forever"})
CODE_NAMES = frozenset({"import_module", "exec_module", "exec", "eval", "__import__"})


def _category(node, modules):
    """`proc`, `net`, `code` ou `fs`: a área da primitiva em `node` (uma chamada ou um valor)."""
    func = node.func if isinstance(node, ast.Call) else node
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    module = _dotted(func.value, modules) if isinstance(func, ast.Attribute) else None
    if module == "subprocess" or name in PROC_NAMES or name.startswith(("exec", "spawn", "posix_spawn")):
        return "code" if name in CODE_NAMES else "proc"
    if name in CODE_NAMES:
        return "code"
    if module in {"urllib.request", "http.client", "socket", "webbrowser"} or name in NET_NAMES:
        return "net"
    if name == "open" and isinstance(func, ast.Attribute) and module is None and _mode_arg(node, 0) is None:
        return "net"  # o `.open(request)` de um opener do `urllib`
    return "fs"


def _operation_of(call):
    """O nome da operação de `effects.op(...)`/`batch(...)` como texto fixo (`load_plugin` não tem
    nome: vale `plugin.load`), ou None quando ele não é um texto fixo e o guard não sabe a área."""
    func = call.func
    if getattr(func, "attr", None) == "load_plugin" or getattr(func, "id", None) == "load_plugin":
        return "plugin.load"
    if call.args:
        return _text(call.args[0])
    keyword = next((kw for kw in call.keywords if kw.arg == "operation"), None)
    return _text(keyword.value) if keyword is not None else None


def _openers(tree):
    """Nomes que recebem um opener do `urllib` (`o = urllib.request.build_opener()`, `o = _opener()`)."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if "opener" in name:
                found.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return found


def _constant_flags(name, constants, seen):
    """As flags de uma constante: a atribuição e todo `|=` dela; `None` se alguma não dá para ler."""
    found = set()
    for value in constants[name]:
        names = _flag_names(value, constants, seen | {name})
        if names is None:
            return None
        found |= names
    return found


def _scope_nodes(scope):
    """Os nós de um escopo (o módulo ou uma função), sem entrar nas funções de dentro."""
    pending = list(scope.body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            pending.extend(ast.iter_child_nodes(node))


def _constants(scope):
    """{nome: [expressões]} de `NOME = expressão` (alvo único) e de `NOME |= expressão` no escopo; um
    nome atribuído mais de uma vez com valores diferentes não conta (não dá para saber qual vale)."""
    found, ambiguous, widened = {}, set(), {}
    for node in _scope_nodes(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in found and ast.dump(found[name]) != ast.dump(node.value):
                ambiguous.add(name)
            found.setdefault(name, node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            # `flags |= os.O_WRONLY` alarga a constante; outro operador a deixa ilegível.
            widened.setdefault(node.target.id, []).append(node.value if isinstance(node.op, ast.BitOr) else None)
    constants = {name: [value] for name, value in found.items() if name not in ambiguous}
    for name, values in widened.items():
        if name in constants:
            constants[name] += values
    return {name: values for name, values in constants.items() if None not in values}


def _mode_arg(call, index):
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return call.args[index] if len(call.args) > index else None


def _writes(mode):
    """O modo de `open` grava? Ausente é leitura; o que não é texto fixo conta como escrita."""
    if mode is None:
        return False
    text = _text(mode)
    return text is None or WRITE_MODE.search(text) is not None


# Um atributo por tabela que a varredura consulta (apelidos, `effects`, constantes, openers, blocos).
class _Scanner(ast.NodeVisitor):  # pylint: disable=too-many-instance-attributes
    def __init__(self, tree):
        self.modules, self.names, self.effects, self.granting = _aliases(tree)
        self.scopes = [_constants(tree)] if isinstance(tree, ast.Module) else [{}]
        self.openers = _openers(tree)
        self.depth = 0
        # Linhas de `effects.op(...)`/`batch(...)` cujo nome de operação não é texto fixo.
        self.dynamic = []
        # Áreas que os blocos `effects` em volta registram (`fs`, `proc`, `net`, `code`).
        self.granted = []
        self.hits = []

    @property
    def constants(self):
        """As constantes que valem aqui: as da função em volta por cima das do módulo."""
        merged = {}
        for scope in self.scopes:
            merged.update(scope)
        return merged

    def _hit(self, node):
        if not self.depth or _category(node, self.modules) not in set().union(*self.granted):
            self.hits.append((node.lineno, node.end_lineno or node.lineno))

    # Anotação de tipo (`proc: subprocess.Popen`, `-> Path`) não chama nada.
    def visit_arg(self, node):
        return None

    def visit_FunctionDef(self, node):
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._visit_function(node)

    def _visit_function(self, node):
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]:
            self.visit(default)
        # Uma função definida dentro de um bloco `effects` roda depois, fora dele: não herda o bloco.
        depth, granted, self.depth, self.granted = self.depth, self.granted, 0, []
        self.scopes.append(_constants(node))
        for statement in node.body:
            self.visit(statement)
        self.scopes.pop()
        self.depth, self.granted = depth, granted

    def visit_Lambda(self, node):
        for default in [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]:
            self.visit(default)
        depth, granted, self.depth, self.granted = self.depth, self.granted, 0, []
        self.visit(node.body)
        self.depth, self.granted = depth, granted

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)

    def visit_With(self, node):
        self._visit_with(node)

    def visit_AsyncWith(self, node):
        self._visit_with(node)

    def _visit_with(self, node):
        # `with effects.op(...) as o, path.open("w") as f:` — what comes after the op is inside it.
        inside = 0
        for item in node.items:
            self.visit(item)
            if self._effects_call(item.context_expr) and _operation_of(item.context_expr) is None:
                self.dynamic.append(item.context_expr.lineno)
            if not inside and self._effects_call(item.context_expr) and _operation_of(item.context_expr):
                inside = 1
                self.depth += 1
                self.granted.append(self._areas(item.context_expr))
        for statement in node.body:
            self.visit(statement)
        self.depth -= inside
        if inside:
            self.granted.pop()

    def _areas(self, call):
        """O que o bloco registra: arquivos sempre; processo, rede ou código só num bloco dessa área
        (`proc.*`, `net.*`/`server.*`, `plugin.*`/`code.*`, `load_plugin`). Um `fs.write` não cobre
        um `subprocess.run` que por acaso está dentro dele. Nome que não é texto fixo cobre tudo."""
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else self.granting.get(getattr(func, "id", ""), "")
        if name == "load_plugin":
            return {"fs", "code"}
        return {"fs", *AREAS.get(str(_operation_of(call)).split(".", 1)[0], ())}

    def _effects_call(self, node):
        """`effects.op(...)`, `effects.batch(...)`, `effects.load_plugin(...)` (ou o mesmo importado com
        `from .effects import op`): só esses registram o bloco inteiro."""
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id in self.effects and func.attr in GRANTING
        return isinstance(func, ast.Name) and self.granting.get(func.id) in GRANTING

    def visit_Call(self, node):
        if self._primitive_call(node):
            self._hit(node)
            # A função de uma chamada já contada não conta de novo como valor passado.
            for child in [*node.args, *node.keywords]:
                self.visit(child)
            if isinstance(node.func, ast.Attribute):
                self.visit(node.func.value)
            return
        # Não é primitiva (`os.kill(pid, 0)`, `effects.unlink(...)`): a função chamada não conta
        # como primitiva passada como valor; o resto da chamada é visitado normalmente.
        self.visit(node.func.value if isinstance(node.func, ast.Attribute) else node.func)
        for child in [*node.args, *node.keywords]:
            self.visit(child)

    def _primitive_call(self, node):
        func = node.func
        module = _dotted(func.value, self.modules) if isinstance(func, ast.Attribute) else None
        if module is not None:
            return self._module_call(node, module, func.attr)
        if isinstance(func, ast.Name):
            return self._name_call(node, func.id)
        if isinstance(func, ast.Attribute) and not self._is_effects(func.value):
            return self._method_call(node, func, self.openers)
        return False

    def _module_call(self, node, module, attr):
        """`<módulo>.<função>(...)`: `os.kill(pid, 0)` só pergunta; `os.open`, `io.open` e `os.fdopen`
        contam só quando gravam."""
        if (module, attr) == ("os", "kill"):
            args = node.args
            return not (len(args) > 1 and isinstance(args[1], ast.Constant) and args[1].value == 0)
        if (module, attr) == ("os", "open"):
            flags = _flag_names(node.args[1], self.constants) if len(node.args) > 1 else set()
            return flags is None or not flags <= READ_FLAGS
        if (module, attr) in {("io", "open"), ("os", "fdopen")}:
            return _writes(_mode_arg(node, 1))
        if module in MODULE_PRIMITIVES:
            return attr in MODULE_PRIMITIVES[module]
        # Um módulo qualquer (`pathlib`, `json`…): o atributo dele ainda pode ser um `Path`.
        return self._method_call(node, node.func, self.openers)

    def _is_effects(self, node):
        return isinstance(node, ast.Name) and (node.id in self.effects or node.id in self.granting)

    def _name_call(self, node, name):
        if name in self.names or name in BUILTINS:
            return True
        if name == "open":
            return _writes(_mode_arg(node, 1))
        if name == "getattr" and len(node.args) > 1:
            module = _dotted(node.args[0], self.modules)
            attr = _text(node.args[1])
            if module in MODULE_PRIMITIVES:
                return attr is None or attr in MODULE_PRIMITIVES[module] or (module == "os" and attr == "open")
            return attr in PATH_METHODS or attr in PATH_ONE_ARG or attr == "serve_forever"
        return False

    @staticmethod
    def _method_call(node, func, openers):
        attr = func.attr
        if attr in PATH_METHODS or attr in {"serve_forever", "exec_module"}:
            return True
        if attr in PROCESS_METHODS:
            return attr == "send_signal" or not (node.args or node.keywords)
        if attr in PATH_ONE_ARG:
            keywords = [keyword.arg for keyword in node.keywords]
            return (len(node.args) == 1 and not keywords) or (not node.args and keywords == ["target"])
        if attr == "open":
            receiver = func.value
            name = receiver.id if isinstance(receiver, ast.Name) else ""
            called = receiver.func if isinstance(receiver, ast.Call) else None
            opener = (
                "opener" in name
                or name in openers
                or (called is not None and "opener" in (getattr(called, "id", "") or getattr(called, "attr", "")))
            )
            return opener or _writes(_mode_arg(node, 0))
        return False

    def visit_Attribute(self, node):
        # Primitiva passada como valor (`callback=os.unlink`, `(os.unlink, os.rmdir)`).
        if isinstance(node.ctx, ast.Load):
            module = _dotted(node.value, self.modules)
            if module in MODULE_PRIMITIVES and node.attr in MODULE_PRIMITIVES[module]:
                self._hit(node)
                return
            if module is None and node.attr in PATH_METHODS and not self._is_effects(node.value):
                self._hit(node)
                return
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in self.names:
            self._hit(node)


def primitive_spans(tree):
    """[(primeira linha, última linha)] de cada primitiva fora de um bloco `effects`."""
    return _scan(tree)[0]


def _scan(tree):
    scanner = _Scanner(tree)
    scanner.visit(tree)
    return sorted(scanner.hits), sorted(scanner.dynamic)


def markers(source):
    """{linha coberta: (quantas, motivo)} de cada `# effect-exempt:` (como `_existence_guard.markers`)."""
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


def bare_effects(source):
    """(linha, código, problema) de cada primitiva sem `effects` nem marcador válido, e de cada
    marcador que não cobre primitiva nenhuma.

    Um marcador cobre a primitiva cuja chamada passa pela linha dele (a do fim de uma linha
    de código) ou começa logo abaixo dele (um comentário sozinho): uma chamada que o
    formatador quebrou em várias linhas leva o marcador em qualquer uma delas.
    """
    lines = source.splitlines()
    covered = markers(source)
    counts = {}
    spans, dynamic = _scan(ast.parse(source))
    for first, last in spans:
        row = next((row for row in range(first, last + 1) if row in covered), first)
        counts[row] = counts.get(row, 0) + 1
    found = []
    for row in sorted(counts):
        code = lines[row - 1].strip()
        if row not in covered:
            found.append((row, code, "sem effects"))
            continue
        count, reason = covered[row]
        if count != counts[row]:
            found.append((row, code, f"marcador cobre {count} de {counts[row]}"))
        elif weak(reason):
            found.append((row, code, "motivo vago"))
    found.extend(
        (row, lines[row - 1].strip() if row <= len(lines) else "", "marcador sem primitiva")
        for row in sorted(set(covered) - set(counts))
    )
    # Nome de operação que não é texto fixo: nem o guard sabe a área, nem o log fica com nomes fixos.
    found.extend((row, lines[row - 1].strip(), "operação não literal") for row in dynamic)
    return sorted(found, key=lambda item: item[0])

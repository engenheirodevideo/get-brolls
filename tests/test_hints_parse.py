"""Toda dica do getbrolls cita subcomando, flag e escolha que existem (docs/CODE_STYLE.md, "Comando novo").

Fontes, lidas do código e dos documentos sem rodar comando nenhum (cada uma com um rótulo):
- `template`: os degraus de `guidance.TEMPLATES` (o `do.command` de `status`, `brief` e `deliver`);
- `cli_hint`: cada chamada a `cli_hint(...)`;
- `rename`: o nome novo de cada `Rename(...)`, `renamed.usage(...)` e `renamed.unavailable(...)` (o antigo, não: é
  o que saiu);
- `span`: todo trecho entre crases de um texto do produto (docstring não conta) que começa por um subcomando, por
  um comando que não existe (`draft --action new`: palavra seguida de `--flag`, fora de `NOT_GETBROLLS`) ou por uma
  flag (vale pelo comando do trecho anterior do mesmo texto);
- `concat`: o mesmo, num texto montado por `+` (os pedaços literais se juntam; o resto vira `VALOR`);
- `prefix`: o texto que começa pelo prefixo da CLI (`cli_prefix_text()`, `_cli_prefix()`, `_command(...)`);
- `bare`: comando em prosa, sem crases (`rode plugins --action new`): só subcomando, flag e escolha são conferidos;
- `doc`: os mesmos trechos em `SKILL.md`, `commands/` e `references/` (a skill e o que o agente lê).

Lugar para a pessoa preencher é uma palavra em MAIÚSCULAS (`NOME`, `PROJETO`, `RECEITA`) ou `"…"` entre
aspas; `<nome>` e reticências soltas (`...`, `…`) são proibidos, também fora das crases quando vêm depois de uma
flag. Campo de f-string ou argumento que não é texto literal vale por qualquer valor (`VALOR`); numa flag com
`choices` o `VALOR` sozinho vale por qualquer escolha e `showVALOR` por qualquer escolha que comece por `show`,
mas `NOME` não vale (a flag tem lista), e `__KIND__` é marca que o gerador troca. Um `*escopo` passado a
`cli_hint` vale por flags quaisquer e fica de fora. A dica pode abreviar (deixar de fora uma opção obrigatória);
nomear subcomando, flag ou escolha que não existe, ou deixar um posicional que o subcomando recusaria
(`roteiro new`, `--by SEU NOME`), não. Docstring não é lida: não é texto de ajuda.
"""

import argparse
import ast
import glob
import re
import shlex
import unittest
from fnmatch import fnmatchcase
from functools import cache

import _isolation  # noqa: F401  (efeito de import: define GB_HOME)  # pylint: disable=unused-import
from _paths import ROOT

from getbrolls import _paths, brief, cli, cli_parser, guidance

SOURCES = ROOT / "scripts" / "getbrolls"
DOCS = ("SKILL.md", "skills/get-brolls/SKILL.md", "commands/*.md", "references/**/*.md")
PREFIXES = (("getbrolls",), ("python3", "scripts/gb.py"))
PREFIX_CALLS = ("cli_prefix_text", "_cli_prefix", "_command")
PREFIX_TEXT = "getbrolls"
HOLE = "VALOR"
ANGLE_RE = re.compile(r"<[^<>]*>")
QUOTED_RE = re.compile(r'"[^"]*"')
SPAN_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")  # crase simples: ``` abre bloco de código, não trecho
WORD_RE = re.compile(r"[a-z][a-z-]*")
MARKER_RE = re.compile(r"__[A-Z0-9_]+__")  # marca que o gerador troca (`scaffold`), não texto para a pessoa
EXTERNAL = "<externo>"  # o trecho anterior era de outro programa: as flags dele não são do getbrolls
PLACEHOLDER_RE = re.compile(r"--[a-z][a-z0-9-]*\s+(?:<[^<>\s]+>|\.\.\.|…)")
# Comandos que não são do getbrolls e podem aparecer entre crases seguidos de `--flag`.
NOT_GETBROLLS = frozenset(
    {
        "git",
        "cp",
        "ffmpeg",
        "ffprobe",
        "npx",
        "npm",
        "node",
        "pip",
        "pip3",
        "uv",
        "x",
        "yt-dlp",
        "python",
        "python3",
        "hyperframes",
    }
)
# Trechos que o ouro da 2.5 pina byte a byte: só mudam com o ok do Bruno e a regravação do ouro.
# (arquivo, dica, pedaço do texto em volta) → caso do ouro; o pedaço separa duas dicas iguais no mesmo arquivo.
GOLDEN_PINNED: dict[tuple[str, str, str], str] = {
    (
        "scripts/getbrolls/guidance.py",
        "inspect --start",
        "daqui é só um ponto de partida",
    ): "fluxo/brief",
    (
        "scripts/getbrolls/guidance.py",
        "inspect --end",
        "daqui é só um ponto de partida",
    ): "fluxo/brief",
    (
        "scripts/getbrolls/brief_report.py",
        "brief --validate --project ...",
        "Resolva os pontos acima e repita",
    ): "fluxo/brief_validate",
    (
        "scripts/getbrolls/guidance.py",
        "import-review --by SEU NOME",
        "que eu rodo",
    ): "fluxo/status_previewed",
}


@cache
def subparsers():
    cli.build_parser()
    return dict(cli_parser._SUBPARSERS)  # pylint: disable=protected-access


@cache
def global_flags():
    """As flags que valem antes de qualquer subcomando (`--env-file`, `--profile`, `--json`…)."""
    return frozenset(cli.build_parser()._option_string_actions)  # pylint: disable=protected-access


def _call_name(call):
    func = call.func
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def _is_prefix_call(node):
    return isinstance(node, ast.Call) and _call_name(node) in PREFIX_CALLS


def _is_join(call):
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "join"
        and isinstance(func.value, ast.Constant)
        and isinstance(func.value.value, str)
        and len(call.args) == 1
        and isinstance(call.args[0], (ast.Tuple, ast.List))
    )


def _fstring_text(node):
    return "".join(
        part.value
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
        else PREFIX_TEXT
        if isinstance(part, ast.FormattedValue) and _is_prefix_call(part.value)
        else HOLE
        for part in node.values
    )


def _call_text(node):
    name = _call_name(node)
    if name in PREFIX_CALLS[:2]:
        return PREFIX_TEXT
    if name == "_command":
        return " ".join([PREFIX_TEXT, *(text_of(arg) or HOLE for arg in node.args)])
    if _is_join(node):
        parts = [text_of(item) or HOLE for item in node.args[0].elts if not isinstance(item, ast.Starred)]
        return node.func.value.value.join(parts) if any(text_of(i) for i in node.args[0].elts) else None
    return None


def text_of(node):
    """Texto de uma constante, f-string, soma de textos ou comando montado; `None` quando não é texto literal.

    Campo de f-string e parte que não é literal viram `VALOR`; o prefixo da CLI vira `getbrolls`.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _fstring_text(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = text_of(node.left), text_of(node.right)
        return None if left is None and right is None else (left or HOLE) + (right or HOLE)
    return _call_text(node) if isinstance(node, ast.Call) else None


def _docstring_ids(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _is_renamed_call(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "Rename"
    if isinstance(func, ast.Attribute):
        on_renamed = isinstance(func.value, ast.Name) and func.value.id == "renamed"
        return func.attr == "Rename" or (on_renamed and func.attr in ("usage", "unavailable"))
    return False


def _is_cli_hint(call):
    return _call_name(call) == "cli_hint"


def _holds_hint_call(node):
    """A soma que carrega uma chamada de dica (`"…" + cli_hint(…)`) não é lida inteira: a chamada vale sozinha."""
    return any(isinstance(item, ast.Call) and (_is_cli_hint(item) or _is_renamed_call(item)) for item in ast.walk(node))


def _words(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _strip_prefix(tokens):
    for prefix in PREFIXES:
        if tuple(tokens[: len(prefix)]) == prefix:
            return tokens[len(prefix) :]
    if len(tokens) > 1 and tokens[0] == "python3" and tokens[1].endswith("scripts/gb.py"):
        return tokens[2:]  # `python3 "${RAIZ}/scripts/gb.py"` nos documentos da skill
    return tokens


def _span_command(span, previous):
    """O comando que o trecho entre crases diz, ou `None` quando ele não fala do getbrolls."""
    tokens = _strip_prefix(_words(span))
    if not tokens:
        return None
    first = tokens[0]
    if first in subparsers():
        return span
    if first.startswith("--"):
        if previous == EXTERNAL:  # flag de um comando que não é do getbrolls
            return None
        return span if first.partition("=")[0] in global_flags() or not previous else f"{previous} {span}"
    if WORD_RE.fullmatch(first) and first not in NOT_GETBROLLS and len(tokens) > 1 and tokens[1].startswith("--"):
        return span
    return None


def span_commands(text):
    """Os comandos entre crases de `text`: com subcomando, com comando que não existe ou só com flags."""
    found = []
    previous = None
    for span in SPAN_RE.findall(text):
        tokens = _strip_prefix(_words(span))
        if tokens and tokens[0] in NOT_GETBROLLS:
            previous = EXTERNAL
        command = _span_command(span, previous)
        if command is None:
            continue
        found.append(command)
        tokens = _strip_prefix(_words(command))
        # Só o nome (`status`, `preview`) não tira o comando que já vinha com flags: é menção, não comando.
        if tokens and tokens[0] in subparsers() and (len(tokens) > 1 or previous in (None, EXTERNAL)):
            previous = tokens[0]
    return found


def _bare_flag(parser, tokens):
    """Flags (e o valor de cada uma) em sequência a partir de `tokens`; para na primeira palavra solta."""
    options = parser._option_string_actions  # pylint: disable=protected-access
    taken = []
    index = 0
    while index < len(tokens):
        token = re.sub(rf"(?<=[.,;:)]){HOLE}$", "", tokens[index]).rstrip(".,;:)")
        if not token.startswith("--"):
            break
        taken.append(token)
        index += 1
        action = options.get(token.partition("=")[0])
        if action is None:
            break
        if action.nargs != 0 and "=" not in token and index < len(tokens) and not tokens[index].startswith("--"):
            taken.append(re.sub(rf"(?<=[.,;:)]){HOLE}$", "", tokens[index]).strip(".,;:)\"'"))
            index += 1
    return taken


def bare_commands(text):
    """Comandos em prosa (`rode plugins --action new`): subcomando conhecido seguido de flags, sem crases."""
    text = SPAN_RE.sub(" ", text)
    names = "|".join(sorted(map(re.escape, subparsers()), key=len, reverse=True))
    found = []
    for match in re.finditer(rf"(?<![\w./`-])({names})(?=\s+--[a-z])", text):
        taken = _bare_flag(subparsers()[match.group(1)], text[match.end() :].split())
        found.append(" ".join([match.group(1), *taken]))
    # Palavra que não é subcomando mas pede `--action`: `client --action remove` (o nome certo é `clients`).
    unknown = (m for m in re.finditer(r"(?<![\w./`-])([a-z][a-z-]*)\s+--action\s+[a-z]", text))
    found.extend(m.group(0) for m in unknown if m.group(1) not in subparsers() and m.group(1) not in NOT_GETBROLLS)
    return found


def placeholders(text):
    """Marcador `<…>` ou reticências logo depois de uma flag, dentro ou fora das crases."""
    return [match.group(0) for match in PLACEHOLDER_RE.finditer(text)]


def _is_text_node(node):
    """Nó que é um texto literal inteiro e não carrega uma chamada de dica."""
    return isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Call)) and (
        text_of(node) is not None and not _holds_hint_call(node)
    )


def _renamed_text(call):
    """O nome novo de um `Rename(...)`/`renamed.usage(...)`; `None` quando não é texto literal."""
    new = call.args[1] if len(call.args) > 1 else next((kw.value for kw in call.keywords if kw.arg == "new"), None)
    return new, (text_of(new) if new is not None else None)


def hints_in_source(source, where):
    """(arquivo, dica, texto em volta, rótulo) de cada dica do código `source`; docstring não conta."""
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    skip = set()
    found = []

    def text_hints(node, text):
        if any(_is_prefix_call(item) for item in ast.walk(node)):
            if _strip_prefix(text.split())[:1] not in ([], [HOLE]):
                found.append((where, text, text, "prefix"))
            return
        label = "concat" if isinstance(node, ast.BinOp) else "span"
        found.extend((where, command, text, label) for command in span_commands(text))
        found.extend((where, command, text, "bare") for command in bare_commands(text))

    def visit(node):
        if id(node) in skip or id(node) in docstrings:
            return
        if isinstance(node, ast.Call) and node.args and _is_cli_hint(node):
            # `*escopo` vale por flags quaisquer (zero ou mais): fica fora da dica, não vira posicional.
            text = " ".join(text_of(arg) or HOLE for arg in node.args if not isinstance(arg, ast.Starred))
            found.append((where, text, text, "cli_hint"))
            return
        if isinstance(node, ast.Call) and node.args and _is_renamed_call(node):
            new, text = _renamed_text(node)
            found.extend([(where, text, text, "rename")] if text else [])
            skip.update({id(node.args[0]), id(new)})
        elif _is_text_node(node):
            text_hints(node, text_of(node))
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def literals_in_source(source):
    """Cada texto literal do código (sem docstring), com os pedaços de uma soma já juntos."""
    tree = ast.parse(source)
    docstrings = _docstring_ids(tree)
    found = []

    def visit(node):
        if id(node) in docstrings:
            return
        if isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            text = text_of(node)
            if text is not None:
                found.append(text)
                return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def _doc_files():
    return sorted({path for pattern in DOCS for path in ROOT.glob(pattern)})


def doc_hints(text, where):
    """Comandos de um documento: trechos entre crases e linhas de bloco de código que começam por subcomando."""
    found = []
    fenced = False
    pending = ""
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            joined = pending + line.strip().removeprefix("$ ")
            pending = joined.removesuffix("\\").rstrip() + " " if joined.endswith("\\") else ""
            if pending:
                continue
            command = joined
            if _strip_prefix(_words(command))[:1] and _strip_prefix(_words(command))[0] in subparsers():
                found.append((where, command, command, "doc"))
        else:
            found.extend((where, command, line, "doc") for command in span_commands(line))
    return found


def labeled_hints():
    """(arquivo, dica, texto em volta, rótulo) de cada dica do produto."""
    found = []
    for template in guidance.TEMPLATES.values():
        text = template.format(project="PROJETO", candidate="ID")
        found.append(("scripts/getbrolls/guidance.py", text, text, "template"))
    for path in sorted(SOURCES.rglob("*.py")):
        found.extend(hints_in_source(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix()))
    for path in _doc_files():
        found.extend(doc_hints(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix()))
    return found


def hints():
    """(arquivo, dica, texto em volta) de cada dica do produto."""
    return [found[:3] for found in labeled_hints()]


def _pinned(where, text, context):
    return any(where == pin[0] and text == pin[1] and pin[2] in context for pin in GOLDEN_PINNED)


def _text_problems(command):
    problems = []
    if ANGLE_RE.search(command):
        problems.append("marcador com <…>: use MAIÚSCULAS")
    bare = QUOTED_RE.sub("", command)
    if "..." in bare or "…" in bare:
        problems.append('reticências soltas: use MAIÚSCULAS ou "…" entre aspas')
    return problems


def _bad_choice(action, value):
    """Escolha que a flag não tem; `VALOR` sozinho vale por qualquer uma, `xVALOR` por qualquer que case."""
    if action.choices is None or value == HOLE or MARKER_RE.fullmatch(value):
        return False
    pattern = "*".join(glob.escape(part) for part in value.split(HOLE))
    return not any(fnmatchcase(str(choice), pattern) for choice in action.choices)


def _positional_limit(parser):
    """Quantos posicionais o subcomando aceita; `None` = qualquer número.

    Posicional escondido (`help=SUPPRESS`: a forma antiga que só existe para recusar com o caminho novo) não conta.
    """
    limit = 0
    for action in parser._actions:  # pylint: disable=protected-access
        if action.option_strings or action.help == argparse.SUPPRESS:
            continue
        if action.nargs in ("*", "+"):
            return None
        limit += 1
    return limit


def _flag_problems(name, parser, tokens):
    """Flags que o subcomando `name` não tem, escolhas que elas não aceitam e posicional que ele recusaria."""
    options = parser._option_string_actions  # pylint: disable=protected-access
    problems = []
    loose = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token.startswith("--"):
            loose.append(token)
            continue
        flag, equals, value = token.partition("=")
        action = options.get(flag)
        takes_next = not equals and index < len(tokens) and not tokens[index].startswith("--")
        if action is None:
            problems.append(f"{name}: a flag {flag} não existe")
            index += takes_next  # o valor da flag desconhecida não é posicional
            continue
        if action.nargs == 0 or not (equals or takes_next):
            continue
        if not equals:
            value = tokens[index]
            index += 1
        if _bad_choice(action, value):
            problems.append(f"{name} {flag}: {value!r} não é uma escolha")
    limit = _positional_limit(parser)
    if limit is not None and len(loose) > limit:
        problems.append(f"{name}: posicional sobrando ({' '.join(loose[limit:])}): o subcomando não aceita")
    return problems


def problems_of(command):
    """O que está errado na dica `command`; lista vazia quando ela cita só o que existe."""
    problems = _text_problems(command)
    try:
        tokens = _strip_prefix(shlex.split(command))
    except ValueError:
        return [*problems, "aspas sem par"]
    if not tokens:
        return problems
    if tokens[0].startswith("--"):  # trecho que é só flag, sem comando antes: a flag tem que existir em algum
        known = global_flags().union(*(parser._option_string_actions for parser in subparsers().values()))  # pylint: disable=protected-access
        return problems + [
            f"a flag {flag} não existe"
            for flag in tokens
            if flag.startswith("--") and flag.partition("=")[0] not in known
        ]
    parser = subparsers().get(tokens[0])
    if parser is None:
        return [*problems, f"o subcomando {tokens[0]} não existe"]
    return problems + _flag_problems(tokens[0], parser, tokens[1:])


class HintsParseTests(unittest.TestCase):
    project = "projeto"

    def test_every_hint_names_real_commands_flags_and_choices(self):
        bad = [
            f"{where}: {text!r}: {problem}"
            for where, text, context in hints()
            if not _pinned(where, text, context)
            for problem in problems_of(text)
        ]
        self.assertEqual([], bad)

    def test_the_sources_are_found(self):
        texts = [text for _, text, _ in hints()]
        self.assertTrue(any(text.startswith("approve --project PROJETO --candidate ID") for text in texts))
        self.assertIn("init --recipe RECEITA", texts)  # o nome novo de um Rename
        self.assertTrue(any(text.startswith("migrate --action plan") for text in texts))  # um cli_hint
        self.assertTrue(any(text.startswith("roteiro --action sync --project PROJETO") for text in texts))  # crases

    def test_the_checker_catches_what_it_must(self):
        cases = {
            "status --project PROJETO": (),
            'approve --project P --candidate ID --by NOME --channel chat --statement "…"': (),
            "profile --action VALOR": (),
            "plugins --action new --kind __KIND__": (),
            "getbrolls doctor": (),
            "python3 scripts/gb.py status --project PROJETO": (),
            "nada --x": ("subcomando",),
            "status --projeto X": ("não existe",),
            "init --recipe <receita>": ("<…>",),
            "roteiro --action sync --project ...": ("reticências",),
            "roteiro --action sync --project …": ("reticências",),
            "profile --action mostrar": ("escolha",),
            'approve --statement "sem fim': ("aspas",),
            "roteiro new --project P": ("posicional",),
            "import-review --by SEU NOME": ("posicional",),
            "profile show": ("posicional",),  # o posicional escondido só existe para recusar
            "queue --action add URL1 URL2": (),  # `urls` aceita quantos vierem
            "status --projeto X --project P": ("não existe",),  # o valor da flag errada não vira posicional
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                found = problems_of(command)
                self.assertEqual(len(expected), len(found), found)
                for piece, problem in zip(expected, found, strict=True):
                    self.assertIn(piece, problem)

    def test_a_span_with_an_unknown_command_is_a_hint(self):
        self.assertEqual(["draft --action new"], span_commands("rode `draft --action new` agora"))
        self.assertEqual(["getbrolls draft --action new"], span_commands("rode `getbrolls draft --action new`"))
        self.assertEqual([], span_commands("rode `git commit --amend`, `cp --help` e `uv pip --version`"))
        self.assertNotEqual([], problems_of("draft --action new"))
        self.assertEqual([], span_commands("o `foo` e `Draft --x`"))

    def test_a_span_that_starts_with_a_flag_belongs_to_the_previous_command(self):
        self.assertEqual(["preview --start", "preview --end"], span_commands("`preview --start` e `--end`"))
        self.assertEqual(["preview --start", "preview --fim"], span_commands("`preview --start` e `--fim`"))
        self.assertNotEqual([], problems_of("preview --fim"))
        self.assertEqual(["--start/--end"], span_commands("sem `--start/--end`"))
        self.assertNotEqual([], problems_of("--start/--end"))
        self.assertEqual([], problems_of("--force"))

    def test_a_span_split_across_a_concatenation_is_joined(self):
        source = 'x = "Rode `remember --candidate " + ident + " --decision approved|rejected --project PROJETO` antes."'
        found = hints_in_source(source, "x.py")
        self.assertEqual(
            [("concat", "remember --candidate VALOR --decision approved|rejected --project PROJETO")],
            [(label, text) for _, text, _, label in found],
        )
        self.assertNotEqual([], problems_of(found[0][1]))

    def test_the_cli_prefix_producers_are_hints(self):
        cases = {
            'fix = _paths.cli_prefix_text() + " setup"': "getbrolls setup",
            'fix = _command("profile", "--action", "show", "--path", str(path))': (
                "getbrolls profile --action show --path VALOR"
            ),
            'fix = " ".join((_paths.cli_prefix_text(), "profile", "--action", "trust", *rest))': (
                "getbrolls profile --action trust"
            ),
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                found = hints_in_source(source, "x.py")
                self.assertEqual([("prefix", expected)], [(label, text) for _, text, _, label in found])
        found = hints_in_source('fix = _paths.cli_prefix_text() + " setap"', "x.py")
        self.assertNotEqual([], problems_of(found[0][1]))
        self.assertEqual([], hints_in_source("x = _paths.cli_prefix_text() + ' ' + template.format(a=1)", "x.py"))

    def test_a_command_in_prose_without_backticks_is_checked_for_names_only(self):
        self.assertEqual(
            ["plugins --action nope", "status --project X"],
            bare_commands("rode plugins --action nope e depois status --project X agora"),
        )
        self.assertEqual([], bare_commands("o status mostra tudo, veja `review --x` aqui"))
        self.assertEqual(["roteiro --action review"], bare_commands("roteiro --action review exige --expect"))
        source = 'x = "Antes, rode plugins --action nope."'
        self.assertEqual(
            [("bare", "plugins --action nope")],
            [(label, text) for _, text, _, label in hints_in_source(source, "x.py")],
        )

    def test_a_choices_flag_takes_a_hole_only_as_a_pattern(self):
        self.assertEqual([], problems_of("profile --action VALOR"))
        self.assertEqual([], problems_of("profile --action showVALOR"))
        self.assertEqual([], problems_of("plugins --action new --kind __KIND__"))
        for command in ("profile --action xyzVALOR", "profile --action NOME", "profile --action PROJETO"):
            with self.subTest(command=command):
                self.assertEqual(1, len(problems_of(command)), problems_of(command))

    def test_an_angle_placeholder_next_to_a_flag_is_found_anywhere(self):
        self.assertEqual(["--expect <sha256>"], placeholders("roteiro exige --expect <sha256>: o review"))
        self.assertEqual(["--project ..."], placeholders("rode status --project ... agora"))
        self.assertEqual([], placeholders("rode `status --project PROJETO` e --expect REVIEW_SHA256"))

    def test_every_source_kind_is_exercised(self):
        kinds = {label for _, _, _, label in labeled_hints()}
        self.assertEqual({"template", "cli_hint", "rename", "span", "concat", "prefix", "bare", "doc"}, kinds)

    def test_a_golden_pin_still_has_its_problem(self):
        for pin in GOLDEN_PINNED:
            with self.subTest(pin=pin):
                self.assertNotEqual([], problems_of(pin[1]), "o ouro foi regravado: tire o pino")

    def test_the_agent_docs_are_scanned(self):
        docs = {where for where, _, _, label in labeled_hints() if label == "doc"}
        self.assertIn("SKILL.md", docs)
        self.assertIn("commands/get-brolls-review.md", docs)
        self.assertTrue(any(where.startswith("references/") for where in docs))
        self.assertIn("skills/get-brolls/SKILL.md", docs)

    def test_the_beat_commands_parse(self):
        for source, extra in (("youtube", {}), ("local", {}), ("instagram", {})):
            beat = {
                "id": "abertura",
                "intent": "literal",
                "target": "ponte ao amanhecer",
                "narration": "a cidade acorda",
                "allowed_sources": [source],
                "resolved": {},
                **extra,
            }
            with self.subTest(source=source):
                commands = brief.beat_commands(self.project, beat)
                real = {name: text for name, text in commands.items() if name != "note"}
                self.assertIn("inspect", real)
                prefix = _paths.cli_prefix_text() + " "
                for name, text in real.items():
                    self.assertTrue(text.startswith(prefix), text)
                    self.assertEqual([], problems_of(text[len(prefix) :]), name)

    def test_no_placeholder_hangs_off_a_flag_in_any_text(self):
        texts = [
            (path.relative_to(ROOT).as_posix(), text)
            for path in sorted(SOURCES.rglob("*.py"))
            for text in literals_in_source(path.read_text(encoding="utf-8"))
        ]
        texts += [
            (path.relative_to(ROOT).as_posix(), line)
            for path in _doc_files()
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        bad = [
            f"{where}: {found!r}"
            for where, text in texts
            if not any(pin[2] in text for pin in GOLDEN_PINNED)
            for found in placeholders(text)
        ]
        self.assertEqual([], bad)

    def test_every_golden_pinned_hint_still_exists(self):
        found = hints()
        for pin in GOLDEN_PINNED:
            with self.subTest(pin=pin):
                self.assertTrue(any(where == pin[0] and text == pin[1] and pin[2] in ctx for where, text, ctx in found))


if __name__ == "__main__":
    unittest.main()

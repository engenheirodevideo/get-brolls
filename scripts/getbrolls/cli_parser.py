"""The argparse parser of the command line: its usage text, the toolchain subcommands and `build_parser`."""

# pylint: disable=missing-function-docstring
# Legado: ocorrência pré-existente em `parse_args` (corpo idêntico ao código anterior à 2.6.0).

import argparse
import difflib
import json
import os
import platform
import re
import sys
from collections.abc import Callable
from gettext import gettext
from typing import NoReturn, cast

from . import __version__, _paths, presets
from .cli_project_args import _add_project_subcommand_args, _checked, _recipe_ref
from .cli_summaries import EXIT_USAGE_ERROR, SUMMARIES
from .models import now
from .renamed import Rename, message_en, refuse_at_parse
from .sdk.scaffold import KINDS as SCAFFOLD_KINDS

# Cor que o argparse do Python 3.14+ pode pôr no `usage`: fora do JSON.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _stderr_is_tty():
    """Stderr (para onde o erro de uso vai) num terminal? Stream fechado ou sem `isatty` conta como não."""
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _close_match(bad, candidates):
    found = difflib.get_close_matches(bad, candidates, n=1, cutoff=0.6)
    return found[0] if found else None


def _suggest_option(root, ns, extras):
    """A opção mais parecida com a primeira `--flag` desconhecida, no subcomando e na raiz."""
    bad = next((token.split("=", 1)[0] for token in extras if token.startswith("--")), None)
    if bad is None:
        return None
    parsers = [_SUBPARSERS.get(str(getattr(ns, "command", ""))), root]
    candidates = [
        option
        for parser in parsers
        if parser is not None
        for action in parser._actions  # pylint: disable=protected-access
        for option in action.option_strings
        if option.startswith("--")
    ]
    return _close_match(bad, candidates)


# Corte mais frouxo quando o candidato é uma das opções obrigatórias que faltaram:
# `library --query x` → `--search` (o que a pessoa quis dizer é o que falta).
_MISSING_REQUIRED_CUTOFF = 0.5


def _missing_required(parser, args):
    """Opções obrigatórias de `parser` que não aparecem em `args`."""
    given = {token.split("=", 1)[0] for token in args if token.startswith("--")}
    return [
        next(option for option in action.option_strings if option.startswith("--"))
        for action in parser._actions  # pylint: disable=protected-access
        if action.required and action.option_strings and not given & set(action.option_strings)
    ]


def _suggest_missing_required(parser, args):
    """Para "the following arguments are required": a opção parecida com a `--flag` desconhecida."""
    known = {option for action in parser._actions for option in action.option_strings}  # pylint: disable=protected-access
    bad = next((t.split("=", 1)[0] for t in args if t.startswith("--") and t.split("=", 1)[0] not in known), None)
    if bad is None:
        return None
    missing = _missing_required(parser, args)
    found = _close_match(bad, [option for option in known if option.startswith("--")])
    if found is None:
        close = difflib.get_close_matches(bad, missing, n=1, cutoff=_MISSING_REQUIRED_CUTOFF)
        found = close[0] if close else (missing[0] if len(missing) == 1 else None)
    return found


# Mensagens comuns do argparse em pt-BR. O original (inglês) continua em `error_en`.
# Padrões das versões 3.11–3.14; mensagem que nenhum casa sai como veio.
_PT_BR_USAGE = (
    (re.compile(r"^the following arguments are required: (?P<a>.+)$"), "faltam argumentos obrigatórios: {a}"),
    (re.compile(r"^one of the arguments (?P<a>.+) is required$"), "um destes argumentos é obrigatório: {a}"),
    (re.compile(r"^unrecognized arguments: (?P<a>.+)$"), "argumentos não reconhecidos: {a}"),
    (
        re.compile(r"^argument (?P<a>.+?): invalid choice: (?P<v>.+?) \(choose from (?P<c>.+)\)$"),
        "argumento {a}: opção inválida: {v} (escolha entre {c})",
    ),
    (re.compile(r"^argument (?P<a>.+?): expected one argument$"), "argumento {a}: falta o valor (esperava um)"),
    (
        re.compile(r"^argument (?P<a>.+?): expected at least one argument$"),
        "argumento {a}: esperava pelo menos um valor",
    ),
    (
        re.compile(r"^argument (?P<a>.+?): not allowed with argument (?P<b>.+)$"),
        "argumento {a}: não vale junto com {b}",
    ),
    (
        re.compile(r"^argument (?P<a>.+?): invalid (?P<t>\S+) value: (?P<v>.+)$"),
        "argumento {a}: valor {t} inválido: {v}",
    ),
    (re.compile(r"^ambiguous option: (?P<a>\S+) could match (?P<m>.+)$"), "opção ambígua: {a} pode ser {m}"),
)


def usage_pt_br(message):
    """A mensagem de uso do argparse em pt-BR (nomes, valores e escolhas ficam como vieram)."""
    for pattern, template in _PT_BR_USAGE:
        found = pattern.match(message)
        if found:
            return template.format(**found.groupdict())
    return message


class GbArgumentParser(argparse.ArgumentParser):
    """ArgumentParser cujo erro de uso sai em JSON fora do terminal e sugere o nome parecido.

    A mensagem (`error`) sai em pt-BR (`usage_pt_br`); a do argparse, byte a byte, fica em
    `error_en` para ferramentas. O código de saída é 2. Num terminal, sai o `usage` e
    `prog: erro: …` em pt-BR e, quando há nome parecido, uma linha "Você quis dizer: …?".
    """

    _bad_choice: tuple[str, tuple[str, ...]] | None = None
    _args: tuple[str, ...] = ()
    # Gancho do subcomando: forma antiga reconhecida nos tokens (sem o valor de cada flag que o tem)
    # → o `renamed.Rename` dela. Quem recusa é `renamed`, nunca o parser.
    renamed: Callable[[tuple[str, ...]], Rename | None] | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Os títulos dos grupos e o texto do `-h` que o argparse traz em inglês (`--help` sai em pt-BR por inteiro).
        self._positionals.title = "argumentos posicionais"
        self._optionals.title = "opções"
        self._option_string_actions["-h"].help = "mostra esta ajuda e sai"

    def format_help(self):
        """A ajuda inteira em pt-BR. A linha `usage:` do erro de uso fica como na 2.5 (o ouro pina os bytes dela)."""
        text = super().format_help().removeprefix("usage: ")
        # "uso: " tem 2 caracteres a menos que "usage: ": o recuo das linhas que continuam o uso acompanha.
        head, sep, rest = text.partition("\n\n")
        first, newline, tail = head.partition("\n")
        shifted = "\n".join(line.removeprefix("  ") for line in tail.split("\n")) if newline else ""
        return "uso: " + first + newline + shifted + sep + rest

    def parse_known_args(self, args=None, namespace=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        """Guarda os tokens recebidos para o erro de opção obrigatória sugerir a opção quase digitada."""
        # O argparse não passa ao `error` os tokens que sobraram; guardá-los aqui deixa o
        # erro de opção obrigatória sugerir a opção que a pessoa quase digitou.
        self._args = tuple(sys.argv[1:] if args is None else map(str, args))
        return super().parse_known_args(args, namespace)

    def _check_value(self, action, value):
        try:
            super()._check_value(action, value)
        except argparse.ArgumentError:
            self._bad_choice = (str(value), tuple(map(str, action.choices or ())))
            raise

    def parse_args(self, args=None, namespace=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        """Interpreta os argumentos e recusa tokens soltos com a mensagem de erro do parser."""
        self._bad_choice = None
        ns, extras = self.parse_known_args(args, namespace)
        if extras:
            self.error(
                gettext("unrecognized arguments: %s") % " ".join(extras), suggestion=_suggest_option(self, ns, extras)
            )
        return ns

    def _get_option_tuples(self, option_string):
        """Abreviações de `option_string`: a flag escondida (retirada) não disputa o prefixo com as visíveis.

        `init --tem` é `--tema`, não "`--tema` ou `--template`": o argparse listaria a flag que saiu. Um prefixo
        que só cabe em flags escondidas continua levando a elas (a recusa de `renamed`).
        """
        found = super()._get_option_tuples(option_string)  # pyright: ignore[reportAttributeAccessIssue]
        shown = [item for item in found if item[0].help != argparse.SUPPRESS]
        return shown or found

    def _option_action(self, arg):
        """A opção que `arg` nomeia (nome inteiro ou prefixo único, como o argparse aceita) ou `None`."""
        known = self._option_string_actions
        if arg in known:
            return known[arg]
        found = {id(action): action for name, action in known.items() if arg.startswith("--") and name.startswith(arg)}
        return next(iter(found.values())) if len(found) == 1 else None

    def _without_flag_values(self, args):
        """`args` sem o token que é o valor de uma flag que recebe valor (`--path trust`: o `trust` sai)."""
        kept = []
        takes_value = False
        for arg in args:
            if takes_value:
                takes_value = False
                continue
            kept.append(arg)
            if arg.startswith("-") and "=" not in arg:
                action = self._option_action(arg)
                takes_value = action is not None and action.nargs != 0
        return tuple(kept)

    def error(self, message, suggestion=None) -> NoReturn:
        """Aborta com a mensagem no formato da CLI, sugerindo a opção parecida quando há."""
        found = self.renamed(self._without_flag_values(self._args)) if self.renamed is not None else None
        if suggestion is None and self._bad_choice is not None:
            suggestion = _close_match(*self._bad_choice)
        if suggestion is None and message.startswith(gettext("the following arguments are required: %s") % ""):
            suggestion = _suggest_missing_required(self, self._args)
        self._bad_choice = None
        translated = usage_pt_br(message)
        if found is not None:
            # Mesma recusa e mesmo `renamed_refused` da execução: o parser só passa o `Rename` adiante.
            translated, suggestion, message = (
                str(refuse_at_parse(found)),
                found.suggestion,
                message_en(found.old, found.new),
            )
        if _stderr_is_tty():
            self.print_usage(sys.stderr)
            sys.stderr.write(f"{self.prog}: erro: {translated}\n")
            if suggestion:
                sys.stderr.write(f"Você quis dizer: {suggestion}?\n")
        else:
            payload = {
                "error": translated,
                "error_en": message,
                "error_code": "USAGE_ERROR",
                "usage": _ANSI.sub("", self.format_usage()).strip(),
                "suggestion": suggestion,
                "prog": self.prog,
            }
            if found is not None:
                # O envelope que a execução dá à mesma recusa: qual comando, quando, e a mensagem.
                payload |= {
                    "operation": self.prog.rpartition(" ")[2],
                    "status": "error",
                    "at": now(),
                    "message": translated,
                }
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        self.exit(EXIT_USAGE_ERROR)


def version_line():
    """`getbrolls <versão> (Python <x.y.z>; dados: wheel|checkout|ausentes)`."""
    origin = _paths.origin()
    label = "ausentes" if origin == "unknown" else origin
    return f"getbrolls {__version__} (Python {platform.python_version()}; dados: {label})"


class _VersionAction(argparse.Action):
    """`--version`: imprime `version_line()` em stdout e sai com 0."""

    def __init__(self, option_strings, **kwargs):
        kwargs.setdefault("dest", argparse.SUPPRESS)
        kwargs.setdefault("default", argparse.SUPPRESS)
        super().__init__(option_strings, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        del namespace, values, option_string  # só imprime e sai
        print(version_line())
        parser.exit(0)


# Subparsers da última `build_parser()`: um erro de uso validado depois do parse
# (ex.: `permit --preset`) sai no mesmo formato do argparse daquele subcomando.
_SUBPARSERS: dict[str, argparse.ArgumentParser] = {}


def _check_preset_name(args):
    """`permit --preset <nome>` desconhecido é erro de uso, como no 2.5.0: sai
    antes de abrir, travar ou registrar o projeto — nenhum `brolls/` é criado. Nome
    embutido nem olha plugin; os de plugin vêm do manifesto (sem rodar código)."""
    name = getattr(args, "preset", None) if args.command == "permit" else None
    if not name or name in presets.PERMIT_PRESETS:
        return
    valid = presets.names()
    parser = _SUBPARSERS.get("permit")
    if name in valid or parser is None:
        return
    # O próprio argparse monta a mensagem, byte a byte como no 2.5.0 (quando `--preset`
    # tinha `choices=`), em qualquer versão do Python: aspas em cada nome e tudo.
    action = next(a for a in parser._actions if a.dest == "preset")  # pylint: disable=protected-access
    action.choices = valid
    try:
        parser._check_value(action, name)  # pylint: disable=protected-access
    except argparse.ArgumentError as exc:
        parser.error(str(exc))


def _add_client_arguments(p):
    """Flags de `client`: a pasta de cada cliente fica onde a pessoa escolher, registrada no GB_HOME."""
    p.add_argument(
        "--action",
        required=True,
        choices=["add", "list", "show", "remove"],
        help=(
            "add: cria (ou reaproveita) <root>/<slug>/ e registra; list/show: só leem o registro e o client.json; "
            "remove: desregistra, sem apagar a pasta"
        ),
    )
    p.add_argument("--slug", help="Slug do cliente: minúsculas, números e - (add/show/remove)")
    p.add_argument("--name", help="Nome de exibição do cliente novo (add); sem ele, o slug")
    p.add_argument("--root", help="Pasta existente, em caminho absoluto, onde nasce <root>/<slug>/ (add)")


def _add_template_arguments(p):
    """Flags de `template`: as receitas saíram daqui (agora `recipe`); as ações antigas só dizem o comando novo."""
    p.add_argument(
        "--action",
        required=True,
        choices=["freeze", "list", "show"],
        help="Ações da versão de testes, que agora só dizem o comando novo: freeze, list e show → recipe",
    )
    # A linha antiga inteira (`template --action freeze --from P --slug S …`) chega à recusa, nunca a
    # "argumentos não reconhecidos": as flags dela ficam aceitas e escondidas.
    for flag, dest in (("--from", "source"), ("--slug", "slug"), ("--client", "client"), ("--ref", "ref"),
                       ("--title", "title"), ("--by", "by")):  # fmt: skip
        p.add_argument(flag, dest=dest, help=argparse.SUPPRESS)
    p.add_argument("--engine-ref", dest="engine_ref", action="append", help=argparse.SUPPRESS)


def _add_recipe_arguments(p):
    """Flags de `recipe` (list e show só leem as pastas de receita dos clientes registrados)."""
    p.add_argument(
        "--action",
        required=True,
        choices=["list", "show"],
        help="list: as versões das receitas dos clientes registrados; show: uma versão, com o sha256 de cada arquivo",
    )
    p.add_argument(
        "--ref",
        type=_checked(_recipe_ref),
        help="Receita: <cliente>.<slug>[@N] ou cat:getbrolls/recipe/<cliente>.<slug>@<N>; sem @N, a última (show)",
    )


def _add_environment_arguments(p):
    """Flags de `environment` (list só lê o inventário das pontes oficiais)."""
    p.add_argument(
        "--action",
        required=True,
        choices=["list"],
        help="list: as pontes de ambiente desta instalação, com versão, sha256 do pin e estado",
    )


# Os comandos que já mostravam `--json` na ajuda. Nos outros ele é aceito e fica fora da ajuda e do
# `usage`: o erro de uso de um comando da 2.5 traz o `usage`, e o ouro da 2.5 o confere byte a byte.
_JSON_SHOWN = ("doctor", "setup", "capabilities", "profile")


def _add_json_flag(p, name):
    """`--json` em todo subcomando (contrato §2): a saída já é JSON; não muda nada nesta versão."""
    shown = "Aceito; a saída já é JSON (reservado para o modo humano)"
    p.add_argument("--json", action="store_true", help=shown if name in _JSON_SHOWN else argparse.SUPPRESS)


def machine_output(args, environ=None):
    """A saída tem que ser JSON de máquina? `--json` ou `GB_OUTPUT=json` (sem contar espaços em volta, como o aviso
    do `config`); outro valor não muda nada."""
    environ = os.environ if environ is None else environ
    return bool(getattr(args, "json", False)) or environ.get("GB_OUTPUT", "").strip() == "json"


def _add_setup_args(p):
    """Flags de `setup`: `--check`, `--upgrade` e `--where` se excluem."""
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Só conferir, sem instalar nada")
    mode.add_argument(
        "--upgrade",
        choices=["ytdlp"],
        help="Atualizar o yt-dlp do runtime gerenciado além da versão fixada",
    )
    mode.add_argument(
        "--where",
        nargs="?",
        const="all",
        choices=["all", "venv", "tools"],
        help="Mostrar onde fica cada parte do runtime (JSON), sem instalar; sem valor, todas",
    )


def _add_doctor_args(p):
    """Flags de `doctor`."""
    # O SKILL.md diz que `--project` vai em todo comando, e a primeira chamada
    # do fluxo é o `doctor`: recusá-lo ali é contradizer a instrução logo na
    # largada. Aceito e ignorado — o diagnóstico é da instalação, não do projeto.
    p.add_argument(
        "--project",
        help="Aceito por uniformidade e ignorado: o diagnóstico é da instalação, não do projeto",
    )
    p.set_defaults(project_not_operated=True)
    p.add_argument(
        "--live",
        action="store_true",
        help="Testar buscas reais/refresh; pode consumir quota de API",
    )


def _add_plugins_more_args(p):
    """As flags de `plugins --action` depois do filtro de busca: `--all`, o pin, a confirmação."""
    p.add_argument(
        "--all",
        action="store_true",
        help=(
            "update: prévia das atualizações de todos os plugins instalados por marketplace, com o comando "
            "de cada um (só prévia; confirme por id)"
        ),
    )
    p.add_argument(
        "--commit",
        help=(
            "install/update: sha completo (40 hex) do commit a instalar; sem ele, a ref é resolvida na "
            "origem (update com --commit volta a um commit anterior); marketplace-add/-update: commit do "
            "índice"
        ),
    )
    p.add_argument(
        "--ref",
        help=(
            "install/marketplace-add: branch, tag ou refs/... a resolver na origem (padrão HEAD); o update "
            "resolve a mesma"
        ),
    )
    p.add_argument("--subdir", help="install: pasta do plugin dentro do repositório git (ex.: plugins/demo)")
    p.add_argument(
        "--allow-rollback",
        action="store_true",
        help=(
            "marketplace-update: aceita fixar um commit do índice que não descende do fixado hoje "
            "(volta atrás ou história reescrita)"
        ),
    )
    p.add_argument(
        "--path",
        help="check: pasta do plugin a validar (executa o register()); new: pasta onde criar o plugin",
    )
    p.add_argument(
        "--kind",
        choices=list(SCAFFOLD_KINDS),
        help="Tipo do plugin gerado por new: fonte, fonte com rota de download, comando ou exportador",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Confirma enable/install/update/remove depois de mostrar manifesto, permissões e origem (ou o "
            "que sai) à pessoa"
        ),
    )
    p.add_argument(
        "--expect",
        help=(
            "sha256 mostrado na prévia (sem --yes) de install/update e do enable de um plugin cujo "
            "conteúdo mudou desde o pin (suspenso ou desligado); obrigatório junto com --yes, "
            "para confirmar que o conteúdo não mudou desde a prévia"
        ),
    )


def _add_plugins_args(p):
    """Flags de `plugins --action`: a ação, o plugin e de onde vem."""
    p.add_argument(
        "--action",
        required=True,
        choices=[
            "list",
            "enable",
            "disable",
            "check",
            "install",
            "update",
            "remove",
            "new",
            "marketplace-add",
            "marketplace-list",
            "marketplace-remove",
            "marketplace-update",
            "search",
        ],
        help=(
            "list: inventário sem executar código; enable/disable: liga/desliga por id; check: valida uma "
            "pasta; install/update: traz de pasta ou git, em dois passos; remove: tira a pasta e o estado, "
            "em dois passos; new: gera um plugin mínimo; marketplace-add/-list/-remove/-update: índices de "
            "plugins fixados por commit; search: procura nos índices em cache, sem rede"
        ),
    )
    p.add_argument(
        "--id",
        help="Id do plugin (enable/disable/update/remove/new); install: <id>@<marketplace> instala pelo índice",
    )
    p.add_argument(
        "--source",
        help=(
            "Pasta local ou URL git (https:// ou git@) do plugin a instalar (install) ou do repositório do "
            "marketplace (marketplace-add)"
        ),
    )
    p.add_argument(
        "--marketplace",
        help=("Nome do marketplace (marketplace-remove; marketplace-update e search, que sem ele usam todos)"),
    )
    p.add_argument("--query", help="search: trecho do id, da descrição ou do tipo de extensão a procurar")
    _add_plugins_more_args(p)


def _add_x_args(p):
    """Flags de `x`: o plugin, o comando dele e os argumentos."""
    p.add_argument("plugin_id", nargs="?", metavar="plugin", help="Id do plugin dono do comando")
    p.add_argument("plugin_command", nargs="?", metavar="comando", help="Nome do comando do plugin")
    p.add_argument("--list", action="store_true", help="Listar os comandos dos plugins habilitados, sem rodar")
    p.add_argument("--project", help="Pasta do projeto que o comando pode ler (somente leitura)")
    p.set_defaults(project_not_operated=True)
    p.add_argument(
        "--arg",
        action="append",
        metavar="CHAVE=VALOR",
        help="Argumento do comando; repita a flag para passar vários",
    )


# Os subcomandos sem `--project` obrigatório, na ordem em que o parser os registra.
_TOOLCHAIN_NAMES = (
    "providers",
    "doctor",
    "plugins",
    "x",
    "setup",
    "capabilities",
    "profile",
    "client",
    "template",
    "recipe",
    "environment",
)


def _add_toolchain_subcommands(sub):
    """Acrescenta os subcomandos sem `--project` obrigatório.

    São eles: providers, doctor, plugins, x, setup, capabilities, profile, client, template, recipe e environment.
    """
    for name in _TOOLCHAIN_NAMES:
        p = sub.add_parser(name, help=SUMMARIES[name], description=SUMMARIES[name])
        _SUBPARSERS[name] = p
        _add_json_flag(p, name)
        adder = _TOOLCHAIN_ARGUMENTS.get(name)
        if adder is not None:
            adder(p)


PROFILE_ACTIONS = ("show", "trust", "untrust")


def _profile_renamed(args):
    """`profile <ação> [arquivo]`, a forma posicional da versão de testes: o `Rename` dela ou `None`."""
    # `arg`, nunca `token`: o ruff (S105) lê `token == "--action"` como senha escrita no código.
    if any(arg == "--action" or arg.startswith("--action=") for arg in args):
        return None
    word = next((arg for arg in args if arg in PROFILE_ACTIONS), None)
    if word is None:
        return None
    return Rename(
        f"profile {word}",
        f"profile --action {word}",
        "profile_positional",
        tail=" O getbrolls.toml vai em `--path`.",
        suggestion=f"--action {word}",
    )


def _add_profile_args(p):
    """Flags de `profile --action show|trust|untrust`."""
    p.add_argument(
        "--action",
        required=True,
        choices=list(PROFILE_ACTIONS),
        help="show: valor e origem de cada campo; trust: confiar (prévia, depois --yes --expect); untrust: desfazer",
    )
    p.add_argument("--path", help="Caminho do getbrolls.toml (padrão: o descoberto)")
    p.add_argument("--project", help="Pasta de onde a busca pelo getbrolls.toml começa (antes da pasta atual)")
    # `--project` só dá o ponto de partida da busca: o comando não opera sobre o projeto nem lê o seu layout.
    p.set_defaults(project_not_operated=True)
    p.add_argument("--yes", action="store_true", help="trust: confirma depois de mostrar a prévia à pessoa")
    p.add_argument("--expect", help="trust: sha256 mostrado na prévia; obrigatório junto com --yes")
    # A forma posicional da versão de testes (`profile show [arquivo]`) recusa com o comando novo.
    p.add_argument("legacy", nargs="*", help=argparse.SUPPRESS)
    p.renamed = _profile_renamed


# Comandos de projeto em que um grupo de flags (os `dest` do argparse) substitui `--project`:
# `init --client C --slug S` cria o projeto em `<pasta do cliente>/projetos/<slug>/`.
PROJECT_ALTERNATIVES: dict[str, tuple[tuple[str, ...], ...]] = {"init": (("client", "slug"),)}
_PROJECT_HELP = "Pasta do projeto que guarda brolls/, fora da instalação da skill"


# As flags próprias de cada subcomando de `_add_toolchain_subcommands` (os demais só têm `--json`).
_TOOLCHAIN_ARGUMENTS = {
    "doctor": _add_doctor_args,
    "plugins": _add_plugins_args,
    "x": _add_x_args,
    "setup": _add_setup_args,
    "profile": _add_profile_args,
    "client": _add_client_arguments,
    "template": _add_template_arguments,
    "recipe": _add_recipe_arguments,
    "environment": _add_environment_arguments,
}


def build_parser():
    """Monta o parser: opções globais e um subparser por subcomando, com as flags específicas de cada um."""
    parser = GbArgumentParser(
        prog="getbrolls",
        description="getbrolls — pesquisar, revisar e coletar trechos por fonte.",
        epilog="Use `<subcomando> --help` para os argumentos de cada etapa.",
    )
    parser.add_argument(
        "--env-file",
        help="Arquivo .env explícito; sem ele: GB_ENV_FILE, depois o .env do checkout, depois $GB_HOME/.env",
    )
    parser.add_argument(
        "--profile",
        help=(
            "getbrolls.toml explícito, ou `off` para não usar perfil; sem ela: GB_PROFILE, depois "
            "getbrolls.toml a partir de --project e da pasta atual"
        ),
    )
    parser.add_argument(
        "--version",
        action=_VersionAction,
        help="Mostrar a versão instalada, o Python e a origem dos dados (wheel ou checkout) e sair",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_toolchain_subcommands(sub)
    for name in (
        "status",
        "search",
        "resolve",
        "inspect",
        "preview",
        "approve",
        "permit",
        "reject",
        "fetch",
        "verify",
        "review",
        "import-review",
        "init",
        "migrate",
        "init-rules",
        "rules",
        "init-brief",
        "brief",
        "remember",
        "references",
        "learn",
        "library",
        "browser-plan",
        "queue",
        "serve",
        "deliver",
        "roteiro",
        "assets",
        "export",
        "analysis",
    ):
        p = sub.add_parser(name, help=SUMMARIES[name], description=SUMMARIES[name])
        _SUBPARSERS[name] = p
        _add_json_flag(p, name)
        alternatives = PROJECT_ALTERNATIVES.get(name, ())
        p.add_argument(
            "--project",
            required=not alternatives,
            help=_PROJECT_HELP
            if not alternatives
            else "Pasta do projeto novo (ou --client e --slug: <pasta do cliente>/projetos/<slug>/)",
        )
        _add_project_subcommand_args(p, name)

    return parser


def _check_project_alternatives(args):
    """`--project` ou um grupo completo de `PROJECT_ALTERNATIVES`, nunca nenhum; `--slug` nunca com `--project`."""
    groups = PROJECT_ALTERNATIVES.get(args.command, ())
    if not groups:
        return
    # Cada subparser é um `GbArgumentParser` (o `add_subparsers` herda a classe): o `error` aceita `suggestion`.
    parser = cast("GbArgumentParser", _SUBPARSERS[args.command])
    project = getattr(args, "project", None)
    if project == "":
        parser.error("argumento --project: não pode ser vazio (diga a pasta do projeto)", suggestion="--project")
    # lenient: dynamic getattr over argparse flag names, not an existence check on a path
    if project is None and not any(all(getattr(args, dest, None) for dest in group) for group in groups):
        named = " ou ".join(" e ".join(f"--{dest.replace('_', '-')}" for dest in group) for group in groups)
        parser.error(f"faltam argumentos obrigatórios: --project (ou {named})", suggestion="--project")
    if project is not None and getattr(args, "slug", None):
        parser.error("argumento --slug: não vale junto com --project (com --project, a pasta já é o projeto)")


def parse_args(argv=None):
    """Interpreta o argv da CLI e valida as alternativas de projeto."""
    args = build_parser().parse_args(argv)
    _check_project_alternatives(args)
    return args

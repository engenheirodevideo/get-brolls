"""Argument contract and structured command output."""

# pylint: disable=missing-function-docstring,broad-exception-caught,too-many-lines
# `too-many-lines`: o contrato inteiro da CLI (parser, erro de uso, saída e códigos de
# saída) fica num módulo só, para quem lê a superfície não caçar em vários arquivos.
# Legado: ocorrências pré-existentes em `parse_args` e `entrypoint` (corpo
# idêntico ao código anterior à 2.6.0).

import argparse
import contextlib
import contextvars
import difflib
import json
import logging
import os
import platform
import re
import sys
import time
import traceback
from gettext import gettext
from typing import NoReturn

from . import __version__, _paths, logs, presets, vocab
from .errors import PrerequisiteError, UsageError
from .runtime import READ_ONLY_ACTIONS, READ_ONLY_COMMANDS, OperationError, audited, error_code_for, exit_code_for
from .sdk.scaffold import KINDS as SCAFFOLD_KINDS

# Named so a caller (script, test, or someone scripting the CLI) never has to hardcode a
# number. Which error leaves with which code is decided in one place: `runtime.ERROR_EXIT`.
EXIT_OK = 0
EXIT_OPERATION_ERROR = 1
EXIT_USAGE_ERROR = 2
EXIT_INTERNAL_ERROR = 3
EXIT_PREREQUISITE = 4
EXIT_INTERRUPTED = 130
EXIT_CODES = (
    (EXIT_OK, "ok", "Deu certo"),
    (EXIT_OPERATION_ERROR, "operation", "Erro de operação ou de dados"),
    (EXIT_USAGE_ERROR, "usage", "Erro de uso: comando, flag ou configuração"),
    (EXIT_INTERNAL_ERROR, "internal", "Erro interno (bug); detalhes só em diagnostics.jsonl"),
    (EXIT_PREREQUISITE, "prerequisite", "Falta um pré-requisito da instalação"),
    (EXIT_INTERRUPTED, "interrupted", "Interrompido (Ctrl+C)"),
)
# Só estes comandos saem 4 com o resultado em stdout (`"ready": false`); nos outros, 4
# é sempre um erro de pré-requisito em JSON no stderr.
PREREQUISITE_COMMANDS = ("doctor", "setup")
_RESULT_EXIT: contextvars.ContextVar[int] = contextvars.ContextVar("getbrolls_result_exit", default=EXIT_OK)

# Uma linha por subcomando: o que ele faz no fluxo coleta → revisão → entrega.
SUMMARIES = {
    "providers": "Listar fontes disponíveis, transporte e chaves configuradas",
    "doctor": "Diagnosticar dependências, caminhos fixados e fontes utilizáveis",
    "plugins": (
        "Listar, instalar, atualizar, criar, habilitar, desabilitar ou validar plugins do SDK (~/.getbrolls/plugins)"
    ),
    "capabilities": (
        "Descrever em JSON os comandos, flags, códigos de saída e comandos de plugin desta instalação (para agentes)"
    ),
    "client": (
        "Registrar, listar, mostrar ou desregistrar pastas de cliente (componentes e templates) no "
        "$GB_HOME/clients.json"
    ),
    "x": "Rodar um comando de plugin habilitado (x --list mostra quais existem); só lê o projeto",
    "setup": (
        "Conferir (--check) o runtime da instalação: venv do yt-dlp, Playwright e FFmpeg, com os comandos que faltam"
    ),
    "status": "Resumir onde o projeto está por etapa, sem alterar arquivos",
    "search": "Pesquisar candidatos numa fonte e registrá-los no projeto (--shot liga ao beat; --dry-run não grava)",
    "resolve": "Registrar um candidato a partir de URL pública ou arquivo local",
    "inspect": "Analisar a fonte (duração, capítulos, legendas) antes de coletar",
    "preview": "Gerar prévia (GIF/contact sheet) do intervalo escolhido",
    "approve": "Registrar aprovação humana já recebida para o intervalo atual",
    "permit": "Registrar as condições reais de uso do trecho antes da coleta",
    "reject": "Marcar candidatos como rejeitados e invalidar suas revisões (--candidate repetível)",
    "fetch": "Produzir o corte final aprovado e permitido em clips/",
    "verify": "Conferir integridade e decodificação dos arquivos coletados",
    "review": "Gerar o Storyboard local em brolls/review.html",
    "import-review": "Importar o JSON de decisões exportado pelo Storyboard",
    "init": "Criar um projeto novo de layout 1: project.json, aroll/, assets/, broll/ e analysis/",
    "migrate": (
        "Adotar o layout 1 num projeto antigo: só acrescenta o project.json, sem mover nada "
        "(--action plan mostra antes; apply grava)"
    ),
    "init-rules": "Criar um RULES.md editável no projeto (--format muda o formato-alvo)",
    "rules": "Mostrar as regras editoriais em vigor no projeto",
    "init-brief": "Criar um BRIEF.md editável com o plano deste vídeo",
    "brief": "Mostrar os beats do vídeo e o comando pronto de cada um",
    "remember": "Registrar referência aprovada ou rejeitada na memória do projeto",
    "references": "Consultar as referências memorizadas do projeto",
    "learn": "Guardar busca, preferência ou trecho útil na biblioteca entre projetos",
    "library": "Consultar a biblioteca entre projetos antes de sair buscando",
    "browser-plan": "Planejar a captura de uma página pelo navegador autorizado",
    "queue": "Enfileirar URLs sociais e ditar o ritmo do lote (add, next, mark, status)",
    "serve": "Servir brolls/review.html em 127.0.0.1 para abrir o Storyboard no navegador",
    "deliver": "Organizar os trechos coletados em entrega/, uma pasta por beat",
    "roteiro": "Criar, validar, revisar e sincronizar o ROTEIRO.md com os beats do BRIEF.md",
    "assets": (
        "Listar componentes do projeto (marca, lettering, sfx, música, imagem, composições, A-ROLL) e resolver nomes"
    ),
    "export": "Transformar o roteiro revisado num projeto de edição (--to hyperframes) numa pasta nova em exports/",
    "analysis": (
        "Registrar mídias do projeto em analysis/ (id por conteúdo) e listar ou conferir os arquivos de análise"
    ),
}

# Subcomandos que `execute()` (commands.py) de fato leva até
# `sync_formats(ledger, rules, confirm=...)`: os demais retornam antes (serve, queue,
# init-rules, init-brief, brief, learn, library, rules) ou estão em READ_ONLY_CONSULTS
# (references, inspect) — `--confirm-format-change` não tem efeito nenhum lá.
FORMAT_GATE_SUBCOMMANDS = (
    "search",
    "resolve",
    "preview",
    "approve",
    "permit",
    "reject",
    "fetch",
    "verify",
    "review",
    "import-review",
    "remember",
    "browser-plan",
    "deliver",
)


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


class GbArgumentParser(argparse.ArgumentParser):
    """ArgumentParser cujo erro de uso sai em JSON fora do terminal e sugere o nome parecido.

    A mensagem (`error`) é a do argparse, byte a byte; o código de saída é 2.
    Num terminal, sai o texto de sempre (`usage` + `prog: error: …`) e, quando há
    nome parecido, uma linha "Você quis dizer: …?".
    """

    _bad_choice: tuple[str, tuple[str, ...]] | None = None
    _args: tuple[str, ...] = ()

    def parse_known_args(self, args=None, namespace=None):  # pyright: ignore[reportIncompatibleMethodOverride]
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
        self._bad_choice = None
        ns, extras = self.parse_known_args(args, namespace)
        if extras:
            self.error(
                gettext("unrecognized arguments: %s") % " ".join(extras), suggestion=_suggest_option(self, ns, extras)
            )
        return ns

    def error(self, message, suggestion=None) -> NoReturn:
        if suggestion is None and self._bad_choice is not None:
            suggestion = _close_match(*self._bad_choice)
        if suggestion is None and message.startswith(gettext("the following arguments are required: %s") % ""):
            suggestion = _suggest_missing_required(self, self._args)
        self._bad_choice = None
        if _stderr_is_tty():
            self.print_usage(sys.stderr)
            sys.stderr.write(gettext("%(prog)s: error: %(message)s\n") % {"prog": self.prog, "message": message})
            if suggestion:
                sys.stderr.write(f"Você quis dizer: {suggestion}?\n")
        else:
            payload = {
                "error": message,
                "error_code": "USAGE_ERROR",
                "usage": _ANSI.sub("", self.format_usage()).strip(),
                "suggestion": suggestion,
                "prog": self.prog,
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


def _add_toolchain_subcommands(sub):
    """Acrescenta os subcomandos sem `--project` obrigatório.

    São eles: providers, doctor, plugins, x, setup, capabilities e client."""
    for name in ("providers", "doctor", "plugins", "x", "setup", "capabilities", "client"):
        p = sub.add_parser(name, help=SUMMARIES[name], description=SUMMARIES[name])
        _SUBPARSERS[name] = p
        if name in ("doctor", "setup", "capabilities"):
            p.add_argument(
                "--json",
                action="store_true",
                help="Aceito; a saída já é JSON (reservado para o modo humano)",
            )
        if name == "setup":
            p.add_argument("--check", action="store_true", help="Só conferir, sem instalar nada")
        if name == "doctor":
            # O SKILL.md diz que `--project` vai em todo comando, e a primeira chamada
            # do fluxo é o `doctor`: recusá-lo ali é contradizer a instrução logo na
            # largada. Aceito e ignorado — o diagnóstico é da instalação, não do projeto.
            p.add_argument(
                "--project",
                help="Aceito por uniformidade e ignorado: o diagnóstico é da instalação, não do projeto",
            )
            p.add_argument(
                "--live",
                action="store_true",
                help="Testar buscas reais/refresh; pode consumir quota de API",
            )
        if name == "plugins":
            p.add_argument(
                "--action",
                required=True,
                choices=["list", "enable", "disable", "check", "install", "update", "new"],
                help=(
                    "list: inventário sem executar código; enable/disable: liga/desliga por id; check: valida uma "
                    "pasta; install/update: traz de pasta ou git, em dois passos; new: gera um plugin mínimo"
                ),
            )
            p.add_argument("--id", help="Id do plugin (enable/disable/update/new)")
            p.add_argument("--source", help="Pasta local ou URL git (https:// ou git@) do plugin a instalar (install)")
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
                help="Confirma enable/install/update depois de mostrar manifesto, permissões e origem à pessoa",
            )
            p.add_argument(
                "--expect",
                help=(
                    "sha256 mostrado na prévia (sem --yes) de install/update e do enable de um plugin cujo "
                    "conteúdo mudou desde o pin (suspenso ou desligado); obrigatório junto com --yes, "
                    "para confirmar que o conteúdo não mudou desde a prévia"
                ),
            )
        if name == "client":
            _add_client_arguments(p)
        if name == "x":
            p.add_argument("plugin_id", nargs="?", metavar="plugin", help="Id do plugin dono do comando")
            p.add_argument("plugin_command", nargs="?", metavar="comando", help="Nome do comando do plugin")
            p.add_argument("--list", action="store_true", help="Listar os comandos dos plugins habilitados, sem rodar")
            p.add_argument("--project", help="Pasta do projeto que o comando pode ler (somente leitura)")
            p.add_argument(
                "--arg",
                action="append",
                metavar="CHAVE=VALOR",
                help="Argumento do comando; repita a flag para passar vários",
            )


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
        p.add_argument(
            "--project",
            required=True,
            help="Pasta do projeto que guarda brolls/, fora da instalação da skill",
        )
        _add_project_subcommand_args(p, name)

    return parser


def _add_confirm_format_change_arg(p, name):
    """`--confirm-format-change`, visível só nos comandos que chegam a `sync_formats`."""
    # `roteiro`, `assets`, `export` e `analysis` nascem sem a flag: eles nunca chegam a `sync_formats`.
    if name in ("status", "init", "migrate", "roteiro", "assets", "export", "analysis"):
        return
    # Mudar o formato-alvo derruba aprovações humanas; qualquer comando que
    # sincronize formato precisa deste sim explícito antes de apagá-las. Mas
    # `execute()` só chega a `sync_formats` (commands.py) depois de passar
    # pelos retornos antecipados de serve/queue/init-rules/init-brief/brief/
    # learn/library/rules e por cima de READ_ONLY_CONSULTS (references,
    # inspect) — nesses a flag continua aceita (scripts e agentes já a
    # passam para eles) mas some do `--help` porque nunca teve efeito ali.
    reaches_sync_formats = name in FORMAT_GATE_SUBCOMMANDS
    p.add_argument(
        "--confirm-format-change",
        action="store_true",
        help="Confirmar que aprovações já dadas podem ser invalidadas pela mudança de formato"
        if reaches_sync_formats
        else argparse.SUPPRESS,
    )


def _add_roteiro_args(p, name):
    """Flags de `roteiro` (new/check/review/plan/sync)."""
    if name != "roteiro":
        return
    from .roteiro import GENRES

    p.add_argument(
        "--action",
        required=True,
        choices=["new", "check", "review", "plan", "sync"],
        help=(
            "new: esqueleto; check: valida e mostra o plano de cena; review: registra a revisão humana; "
            "plan: mostra o sync sem gravar; sync: grava ids e beats"
        ),
    )
    p.add_argument("--genero", choices=sorted(GENRES), help="Gênero do conteúdo (new)")
    p.add_argument("--tema", help="Tema do vídeo numa linha (new)")
    p.add_argument(
        "--force",
        action="store_true",
        help="Recomeçar do esqueleto; o atual vira ROTEIRO.md.bak, ou uma cópia com data se ele já existe (new)",
    )
    p.add_argument("--by", help="Nome de quem revisou o roteiro (review)")
    p.add_argument("--channel", choices=["chat"], default="chat", help="Por onde a revisão chegou (review)")
    p.add_argument("--statement", help="Frase exata dita por quem revisou (review)")
    p.add_argument(
        "--expect",
        help=(
            "review.sha256 que check e plan mostram, da versão que a pessoa viu; obrigatório no review, "
            "que recusa se o roteiro mudou desde então (review)"
        ),
    )
    p.add_argument(
        "--confirm-target-change",
        action="store_true",
        help="Aceitar que aprovações de beats com alvo novo voltem a pendente (sync)",
    )


def _add_assets_args(p, name):
    """Flags de `assets` (list/where)."""
    if name != "assets":
        return
    from .assets import ASSET_KINDS

    p.add_argument(
        "--action",
        required=True,
        choices=["list", "where"],
        help="list: inventário; where: onde um nome resolve",
    )
    p.add_argument("--kind", choices=sorted(ASSET_KINDS), help="Tipo de componente")
    p.add_argument("--name", help="Nome do componente, sem extensão (where)")


def _add_analysis_args(p, name):
    """Flags de `analysis` (list/check/register)."""
    if name != "analysis":
        return
    from .vocab import MEDIA_ROLES

    p.add_argument(
        "--action",
        required=True,
        choices=["list", "check", "register"],
        help=(
            "list: mídias registradas; check: confere os arquivos de analysis/; register: calcula o id por "
            "conteúdo e grava media.json (list e check só leem)"
        ),
    )
    p.add_argument("--path", help="Mídia a registrar, relativa ao projeto, com / (ex.: aroll/c01.mp4) (register)")
    p.add_argument(
        "--role",
        choices=list(MEDIA_ROLES),
        help="Papel da mídia (register); sem ele, vem da pasta: aroll/, broll/, assets/musica/, assets/sfx/",
    )


def _add_export_args(p, name):
    """Flags de `export`."""
    if name != "export":
        return
    p.add_argument("--to", required=True, help="Exporter de destino, de um plugin habilitado (ex.: hyperframes)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar a pasta, os arquivos e a mídia que o export gravaria, sem gravar nada",
    )


def _add_serve_args(p, name):
    """Flags de `serve`."""
    if name != "serve":
        return
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--background",
        action="store_true",
        help="Subir o servidor num processo solto e devolver a URL na hora (PID em brolls/.serve.pid)",
    )
    g.add_argument(
        "--stop",
        action="store_true",
        help="Encerrar o servidor de fundo pelo PID gravado em brolls/.serve.pid",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="Porta local para o servidor (padrão 8767; se ocupada, usa uma porta livre)",
    )


def _add_deliver_args(p, name):
    """Flags de `deliver`."""
    if name != "deliver":
        return
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar o que iria para entrega/ sem criar, ligar ou apagar nada",
    )


def _add_queue_args(p, name):
    """Flags de `queue` (add/next/mark/status)."""
    if name != "queue":
        return
    mark_help = (
        "mark: item falhou; motivo com 403/429, challenge/login, 'rate limit'/'too many requests' ou as "
        "mensagens de bloqueio da própria skill (sessão de acesso, IP bloqueado, limite de requisições) "
        "abre cooldown"
    )
    p.add_argument(
        "--action",
        choices=["add", "next", "mark", "status"],
        required=True,
        help=(
            "add: enfileirar URLs; next: próximo item ou tempo de espera; mark: registrar resultado; "
            "status: contagens e cooldown"
        ),
    )
    p.add_argument(
        "--provider",
        choices=["instagram", "tiktok", "youtube"],
        help="Fonte das URLs em add; em next, limita a fila a essa fonte",
    )
    p.add_argument("urls", nargs="*", help="URLs públicas a enfileirar (add); repetidas são ignoradas")
    p.add_argument("--url", action="append", help="URL pública a enfileirar (add); pode repetir")
    p.add_argument("--id", help="ID do item retornado por next (mark)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--done", action="store_true", help="mark: item coletado com sucesso; zera o cooldown")
    g.add_argument("--failed", action="store_true", help=mark_help)
    g.add_argument("--skipped", action="store_true", help="mark: item pulado sem tentar")
    p.add_argument("--reason", help="Motivo real registrado no item (mark)")


def _add_candidate_arg(p, name):
    """`--candidate`, com a exigência e a repetição corretas para cada comando."""
    if name == "approve":
        # Repetível de propósito: "aprovei todos" do usuário quer dizer "os que
        # você me mostrou", e só quem mostrou sabe quais foram. Listar os IDs é
        # mais barato que descobrir depois que `--all` pegou um descarte com
        # prévia esquecida em disco.
        p.add_argument(
            "--candidate",
            action="append",
            help="ID do candidato a aprovar; repita a flag para aprovar vários (ou use --all)",
        )
    elif name == "reject":
        # Repetível como `approve`, e pelo mesmo motivo: quem descarta descarta em
        # leva, olhando a mesma lista que mostrou. Sem `--all`: rejeitar em massa o
        # que ninguém viu apagaria candidato bom por engano, e aqui não há `--all`
        # que valha o risco.
        p.add_argument(
            "--candidate",
            action="append",
            required=True,
            help="ID do candidato a rejeitar; repita a flag para rejeitar vários",
        )
    elif name in ("preview", "permit", "fetch", "remember"):
        p.add_argument(
            "--candidate",
            required=True,
            help="ID do candidato retornado por search/resolve",
        )


def _add_fetch_args(p, name):
    """Flags de `fetch`."""
    if name != "fetch":
        return
    p.add_argument(
        "--reacquire",
        action="store_true",
        help=(
            "Rota de plugin cuja licença já foi consumida e cujo arquivo sumiu do cache: "
            "roda a rota de novo (nova licença/cota), só com o ok da pessoa"
        ),
    )


def _add_start_end_args(p, name):
    """`--start`/`--end`, comuns a `preview` e `approve`."""
    if name not in ("preview", "approve"):
        return
    p.add_argument("--start", type=float, help="Início do trecho na origem, em segundos")
    p.add_argument("--end", type=float, help="Fim do trecho na origem, em segundos")


def _add_inspect_args(p, name):
    """Flags de `inspect`."""
    if name != "inspect":
        return
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--candidate",
        help="ID do candidato já registrado; grava só media.duration_s",
    )
    g.add_argument("--url", help="URL pública da fonte, sem registrar candidato")
    p.add_argument(
        "--query",
        help="Fala ou alvo do trecho; pontua as janelas candidatas",
    )
    p.add_argument(
        "--max-windows",
        type=int,
        default=3,
        help="Quantas janelas candidatas devolver, 1–20 (padrão 3)",
    )


def _add_preview_args(p, name):
    """Flags de `preview` além de `--candidate`/`--start`/`--end`."""
    if name != "preview":
        return
    p.add_argument(
        "--scan",
        action="store_true",
        help="Varrer o vídeo inteiro num contact sheet de baixa resolução, sem definir intervalo",
    )
    p.add_argument(
        "--reference-only",
        action="store_true",
        help="Gerar apenas referência estática, sem obter trecho remoto",
    )
    p.add_argument("--narration", help="Fala exata do roteiro")
    p.add_argument("--reason", help="Decisão de coleta desta fonte")


def _add_import_review_args(p, name):
    """Flags de `import-review`."""
    if name != "import-review":
        return
    p.add_argument(
        "--file",
        help="JSON de decisões; sem esta flag usa o mais recente de brolls/reviews/",
    )
    p.add_argument("--by", required=True, help="Nome de quem revisou e assinou as decisões")


def _add_approve_args(p, name):
    """Flags de `approve` além de `--candidate`/`--start`/`--end`."""
    if name != "approve":
        return
    p.add_argument(
        "--by",
        required=True,
        help="Nome de quem já aprovou explicitamente o trecho",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help=(
            "Aplicar a mesma aprovação a todo candidato com prévia gerada e sem "
            "aprovação válida; use só quando todos eles foram mostrados à pessoa"
        ),
    )
    p.add_argument(
        "--channel",
        choices=["chat", "storyboard"],
        default="chat",
        help="Por onde a decisão humana chegou; padrão chat",
    )
    p.add_argument(
        "--statement",
        help="Frase exata dita por quem aprovou, registrada literalmente",
    )


def _add_permit_args(p, name):
    """Flags de `permit`."""
    if name != "permit":
        return
    p.add_argument(
        "--preset",
        # Sem `choices=`: isso exigiria hashear a pasta de todo plugin instalado
        # (`presets.names()` -> `loader.declared`) a cada comando, `--help`
        # incluso, e ignoraria o `GB_PLUGINS`/`GB_HOME` de um `--env-file` (lido
        # depois do parser). `main()` valida logo depois do `.env`, antes de
        # abrir o projeto (`_check_preset_name`), com a mensagem do argparse.
        help=(
            "Condições genéricas da fonte, sempre com o pedido de conferir a "
            "página original. Nomes embutidos: " + ", ".join(sorted(presets.PERMIT_PRESETS)) + "; "
            "ou o nome de um preset de plugin habilitado."
        ),
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--evidence", help="Evidência real fornecida ou verificada")
    g.add_argument(
        "--declaration",
        action="store_true",
        help="Registrar declaração que o usuário preencheu em RULES.md",
    )
    p.add_argument(
        "--declared-by",
        help="Nome de quem declarou a responsabilidade pelo uso, dito no chat",
    )
    p.add_argument(
        "--declaration-text",
        help="Frase literal da declaração de responsabilidade, com 20 caracteres ou mais",
    )


def _add_remember_args(p, name):
    """Flags de `remember`."""
    if name != "remember":
        return
    p.add_argument(
        "--decision",
        choices=["approved", "rejected"],
        required=True,
        help="Decisão humana registrada para esta referência",
    )
    p.add_argument("--reason", required=True, help="Motivo real da decisão registrada")
    p.add_argument("--by", required=True, help="Nome de quem decidiu")


def _add_learn_args(p, name):
    """Flags de `learn`."""
    if name != "learn":
        return
    p.add_argument("--query", help="Busca real que você fez, como digitada na fonte")
    p.add_argument("--provider", help="Fonte onde essa busca rodou (exige --query)")
    p.add_argument(
        "--outcome",
        choices=["hit", "miss"],
        help="hit: a busca rendeu material usável; miss: não rendeu (exige --query)",
    )
    p.add_argument("--preference", help="Preferência editorial dita pela pessoa, literal")
    p.add_argument(
        "--from-candidate",
        help="ID do candidato já memorizado com `remember`, guardado como ponteiro",
    )
    p.add_argument("--shot", help="Beat em que esse trecho foi usado")
    p.add_argument("--note", help="Observação livre, gravada em notes/<sha>.md")
    p.add_argument("--by", help="Nome de quem disse a preferência")


def _add_library_args(p, name):
    """Flags de `library`."""
    if name != "library":
        return
    p.add_argument(
        "--search",
        required=True,
        help="Termo procurado entre assets, buscas e preferências guardadas",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Máximo de resultados por tipo, 1–20 (padrão 5)",
    )


def _checked(parse):
    """Tipo de argparse a partir de um parser que levanta `ValueError`: valor ruim é erro de uso."""

    def convert(text):
        try:
            return parse(text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from None

    convert.__name__ = parse.__name__
    return convert


def _client_slug(text):
    from .layout import check_client

    return check_client(text, "--client")


def _parse_canvas(text):
    from .layout import parse_canvas

    return parse_canvas(text)


def _parse_fps(text):
    from .layout import parse_fps

    return parse_fps(text)


def _add_init_args(p, name):
    """Flags de `init`."""
    if name != "init":
        return
    p.add_argument("--client", type=_checked(_client_slug), help="Slug do cliente do projeto (ex.: acme-corp)")
    p.add_argument(
        "--canvas", type=_checked(_parse_canvas), help="Tamanho do quadro em pixels, LARGURAxALTURA (ex.: 1080x1920)"
    )
    p.add_argument("--fps", type=_checked(_parse_fps), help="Quadros por segundo: inteiro (30) ou fração (30000/1001)")


def _add_migrate_args(p, name):
    """Flags de `migrate`."""
    if name != "migrate":
        return
    p.add_argument(
        "--action",
        required=True,
        choices=["plan", "apply"],
        help="plan: mostra o project.json que seria gravado, sem gravar; apply: grava",
    )
    p.add_argument(
        "--client",
        type=_checked(_client_slug),
        help="Slug do cliente (padrão: o cliente do frontmatter do ROTEIRO.md, se houver)",
    )


def _add_init_rules_args(p, name):
    """Flags de `init-rules`."""
    if name != "init-rules":
        return
    p.add_argument(
        "--mode",
        choices=["per_item_evidence", "user_declaration"],
        help="Modo de direitos gravado no bloco JSON; padrão per_item_evidence",
    )
    p.add_argument(
        "--responsible",
        help="Nome de quem assume a responsabilidade no modo user_declaration",
    )
    p.add_argument(
        "--declaration",
        help="Texto literal da declaração de responsabilidade do usuário",
    )
    p.add_argument(
        "--format",
        dest="video_format",
        choices=list(vocab.FORMATS),
        help="Formato-alvo gravado em video_format; regravar exige --force",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Regravar o RULES.md existente com as escolhas informadas",
    )


def _add_brief_args(p, name):
    """Flags de `brief`."""
    if name != "brief":
        return
    p.add_argument(
        "--validate",
        action="store_true",
        help="Só conferir o BRIEF.md e dizer o que está errado, sem listar comandos",
    )
    p.add_argument(
        "--beat",
        help="Mostrar apenas este beat, pelo id gravado no BRIEF.md",
    )


def _add_browser_plan_args(p, name):
    """Flags de `browser-plan`."""
    if name != "browser-plan":
        return
    p.add_argument("--url", required=True, help="URL pública da página a capturar")


def _add_search_args(p, name):
    """Flags de `search`."""
    if name != "search":
        return
    p.add_argument(
        "--provider",
        default="auto",
        help=(
            "Fonte: youtube, pexels, pixabay, commons, nasa, uma fonte de plugin habilitado ou auto "
            "(padrão); rode `providers` para listar as disponíveis, inclusive as de plugin"
        ),
    )
    p.add_argument("--query", required=True, help="Termos da busca na fonte")
    p.add_argument("--limit", type=int, default=8, help="Máximo de candidatos, 1–50 (padrão 8)")
    p.add_argument(
        "--intent",
        choices=["literal", "illustrative"],
        default="literal",
        help="literal: entidade nomeada; illustrative: ideia genérica",
    )
    p.add_argument(
        "--media",
        choices=["image", "video", "any"],
        default="any",
        help="Tipo de arquivo na fonte: image, video ou any (padrão); só NASA e Commons têm os dois",
    )
    p.add_argument(
        "--shot",
        help="Beat do BRIEF.md a que estes candidatos pertencem, ex.: abertura",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Listar o que a fonte devolveu sem registrar nada no projeto",
    )


def _add_reject_args(p, name):
    """Flags de `reject` além de `--candidate`."""
    if name != "reject":
        return
    p.add_argument(
        "--reason",
        help="Por que este material foi descartado; fica gravado no candidato",
    )


def _add_resolve_args(p, name):
    """Flags de `resolve`."""
    if name != "resolve":
        return
    p.add_argument("--context-image", help="Print opcional da pessoa; permanece estático")
    p.add_argument(
        "--full-preview-file",
        help="Composição pronta contendo apenas este insert, usada em GB_GIF_SCOPE=full",
    )
    p.add_argument(
        "--asset-type",
        choices=["video", "image", "news_screenshot", "web_screenshot"],
        help="Tipo do arquivo local; padrão é inferido pela extensão",
    )
    p.add_argument("--title", help="Título do asset/notícia")
    p.add_argument("--captured-at", help="Data da captura, ISO 8601")
    p.add_argument("--source-url", help="URL pública original do arquivo local")
    p.add_argument("--creator", help="Autor informado da fonte")
    p.add_argument("--shot", help="Identificador único do insert, ex.: insert-02")
    p.add_argument(
        "--intent",
        choices=["literal", "illustrative"],
        default="literal",
        help="literal: entidade nomeada; illustrative: ideia genérica",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--url",
        help="URL pública da fonte (YouTube, Instagram, TikTok, Wikimedia Commons, NASA)",
    )
    g.add_argument("--file", help="Arquivo local já autorizado para importação")


_PROJECT_SUBCOMMAND_ARG_ADDERS = (
    _add_confirm_format_change_arg,
    _add_roteiro_args,
    _add_assets_args,
    _add_analysis_args,
    _add_export_args,
    _add_serve_args,
    _add_deliver_args,
    _add_queue_args,
    _add_candidate_arg,
    _add_fetch_args,
    _add_start_end_args,
    _add_inspect_args,
    _add_preview_args,
    _add_import_review_args,
    _add_approve_args,
    _add_permit_args,
    _add_remember_args,
    _add_learn_args,
    _add_library_args,
    _add_init_args,
    _add_migrate_args,
    _add_init_rules_args,
    _add_brief_args,
    _add_browser_plan_args,
    _add_search_args,
    _add_reject_args,
    _add_resolve_args,
)


def _add_project_subcommand_args(p, name):
    """Acrescenta as flags específicas de `name` ao subparser (todo comando com `--project`)."""
    for add_args in _PROJECT_SUBCOMMAND_ARG_ADDERS:
        add_args(p, name)


def parse_args(argv=None):
    return build_parser().parse_args(argv)


def _given_option_names(argv, args):
    """Names of the options passed, never their values (values can be URLs or free text).

    A token only counts when the parsed namespace has that option: a free-text value
    that happens to start with `--` must not reach the log as if it were a flag name.
    """
    names = []
    for token in argv:
        if not token.startswith("--"):
            continue
        name = token[2:].split("=", 1)[0]
        if hasattr(args, name.replace("-", "_")) and name not in names:
            names.append(name)
    return ",".join(names) if names else None


def _load_env_early(args):
    """Escolhe e carrega o `.env` antes de configurar o log (GB_LOG_LEVEL pode morar nele).

    Erro de uso (`UsageError`: arquivo que falta, `GB_ENV_FILE` ou `GB_HOME` onde não
    podem) vira `OperationError` aqui mesmo, sem tocar no projeto. Outro erro do `.env`
    (chave desconhecida) fica para `execute()`, que o levanta dentro da auditoria; o
    carregamento repetido lá é inofensivo (`setdefault`).
    """
    from .config import load_env_choice

    try:
        load_env_choice(_paths.env_file(args.env_file), warn=False)
    except UsageError as exc:
        raise OperationError(
            {"operation": args.command, "status": "error", "error_code": "USAGE_ERROR", "message": str(exc)}
        ) from None
    except ValueError:
        pass  # `execute()` levanta de novo, dentro da auditoria


def main(argv=None):
    """Faz o parse, configura logging/trava e roda o comando com auditoria e log de início/fim."""
    from .commands import execute, with_summary

    _paths.apply_env_aliases()
    args = parse_args(argv)
    if args.command == "serve" and not (args.background or args.stop):
        # `serve` blocks in serve_forever() and owns its own stdout contract (one JSON
        # line with the URLs, printed by serve.run() itself, then nothing else): it does
        # not go through the JSON-wrapping in entrypoint(), so it exits directly here.
        try:
            raise SystemExit(execute(args))
        except ValueError as exc:
            code = error_code_for(exc)
            print(json.dumps({"error": str(exc), "error_code": code}, ensure_ascii=False))
            raise SystemExit(exit_code_for(code)) from None

    # `.env` que falta ou que tenta definir o que não pode é erro de uso: sai antes do
    # log, da trava e da auditoria, sem criar nada em `brolls/`. (`serve` em primeiro
    # plano, acima, tem contrato próprio de saída e recebe o erro de `execute()`.)
    _load_env_early(args)
    project = getattr(args, "project", None)
    read_only = args.command in READ_ONLY_COMMANDS or (args.command, getattr(args, "action", None)) in READ_ONLY_ACTIONS
    _check_preset_name(args)
    logs.configure(project, read_only=read_only)

    try:
        log = logs.get("cli")
        logs.event(
            log,
            logging.INFO,
            "command_start",
            command=args.command,
            read_only=read_only,
            options=_given_option_names(sys.argv[1:] if argv is None else argv, args),
        )
        started = time.monotonic()
        try:
            result = audited(args, lambda parsed: with_summary(parsed.command, execute(parsed)))
        except OperationError as exc:
            logs.event(
                log,
                logging.INFO,
                "command_end",
                command=args.command,
                status="error",
                error_code=exc.payload.get("error_code"),
                ms=round((time.monotonic() - started) * 1000),
            )
            raise
        logs.event(
            log,
            logging.INFO,
            "command_end",
            command=args.command,
            status="ok",
            error_code=None,
            ms=round((time.monotonic() - started) * 1000),
        )
        _RESULT_EXIT.set(result_exit(args.command, result))
        return result
    finally:
        logs.shutdown()


def result_exit(command, result):
    """Código de saída de um comando que deu certo: 4 só para `doctor`/`setup` com
    `"ready": false` (o resultado sai em stdout do mesmo jeito); 0 para o resto."""
    if command in PREREQUISITE_COMMANDS and isinstance(result, dict) and result.get("ready") is False:
        return EXIT_PREREQUISITE
    return EXIT_OK


def _print_error(payload):
    """Uma linha JSON de erro em stderr, sem traceback nem repr (esses ficam no diagnostics)."""
    clean = {k: v for k, v in payload.items() if k not in ("traceback", "repr")}
    print(json.dumps(clean, ensure_ascii=False), file=sys.stderr)


def _silence_stdout():
    """Depois de um `BrokenPipeError`, aponta stdout para o devnull: o flush da saída do
    interpretador não levanta de novo (nem imprime "Exception ignored ...")."""
    try:
        target = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return  # stdout sem descritor (redirecionado em memória): nada a flushar no fim
    if not isinstance(target, int):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, target)
    except OSError:
        pass
    finally:
        os.close(devnull)


def entrypoint():
    for stream in (sys.stdout, sys.stderr):
        # TextIO não declara `reconfigure`; quem não tiver cai no except.
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    _RESULT_EXIT.set(EXIT_OK)
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
        # Flush aqui, dentro do `try`: com `| head` o erro de pipe chega agora, e não no
        # encerramento do interpretador, onde viraria traceback em stderr.
        sys.stdout.flush()
        return _RESULT_EXIT.get()
    except OperationError as exc:
        _print_error({"error": str(exc), **exc.payload})
        return exit_code_for(exc.payload.get("error_code"))
    except (UsageError, PrerequisiteError) as exc:
        # Levantado fora de `audited` (antes do parse terminar, por exemplo).
        code = error_code_for(exc)
        _print_error({"error": str(exc), "error_code": code})
        return exit_code_for(code)
    except KeyboardInterrupt:
        _print_error({"error": "Operação interrompida.", "error_code": "INTERRUPTED"})
        return EXIT_INTERRUPTED
    except BrokenPipeError:
        # The consumer end of a pipe (e.g. `| head`) closed early; this is an ordinary,
        # expected shutdown, not a bug — do not report it as INTERNAL_ERROR. The command
        # already ran: its exit (4 for `doctor` with `ready: false`) still stands.
        _silence_stdout()
        return _RESULT_EXIT.get()
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary, must exit as JSON not a raw traceback
        return _internal_error(exc)


def _internal_error(exc):
    """Último recurso: grava o traceback no diagnostics e mostra só o tipo do erro."""
    # Anything audited() didn't already turn into an OperationError (e.g. an argparse-time
    # bug) must still exit as JSON, not a raw traceback breaking the CLI's output contract.
    from .runtime import redact, scrub_home, write_diagnostics_log

    project = _project_from_argv()
    event = {
        "operation": None,
        "status": "error",
        "error_code": "INTERNAL_ERROR",
        "type": type(exc).__name__,
        "repr": scrub_home(redact(repr(exc))),
        "traceback": scrub_home(redact(traceback.format_exc())),
    }
    log = write_diagnostics_log(project, event)
    message = f"Erro interno inesperado (bug) [type: {type(exc).__name__}]."
    if log:
        message += f" Detalhes em {log} (diagnostics.jsonl)."
    else:
        message += " Não foi possível gravar diagnostics.jsonl."
    _print_error(
        {
            "error": message,
            "error_code": "INTERNAL_ERROR",
            "type": type(exc).__name__,
            "log": str(log) if log else None,
            "app_log": str(logs.log_path(project)) if logs.log_path(project) else None,
        }
    )
    return EXIT_INTERNAL_ERROR


def _project_from_argv():
    """Best-effort --project value from sys.argv, for diagnostics logging before/around parse_args."""
    argv = sys.argv[1:]
    if "--project" in argv:
        index = argv.index("--project")
        if index + 1 < len(argv):
            return argv[index + 1]
    for item in argv:
        if item.startswith("--project="):
            return item.split("=", 1)[1]
    return None

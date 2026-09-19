"""Argument contract and structured command output."""

import argparse
import contextlib
import json
import logging
import sys
import time
import traceback
from pathlib import Path

from . import __version__, logs
from .presets import PERMIT_PRESETS
from .runtime import READ_ONLY_ACTIONS, READ_ONLY_COMMANDS, OperationError, audited

# Named so a caller (script, test, or someone scripting the CLI) never has to hardcode 2/3.
EXIT_OPERATION_ERROR = 2
EXIT_INTERNAL_ERROR = 3

# Uma linha por subcomando: o que ele faz no fluxo coleta → revisão → entrega.
SUMMARIES = {
    "providers": "Listar fontes disponíveis, transporte e chaves configuradas",
    "doctor": "Diagnosticar dependências, caminhos fixados e fontes utilizáveis",
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


def build_parser():
    parser = argparse.ArgumentParser(
        description="Get B-rolls — pesquisar, revisar e coletar trechos por fonte.",
        epilog="Use `<subcomando> --help` para os argumentos de cada etapa.",
    )
    parser.add_argument("--env-file", help="Arquivo .env explícito; padrão: .env na raiz da skill")
    parser.add_argument(
        "--version",
        action="version",
        version=f"get-brolls {__version__}",
        help="Mostrar a versão instalada da skill e sair",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("providers", "doctor"):
        p = sub.add_parser(name, help=SUMMARIES[name], description=SUMMARIES[name])
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
    ):
        p = sub.add_parser(name, help=SUMMARIES[name], description=SUMMARIES[name])
        p.add_argument(
            "--project",
            required=True,
            help="Pasta do projeto que guarda brolls/, fora da instalação da skill",
        )
        if name != "status":
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
        if name == "serve":
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
        if name == "deliver":
            p.add_argument(
                "--dry-run",
                action="store_true",
                help="Mostrar o que iria para entrega/ sem criar, ligar ou apagar nada",
            )
        if name == "queue":
            p.add_argument(
                "--action",
                choices=["add", "next", "mark", "status"],
                required=True,
                help="add: enfileirar URLs; next: próximo item ou tempo de espera; mark: registrar resultado; status: contagens e cooldown",
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
            g.add_argument(
                "--failed",
                action="store_true",
                help="mark: item falhou; motivo com 403/429, challenge/login, 'rate limit'/'too many requests' ou as mensagens de bloqueio da própria skill (sessão de acesso, IP bloqueado, limite de requisições) abre cooldown",
            )
            g.add_argument("--skipped", action="store_true", help="mark: item pulado sem tentar")
            p.add_argument("--reason", help="Motivo real registrado no item (mark)")
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
        if name in ("preview", "approve"):
            p.add_argument("--start", type=float, help="Início do trecho na origem, em segundos")
            p.add_argument("--end", type=float, help="Fim do trecho na origem, em segundos")
        if name == "inspect":
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
        if name == "preview":
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
        if name == "import-review":
            p.add_argument(
                "--file",
                help="JSON de decisões; sem esta flag usa o mais recente de brolls/reviews/",
            )
            p.add_argument("--by", required=True, help="Nome de quem revisou e assinou as decisões")
        if name == "approve":
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
        if name == "permit":
            p.add_argument(
                "--preset",
                choices=sorted(PERMIT_PRESETS),
                help="Condições genéricas da fonte, sempre com o pedido de conferir a página original",
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
        if name == "remember":
            p.add_argument(
                "--decision",
                choices=["approved", "rejected"],
                required=True,
                help="Decisão humana registrada para esta referência",
            )
            p.add_argument("--reason", required=True, help="Motivo real da decisão registrada")
            p.add_argument("--by", required=True, help="Nome de quem decidiu")
        if name == "learn":
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
        if name == "library":
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
        if name == "init-rules":
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
                choices=["native", "reels", "horizontal"],
                help="Formato-alvo gravado em video_format; regravar exige --force",
            )
            p.add_argument(
                "--force",
                action="store_true",
                help="Regravar o RULES.md existente com as escolhas informadas",
            )
        if name == "brief":
            p.add_argument(
                "--validate",
                action="store_true",
                help="Só conferir o BRIEF.md e dizer o que está errado, sem listar comandos",
            )
            p.add_argument(
                "--beat",
                help="Mostrar apenas este beat, pelo id gravado no BRIEF.md",
            )
        if name == "browser-plan":
            p.add_argument("--url", required=True, help="URL pública da página a capturar")
        if name == "search":
            p.add_argument(
                "--provider",
                default="auto",
                help="Fonte: youtube, pexels, pixabay, commons, nasa ou auto (padrão)",
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
        if name == "reject":
            p.add_argument(
                "--reason",
                help="Por que este material foi descartado; fica gravado no candidato",
            )
        if name == "resolve":
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
    return parser


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


def main(argv=None):
    from .commands import execute, with_summary
    from .config import load_env

    args = parse_args(argv)
    if args.command == "serve" and not (args.background or args.stop):
        # `serve` blocks in serve_forever() and owns its own stdout contract (one JSON
        # line with the URLs, printed by serve.run() itself, then nothing else): it does
        # not go through the JSON-wrapping in entrypoint(), so it exits directly here.
        try:
            raise SystemExit(execute(args))
        except ValueError as exc:
            print(json.dumps({"error": str(exc), "error_code": "INVALID_DATA"}, ensure_ascii=False))
            raise SystemExit(EXIT_OPERATION_ERROR) from None

    project = getattr(args, "project", None)
    read_only = args.command in READ_ONLY_COMMANDS or (args.command, getattr(args, "action", None)) in READ_ONLY_ACTIONS
    # GB_LOG_LEVEL/GB_LOG_STDERR may live only in .env; load it before configuring
    # logging. Harmless to call again inside execute() (setdefault-based); a bad
    # .env here is silently skipped and raised properly by execute() itself.
    with contextlib.suppress(ValueError):
        load_env(args.env_file or Path(__file__).resolve().parents[2] / ".env")
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
        return result
    finally:
        logs.shutdown()


def entrypoint():
    for stream in (sys.stdout, sys.stderr):
        # TextIO não declara `reconfigure`; quem não tiver cai no except.
        with contextlib.suppress(AttributeError, OSError):
            stream.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
        return 0
    except OperationError as exc:
        print(
            json.dumps({"error": str(exc), **exc.payload}, ensure_ascii=False),
            file=sys.stderr,
        )
        return EXIT_OPERATION_ERROR
    except BrokenPipeError:
        # The consumer end of a pipe (e.g. `| head`) closed early; this is an ordinary,
        # expected shutdown, not a bug — do not report it as INTERNAL_ERROR.
        with contextlib.suppress(Exception):
            sys.stdout.close()
        return 0
    except Exception as exc:  # noqa: BLE001 - last-resort CLI boundary, must exit as JSON not a raw traceback
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
        log = write_diagnostics_log(project, event) if project else None
        message = "Erro interno inesperado."
        if log:
            message += f" Detalhes em {log} (diagnostics.jsonl)."
        else:
            message += " Consulte diagnostics.jsonl no projeto (--project), se disponível."
        print(
            json.dumps(
                {
                    "error": message,
                    "error_code": "INTERNAL_ERROR",
                    "type": type(exc).__name__,
                    "message": redact(repr(exc)),
                    "traceback": event["traceback"],
                    "app_log": str(logs.log_path(project)) if logs.log_path(project) else None,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
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

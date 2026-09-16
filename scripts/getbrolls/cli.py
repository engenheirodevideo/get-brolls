"""Argument contract and structured command output."""

import argparse, json, sys
from . import __version__
from .runtime import audited, OperationError

# Uma linha por subcomando: o que ele faz no fluxo coleta → revisão → entrega.
SUMMARIES = {
    "providers": "Listar fontes disponíveis, transporte e chaves configuradas",
    "doctor": "Diagnosticar dependências, caminhos fixados e fontes utilizáveis",
    "status": "Resumir onde o projeto está por etapa, sem alterar arquivos",
    "search": "Pesquisar candidatos numa fonte e registrá-los no projeto",
    "resolve": "Registrar um candidato a partir de URL pública ou arquivo local",
    "preview": "Gerar prévia (GIF/contact sheet) do intervalo escolhido",
    "approve": "Registrar aprovação humana já recebida para o intervalo atual",
    "permit": "Registrar as condições reais de uso do trecho antes da coleta",
    "reject": "Marcar o candidato como rejeitado e invalidar sua revisão",
    "fetch": "Produzir o corte final aprovado e permitido em clips/",
    "verify": "Conferir integridade e decodificação dos arquivos coletados",
    "review": "Gerar o Storyboard local em brolls/review.html",
    "import-review": "Importar o JSON de decisões exportado pelo Storyboard",
    "init-rules": "Criar um RULES.md editável no projeto",
    "rules": "Mostrar as regras editoriais em vigor no projeto",
    "remember": "Registrar referência aprovada ou rejeitada na memória do projeto",
    "references": "Consultar as referências memorizadas do projeto",
    "browser-plan": "Planejar a captura de uma página pelo navegador autorizado",
}


def build_parser():
    parser = argparse.ArgumentParser(
        description="Get B-rolls — pesquisar, revisar e coletar trechos por fonte.",
        epilog="Use `<subcomando> --help` para os argumentos de cada etapa.",
    )
    parser.add_argument(
        "--env-file", help="Arquivo .env explícito; padrão: .env na raiz da skill"
    )
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
            p.add_argument(
                "--live",
                action="store_true",
                help="Testar buscas reais/refresh; pode consumir quota de API",
            )
    for name in (
        "status",
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
        "init-rules",
        "rules",
        "remember",
        "references",
        "browser-plan",
    ):
        p = sub.add_parser(name, help=SUMMARIES[name], description=SUMMARIES[name])
        p.add_argument(
            "--project",
            required=True,
            help="Pasta do projeto que guarda brolls/, fora da instalação da skill",
        )
        if name in ("preview", "approve", "permit", "reject", "fetch", "remember"):
            p.add_argument(
                "--candidate",
                required=True,
                help="ID do candidato retornado por search/resolve",
            )
        if name in ("preview", "approve"):
            p.add_argument(
                "--start", type=float, help="Início do trecho na origem, em segundos"
            )
            p.add_argument(
                "--end", type=float, help="Fim do trecho na origem, em segundos"
            )
        if name == "preview":
            p.add_argument("--reference-only", action="store_true", help="Gerar apenas referência estática, sem obter trecho remoto")
            p.add_argument("--narration", help="Fala exata do roteiro")
            p.add_argument("--reason", help="Decisão de coleta desta fonte")
        if name == "import-review":
            p.add_argument(
                "--file", required=True, help="JSON exportado pelo Storyboard"
            )
            p.add_argument(
                "--by", required=True, help="Nome de quem revisou e assinou as decisões"
            )
        if name == "approve":
            p.add_argument(
                "--by",
                required=True,
                help="Nome de quem já aprovou explicitamente o trecho",
            )
        if name == "permit":
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--evidence", help="Evidência real fornecida ou verificada")
            g.add_argument(
                "--declaration",
                action="store_true",
                help="Registrar declaração que o usuário preencheu em RULES.md",
            )
        if name == "remember":
            p.add_argument(
                "--decision",
                choices=["approved", "rejected"],
                required=True,
                help="Decisão humana registrada para esta referência",
            )
            p.add_argument(
                "--reason", required=True, help="Motivo real da decisão registrada"
            )
            p.add_argument("--by", required=True, help="Nome de quem decidiu")
        if name == "browser-plan":
            p.add_argument(
                "--url", required=True, help="URL pública da página a capturar"
            )
        if name == "search":
            p.add_argument(
                "--provider",
                default="auto",
                help="Fonte: youtube, pexels, pixabay, commons, nasa ou auto (padrão)",
            )
            p.add_argument("--query", required=True, help="Termos da busca na fonte")
            p.add_argument(
                "--limit", type=int, default=8, help="Máximo de candidatos, 1–50 (padrão 8)"
            )
            p.add_argument(
                "--intent",
                choices=["literal", "illustrative"],
                default="literal",
                help="literal: entidade nomeada; illustrative: ideia genérica",
            )
        if name == "resolve":
            p.add_argument(
                "--context-image", help="Print opcional da pessoa; permanece estático"
            )
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
            p.add_argument(
                "--shot", help="Identificador único do insert, ex.: insert-02"
            )
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--url", help="URL pública da fonte (YouTube, Instagram, TikTok)")
            g.add_argument("--file", help="Arquivo local já autorizado para importação")
    return parser


def parse_args(argv=None):
    return build_parser().parse_args(argv)


def main(argv=None):
    from .commands import execute, with_summary

    args = parse_args(argv)
    return audited(args, lambda parsed: with_summary(parsed.command, execute(parsed)))


def entrypoint():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
        return 0
    except OperationError as exc:
        print(
            json.dumps({"error": str(exc), **exc.payload}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2

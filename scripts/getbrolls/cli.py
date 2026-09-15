"""Argument contract and structured command output."""

import argparse, json, sys
from .runtime import audited, OperationError


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Get B-rolls — pesquisar, revisar e coletar trechos por fonte."
    )
    parser.add_argument(
        "--env-file", help="Arquivo .env explícito; padrão: .env na raiz da skill"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("providers", "doctor"):
        p = sub.add_parser(name)
        if name == "doctor":
            p.add_argument(
                "--live",
                action="store_true",
                help="Testar buscas reais/refresh; pode consumir quota de API",
            )
    for name in (
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
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        if name in ("preview", "approve", "permit", "reject", "fetch", "remember"):
            p.add_argument("--candidate", required=True)
        if name in ("preview", "approve"):
            p.add_argument("--start", type=float)
            p.add_argument("--end", type=float)
        if name == "preview":
            p.add_argument("--reference-only", action="store_true", help="Gerar apenas referência estática, sem obter trecho remoto")
            p.add_argument("--narration", help="Fala exata do roteiro")
            p.add_argument("--reason", help="Decisão de coleta desta fonte")
        if name == "import-review":
            p.add_argument("--file", required=True)
            p.add_argument("--by", required=True)
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
                "--decision", choices=["approved", "rejected"], required=True
            )
            p.add_argument("--reason", required=True)
            p.add_argument("--by", required=True)
        if name == "browser-plan":
            p.add_argument("--url", required=True)
        if name == "search":
            p.add_argument("--provider", default="auto")
            p.add_argument("--query", required=True)
            p.add_argument("--limit", type=int, default=8)
            p.add_argument(
                "--intent", choices=["literal", "illustrative"], default="literal"
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
            )
            p.add_argument("--title", help="Título do asset/notícia")
            p.add_argument("--captured-at", help="Data da captura, ISO 8601")
            p.add_argument("--source-url", help="URL pública original do arquivo local")
            p.add_argument("--creator", help="Autor informado da fonte")
            p.add_argument(
                "--shot", help="Identificador único do insert, ex.: insert-02"
            )
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--url")
            g.add_argument("--file")
    return parser.parse_args(argv)


def main(argv=None):
    from .commands import execute

    args = parse_args(argv)
    return audited(args, execute)


def entrypoint():
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
        return 0
    except OperationError as exc:
        print(
            json.dumps({"error": str(exc), **exc.payload}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2

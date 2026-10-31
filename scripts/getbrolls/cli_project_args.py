"""The per-subcommand argument adders of the project commands."""

# pylint: disable=duplicate-code
# `FORMAT_GATE_SUBCOMMANDS` repete, de propósito, os nomes de subcomando que `cli_parser.build_parser` registra.

import argparse

from . import presets, renamed, vocab

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


def _recipe_ref(text):
    from .refs import recipe_parts

    recipe_parts(text)
    return text


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
    p.add_argument(
        "--to", required=True, help="Exporter de destino, de um plugin de terceiros habilitado (ex.: meu_exporter)"
    )
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
        type=_IntOption(_port),
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
        "mark: item falhou; motivo com 429, 'rate limit', 'too many requests' ou 'limite de requisições' abre "
        "cooldown (ritmo); motivo com "
        "login, challenge, captcha, 401/403 ou as mensagens de bloqueio da própria skill (sessão de acesso, IP "
        "bloqueado) para a fila do provedor, sem cooldown e sem horário para voltar"
    )
    p.add_argument(
        "--action",
        choices=["add", "next", "mark", "status", "resume"],
        required=True,
        help=(
            "add: enfileirar URLs; next: próximo item ou tempo de espera; mark: registrar resultado; "
            "status: contagens, cooldown e paradas; "
            "resume: com --provider, --by, --phrase e --expect, limpa a parada de um provedor depois que a "
            "pessoa confirmou, no chat, que o acesso voltou"
        ),
    )
    p.add_argument(
        "--provider",
        choices=["instagram", "tiktok", "youtube"],
        help="Fonte das URLs em add; em next, limita a fila a essa fonte; em resume, o provedor a retomar",
    )
    p.add_argument("urls", nargs="*", help="URLs públicas a enfileirar (add); repetidas são ignoradas")
    p.add_argument("--url", action="append", help="URL pública a enfileirar (add); pode repetir")
    p.add_argument("--id", help="ID do item retornado por next (mark)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--done", action="store_true", help="mark: item coletado com sucesso; zera o cooldown")
    g.add_argument("--failed", action="store_true", help=mark_help)
    g.add_argument("--skipped", action="store_true", help="mark: item pulado sem tentar")
    p.add_argument("--reason", help="Motivo real registrado no item (mark)")
    p.add_argument("--by", help="resume: nome de quem confirmou, no chat, que o acesso voltou")
    p.add_argument(
        "--phrase", dest="statement", help="resume: frase exata dita por essa pessoa, registrada literalmente"
    )
    p.add_argument("--expect", help="resume: o instante `at` da parada que a pessoa viu (status: stopped.at)")


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


class _IntOption:  # pylint: disable=too-few-public-methods
    """Tipo de argparse de um inteiro validado por `parse` (como `_checked`).

    `value_type` faz o `capabilities` continuar descrevendo a opção como `int`, como uma
    opção de `type=int`."""

    value_type = "int"

    def __init__(self, parse):
        self._convert = _checked(parse)
        self.__name__ = parse.__name__

    def __call__(self, text):
        return self._convert(text)


PORT_RANGE = (1, 65535)


def _port(text):
    """Porta TCP de `--port`: 1 a 65535. Fora disso o bind daria `OverflowError` (saída 3), e
    0 viraria a porta padrão em silêncio."""
    low, high = PORT_RANGE
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"porta inválida: {text!r}; use um número de {low} a {high}.") from None
    if not low <= value <= high:
        raise ValueError(f"porta {value} fora do intervalo; use um número de {low} a {high}.")
    return value


def _project_slug(text):
    from .layout import check_client

    return check_client(text, "--slug")


def _client_slug(text):
    from .layout import check_client

    return check_client(text, "--client")


def _parse_canvas(text):
    from .layout import parse_canvas

    return parse_canvas(text)


def _parse_fps(text):
    from .layout import parse_fps

    return parse_fps(text)


def _init_renamed(args):
    """`init --template` (também abreviado a partir de `--templ`): o `Rename` dele, antes de qualquer outro erro."""
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag.startswith("--templ") and "--template".startswith(flag):
            return renamed.init_template()
    return None


def _add_init_args(p, name):
    """Flags de `init`."""
    if name != "init":
        return
    p.add_argument(
        "--client",
        type=_checked(_client_slug),
        help="Slug do cliente registrado do projeto (ex.: cliente-exemplo); obrigatório com --recipe",
    )
    p.add_argument(
        "--slug",
        type=_checked(_project_slug),
        help=(
            "Slug do projeto novo; com --client, ele nasce em <pasta do cliente>/projetos/<slug>/ "
            "(no lugar de --project)"
        ),
    )
    p.add_argument(
        "--recipe",
        type=_checked(_recipe_ref),
        help=(
            "Criar o projeto da receita do cliente --client: <slug>[@N], <cliente>.<slug>[@N] ou "
            "cat:getbrolls/recipe/<cliente>.<slug>@<N> (sem @N, a última versão)"
        ),
    )
    # `--template` da versão de testes: recusado com o `--recipe` (saída 2), nunca lido.
    p.add_argument("--template", dest="old_template", help=argparse.SUPPRESS)
    p.renamed = _init_renamed
    p.add_argument("--tema", help="Tema do ROTEIRO.md criado da receita (obrigatório com --recipe)")
    p.add_argument(
        "--canvas", type=_checked(_parse_canvas), help="Tamanho da tela em pixels, LARGURAxALTURA (ex.: 1080x1920)"
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

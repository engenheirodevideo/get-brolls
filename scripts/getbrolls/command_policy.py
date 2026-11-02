"""Which commands read, which write, and the shared state of the command in progress.

The tables here decide the lock a command takes (`runtime.audited`), whether it opens the installation
log and which errors come out without a recovery hint. They are plain data, so every layer can read them
without importing the runtime."""

import contextvars

ACTIVE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("getbrolls_operation", default=None)


# Comandos que só leem o projeto: sem trava exclusiva e sem criar a árvore.
# `brief` entra aqui porque só lê BRIEF.md, RULES.md e o manifesto já existente.
# `doctor` aceita `--project` por uniformidade com o resto da CLI, mas diagnostica a
# instalação: não pode criar `brolls/` numa pasta que talvez nem seja um projeto.
# `capabilities` só descreve o parser e o manifesto dos plugins, sem projeto.
# `setup` nunca recebe nem toca projeto; instala só em `$GB_HOME/runtime` ou `GB_RUNTIME_DIR`.
# `x` roda comando de plugin, que só lê o projeto por cópias (CommandContext).
# `profile` só grava `$GB_HOME/trusted-profiles.json` (trust/untrust), nunca um projeto.
# `rules` só mostra as regras em vigor; `library --search` só consulta a biblioteca pessoal
# (`$GB_HOME/library`); `references` só lê as referências já decididas do projeto. `providers` só lista
# as fontes e o que está configurado, sem projeto.
READ_ONLY_COMMANDS = (
    "status",
    "serve",
    "brief",
    "doctor",
    "setup",
    "x",
    "capabilities",
    "profile",
    "rules",
    "library",
    "references",
    "providers",
    "environment",
)
# (comando, ação) somente leitura, além dos comandos inteiros acima: `queue --action status` só consulta
# queue.json (mesmo contrato de `status`), nunca deve tomar a trava exclusiva. `roteiro --action check|plan`
# e `assets` também só leem: plano de cena, sync simulado e inventário de componentes, sem trava nem árvore
# nova. `client --action list|show` só lê `$GB_HOME/clients.json` e o `client.json` de cada pasta;
# `recipe --action list|show` só lê as pastas de receita dos clientes registrados, e `template --action
# freeze|list|show` só recusa com o comando novo. `migrate --action plan` só mostra o `project.json` que o
# `apply` gravaria. `analysis --action list|check` só lê `analysis/`, sem criar a pasta. `plugins --action
# list` só lê o inventário. O `plugins --action check` fica de fora: ele executa o `register()` do plugin
# (código de terceiros, sem sandbox), e o core não garante que esse código não grave.
READ_ONLY_ACTIONS = {
    ("analysis", "check"),
    ("analysis", "list"),
    ("client", "list"),
    ("client", "show"),
    ("migrate", "plan"),
    ("recipe", "list"),
    ("recipe", "show"),
    ("template", "freeze"),
    ("template", "list"),
    ("template", "show"),
    ("queue", "status"),
    ("roteiro", "check"),
    ("roteiro", "plan"),
    ("assets", "list"),
    ("assets", "where"),
    ("plugins", "list"),
    ("plugins", "marketplace-list"),
    ("plugins", "search"),
}

# (comando, ação) que grava só sob a própria trava, nunca sob a do projeto: `analysis
# --action register` calcula o sha256 de uma mídia grande sem segurar o projeto, e só a
# troca dos arquivos de `analysis/` fica dentro de `analysis/.lock`.
OWN_LOCK_ACTIONS = {("analysis", "register")}

# Comandos que criam o projeto (ou o `project.json`) e só tomam a trava dele depois de
# validar tudo: um `init` recusado (cliente desconhecido, template adulterado, flag errada)
# ou um `migrate` numa pasta que não existe não pode deixar para trás a pasta do projeto,
# `brolls/.command.lock` nem um log. O log de auditoria só é
# gravado quando `brolls/` já existe (ou seja, depois que o próprio comando a criou).
SELF_LOCKED_COMMANDS = ("init", "migrate")

# Comandos de etapa: trabalham sobre candidatos que `search`/`resolve` já registraram. Um typo
# no `--project` de um deles (uma pasta que não existe) é recusado antes da trava e do log:
# nunca cria um projeto vazio. Numa pasta que já existe, mesmo sem `brolls/`, eles rodam como
# sempre (`review` monta o Storyboard vazio). `search`, `resolve`, `init*`, `roteiro` e `queue`
# continuam criando o projeto, inclusive a pasta.
_DECISION_STAGES = ("preview", "approve", "permit", "reject", "review", "import-review", "remember")
_FILE_STAGES = ("fetch", "verify", "deliver")
STAGE_COMMANDS = _DECISION_STAGES + _FILE_STAGES


# Comandos que lidam com um `project.json` ilegível do seu jeito (`status` o mostra como problema do layout; `init`
# e `migrate` o recusam por nome); nos outros ele é um erro de E/S, nunca um projeto sem componentes.
LAYOUT_TOLERANT = frozenset({"status", "init", "migrate"})


# Ações que gravam em `$GB_HOME` (plugins/, plugins.json, marketplaces): uma por vez,
# sob `$GB_HOME/.plugins.lock`. A prévia também entra: ela monta o staging em plugins/.
WRITING_ACTIONS = frozenset(
    {"enable", "disable", "install", "update", "remove", "marketplace-add", "marketplace-remove", "marketplace-update"}
)


# (comando, ação) sem projeto que muda o estado da instalação (`$GB_HOME`): só eles gravam o log do app da
# instalação (`$GB_HOME/logs/getbrolls.log`), do mesmo jeito que só o comando que grava num projeto grava o
# `brolls/getbrolls.log`. `plugins` usa as ações que já tomam a trava dos plugins e `setup`/`profile` entram
# por flag e ação (ver `writes_installation`); `list`, `show`, `check`, `search`, `doctor` e o resto só leem.
INSTALLATION_WRITES = frozenset({("client", "add"), ("client", "remove")})


def writes_installation(args):
    """O comando muda a instalação (plugins, marketplaces, clientes, perfil confiável, `setup`)?

    Só ele abre o log do app da instalação; um comando que só lê não cria o arquivo. A
    prévia do `profile --action trust` (sem `--yes`) só lê o getbrolls.toml, e a forma
    posicional antiga (`legacy`) só recusa: o `trust --yes` e o `untrust` é que gravam `trusted-profiles.json`.
    """
    command = args.command
    if command == "plugins":
        return getattr(args, "action", None) in WRITING_ACTIONS
    if command == "setup":
        return not (getattr(args, "check", False) or getattr(args, "where", None))
    if command == "profile":
        action = getattr(args, "action", None)
        if getattr(args, "legacy", None):
            return False  # a forma antiga só recusa (`renamed`): nada grava, nem o log
        return action == "untrust" or (action == "trust" and bool(getattr(args, "yes", False)))
    return (command, getattr(args, "action", None)) in INSTALLATION_WRITES


# Comandos cujo erro sai sem a dica de recovery (nenhum erro mostra traceback nem
# repr à pessoa; esses ficam só em diagnostics.jsonl).
QUIET_ERROR_COMMANDS = ("plugins", "x", "export", "assets", "client", "analysis", "template", "recipe")

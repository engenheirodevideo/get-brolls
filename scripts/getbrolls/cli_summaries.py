"""Subcommand summaries and the exit codes of the command line."""

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

# Uma linha por subcomando: o que ele faz no fluxo coleta → revisão → entrega.
SUMMARIES = {
    "providers": "Listar fontes disponíveis, transporte e chaves configuradas",
    "doctor": "Diagnosticar dependências, caminhos fixados e fontes utilizáveis",
    "plugins": (
        "Listar, instalar (de pasta, git ou marketplace), atualizar, remover, criar, habilitar, desabilitar ou "
        "validar plugins do SDK ($GB_HOME/plugins, padrão ~/.getbrolls/plugins), procurar nos marketplaces e "
        "gerenciá-los"
    ),
    "capabilities": (
        "Descrever em JSON os comandos, flags, códigos de saída e comandos de plugin desta instalação (para agentes)"
    ),
    "environment": (
        "Listar os ambientes que vêm com o getbrolls (pontes oficiais, hoje o HyperFrames): versão, procedência e "
        "estado, sem rodar código delas"
    ),
    "client": (
        "Registrar, listar, mostrar ou desregistrar pastas de cliente (prateleira em assets/, projetos/) no "
        "$GB_HOME/clients.json"
    ),
    "template": "Comando antigo das receitas (agora recipe): freeze, list e show só dizem o comando novo",
    "recipe": ("Listar e mostrar as receitas seladas dos clientes, conferindo a integridade de cada arquivo (só leem)"),
    "x": "Rodar um comando de plugin habilitado (x --list mostra quais existem); só lê o projeto",
    "profile": "Mostrar o perfil getbrolls.toml em vigor (valor e origem de cada campo) ou confiar/desconfiar dele",
    "setup": (
        "Instalar o runtime da instalação (yt-dlp numa venv e Playwright via npm) em $GB_HOME/runtime; "
        "--check só confere; --where mostra as pastas; FFmpeg e Node só são verificados"
    ),
    "status": "Resumir onde o projeto está por etapa, sem alterar arquivos",
    "search": "Pesquisar candidatos numa fonte e registrá-los no projeto (--shot liga ao beat; --dry-run não grava)",
    "resolve": "Registrar um candidato a partir de URL pública ou arquivo local",
    "inspect": "Analisar a fonte (duração, capítulos, legendas) antes de coletar",
    "preview": "Gerar prévia (GIF/contact sheet) do intervalo escolhido",
    "approve": "Registrar aprovação humana já recebida para o intervalo atual",
    "permit": "Registrar as condições reais de uso do trecho antes da coleta",
    "reject": "Marcar candidatos como rejeitados e invalidar suas revisões (--candidate repetível)",
    "fetch": "Produzir o corte final aprovado e permitido (broll/ no layout 1, brolls/clips/ no 0)",
    "verify": "Conferir integridade e decodificação dos arquivos coletados",
    "review": "Gerar o Storyboard local em brolls/review.html",
    "import-review": "Importar o JSON de decisões exportado pelo Storyboard",
    "init": (
        "Criar um projeto novo de layout 1: project.json, aroll/, assets/, broll/ e analysis/ "
        "(--recipe: a partir de uma receita do cliente)"
    ),
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
    "queue": "Enfileirar URLs sociais e ditar o ritmo do lote (add, next, mark, status, resume)",
    "serve": "Servir brolls/review.html em 127.0.0.1 para abrir o Storyboard no navegador",
    "deliver": "Organizar os trechos coletados em entrega/, uma pasta por beat",
    "roteiro": "Criar, validar, revisar e sincronizar o ROTEIRO.md com os beats do BRIEF.md",
    "assets": (
        "Listar componentes do projeto (marca, lettering, sfx, música, imagem, composições, A-ROLL) e resolver nomes"
    ),
    "export": (
        "Transformar o roteiro revisado num projeto de edição por um exporter de plugin de terceiros,"
        " numa pasta nova em exports/"
    ),
    "analysis": (
        "Registrar mídias do projeto em analysis/ (id por conteúdo) e listar ou conferir os arquivos de análise"
    ),
}

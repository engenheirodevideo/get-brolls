---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-26
tags: [get-brolls]
---

# Segurança e privacidade

Não comite `.env`, originais, manifestos privados ou exports de revisão de clientes. Revise a árvore antes de publicar. O servidor de exemplo escuta apenas localhost. Não distribua `.venv/`, `.tools/`, perfis de navegador, cookies ou pares CDN assinados. Os instaladores obtêm as dependências nos registros oficiais.

Projetos/JSON são dados locais confiáveis. Não execute a skill como serviço público aceitando URLs, caminhos ou manifests arbitrários. O transporte HTTP interno de APIs/downloads resolve e valida todos os IPs antes da conexão, conecta aos IPs validados sem uma segunda resolução, mantém certificado/hostname HTTPS e bloqueia redirects. Ele não usa proxies automáticos do ambiente/sistema. Essa proteção não é um isolamento de rede de processos externos como yt-dlp, FFmpeg ou navegador.

A revisão não autentica quem clicou: importação exige atribuição humana com `--by`. O importador confere assinatura do conteúdo e versão da decisão (`reviewEpoch`) para impedir que exports antigos substituam decisões posteriores. Arquivos sem versão da decisão devem ser regenerados pelo Storyboard; não edite o JSON para contornar uma recusa.

## Para onde os dados vão

| Momento | Destinos | O que trafega |
|---|---|---|
| Instalação | PyPI (conjunto fixado em `requirements.txt`), registro npm (`package-lock.json`), Chrome/navegador opcional em passo separado | Apenas download das dependências. `npm ci --ignore-scripts` não baixa navegador nem executa scripts de pacote. |
| Execução | CDNs de YouTube, TikTok e Instagram via yt-dlp/curl; APIs de Pexels, Pixabay, Wikimedia Commons e NASA | URL solicitada, termos de busca e, quando existir, a chave do banco escolhido. |
| Nunca sai | Projetos, originais importados, JSON de revisão, chaves, `.env`, sessões e pares CDN assinados | Permanecem no disco local; nenhuma dessas informações é enviada a terceiros pela skill. |

Sem telemetria: a skill não envia dados a nenhum serviço próprio. Não há endpoint do autor, coleta de uso ou relatório automático de erro; todo tráfego sai para a fonte que você escolheu ou para os registros oficiais de dependências.

**Export HyperFrames.** O `EXPORT.md` e o `package.json` do export mandam rodar `npx hyperframes@0.8.73`. O `npx` baixa e executa a CLI e as dependências dela (inclusive scripts de instalação); rode num ambiente em que você confia. A CLI HyperFrames, que não é do get-brolls, envia telemetria de uso (PostHog) por padrão: `HYPERFRAMES_NO_TELEMETRY=1` desliga (no PowerShell, `$env:HYPERFRAMES_NO_TELEMETRY = "1"`). O `EXPORT.md` já traz essa linha antes dos comandos; os scripts do `package.json` não trazem, porque `VAR=1 comando` não funciona no `cmd` do Windows. O projeto gerado busca o GSAP no jsdelivr com Subresource Integrity (`integrity="sha384-…"` e `crossorigin="anonymous"` na tag `<script>`, travado por teste): o navegador recusa o arquivo se ele não bater com o hash pinado. A fonte Inter (Google Fonts) não tem esse mecanismo, e o `transcribe` pode baixar o modelo do whisper.

## `.env` e caminhos que viram execução

O `.env` que vale é, nesta ordem: `--env-file`, `GB_ENV_FILE` (só no ambiente do processo, nunca dentro de um `.env`), o `.env` da pasta da instalação (checkout) e, por fim, `$GB_HOME/.env`. Só um é lido; os dois nunca se misturam. Quem escreve em qualquer um deles escolhe executáveis e pastas de runtime: `GB_VENV_PATH` e os `GB_*_PATH` apontam o yt-dlp/ffmpeg/ffprobe, e `GB_RUNTIME_DIR` aponta a pasta com `.venv/` e `.tools/` de onde saem o yt-dlp e o Playwright. `GB_RUNTIME_DIR` pode vir de `GB_ENV_FILE` ou do `.env` do checkout, o mesmo vetor de `GB_VENV_PATH`: trate esses arquivos (e a pasta da instalação) com a mesma confiança que os próprios executáveis.

## Plugins

Plugins do SDK (`docs/SDK.md`) são opt-in por id: nada em `$GB_HOME/plugins/`
roda sem um `plugins --action enable --id <id> --yes` explícito, e habilitar
grava um pin de hash sobre toda a pasta — qualquer mudança de conteúdo
suspende o plugin até nova revisão. As permissões declaradas no manifesto
(`permissions.network` e `permissions.env`) são conferidas nos próprios canais
do SDK (`api.get_json`, `api.env`), não numa camada de isolamento do processo.
O `.env` só entrega a um plugin variáveis do espaço de nomes dele (`<ID>_...`),
nunca uma do core ou de outro plugin, e essa variável nunca é exportada: só
`api.env` do plugin dono a lê, e subprocessos (git, ffmpeg, yt-dlp, playwright),
OpenSSL e o `ssl` do Python não a enxergam. Um nome que ferramentas do sistema
leem (`GIT_*`, `XDG_*`, `OPENSSL_*`, proxies...) só gera aviso, porque o valor
não chega ao ambiente.
**O SDK não é uma caixa de areia**: um plugin habilitado roda com as mesmas
permissões de quem executa a CLI, e não tem egress próprio além do que o
código dele fizer — só habilite plugins cujo código você leu e em que confia.

Rotas de plugin trazem o arquivo, mas o core decide o resto: a rota grava só na
pasta de trabalho que o core cria em `.getbrolls-sources/` (e apaga ao fim), e o
arquivo devolvido é recusado se estiver fora dela, for link simbólico, vazio,
maior que 512 MB ou ilegível pelo `ffprobe`. Rota que consome licença ou cota
(`stage="fetch"`) nunca roda em `inspect`, `preview` ou varredura — só no
`fetch`, depois da aprovação humana e do `permit`. `api.download` segue o
transporte do core (HTTPS, IP público, sem redirect, host em
`permissions.network`) e aceita header de autorização e URL assinada sem
registrá-los em log nem em mensagem de erro. `api.local_file` só lê arquivos
dentro de `permissions.paths` (pastas específicas — nunca `/`, a raiz de uma
unidade, `~` nem a pasta pessoal inteira), sem seguir link e sempre copiando. No
macOS e no Linux, a pasta de `permissions.paths` é aberta descendo de `/` pelo
caminho gravado no pin no `enable` (mostrado na prévia), sem seguir link; uma
pasta que não confere mais com a gravada fica ignorada, com aviso. No Windows
vale a conferência antiga, que ainda deixa uma janela de corrida, e uma junction
não exige administrador. `api.local_file` aceita hardlink e o registra no log;
os resolvedores o recusam. `permissions.paths` e `permissions.network` aparecem
no preview do `enable` e do `install`. A resposta de `api.get_json` de um plugin
nunca vai para o cache em disco. Comandos de plugin (`gb x`) só leem o projeto,
por cópias. `install`/`update` clonam com `GIT_TERMINAL_PROMPT=0`, recusam URL
com credencial, link simbólico e pasta de controle de versão fora do topo, e não
executam código do plugin. `GB_PLUGINS=off` desliga tudo; `GB_PLUGINS=id1,id2`
só escolhe entre os plugins já habilitados com pin válido — nunca carrega um
plugin não habilitado nem um com conteúdo mudado desde o `enable`.

Exceção levantada por código de plugin aparece só pelo tipo — o texto dela
(que pode carregar um token) não chega à mensagem, ao `diagnostics.jsonl` nem
ao log; só `PluginError`, saneado e com os valores de `permissions.env`
trocados por `[REDACTED]`, mostra texto. O plugin também não escreve evidência
de direitos: `ORIGEM.md` e `credits.md` só trazem o que o humano registrou no
`permit` e a licença que o core anotou da rota de `fetch`, cada campo numa
linha. Chamada de plugin não tem tempo limite: um plugin travado segura o
comando até Ctrl+C.

Para reportar vulnerabilidades, use o relatório privado do GitHub em **Security → Report a vulnerability**, habilitado neste repositório. Não publique segredos ou dados de clientes em issues públicas. Nenhum endereço de contato é presumido neste pacote.

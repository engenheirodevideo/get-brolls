---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-30
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

O `.env` que vale é, nesta ordem: `--env-file`, `GB_ENV_FILE` (só no ambiente do processo, nunca dentro de um `.env`), o `.env` da pasta da instalação (checkout) e, por fim, `$GB_HOME/.env`. Só um é lido; os dois nunca se misturam. Quem escreve em qualquer um deles escolhe executáveis e pastas de runtime: `GB_VENV_PATH` e os `GB_*_PATH` apontam o yt-dlp/ffmpeg/ffprobe, e `GB_RUNTIME_DIR` aponta a pasta com `.venv/` e `.tools/` de onde saem o yt-dlp e o Playwright. `GB_RUNTIME_DIR` pode vir de `GB_ENV_FILE` ou do `.env` do checkout, o mesmo vetor de `GB_VENV_PATH`: trate esses arquivos (e a pasta da instalação) com a mesma confiança que os próprios executáveis. Sem `GB_RUNTIME_DIR`, o runtime (do pacote, do checkout e do plugin; os instaladores só chamam o `setup`) vive em `$GB_HOME/runtime/<versão das dependências>/`: a pasta tem a mesma confiança que um executável seu.

O `setup` roda o pip (versões fixadas em `requirements.txt`, sem hashes nesta versão) e o `npm ci --ignore-scripts` com argumentos fixos, nunca por um shell. O ambiente desses processos filhos não leva variáveis terminadas em `_API_KEY`, `_TOKEN` ou `_SECRET`, nem `PYTHONPATH`/`PYTHONHOME`; a única exceção é o `NPM_TOKEN`, entregue só ao `npm ci` (o `.npmrc` de um registro privado o lê). A pasta `runtime/<versão>` que é link simbólico ou junção é recusada: o `setup` não instala no destino do link.

## Perfil `getbrolls.toml`

O perfil é configuração que vira execução: `tools.ffmpeg`, `tools.ffprobe`, `tools.ytdlp` e `tools.venv` passam a ser os executáveis (e a venv) que a CLI roda, e `runtime_dir` aponta a pasta de onde saem o yt-dlp e o Playwright. Por isso ele só vale com confiança:

- **Quem confia.** `GB_PROFILE` no ambiente do processo (a mesma fronteira de `GB_VENV_PATH`), o `$GB_HOME/getbrolls.toml` exato, ou um `profile trust --yes --expect <sha256>` que grava o sha dos bytes em `trusted-profiles.json`. Mudou um byte, deixa de valer até nova confiança.
- **Onde mora a confiança.** O `trusted-profiles.json` fica no `GB_HOME` de antes do perfil, nunca no `home` que o próprio perfil escolhe: senão ele se autoconfiaria.
- **Filhos.** A CLI passa aos processos filhos o perfil já conferido em `GB_PROFILE` + `GB_PROFILE_SHA256`; o filho confia nele como veio do ambiente, mas recusa se o sha não bater mais com o arquivo (editado no meio do caminho). Sem perfil válido, o filho recebe `GB_PROFILE=off`, nunca a busca de novo.
- **Recusas.** O `getbrolls.toml` nunca é link simbólico, é um arquivo comum e, no POSIX, é do usuário atual e não é gravável por grupo nem por outros.
- **O `home` relativo.** Um `home` (ou `runtime_dir`, `cache_dir`) relativo pode cair dentro do repositório onde o toml mora. O sha do perfil cobre só o toml: quem grava no repositório poderia deixar depois um `.env` (que escolhe executáveis) ou um `plugins.json` (que habilita plugins) nessa pasta. Mitigação: a prévia do `profile trust` avisa em `warnings` quando uma dessas pastas fica dentro da pasta do toml (e quando `plugins` cita id não instalado), e, com o `GB_HOME` vindo do perfil, o `.env` e o `plugins.json` de lá passam pelas mesmas recusas do toml (link, outro dono, gravável por grupo ou outros). Prefira pastas fora do repositório.
- **Sem confiança**, só os comandos que diagnosticam (`doctor`, `profile`, `capabilities`, `setup --check`/`--where`) rodam, relatando o problema; o resto para com código `2` antes de tocar em nada.

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
por cópias. `install`/`update` fixam todo repositório git por commit (sha
completo de 40 caracteres, buscado sozinho com `fetch --depth 1`), nunca fazem
`checkout` e escrevem cada arquivo a partir do blob cru, sem filtro, hook nem
`autocrlf`. O git roda com `GIT_TERMINAL_PROMPT=0`, sem nenhuma variável `GIT_*`
herdada, só com os transportes `https` e `ssh` (e `file` apenas quando a origem
é uma pasta local indicada pela pessoa; `ext::` é recusado), com
`transfer.fsckObjects=true`, sem template de `init` e sem hooks. `--commit`,
`--ref` e `--subdir` são validados antes de qualquer git rodar (nada que comece
com `-`, sem `..`, sem pasta de VCS). São recusados: URL com credencial, query
ou fragmento; link simbólico e submódulo; pasta de controle de versão fora do
topo; ponteiro do Git LFS (o LFS nunca roda); e nome de arquivo fora de NFC. Nenhum
código do plugin roda no install. Limites do sha256 do pin: os bits de modo
(executável ou não) não entram na conta, e lixo de SO (`.DS_Store`,
`Thumbs.db`, `desktop.ini`) commitado no repositório é materializado mas não
entra no hash — o código do plugin não deve ler esses nomes. `plugins --action remove`
apaga a pasta do plugin e o estado dele em `plugins.json`; quando `plugins/<id>`
é um link, só o link sai, e o alvo não é tocado. `GB_PLUGINS=off` desliga tudo; `GB_PLUGINS=id1,id2`
só escolhe entre os plugins já habilitados com pin válido — nunca carrega um
plugin não habilitado nem um com conteúdo mudado desde o `enable`.

Marketplaces de plugins são índices fixados por commit: o índice é lido só no
commit gravado e o cache em `$GB_HOME/marketplaces/` é conferido por sha256 a
cada leitura. Instalar pelo marketplace (`install --id <id>@<marketplace>`)
não confia no índice para nada além de dizer de onde vem o plugin e o que ele
tem que ser: o conteúdo é materializado como num `--source` e recusado se o
sha256 não for o `content_sha256` da entrada ou se o manifesto divergir dela
(permissões, contribuições, compatibilidade, licença). Entrada retirada
(`yanked`) é recusada, e o perfil de workspace pode limitar os marketplaces
aceitos. O `tier` da entrada é só informação, nunca libera uma conferência. O
índice pré-preenche o `--expect` da confirmação: um agente que copia esse valor
pula a leitura humana, e por isso o `install` sem `--yes` sempre para na prévia
e a skill orienta mostrá-la à pessoa. No `update` pelo marketplace, uma permissão
acrescentada muda o `--expect`: ele passa a ser um valor que só a prévia mostra,
e o sha256 do índice deixa de confirmar. `update --all` só lista; nada é
atualizado em lote. Quem mantém o marketplace escolhe o que
entra no índice; ele não revisa nem assina o código — continue lendo o código
de quem você habilita.

Exceção levantada por código de plugin aparece só pelo tipo — o texto dela
(que pode carregar um token) não chega à mensagem, ao `diagnostics.jsonl` nem
ao log; só `PluginError`, saneado e com os valores de `permissions.env`
trocados por `[REDACTED]`, mostra texto. O plugin também não escreve evidência
de direitos: `ORIGEM.md` e `credits.md` só trazem o que o humano registrou no
`permit` e a licença que o core anotou da rota de `fetch`, cada campo numa
linha. Chamada de plugin não tem tempo limite: um plugin travado segura o
comando até Ctrl+C.

Para reportar vulnerabilidades, use o relatório privado do GitHub em **Security → Report a vulnerability**, habilitado neste repositório. Não publique segredos ou dados de clientes em issues públicas. Nenhum endereço de contato é presumido neste pacote.

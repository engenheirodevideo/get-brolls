---
type: documentation
status: current
created: 2026-09-15
updated: 2026-10-17
tags: [get-brolls]
---

# Segurança e privacidade

Não comite `.env`, originais, manifestos privados ou exports de revisão de clientes. Revise a árvore antes de publicar. O servidor de exemplo escuta apenas localhost. Não distribua `.venv/`, `.tools/`, perfis de navegador, cookies ou pares CDN assinados. Os instaladores obtêm as dependências nos registros oficiais.

Projetos/JSON são dados locais confiáveis. Não execute a skill como serviço público aceitando URLs, caminhos ou manifests arbitrários. O transporte HTTP interno de APIs/downloads resolve e valida todos os IPs antes da conexão, conecta aos IPs validados sem uma segunda resolução, mantém certificado/hostname HTTPS e bloqueia redirects. Ele não usa proxies automáticos do ambiente/sistema. Essa proteção não é um isolamento de rede de processos externos como yt-dlp, FFmpeg ou navegador.

A revisão não autentica quem clicou: importação exige atribuição humana com `--by`, que registra quem atribuiu e não autentica ninguém. O importador confere assinatura do conteúdo e versão da decisão (`reviewEpoch`) para impedir que exports antigos substituam decisões posteriores. Arquivos sem versão da decisão devem ser regenerados pelo Storyboard; não edite o JSON para contornar uma recusa.

Permissões de arquivo: no macOS e no Linux, o getbrolls grava arquivos privados com modo 0600 e pastas privadas com 0700. No Windows esse modo do POSIX não tem equivalente: o getbrolls aplica a ACL equivalente ao criar cada arquivo e pasta privados, com os limites descritos em [Arquivos e pastas privados](#arquivos-e-pastas-privados).

## Para onde os dados vão

| Momento | Destinos | O que trafega |
|---|---|---|
| Instalação | PyPI (pip em `requirements-bootstrap.txt` e conjunto em `requirements.txt`, fixados por versão e sha256, instalados com `--require-hashes --only-binary :all:`), registro npm (`package-lock.json`), Chrome/navegador opcional em passo separado | Apenas download das dependências. `npm ci --ignore-scripts` não baixa navegador nem executa scripts de pacote. |
| Execução | CDNs de YouTube, TikTok e Instagram via yt-dlp/curl; APIs de Pexels, Pixabay, Wikimedia Commons e NASA | URL solicitada, termos de busca e, quando existir, a chave do banco escolhido, que vai só ao provedor dela, por HTTPS (a do Pixabay vai na query string, pela API desse provedor; sai redigida dos logs e do texto de erro, e o nome do arquivo de cache é um sha256 da URL inteira, chave incluída, numa pasta privada). A URL assinada de CDN vai só a essa CDN, pelo curl. |
| Nunca sai | Projetos, originais importados, JSON de revisão, o `.env` inteiro, cookies, cabeçalhos de autenticação e perfis de navegador | Permanecem no disco local; nenhuma dessas informações é enviada a terceiros pela skill. Chaves e pares CDN assinados só vão ao provedor ou à CDN a que pertencem, como acima, e não entram em relatórios nem no Storyboard. |

Sem telemetria: a skill não envia dados a nenhum serviço próprio. Não há endpoint do autor, coleta de uso ou relatório automático de erro; todo tráfego sai para a fonte que você escolheu ou para os registros oficiais de dependências.

## `.env` e caminhos que viram execução

O `.env` que vale é, nesta ordem: `--env-file`, `GB_ENV_FILE` (só no ambiente do processo, nunca dentro de um `.env`), o `.env` da pasta da instalação (checkout) e, por fim, `$GB_HOME/.env`. Só um é lido; os dois nunca se misturam. Quem escreve em qualquer um deles escolhe executáveis e pastas de runtime: `GB_VENV_PATH` e os `GB_*_PATH` apontam o yt-dlp/ffmpeg/ffprobe, e `GB_RUNTIME_DIR` aponta a pasta com `.venv/` e `.tools/` de onde saem o yt-dlp e o Playwright. `GB_RUNTIME_DIR` pode vir de `GB_ENV_FILE` ou do `.env` do checkout, o mesmo vetor de `GB_VENV_PATH`: trate esses arquivos (e a pasta da instalação) com a mesma confiança que os próprios executáveis. Sem `GB_RUNTIME_DIR`, o runtime (do pacote, do checkout e do plugin; os instaladores só chamam o `setup`) vive em `$GB_HOME/runtime/<versão das dependências>/`: a pasta tem a mesma confiança que um executável seu.

O `setup` roda o `pip install --require-hashes --only-binary :all:` e o `npm ci --ignore-scripts` com argumentos fixos, nunca por um shell. O `requirements.txt` fixa cada dependência Python, as transitivas inclusas, pela versão e pelo sha256 dos arquivos que o PyPI publica; o `setup` instala só rodas (wheels), sem etapa de construção: o pip recusa uma roda cujo hash não está na lista, e a opção `--only-binary` (que vale mais que `PIP_NO_BINARY` e o `pip.conf`) impede o sdist, cujas ferramentas de construção o pip não confere por hash. Em Linux, macOS e Windows com Python 3.11 a 3.14 há roda para todo pacote; numa plataforma sem roda (Python 3.15, Windows ARM64, Linux 32 bits) o `setup` falha logo, com a mensagem de pip de sempre, em vez de construir. O pip e o setuptools da venv vêm do `ensurepip` do Python, e a confiança neles é a do instalador do Python, fora da lista de hashes. O `setup --upgrade ytdlp` é a saída explícita: pede a versão mais nova ao índice, sem hash e sem `--only-binary`: use só quando quiser sair do conjunto revisado. O `npm` tem a trava equivalente em `package-lock.json` com `npm ci`. Os hashes provam que o arquivo é o que o PyPI publicou quando o conjunto foi revisado, não que o pacote é inocente. O ambiente desses processos filhos não leva variáveis terminadas em `_API_KEY`, `_TOKEN` ou `_SECRET`, nem `PYTHONPATH`/`PYTHONHOME`; a única exceção é o `NPM_TOKEN`, entregue só ao `npm ci` (o `.npmrc` de um registro privado o lê). A pasta `runtime/<versão>` que é link simbólico ou junção é recusada: o `setup` não instala no destino do link.

## Arquivos e pastas privados

No macOS e no Linux, o que esta documentação chama de privado nasce 0600 (arquivo) ou 0700 (pasta): `diagnostics.jsonl`, `roteiro-reviews.jsonl`, `queue.json`, o índice da biblioteca, `trusted-profiles.json`, `.serve.pid`, `.serve.log` e o log girado, as decisões salvas pelo Storyboard, o cache `.getbrolls-sources/` com a mídia dentro dele e a pasta de trabalho de cada rota de plugin, o cache de respostas de provedores, `plugin-data/<id>/`, a pasta do runtime `$GB_HOME/runtime/<versão>/`, `$GB_HOME/logs/` com o `getbrolls.log` e o sal que embaralha nomes nos logs. No Windows esses bits não existem, então o getbrolls aplica a ACL equivalente **na criação**, com o `icacls.exe` da pasta do sistema (`%SystemRoot%\System32`; nunca um `icacls` achado na pasta atual ou no `PATH`): `icacls <caminho> /grant:r *S-1-3-4:(F) *S-1-5-18:(F) *S-1-5-32-544:(F) /inheritance:r /L` (numa pasta, `(OI)(CI)(F)` em cada um, para o que for criado dentro dela herdar o mesmo), por uma lista de argumentos, sem shell, e logo depois `icacls <pasta> /restore <arquivo> /L`, que grava a DACL inteira de uma vez a partir de um texto SDDL com só esses três SIDs (o `/grant:r` troca só as entradas deles e deixa qualquer outra entrada explícita, como a da conta que criou o arquivo; o `/restore` a tira, e roda só depois de a concessão dar certo). O texto que o `/restore` lê só nasce depois de a concessão dar certo, na pasta temporária do perfil (`%LOCALAPPDATA%\Temp`; nunca a do `TEMP`, que pode apontar para uma pasta em que outras contas gravam), e é apagado em seguida; sem essa pasta, o `/restore` não roda e a linha sai com `outcome=error`. Ficam com acesso o dono (OWNER RIGHTS: quem é o dono do arquivo, em geral a conta que o criou), SYSTEM e Administradores, o mesmo conjunto que o próprio Python dá no Windows a uma pasta criada com `os.mkdir(caminho, 0o700)` nas versões com a correção do CVE-2024-4030; nada é herdado da pasta de cima, e os outros usuários da máquina perdem o acesso. As contas vão por SID bem conhecido, nunca por nome: nada depende de `USERNAME` ou `USERDOMAIN`, e outra conta com o mesmo nome nunca toma o lugar da sua. Numa sessão elevada de um administrador, com a política do Windows que torna o grupo Administradores o dono do que seus membros criam, o dono passa a ser Administradores, e a mesma conta, rodando depois sem elevação, deixa de ler o arquivo (o mesmo vale para o `os.mkdir(caminho, 0o700)` do Python). A concessão vem antes de `/inheritance:r`, para que uma concessão recusada não leve junto a herança (que isso vale para o `icacls` de fato, o CI do Windows confirma), e `/L` faz o `icacls` agir sobre um link, nunca sobre o destino dele (um link encontrado no lugar do arquivo é recusado antes).

O conteúdo só é gravado depois da ACL: o arquivo nasce vazio, recebe a ACL e só então é escrito (os temporários de `queue.json`, `trusted-profiles.json` e do índice de fontes, o cache de respostas, o VTT, as decisões salvas, as cópias privadas e o sal nascem também exclusivos, com `O_EXCL`; `.serve.pid`, `.serve.log` e o log girado, `diagnostics.jsonl`, `roteiro-reviews.jsonl`, o log da instalação e o temporário da biblioteca nascem vazios, não exclusivos). A mídia baixada é a exceção, porque quem a grava é o yt-dlp ou o plugin, dentro da pasta de trabalho privada; ela recebe a ACL antes de ganhar o nome final no cache. Entre a criação e a ACL resta um instante em que o arquivo (vazio) tem a ACL que a pasta dá, e quem já tem acesso à pasta pode abri-lo nesse instante e manter o acesso; só criar o arquivo já com os atributos de segurança do Win32 fecha esse instante, o que fica para a 2.7. Dentro das pastas privadas o instante não existe: o que nasce nelas já herda a ACL delas.

Se a ACL não puder ser aplicada (sem `icacls.exe` na pasta do sistema, link no lugar do arquivo, tempo esgotado, um disco sem ACL como exFAT), o comando segue e o arquivo fica com a ACL que a pasta já dava; o motivo vai como uma linha `event=op` (`operation=proc.run target=icacls outcome=error`) no `getbrolls.log`, a mesma linha única que registra os dois passos quando dão certo (`outcome=ok`). Se a concessão passou e o `/restore` falhou, o arquivo fica com a concessão e sem herança, mas com as outras entradas explícitas que já tinha. O log da instalação e o sal dos logs são a exceção: são criados enquanto uma linha está sendo gravada, e uma linha sobre eles se chamaria a si mesma, então ali a falha é silenciosa. A ACL é aplicada só quando o arquivo ou a pasta nasce: o que uma versão anterior deixou no disco não é reajustado, e uma pasta que já existia (um `GB_HOME` escolhido por você, por exemplo) nunca é restringida. Isso não protege contra quem já roda como o seu usuário nem contra um administrador da máquina, que continua com acesso; o ganho é fechar a leitura para os outros usuários.

### Ferramentas externas no Windows (resíduo conhecido)

Só o `icacls` é procurado pelo caminho absoluto da pasta do sistema. As outras ferramentas externas rodam pelo nome simples (`curl`, `ffmpeg` e `ffprobe`, quando nenhum `GB_FFMPEG_PATH`/`GB_FFPROBE_PATH` aponta um executável) ou são achadas com `shutil.which` (`git`, `npm`, `node`, `yt-dlp`, `deno`). No Windows, o `CreateProcess` com nome simples e o `shutil.which` olham a **pasta atual antes** do `PATH`: um `curl.exe`, `ffmpeg.exe` ou `git.exe` colocado na pasta de onde você roda o comando vence o do sistema. No macOS e no Linux a pasta atual não entra na busca.

Isso fica documentado como resíduo, não como proteção: projetos são dados locais confiáveis (veja o começo desta página), e explorar exige que alguém já tenha gravado um executável na pasta em que você roda. Até a 2.7, que passa a resolver cada ferramenta uma vez para um caminho absoluto sem a pasta atual, rode o getbrolls de uma pasta que só você controla (não de uma pasta baixada ou compartilhada) ou aponte `GB_FFMPEG_PATH`, `GB_FFPROBE_PATH` e `GB_YTDLP_PATH` para o executável que você instalou. As outras ferramentas não têm variável de caminho: confira que não há `curl.exe`, `git.exe` nem `npm.cmd` na pasta atual.

## Perfil `getbrolls.toml`

O perfil é configuração que vira execução: `tools.ffmpeg`, `tools.ffprobe`, `tools.ytdlp` e `tools.venv` passam a ser os executáveis (e a venv) que a CLI roda, e `runtime_dir` aponta a pasta de onde saem o yt-dlp e o Playwright. Por isso ele só vale com confiança:

- **Quem confia.** `GB_PROFILE` no ambiente do processo (a mesma fronteira de `GB_VENV_PATH`), o `$GB_HOME/getbrolls.toml` exato, ou um `profile --action trust --yes --expect <sha256>` que grava o sha dos bytes em `trusted-profiles.json`. Mudou um byte, deixa de valer até nova confiança.
- **Onde mora a confiança.** O `trusted-profiles.json` fica no `GB_HOME` de antes do perfil, nunca no `home` que o próprio perfil escolhe: senão ele se autoconfiaria.
- **Filhos.** A CLI passa aos processos filhos o perfil já conferido em `GB_PROFILE` + `GB_PROFILE_SHA256`; o filho confia nele como veio do ambiente, mas recusa se o sha não bater mais com o arquivo (editado no meio do caminho). Sem perfil válido, o filho recebe `GB_PROFILE=off`, nunca a busca de novo.
- **Recusas.** O `getbrolls.toml` nunca é link simbólico, é um arquivo comum e, no POSIX, é do usuário atual e não é gravável por grupo nem por outros.
- **O `home` relativo.** Um `home` (ou `runtime_dir`, `cache_dir`) relativo pode cair dentro do repositório onde o toml mora. O sha do perfil cobre só o toml: quem grava no repositório poderia deixar depois um `.env` (que escolhe executáveis) ou um `plugins.json` (que habilita plugins) nessa pasta. Mitigação: a prévia do `profile --action trust` avisa em `warnings` quando uma dessas pastas fica dentro da pasta do toml (e quando `plugins` cita id não instalado), e, com o `GB_HOME` vindo do perfil, o `.env` e o `plugins.json` de lá passam pelas mesmas recusas do toml (link, outro dono, gravável por grupo ou outros). Prefira pastas fora do repositório.
- **Sem confiança**, só os comandos que diagnosticam (`doctor`, `profile`, `capabilities`, `setup --check`/`--where`) rodam, relatando o problema; o resto para com código `2` antes de tocar em nada.

## Arquivos de fora (leitor único)

Na 2.6.0 nenhum comando usa esse leitor ainda: ele existe para os comandos de volta de ambiente (`handoffs/`) e de `--project-dir` de uma versão seguinte. O que `resolve --file`, `import-review --file` e os outros comandos desta versão leem, escolhido por você na linha de comando, não passa por ele, e nenhum comando emite `UNSAFE_INPUT` hoje. As regras abaixo descrevem o leitor, para quando esses comandos chegarem.

O que o getbrolls lê de uma pasta que outra pessoa ou outro programa escreveu (a volta de um ambiente em `handoffs/<ambiente>/NNN/`, um arquivo que um comando copia para o projeto, a pasta de `--project-dir`) passa por um leitor só, `scripts/getbrolls/reader.py`. Ele desce da raiz que o core escolheu um nome por vez e recusa link ou junção em qualquer pedaço do caminho; abre só arquivo comum, sem seguir link (no macOS e no Linux, pasta por pasta pelo descritor; no Windows, conferindo cada pedaço e a identidade do arquivo aberto, e, na listagem, a identidade de cada subpasta ao abri-la e de novo depois de listá-la; no Windows ainda resta uma janela de corrida entre as conferências, por exemplo uma junção posta no lugar de uma pasta depois da descida e desfeita antes da reconferência; no POSIX a listagem reabre cada pasta pelo descritor a partir da raiz e não precisa dessa reconferência); recusa texto com mais de um nome no disco (hardlink), arquivo acima do teto (256 KiB para texto, 64 KiB para JSON, 5 MB para um componente) sem ler além dele, texto fora de UTF-8 ou com NUL e JSON com chave repetida, NaN ou aninhamento fundo. Uma pasta sem permissão de leitura é erro de E/S (`IO_ERROR`), nunca "não existe". A recusa sai com `reason_code` `UNSAFE_INPUT`.

A cópia para o projeto sai do mesmo descritor aberto, com o sha256 contado no mesmo passe: o arquivo gravado é exatamente o que foi lido, e nunca vai por cima de outro. Texto de fora que chega a uma saída (o que a volta de um ambiente diz, por exemplo) vira `untrusted_text`: um parágrafo de até 4 KiB, sem caractere de controle nem de direção (bidi), sem segredo, URL ou caminho absoluto, sempre com a origem. É dado, nunca ordem. O "sem segredo" tem limites: o redator cobre segredos de uma palavra, então um segredo de várias palavras cortado pela janela de entrada (quatro vezes o teto) pode deixar um pedaço; um caractere invisível que não é de formatação (junção de grafemas, seletor de variação, preenchimento hangul, uso privado) ainda parte um token em dois; e um caractere invisível colado a uma aspa de uma chave entre aspas impede que o valor seja reconhecido. Zero-width, bidi, BOM e hífen suave entre duas letras ASCII, dígitos, `_` ou `-` são apagados (o token volta inteiro), exceto antes de um caminho, de uma URL ou de uma letra de unidade e em volta de uma palavra-chave de segredo ou de `Bearer`/`Basic`, onde viram espaço.

## Plugins

Plugins do SDK (`docs/SDK.md`) são opt-in por id: nada em `$GB_HOME/plugins/`
roda sem um `plugins --action enable --id <id> --yes` explícito, e habilitar
grava um pin de hash sobre toda a pasta — qualquer mudança de conteúdo
suspende o plugin até nova revisão. O hash enquadra cada arquivo (tamanho do
caminho, caminho e sha256 do arquivo, sob uma etiqueta de domínio), então duas
pastas diferentes não têm o mesmo pin; um pin do formato de antes da 2.6.0, que
não tinha esse enquadramento, deixa o plugin suspenso até um novo `enable`, que
mostra o diff e exige `--expect`. As permissões declaradas no manifesto
(`permissions.network` e `permissions.env`) são conferidas nos próprios canais
do SDK (`api.get_json`, `api.env`), não numa camada de isolamento do processo.
Os ids dos plugins oficiais (`hyperframes`, `davinci`, `asr`, `analysis_vision`, `analysis_audio`, `analysis_ocr` e `analysis_game`) e o prefixo `<oficial>_` são recusados em qualquer pasta de plugins e em qualquer índice de marketplace: um plugin de terceiros não se passa por oficial. A ponte oficial (hoje `hyperframes`) vem no pacote, em `plugins_official/`, e só carrega se o hash da pasta for o que o core fixou em `official_pins.py`; ela não passa pelo `plugins.json` e nenhuma ação de `plugins` a liga, desliga ou remove (só `GB_PLUGINS` e o perfil a desligam). A garantia é o pin (o hash da pasta que veio no pacote, gerado dentro do core) mais a origem do wheel: só o código oficial que o pacote leva é aceito, e uma cópia do usuário com id oficial fica `invalid` e nunca roda. Esse pin protege contra edição ou cópia acidental, arquivo a mais, link, bytecode fora do que o instalador compila e uma pasta de mesmo id em `$GB_HOME/plugins`; não protege contra quem pode gravar na pasta do pacote instalado, que já pode editar o próprio core. Não há caixa de areia: o código oficial roda com as permissões de quem executa a CLI. O teste `tests/test_official_plugins_purity.py` é um apoio de revisão que roda na suíte e recusa as famílias de fuga conhecidas (imports, nomes que abrem arquivo ou rodam código, atributos de frame, membros privados ou fora da lista do objeto `api`); ele não é uma caixa de areia nem uma garantia. O `.env` só entrega a um plugin variáveis do espaço de nomes dele (`<ID>_...`),
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
nunca vai para o cache em disco. Comandos de plugin (`getbrolls x`) só leem o projeto,
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
aceitos. O `tier` da entrada é o que o índice **declara**: qualquer marketplace
pode escrever `official`. Só o índice oficial (nome `getbrolls-plugins` e origem
fixada num dos repositórios oficiais) sai com `tier_verified: true`; nos outros a
saída diz "declarado pelo marketplace, não verificado", e `marketplace-add` de
outra origem avisa. O tier nunca libera uma conferência. Para um plugin sem
permissão, o índice pré-preenche o `--expect`: um agente que copia esse valor
pula a leitura humana. Instalar um plugin que pede qualquer permissão, ou um
`update` que acrescenta permissão, exige outro `--expect`: um valor derivado do
sha256 do conteúdo e das permissões, que o índice não traz. Ele não é segredo —
um agente consegue calculá-lo —; atrapalha quem copia o sha do índice, mas não
garante leitura humana. O que orienta mostrar a prévia à pessoa é a prévia em
chamada separada (o `install` sem `--yes` sempre para nela) e a skill.
`update --all` só lista; nada é atualizado em lote. `marketplace-update` só
aceita um commit do índice que não descende do fixado (volta atrás, história
reescrita) com `--allow-rollback`. Um commit fixado com `ref` que não é a ponta
dela vira aviso na prévia: na rede de forks do GitHub, um commit de um fork
também é servido pelo sha no repositório original, então o sha sozinho não prova
de que repositório o código veio. Quem mantém o marketplace escolhe o que
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

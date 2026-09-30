---
type: documentation
status: current
created: 2026-09-23
updated: 2026-09-26
tags: [get-brolls, sdk, plugins]
---

# SDK de extensões — Get B-rolls

> **Experimental:** `sdk_api` 1 pode mudar em versão minor; plugins declaram
> `requires_getbrolls`. Confira o CHANGELOG antes de atualizar o Get B-rolls.

O Get B-rolls aceita extensões locais em Python: **plugins** instalados numa
pasta pessoal, com opt-in explícito, que contribuem fontes de busca
(`providers`), rotas que trazem o arquivo (`routes`), comandos próprios
(`commands`) e presets de licença. Este documento é a referência do SDK; para
o caminho curto de criação, segurança e testes, veja
[`PLUGIN_DEV_QUICKSTART.md`](PLUGIN_DEV_QUICKSTART.md). Para exemplos completos
e funcionais, veja
[`examples/plugins/pasta_local`](../examples/plugins/pasta_local/README.md)
(acervo local, rota de prévia e comando) e
[`examples/plugins/banco_http`](../examples/plugins/banco_http/README.md)
(API autenticada, rota de `fetch`); para os contratos experimentais de export,
[`examples/plugins/hyperframes`](../examples/plugins/hyperframes/README.md)
(exportador de roteiro para um projeto HyperFrames e resolvedor do acervo `media-use`).

O princípio: **o plugin traz o arquivo; o core decide o resto.** Aprovação,
permit, hash, corte, ledger e entrega continuam só do core.

## O que é um plugin

Um plugin é uma pasta com um manifesto (`getbrolls-plugin.json`) e um arquivo
de entrada em Python que define `register(api)`. Ele roda **como código seu**,
com as mesmas permissões de quem executa a CLI — não é uma extensão isolada
nem carregada de um repositório remoto. Você escreve o plugin, revisa o
manifesto e decide habilitá-lo.

Nesta versão do SDK, um plugin pode contribuir seis tipos de extensão:

- **`providers`** — uma fonte de busca nova (`search`/`resolve`/`refresh`).
- **`routes`** — como o arquivo de um candidato chega (`prepare(item, workdir)`), para quem precisa de token, URL assinada ou pasta local.
- **`commands`** — rotinas próprias, rodadas com `gb x <plugin> <comando>` (ex.: sincronizar acervo, ver cota).
- **`presets`** — um preset de texto de licença, reaproveitado por `permit`.
- **`exporters`** (experimental) — transforma o roteiro revisado num projeto de edição, rodado por `gb export --to <nome>` (veja [Exportadores](#exportadores)).
- **`resolvers`** (experimental) — acha som ou música que o projeto não tem, só dentro do `gb export` (veja [Resolvedores](#resolvedores)).

## Estrutura da pasta

```
~/.getbrolls/plugins/<id>/
├── getbrolls-plugin.json   # manifesto
└── plugin.py                # arquivo de entrada (nome livre, declarado em "entry")
```

O nome da pasta tem que ser igual ao `id` do manifesto. Só pastas diretamente
dentro de `$GB_HOME/plugins/` são consideradas (por padrão,
`~/.getbrolls/plugins/`); pastas começando por `.` ou `_` são ignoradas.

## Manifesto

Campos de `getbrolls-plugin.json`:

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `id` | string | sim | 2–32 caracteres `a-z0-9_`, começando por letra; não pode ser um id reservado (`core`, `cliente`, `catalogo`, `direcao`, `template`, `projeto`). Igual ao nome da pasta. |
| `name` | string | sim | Nome de exibição, até 80 caracteres. |
| `description` | string | não | Até 500 caracteres. |
| `version` | string | sim | `X.Y.Z` do próprio plugin. |
| `sdk_api` | inteiro | sim | Versão do contrato do SDK que o plugin fala; hoje `1`. Diferente da versão instalada do Get B-rolls, o plugin é recusado. |
| `requires_getbrolls` | string | sim | Faixa de compatibilidade, ex.: `">=2.5,<3"`. |
| `entry` | string | sim | Nome do arquivo de entrada na raiz da pasta do plugin: `[A-Za-z0-9_]{1,64}\.py` (sem subpasta, hífen ou ponto extra). |
| `contributes` | objeto | não (padrão `{}`) | Listas por tipo de contribuição — veja abaixo. Chave ausente vale lista vazia. |
| `permissions` | objeto | não (padrão `{}`) | `network` (hosts liberados para `get_json`/`download`), `env` (variáveis liberadas para `env`) e `paths` (pastas liberadas para `local_file`); cada chave ausente vale lista vazia. |

`contributes` aceita as chaves `providers`, `presets`, `routes`, `commands`,
`exporters`, `resolvers`, `rules`, `hooks`, `themes`, `brief_templates`,
`eval_rubrics`, `capturers`, `engines`, `catalogs` e `roteiro_templates`, mas
**só `providers`, `presets`, `routes`, `commands`, `exporters` e `resolvers` são
suportados nesta versão do SDK** — declarar qualquer nome nas outras chaves faz o
manifesto ser recusado com "ainda não é suportado nesta versão do SDK". Os quatro
últimos são nomes guardados para o que vem depois: `capturers` (trazer um item
editado no motor de volta como item de catálogo), `engines` (chamar a CLI de um
motor com versão conferida e log), `catalogs` (catálogo de componentes por
projeto, cliente ou pessoa) e `roteiro_templates` (modelos de roteiro por gênero
ou cliente). `exporters` e
`resolvers` são experimentais (veja [Exportadores](#exportadores) e
[Resolvedores](#resolvedores)): o plugin os registra, o `plugins --action check`
os confere e o `gb export` os usa. Nomes de
provider, preset, rota, exportador e resolvedor têm que ser iguais ao `id` do
plugin ou começar por `<id>_`; nomes de comando só seguem a regra de nome
(`a-z0-9_`), porque `gb x <plugin> <comando>` já dá o espaço de nomes. Tudo o
que é declarado precisa ser efetivamente registrado em `register(api)`
(conferido por `api.finish()`).

`permissions.paths` lista pastas específicas, absolutas ou começando por `~/`
(sem `..`), ex.: `["~/Movies", "/Volumes/NAS/brolls", "D:\\Acervo"]`. Só arquivos
dentro delas passam por `api.local_file`. Uma raiz ampla demais é recusada no
manifesto, porque tornaria a declaração sem sentido: a raiz do sistema (`/`,
`\`), a raiz de uma unidade (`C:\`, `C:/`, `C:`), `~` sozinho (`~`, `~/`) e a
própria pasta pessoal escrita por extenso — `~` inteiro cobriria `~/.ssh` e o
`plugin-data` de outros plugins. O manifesto é conferido do mesmo jeito em
qualquer sistema (um caminho POSIX ou Windows absoluto vale nos dois); na hora de
usar, só entram as raízes absolutas no sistema atual (uma `D:\Acervo` é ignorada
no macOS). Na hora de usar, a raiz também é conferida pelo arquivo de verdade:
se, resolvida, ela for a raiz de um disco, um ponto de montagem
(`/Volumes/Backup`), a pasta pessoal ou uma pasta que a contém (um link,
`/Volumes/Macintosh HD`, `/Users`, a pasta pessoal com outra caixa), ela é
ignorada e o log registra `plugin_path_refused`. A prévia de `enable`/`install`
e o `plugins --action check` listam essas raízes em `warnings`. Num disco que não
diferencia maiúsculas de minúsculas, escreva a raiz com a mesma caixa que o
sistema mostra.

Os campos `schema`, `signed_fields` e `engines` existem no formato do manifesto
para versões futuras do SDK; nesta versão eles têm que ficar ausentes ou ser o
objeto vazio `{}` — conteúdo, `[]`, `""` ou `null` são recusados com "ainda não é
suportado nesta versão do SDK". `engines` vai dizer a faixa de versão de cada
motor que o plugin chama (ex.: `{"hyperframes": ">=0.8.73,<0.9"}`). Qualquer
outro campo de topo fora da tabela também recusa o manifesto.

Ids reservados: além de `core`, os ids `cliente`, `catalogo`, `direcao`,
`template` e `projeto` são do get-brolls. O manifesto com um deles é recusado, e
por isso `install`, `enable` e `new` também recusam: um plugin chamado
`direcao` seria dono da diretiva `[direcao:x]` no roteiro.

## Contrato de Provider

Um provider é um objeto com dois atributos e três métodos:

```python
class Provider(Protocol):
    name: str
    capabilities: ProviderCapabilities

    def search(self, query: str, limit: int, media: str) -> list[dict]: ...
    def resolve(self, url: str) -> dict | None: ...
    def refresh(self, item: dict) -> dict: ...
```

`name` segue a mesma regra de `id` (2–32 caracteres `a-z0-9_`) e tem que
casar com o `id` do plugin, ou começar por `<id>_`.

O core lê `capabilities` **uma vez**, no registro, confere o tipo de cada campo
(`bool` em `search`/`resolve_url`/`download`; texto em `match_kind`/`transport`/
`seek`; tupla — ou lista — de textos em `media_kinds`/`url_hosts`; `None` ou texto
em `env_key`/`route`) e guarda uma cópia. Depois disso nunca mais lê o objeto do
plugin: uma `property` que muda de valor, levanta ou devolve `bytes`/`NaN` no
lugar de texto faz o plugin falhar no carregamento (`failed`, com o campo no
motivo), não os comandos do core.

`ProviderCapabilities`, campo a campo:

| Campo | Padrão | Significado |
|---|---|---|
| `search` | `False` | A fonte implementa `search()`. |
| `resolve_url` | `False` | A fonte implementa `resolve()` para URLs próprias. |
| `media_kinds` | `("video",)` | Tipos de mídia que a fonte devolve: `video`, `image`, ou os dois. |
| `match_kind` | `"literal"` | Como o core rotula a correspondência: `literal` (entidade nomeada) ou `illustrative` (ideia genérica). |
| `env_key` | `None` | Nome da variável cuja presença indica "fonte configurada" em `providers`, `doctor` e no BRIEF (informativo; a leitura real passa por `api.env`). Conta o ambiente do processo e, do `.env`, só o valor do espaço de nomes deste plugin — declarar a variável de outro plugin nunca mostra a fonte como configurada. |
| `transport` | `"https"` | Rótulo do transporte usado, para exibição (`https`, `local`, etc.). |
| `url_hosts` | `()` | Hosts que só esta fonte pode reivindicar em `resolve`; colisão com outra fonte (built-in ou plugin) é recusada no registro. |
| `seek` | `"unsupported"` | Rótulo de que tipo de busca por tempo a fonte oferece. |
| `download` | `True` | Se o core pode baixar o arquivo por `https`/`yt-dlp` (`False` para fontes só-metadados). Com `route`, vale a rota. |
| `route` | `None` | Nome da rota **do mesmo plugin** que entrega o arquivo dos candidatos desta fonte; conferido em `api.finish()`. Com ela, o core grava `acquisition = {"status": "available", "method": "plugin:<rota>", "evidence": []}`. |

`search(query, limit, media)` devolve uma lista (ou gerador) de candidatos
construídos com `api.candidate(...)`. `resolve(url)` devolve um candidato para
uma URL que a fonte reconhece, ou `None`. `refresh(item)` recebe o candidato
atual e devolve uma versão com `media_url` renovado (útil para links que
expiram).

## Rotas

Uma rota diz como o arquivo de um candidato chega à máquina:

```python
class Route(Protocol):
    name: str
    stage: str  # "preview" ou "fetch"

    def prepare(self, item: dict, workdir: Path) -> RouteResult: ...


@dataclass(frozen=True)
class RouteResult:
    path: Path
    license: str | None = None
```

- `stage="preview"`: a rota pode trazer mídia de trabalho para `inspect`,
  `preview` e `preview --scan` (ex.: cópia de um arquivo do acervo local).
- `stage="fetch"`: trazer o arquivo consome licença ou cota, então o core só
  chama a rota no `fetch`, depois da aprovação humana e do `permit`. Em
  `inspect`/`preview`/varredura ela é recusada com uma mensagem que manda usar
  `preview --candidate ID --start ... --end ... --reference-only` — numa foto,
  `preview --candidate ID --reference-only`, sem `--start/--end`. Essa
  referência é a miniatura da fonte (`preview.poster_url`): preencha
  `poster_url` ou `embed_url` no candidato. Candidato de plugin sem prévia
  local, sem `poster_url` e sem `embed_url` não tem nada que a pessoa possa ter
  visto — o `preview --reference-only` avisa "Sem imagem de referência" e o
  `approve` recusa.
- `item` é uma **cópia** do candidato: mexer nela não muda nada no projeto.
- `workdir` é criado pelo core em `.getbrolls-sources/plugin-<id>-<uuid>/` e
  apagado ao fim, com ou sem erro. Use `api.download` ou `api.local_file`
  para gravar nele.

O que a rota devolve passa pela verificação do core antes de qualquer uso:
arquivo real (link simbólico é recusado) dentro do `workdir`, não vazio, até o
teto de 512 MB e legível pelo `ffprobe` como vídeo ou imagem. Depois disso o
fluxo é o de sempre: sha256, cache privado, prévia, corte e ledger.

`RouteResult.license` (até 500 caracteres) vira evidência extra em
`rights.evidence` — `"Licença registrada pelo plugin <id>: <texto>"` — quando a
rota roda no `fetch`, **depois** do permit humano; nunca substitui o permit.
O texto entra numa linha só, sem caractere de controle nem de direção (bidi),
com até 300 caracteres e com `;`/`|` trocados por `,`/`/` — assim ele nunca
vira outro item de evidência nem uma "Declaração do usuário" em ORIGEM.md ou
credits.md. O texto de um preset de plugin passa pelo mesmo saneamento e é
gravado como `"Condições informadas pelo plugin <id>: <texto>"`.
Uma rota `stage="preview"` que já deixou a mídia de trabalho pronta não roda de
novo no `fetch`, então a licença dela não é registrada.

Rota `stage="fetch"` consome licença ou cota **uma vez só**:

- O arquivo que ela trouxe vai para o cache privado do projeto
  (`.getbrolls-sources/`, índice por candidato + sha256, separado da mídia de
  trabalho de `inspect`/`preview`), junto com a licença. O índice guarda só o
  nome do arquivo, procurado sempre no cache do projeto atual: um projeto
  movido continua achando o arquivo, e uma cópia nunca lê o cache do projeto
  original (uma entrada antiga com caminho absoluto vale pelo nome do arquivo).
- Antes de cortar, o `fetch` já grava no ledger a evidência da licença e o
  marcador `acquisition.route_consumed_at`. Se o corte falhar (por exemplo, o
  trecho aprovado passa da duração real que a fonte entregou — o `fetch` avisa
  com a duração), o próximo `fetch` reaproveita o arquivo do cache: a rota não
  é chamada de novo e a licença aparece uma vez só.
- Com `route_consumed_at` gravado e o arquivo fora do cache (a pasta
  `.getbrolls-sources/` foi apagada, ou o projeto mudou de computador levando só
  `brolls/`), o `fetch` **recusa** sem chamar a rota e diz em que data a licença
  foi consumida. Restaure `.getbrolls-sources/`, ou — só com o ok da pessoa,
  porque é uma nova compra/cota — rode `fetch --candidate <ID> --reacquire`: a
  rota roda de novo, a nova licença entra na evidência e a data vai para
  `acquisition.route_reacquired_at`. Com o cache presente, `--reacquire` não
  muda nada (o arquivo é reaproveitado).
- A busca grava `preview.route_stage = "fetch"` nos candidatos dessa fonte:
  `status`/guidance leem isso (sem rodar plugin) e nunca sugerem `inspect` nem
  prévia com mídia para eles — sugerem `preview --reference-only`, depois
  approve, permit e fetch.
- Numa imagem entregue pela rota, a extensão vem do **conteúdo** do arquivo
  (`media.image_suffix`), como nas fotos do Commons e da NASA: `.jpg`, `.png`,
  `.webp`, `.gif`, `.tif` ou `.bmp` (`SNIFFED_IMAGE_SUFFIXES`), e é essa que vai
  para o nome em `clips/`. O nome que o plugin deu ao arquivo é ignorado — um PNG
  gravado como `foto.jpg` sai `.png`. Conteúdo que não é nenhum desses formatos
  é recusado depois de a licença e `route_consumed_at` ficarem gravados, então o
  retry não roda a rota de novo.

## PluginApi

`register(api)` recebe a única porta de entrada no registro:

- `api.provider(provider)` — registra um `Provider`; o `name` tem que estar em `contributes.providers`.
- `api.preset(name, url, text)` — registra um preset de licença; `text` tem que terminar em `"verifique a página da fonte: {url}"`.
- `api.candidate(provider, source_id, title, source_url=None)` — monta um candidato vazio, no formato que o core espera; use isto em vez de montar o dicionário à mão.
- `api.env(key)` — lê uma variável de ambiente; `key` tem que estar em `permissions.env` do manifesto, senão levanta erro.
  A variável pode vir do ambiente ou do `.env` da skill (ou `--env-file`). O `.env` aceita, para um plugin instalado, só as variáveis de `permissions.env` do **espaço de nomes dele**: o id em maiúsculas seguido de `_` (`BANCO_HTTP_TOKEN` para `banco_http`, `PASTA_LOCAL_DIR` para `pasta_local`, `<ID>_TOKEN` no scaffold); variável do core (`GB_*`) ou de outro plugin é recusada, com uma mensagem que diz o porquê. O valor de uma variável de plugin vinda do `.env` **nunca vai para o ambiente do processo**: fica num mapa do core que só `api.env` do plugin dono lê. Subprocessos (ffmpeg, yt-dlp, git, playwright), OpenSSL e o `ssl` do Python não a enxergam, e outro plugin que peça o mesmo nome recebe `None`. A precedência é a mesma das variáveis do core: se a variável tiver valor no ambiente real do processo, `api.env` devolve esse valor (o ambiente real é escolha da pessoa e não é tocado); senão, o do `.env`. Um nome que ferramentas do sistema leem (`GIT_*`, `XDG_*`, `OPENSSL_*`, `SSL_*`, `LD_*`, `DYLD_*`, `NODE_*`, `PLAYWRIGHT_*`, `DENO_*`, `FONTCONFIG_*`, proxies...) não é recusado — ele não chega ao ambiente —, mas gera aviso ao ler o `.env` e na prévia. A prévia de `enable`/`install`/`check` traz `warnings` quando `permissions.env` pede uma variável do core, de ferramenta do sistema, de outro plugin ou fora do próprio espaço de nomes, e quando outro plugin instalado pede uma variável do espaço de nomes deste (ex.: `banco` pedindo `BANCO_HTTP_TOKEN` com `banco_http` instalado). Uma linha que sobrou no `.env` de um plugin removido diz para tirar a linha ou reinstalar o plugin.
- `api.get_json(url, params=None, headers=None, cache_ttl=0, keep_signed=False)` — faz uma requisição HTTP GET com o mesmo transporte validado do core (resolução de IP, HTTPS, sem redirect); o host de `url` tem que estar em `permissions.network`. A resposta de um plugin **nunca vai para o cache** em disco (`cache_ttl` é aceito e ignorado) e passa por uma limpeza mais rígida que a dos built-ins: URL assinada vira `None` (inclusive com esquema em maiúsculas, `HTTPS://`) e uma URL assinada no meio de um texto vira `[URL omitida]`. Com `keep_signed=True`, a URL assinada **fica** na resposta — é o caso de uma API que devolve um `download_url` pré-assinado para o arquivo licenciado (estilo Envato): leia o `download_url` e passe para `api.download` dentro da rota. O corpo de um erro HTTP nunca aparece na mensagem.

  Chave de JSON com nome de credencial some. O nome é normalizado antes de comparar — minúsculo, sem `-`/`_`/espaço, então `access_token`, `accessToken` e `X-Api-Key` contam como o mesmo nome — e é comparado por igualdade ou por **terminar** num destes marcadores fortes: `access_token`, `refresh_token`, `id_token`, `auth_token`, `session_token`, `security_token`, `bearer_token`, `api_key`, `api_token`, `api_secret`, `client_secret`, `secret_key`, `secret_access_key`, `private_key`, `signing_key`, `encryption_key`, `password`, `passwd`, `credentials`, `jwt`, `secret` — mais `pwd` e `hmac`, só como nome inteiro (curtos demais para valer como sufixo) — mais `key`, `token`, `authorization`, `signature` e `sig` (esses cinco já saem para todo mundo, plugin ou não, e são nome exato, não normalizado). Isso cobre chave composta de verdade (`aws_secret_access_key`, `x-api-key`, `x-amz-security-token`) sem depender de snake_case exato. **Chave de paginação/id sobrevive**: `next_page_token`, `nextPageToken`, `page_token`, `continuation_token`, `sort_key`, `cursor_key`, `cursor`... nenhuma delas termina nos marcadores acima — de propósito: uma versão mais ampla dessa checagem (o mesmo regex usado para nome de parâmetro de URL) derrubava qualquer chave só por terminar em `key`/`token`/`policy` sozinho, o que sumia com paginação de API real. Se o seu plugin usa um nome de credencial fora dessa lista, a resposta da API não é reescrita antes do scrub — a chave sai como veio; ou trate a URL assinada com `keep_signed`.
- `api.route(route)` — registra uma `Route`; o `name` tem que estar em `contributes.routes`.
- `api.command(name, handler, help)` — registra um comando; `name` tem que estar em `contributes.commands`, `help` é a frase que `x --list` mostra.
- `api.exporter(name, export, description)` — experimental: registra um exportador; `name` tem que estar em `contributes.exporters`, `export(plan, options)` devolve um `ExportResult(files, media=[], notes=[])` e `description` tem de 1 a 200 caracteres. Veja [Exportadores](#exportadores).
- `api.resolver(name, resolve, kinds)` — experimental: registra um resolvedor; `name` tem que estar em `contributes.resolvers`, `resolve(kind, name)` devolve um `ResolverHit(path, license=None)` ou `None`, e `kinds` é uma lista não vazia, sem repetição, com `"sfx"` e/ou `"musica"` (`RESOLVER_KINDS`). As pastas em que ele pode achar arquivo são as de `permissions.paths`, conferidas como em `api.local_file`. Veja [Resolvedores](#resolvedores).
- `api.download(url, name, headers=None)` — só dentro de `Route.prepare`: baixa `url` (https, host em `permissions.network`, IP público, sem redirect, teto de 512 MB) para `workdir/name` e devolve o caminho. Aceita URL assinada (ex.: um link S3 que o próprio plugin assinou) e headers como `Authorization`; nenhum dos dois vai para log ou mensagem de erro. `name` é só nome de arquivo (`[A-Za-z0-9._-]`, sem `/` nem `..`).
- `api.local_file(path)` — só dentro de `Route.prepare`: copia um arquivo que
  esteja dentro de `permissions.paths` para o `workdir`. Sempre cópia, nunca
  hardlink — o original da pessoa não muda. No macOS e no Linux, a pasta de
  `permissions.paths` é aberta descendo de `/` um nome por vez, sem seguir link,
  pelo caminho gravado no pin no `enable`/`install`/`update` (a prévia mostra
  esse caminho), e dela até o arquivo a descida segue igual, uma pasta por vez:
  trocar a própria pasta de `permissions.paths`, uma pasta acima dela ou uma
  pasta do caminho por um link, antes da chamada ou durante a abertura, não
  entrega mais um arquivo de fora dela. Uma pasta de `permissions.paths` que
  hoje não confere com a gravada no pin do enable (aponta para outro lugar, ou o
  pin não a guardou) fica ignorada, com aviso; com pin de versão anterior, sem
  esse campo, vale o caminho resolvido na carga, com aviso para habilitar de
  novo. O arquivo em si abre com `O_NOFOLLOW` (um link trocado ali não é
  seguido) e `O_NONBLOCK` (uma FIFO não trava), conferido pelo próprio descritor
  (arquivo regular, até 512 MB) e copiado dele com o teto contado nos bytes
  lidos; o destino é criado exclusivo, sem seguir link plantado. Hardlink é
  aceito (snapshot de NAS, `rsync --link-dest`) e fica registrado no log
  (`plugin_path_hardlink`, só com o id do plugin). A recusa nomeia o arquivo que
  o plugin pediu, nunca o alvo resolvido. No Windows, onde `O_NOFOLLOW` e
  `dir_fd` não existem, vale a conferência antiga — resolução + conferência de
  raiz antes de abrir e de novo depois; ela ainda deixa uma janela para quem
  troca pastas do caminho durante a abertura, e uma junction de pasta (`mklink
  /J`) não exige administrador nem modo de desenvolvedor: a descida sem seguir
  link, por enquanto, é só do macOS e do Linux.
- `api.data_dir` — `$GB_HOME/plugin-data/<id>/` (0700), criado na primeira leitura: estado e cache do plugin. Fica fora da pasta do plugin, então escrever ali não muda o pin de hash.
- `api.cli_argv()` — argv (lista) para rodar esta mesma instalação num subprocesso: `[*api.cli_argv(), "status", "--project", p]`. Nunca monte o caminho de `gb.py` à mão: no pacote instalado ele não existe.
- `api.config()` — lê `data_dir/settings.json` como dicionário (`{}` sem arquivo); JSON inválido ou que não é objeto vira erro com o caminho relativo.
- `api.finish()` — chamado automaticamente pelo loader depois de `register()`; confere se tudo declarado em `contributes` foi mesmo registrado e se cada `capabilities.route` aponta para uma rota registrada pelo próprio plugin.

## Guarda-corpos

Tudo que um provider devolve passa pelo core antes de virar candidato de
verdade. O core reescreve, e nunca lê do plugin, os campos que representam
decisão humana ou estado interno: `segment`, `approval`, `output`, `state` e
`errors` sempre voltam para o valor inicial (pendente/vazio), não importa o
que o plugin tenha colocado ali.

Dentro dos campos que o plugin pode preencher, só um allowlist de subcampos
sobrevive:

- `creator`: `name`, `url`, `handle`.
- `match`: `kind`, `reason`.
- `media`: `duration_s`, `width`, `height`, `fps`, `kind`.
- `preview`: só `poster_url`, `embed_url` e `seek_mode` — `poster_path` e `contact_sheet_path` são sempre `None` na saída do plugin; só o core grava caminho local.
- `rights`: `license_name`, `license_url`, `evidence`, `attribution` — `status` sempre volta para `"unknown"`; a decisão de direitos é humana.
- `acquisition`: `status` e `method`, restritos a valores conhecidos (`available`/`unavailable`, `https`/`yt-dlp`/`None`); `evidence`. Um `method` `plugin:*` escrito pelo próprio plugin é recusado. Com `capabilities.route`, quem escreve é o core: `{"status": "available", "method": "plugin:<rota>", "evidence": []}`, não importa o que o candidato trouxe. Sem rota e com `capabilities.download=False` (fonte só-metadados), o core força `{"status": "unavailable", "method": None, "evidence": []}` — o core nunca vai baixar por essa fonte, então essa promessa não pode chegar ao candidato.

Qualquer campo de topo ou subcampo fora dessas listas é descartado em
silêncio (do ponto de vista do retorno da CLI) e registrado no log estruturado
`plugin_candidate_sanitized`, com os nomes dos campos removidos. `media_url`,
`source_url`, `preview.poster_url`/`embed_url`, `creator.url` e
`rights.license_url` sempre passam pelo `public_url()` do core no modo
estrito: só HTTPS público, e URL com parâmetro de credencial na query (`key`,
`token`, `signature`, `password`, `hmac`, `jwt`, `client_secret`, `*_token`,
Akamai `__token__`/`hdnts`/`hdnea`, CloudFront `Policy`/`Key-Pair-Id`, `X-Amz-*`,
`X-Goog-*`) vira `None`. As fontes embutidas seguem o filtro de sempre (só os
nomes exatos e as assinaturas S3/GCS). O `source_id` de `api.candidate` tem que
ter de 1 a 128 caracteres entre `A-Z`, `a-z`, `0-9`, `.`, `_`, `:` e `-`: ele
vira parte do id do candidato, que aparece em comandos sugeridos; qualquer
outro caractere recusa o candidato (evento `plugin_candidate_refused` no log).

O registro de proveniência que a pessoa lê para decidir (`ORIGEM.md`,
`credits.md`, Storyboard) não aceita texto do plugin como decisão:

- `rights.evidence` vindo do plugin é **descartado**. Evidência só entra pelo
  `permit` humano e, depois dele, pela licença que o core registra da rota de
  `fetch`.
- `title`, `creator.name`/`handle`, `rights.license_name`/`attribution` e
  `match.reason` viram uma linha só, sem caractere de controle, com até 300
  caracteres — um título com quebra de linha não forja uma linha
  "Direitos:"/"Aprovado por:" no `ORIGEM.md`. O próprio `ORIGEM.md`/`credits.md`
  também escreve cada valor numa linha só (texto normal sai idêntico).
- Num candidato de plugin, `ORIGEM.md`, `credits.md` e a tabela de
  `entrega/README.md` escapam com barra invertida os caracteres que abrem
  marcação dentro de uma linha (`` \ ` * _ [ ] ( ) < > ! | % ~ = # $ { } ``) no
  título, autor, licença, URL da licença, URL da fonte e no texto da
  licença/preset registrado pelo plugin, e quebram só o gatilho do autolink
  (`https\://`, `www\.`): um `<img>`, um `[link](url)`, um `**negrito**`,
  `%%comentário%%`/`==realce==` do Obsidian, `~~riscado~~`, `#tag`,
  `$matemática$` ou um endereço solto sai como texto. Ponto, vírgula, hífen e
  dois-pontos ficam como estão, então um título comum sai idêntico. Fonte
  embutida sai como sempre.

Uma exceção levantada dentro de `search`, `resolve`, `refresh` ou `Route.prepare` nunca derruba
a CLI: ela vira `ProviderError` com a mensagem `Plugin <id>: ...`, e a busca
continua com as outras fontes. `refresh` só pode mudar `media_url` — qualquer
outra diferença entre o candidato atual e o que o plugin devolveu é ignorada.

## Mensagens de erro: `PluginError`

Para dizer à pessoa o que fazer (configurar um token, rodar a busca de novo),
levante `PluginError` — é a **única** exceção de plugin cujo texto chega a quem
usa:

```python
from getbrolls.sdk import PluginError

token = api.env("MEU_BANCO_TOKEN")
if not token:
    raise PluginError("Configure MEU_BANCO_TOKEN com o token da sua conta.")
```

- O core mostra `Plugin <id>: <mensagem>` em `search`, `resolve`, `refresh`,
  rotas, comandos (`gb x`) e no motivo de um `register()` que falhou.
- A mensagem é saneada antes: vira uma linha só, sem caractere de controle,
  passa por `redact()` (URLs, headers e chaves conhecidas somem), o valor de
  cada variável declarada em `permissions.env` — e todo texto de 8 caracteres
  ou mais do `settings.json` lido por `api.config()` — vira `[REDACTED]` e o
  texto é cortado em 300 caracteres.
- Vale só a própria classe, com um único argumento de texto: uma subclasse de
  `PluginError` (ou `PluginError(123)`) aparece só pelo tipo.
- Qualquer outra exceção (`ValueError`, `KeyError`, `requests`-like, uma
  `BaseException` custom, `SystemExit`) aparece **só pelo tipo**, ex.:
  `Plugin <id>: falha em <fonte> (ValueError).` O texto dela nunca chega à
  mensagem, ao traceback de `diagnostics.jsonl` nem ao log: é assim que um
  token colado numa exceção por engano não vaza.
- Recusas do próprio core (host fora de `permissions.network`, arquivo fora de
  `permissions.paths`, nome não declarado em `contributes`) também aparecem
  por inteiro, com o mesmo saneamento.
- `plugins --action check` é a exceção a essa regra: como roda a pasta que
  você mesmo apontou, ele também mostra o texto de uma exceção embutida do
  Python (ex.: `RuntimeError('boom')`), para facilitar a depuração.

Eventos do log estruturado relacionados a plugins:
`plugin_loaded` (plugin, versão, providers, presets e a quantidade de rotas e
comandos registrados), `plugin_skipped`, `plugin_failed`, `plugin_enabled`,
`plugin_disabled`, `plugin_installed`, `plugin_updated`,
`plugin_request_refused`, `plugin_path_refused`, `plugin_call_failed`,
`plugin_candidate_sanitized`, `plugin_route` (plugin, rota, estágio, bytes, ms)
e `plugin_command` (plugin, comando, quantidade de argumentos, ms). Nenhum
deles carrega token, header, URL assinada, caminho absoluto de mídia nem valor
de argumento.

## Comandos

```python
def recentes(args: dict, ctx: CommandContext) -> dict: ...


api.command("recentes", recentes, "Lista os vídeos mais recentes da pasta")
```

- `gb x --list` lista os comandos dos plugins habilitados, lendo só o
  manifesto (nenhum código roda).
- `gb capabilities --json` descreve, em JSON, os comandos do get-brolls e os `plugin_commands` habilitados (só o manifesto): plugins e agentes descobrem o que existe sem ler `--help`.
- `gb x <plugin> <comando> [--project P] [--arg chave=valor]...` carrega os
  plugins, chama `handler(args, ctx)` e imprime `{"plugin", "command",
  "result"}`. `args` é um dicionário de texto; chave repetida é erro.
- `ctx.plugin_id`, `ctx.project` (ou `None`), `ctx.candidates()` (cópias dos
  candidatos do projeto) e `ctx.brief()` (cópia do JSON do BRIEF.md, ou
  `None`). Com `ROTEIRO.md` do get-brolls (`type: roteiro`), beat aposentado
  pelo roteiro (`"retired": true`) sai de `beats`: o plugin vê os mesmos beats
  que o `status`. `ctx.retired_beat_ids()` devolve exatamente os ids que saíram,
  na ordem do BRIEF.md; beat marcado `retired` com id fora do formato continua
  em `beats`.
  Comando **só lê** o projeto: não há como gravar no ledger, e `x` não toma a
  trava exclusiva nem cria `brolls/`.
- O retorno tem que ser um objeto JSON (dict). Exceção no handler vira erro
  `Plugin <id>: o comando <nome> falhou (<tipo>)`, exit 1; com `PluginError`,
  a mensagem é `Plugin <id>: <texto>` (veja
  [Mensagens de erro](#mensagens-de-erro-pluginerror)).

## Exportadores

> **Experimental.** O formato do plano e as regras abaixo podem mudar em versão
> minor. Quem roda exportador é o `gb export --to <nome> --project <projeto>`,
> sobre um roteiro revisado e sincronizado (passo a passo e portões em
> [`references/roteiro.md`](../references/roteiro.md#export-do-roteiro-ao-projeto-de-edição)).

### Seu exporter em 30 minutos

O mínimo que passa no contrato: manifesto declarando o exporter e uma função
pura que devolve um `index.html`, sem pedir mídia nenhuma. A função do
exportador recebe dois argumentos, `plan` (o plano de export, detalhado
abaixo) e `options` (hoje sempre `{"args": {}}`; reservado para opções
futuras — o exportador dos exemplos abaixo só o declara, sem usar).

`getbrolls-plugin.json`:

```json
{
  "id": "meu_exporter",
  "name": "Meu exporter",
  "description": "Exemplo mínimo de exporter: grava um índice HTML com o título de cada cena.",
  "version": "0.1.0",
  "sdk_api": 1,
  "requires_getbrolls": ">=2.6,<3",
  "entry": "plugin.py",
  "contributes": {"exporters": ["meu_exporter"]},
  "permissions": {"network": [], "env": [], "paths": []}
}
```

`plugin.py`:

```python
"""Exporter mínimo: um índice com o título de cada cena, sem mídia nenhuma."""

from html import escape

from getbrolls.sdk import ExportResult, PluginError


def exporta(plan, options):  # noqa: ARG001 - options fica reservado ao contrato
    if plan["export_version"] != 1:
        raise PluginError(f"export_version {plan['export_version']} não é suportado por este exportador.")
    linhas = [f"<li>{escape(cena['title'])}</li>" for cena in plan["scenes"]]
    indice = "<h1>" + escape(plan["meta"]["tema"]) + "</h1><ul>" + "".join(linhas) + "</ul>"
    return ExportResult(files={"index.html": indice})


def register(api):
    api.exporter("meu_exporter", exporta, "Índice HTML com o título de cada cena")
```

Confira o contrato sem instalar nada, rodando contra a pasta local:

```
python3 scripts/gb.py plugins --action check --path /caminho/para/meu_exporter
```

`"ok": true` quer dizer que o manifesto é válido e que `exporta()` rodou de
verdade contra o plano de exemplo
[`examples/plans/reels.plan.json`](../examples/plans/reels.plan.json) sem
quebrar nenhuma regra do `ExportResult` (veja a tabela abaixo). Só depois disso
instale de fato (`plugins --action install --source /caminho --yes --expect
<sha256>`) e rode `gb export --to meu_exporter --project <projeto>` contra um
roteiro revisado e sincronizado.

Com mídia (dentro de `def register(api):`, como no exemplo anterior):

```python
from html import escape

from getbrolls.sdk import ExportResult, MediaRequest, PluginError


def exporta(plan: dict, options: dict) -> ExportResult:
    if plan["export_version"] != 1:
        raise PluginError(
            f"export_version {plan['export_version']} não é suportado por este exportador: atualize o plugin."
        )
    partes = ["<h1>" + escape(plan["meta"]["tema"]) + "</h1>"]
    partes += ["<h2>" + escape(cena["title"]) + "</h2>" for cena in plan["scenes"]]
    media = [
        MediaRequest(media_id, f"assets/midia-{n}{row['ext']}")
        for n, (media_id, row) in enumerate(sorted(plan["media"].items()), start=1)
        if row["available"]
    ]
    return ExportResult(
        files={"index.html": "\n".join(partes)},
        media=media,
        notes=["Abra index.html no navegador."],
    )


def register(api):
    api.exporter("meu_exporter", exporta, "Exporta o plano como página HTML, com mídia")
```

- **O plano.** Um dict JSON descrito em
  [`schemas/export_plan.schema.json`](../schemas/export_plan.schema.json):
  `export_version`, `exporter`, `out_dir` (`exports/<nome>/NNN`, a pasta que o
  core vai criar), `generated_at`, `getbrolls_version`, `plan_version`, `meta`
  (`aspecto`, `legenda`, `duracao_alvo_s`, `genero`, `tema`, `projeto_id`,
  `cliente`, `direcao`, `fps`, `canvas`), `total_s`, `timing`, `scenes`, `media`
  e `warnings`. Não há título no topo: o nome do
  vídeo é `plan["meta"]["tema"]`. Cada cena traz `id`, `title`, tempo global
  (`start_s`, `duration_s`), `layout` com as vagas (`slots`), `voice_media_ids`,
  `words_timed` (legenda palavra a palavra, ou `None`), `layers`, `extensions` e
  `speech_clean`. `media` mapeia cada id lógico (`clip:…`, `aroll:…`,
  `asset:…`, `plugin:…`) para `kind`, `ext`, `available`, `credit` e o resto da
  linha; a mídia com `available: false` não tem arquivo para pôr. Um exportador
  deve tratar um `problem` que não conhece como indisponível: valor novo pode
  aparecer sem mudar `export_version`, e os valores atuais estão listados no
  schema.
- **Campos que só dão nome.** `meta.projeto_id` é o id do projeto (o mesmo
  `project_id` de `brolls/manifest.json`; o export só lê o id e manda `null`
  quando o projeto ainda não tem um). `meta.cliente` e `meta.direcao`
  vêm do frontmatter do roteiro (slug ou `null`). `meta.fps` (`{num, den}`, 30 é
  `{num: 30, den: 1}`) e `meta.canvas` (`{width, height}` em pixels) são `null`
  nesta versão: com `null`, o exportador usa o padrão dele; com valor, usa o
  valor. Cada camada traz `ref` (id de item de catálogo, sempre `null` por
  enquanto), cada cena traz `direction` (intenções de direção, sempre `[]`) e
  `origin` de mídia aceita `"client"` (biblioteca do cliente, ainda sem uso).
- **Plano gravado.** Toda pasta `exports/<nome>/NNN/` recebe
  `getbrolls-plan.json`, o plano exato entregue ao exportador. O nome é
  reservado: `files` com ele (ou com `.getbrolls-export.json`) é recusado.
- **Função pura.** O exportador recebe cópias do plano e das opções (`options`
  é `{"args": {}}` por enquanto) e devolve texto e pedidos de mídia. Ele nunca
  toca no disco: quem grava os `files` e coloca cada mídia no `dest` pedido é o
  core. Mudar o plano recebido não muda nada fora do exportador.
- **Texto do plano não é confiável.** Títulos, falas, autores e créditos vêm de
  fontes, inclusive de outros plugins. Todo texto do plano, incluindo `credit`, é
  cru: o core não escapa nada para Markdown ou HTML, de fonte nenhuma. O
  exportador escapa esse texto no formato de saída: HTML com `html.escape`,
  JSON com `json.dumps`, JavaScript só dentro de um JSON (nunca concatenado no
  código), Markdown com as marcações escapadas. O core confere a estrutura de
  `files`, mas **não** saneia o conteúdo dos arquivos.
- **Nada de caminho absoluto.** O plano traz ids lógicos de mídia e caminhos
  relativos ao projeto (`exports/meu_banco_html/003`), nunca um caminho do disco.
  Um export costuma ser compartilhado, e caminho local vaza nome de usuário e
  pastas: não escreva um em `files`. A mídia entra só por `media`. O `gb export`
  recusa, sem gravar nada, arquivo que contém um caminho real desta máquina (a
  pasta do projeto, a pasta pessoal, `GB_HOME`, o caminho de uma mídia, uma
  pasta de `permissions.paths`), em qualquer grafia: sem caixa, com `\` ou `\\`
  no lugar de `/` (`C:/Users/…`), `\/` de JSON, percent-encoded (`file:///…%20…`,
  até três vezes), NFD, com barras repetidas ou, no Windows, com o nome curto
  (8.3, como `PROGRA~1`) de uma pasta do caminho. Texto que só tem cara de caminho
  (vindo da fala do roteiro, por exemplo) sai como está, com um aviso.
- **Falha.** Exceção no exportador vira erro `Plugin <id>: …` (exit 1): o texto
  de um `PluginError`, ou só o tipo de qualquer outra exceção. Um resultado fora
  das regras abaixo também vira erro, que diz qual regra quebrou.
- **Plugin indisponível.** Só plugin `enabled` exporta. Com o plugin
  `suspended`, `failed` ou fora de `GB_PLUGINS`, o erro nomeia o plugin, o
  status e o que fazer.

O que o core confere no `ExportResult` (tipos exatos; subclasse é recusada):

| Regra | Valor |
|---|---|
| Tipos | `files` é um `dict` de texto para texto; `media`, uma `list` de `MediaRequest(media_id, dest)`; `notes`, uma `list` de texto. |
| Caminhos | Relativos, com `/` (nunca `\`), até 240 caracteres e 6 níveis. Cada parte usa letras, números, `.`, `_` ou `-`, não começa por `.`, não é `..`, não termina em `.` e não é nome reservado do Windows (`CON`, `NUL`, `COM0`–`COM9`, `LPT0`–`LPT9`…). |
| Colisões | Nenhum caminho repetido ignorando maiúsculas, em `files`, em `media` e entre os dois; nenhum arquivo pode ser a pasta de outro (`a.html` e `a.html/b.css`). |
| `assets/` | Reservado à mídia: nenhum arquivo de `files` fica ali, e todo `media[].dest` fica dentro dela (`assets/<arquivo>`). |
| Extensões | `files` só em `.html`, `.json`, `.css`, `.js`, `.md` ou `.txt`, em minúsculas. |
| Texto | Sem o caractere NUL e em UTF-8 válido. |
| Tetos | Até 200 arquivos, 2 MB por arquivo e 8 MB no total; até 500 pedidos de mídia, com `media_id` de até 200 caracteres; até 50 notas, cada uma saneada para uma linha de até 300 caracteres. |

As notas saem numa linha, mas com a marcação como o plugin escreveu: quem as
mostra (o `gb export`, por exemplo) passa cada uma por `delivery.inert`, com
`getbrolls.sdk.exporters.note_line(owner, texto)`, que devolve a linha já inerte
e com o prefixo "Nota do plugin <id>:".

O `plugins --action check` (e `sdk.testing.check_exporter`) roda o exportador
com o plano de exemplo
[`examples/plans/reels.plan.json`](../examples/plans/reels.plan.json)
(`getbrolls.sdk.exporters.sample_plan()`: quatro cenas de layouts diferentes,
clipe, A-ROLL, componentes, legenda, camadas e mídia faltando) e passa o
resultado pelo mesmo validador: a regra quebrada aparece antes de qualquer
export. Use o mesmo arquivo nos testes do seu exportador.

Depois do validador, o `gb export` confere cada pedido de mídia contra o plano:
o `media_id` existe em `plan["media"]` com `available: true`; o `dest` fica em
`assets/…`, sem trecho vazio, `.` ou `..`, e termina na extensão `ext` da mídia
(sem diferenciar maiúsculas); dois pedidos nunca vão para o mesmo `dest`.
Passando tudo, o core grava os `files` e põe cada mídia numa pasta nova
`exports/<nome>/NNN/` (clipe já congelado pelo `deliver`, por hardlink; ainda
gravável, por clone ou cópia, como a mídia da pessoa; acerto de resolvedor,
sempre por cópia), e nunca apaga nem sobrescreve uma
pasta de export. Com `--dry-run`, o exportador roda, o resultado é conferido e
nada é gravado.

### Evolução do plano de export

O plano muda de versão em versão do Get B-rolls. Estas regras dizem o que um
exportador pode esperar:

- **Chave nova não quebra.** O exportador ignora chave desconhecida em qualquer
  nível do plano. Todo objeto do schema publicado fica aberto (topo, `meta`,
  `fps`, `canvas`, cena, `layout`, vaga, palavra de `words_timed`, camada,
  extensão e entrada de `media`); não feche esses objetos nos testes do seu
  plugin.
- **Valor de enum desconhecido é "não suportado".** Um aspecto, `timing`,
  layout, papel de vaga, tipo de camada ou tipo de mídia que o exportador não
  conhece vira um `PluginError` claro, dizendo o valor e que esta versão do
  plugin não o trata, nunca uma saída pela metade. A exceção é `problem`: valor
  desconhecido vale como mídia indisponível, como já é hoje.
- **`export_version` conferido.** O exportador recusa `export_version` que não
  conhece, com `PluginError` (como no exemplo acima): é o que protege o seu
  código de um plano com mudança que quebra.
- **Mudança aditiva.** Campo novo opcional ou `null` e valor novo de enum não
  sobem `export_version` e aparecem no CHANGELOG. Depois da 2.6.0, campo novo
  entra no schema publicado fora de `required`, para um `getbrolls-plan.json`
  gravado por uma versão anterior continuar válido; os campos da 2.6.0 são a
  base e ficam obrigatórios.
- **Mudança que quebra.** Remover, renomear ou mudar o significado de um campo
  sobe `export_version`.
- **Duas versões.** `export_version` é o contrato entre o core e o exportador: é
  ele que diz se o seu código entende o plano. `plan_version` é o formato do
  plano de cena do roteiro (`roteiro --action plan`), de onde vêm as cenas; ele
  pode subir sem mudar nada para o exportador. No schema publicado,
  `plan_version` só exige `>= 2` — o exportador não deve recusar por esse
  valor.
- **`$id` por versão.** O `$id` do schema aponta para a tag da versão
  (`.../get-brolls/v2.6.0/schemas/export_plan.schema.json`), não para a `main`.

Nenhum schema é aplicado em tempo de execução. Os testes do core conferem a
saída com uma variante fechada do schema, que recusa chave fora dele e exige
todos os campos: campo novo só entra no plano junto com o schema, o CHANGELOG e
o plano de exemplo
[`examples/plans/reels.plan.json`](../examples/plans/reels.plan.json).

## Resolvedores

> **Experimental.** Resolvedores rodam só dentro do `gb export`, para os `SFX` e
> as `MUSICA` do roteiro que as pastas do projeto e a biblioteca pessoal não
> têm. `assets --action list/where` não os consultam.

```python
from pathlib import Path

from getbrolls.sdk import ResolverHit

pasta = Path("~/Sons").expanduser()  # dentro de permissions.paths


def acha(kind: str, name: str) -> ResolverHit | None:
    path = pasta / f"{name}.wav"
    return ResolverHit(path, license="CC BY 4.0 — Acervo Sonoro") if path.is_file() else None


api.resolver("meu_banco", acha, ["sfx", "musica"])
```

Um resolvedor acha um arquivo de som (`"sfx"`) ou de música (`"musica"`) pelo
nome, dentro das pastas de `permissions.paths`. Nunca vale para a gravação
(`aroll`) nem para marca. O core só pergunta aos resolvedores depois de as pastas
do projeto e da pessoa não acharem nada.

- **Ordem.** Pelo id do plugin dono e, dentro dele, pela ordem de registro. O
  primeiro acerto válido vence; um resolvedor que falha ou devolve algo inválido
  vira um aviso `Plugin <id>: …` e o próximo é consultado.
- **Raízes.** As mesmas de `api.local_file`: as pastas de `permissions.paths`
  que valem no sistema atual, conferidas quando o plugin carrega; uma raiz ampla
  demais fica ignorada. "Dentro da raiz" é conferido pela pasta de verdade, não
  pelo texto: num disco que não diferencia maiúsculas, `.../SONS/porta.wav` fica
  dentro da raiz `.../Sons`.
- **O que o core confere.** `ResolverHit` exato, com caminho absoluto; o caminho
  não é link simbólico nem junction; resolvido, fica dentro de uma raiz; é um
  arquivo regular com um só nome no disco (hardlink é recusado, ao contrário de
  `api.local_file`); no macOS e no Linux, a abertura desce de `/` até a raiz —
  pelo caminho gravado no pin — e da raiz até o arquivo, um nome por vez, sem
  seguir link, como em `api.local_file`: trocar a própria raiz, uma pasta acima
  dela ou uma pasta do caminho por um link, antes ou durante a abertura, não
  entrega mais um arquivo de fora dela (no Windows vale a conferência antiga,
  com a mesma janela de `api.local_file`); tem uma extensão aceita para o tipo,
  comparada sem diferenciar maiúsculas (`PORTA.WAV` vale como `.wav`); e tem até
  512 MB.
- **Sempre cópia.** O arquivo é da pessoa: o core nunca faz hardlink nem muda a
  permissão dele. Na hora de copiar, o core abre de novo, confere que dispositivo,
  inode e tamanho são os mesmos do acerto e que o caminho segue dentro da raiz, e
  copia desse descritor.
- **Loja.** O acerto fica registrado com `store` igual ao id do plugin; o plugin
  não escolhe esse valor. No plano de export ele vira a mídia
  `plugin:<id>:<tipo>:<nome>`, e cada par (tipo, nome) é perguntado uma vez só
  por export.
- **Licença informativa.** `license` é texto de até 500 caracteres. No plano de
  export ela vira o `credit` da mídia, numa linha só, crua e com o prefixo
  "Licença informada pelo plugin <id>:"; quem escapa a marcação é o exportador.
  Ela nunca vale como `permit` e nunca entra em `rights.evidence`.

## Instalar e atualizar

```sh
python3 scripts/gb.py plugins --action install --source <pasta-ou-url-git>
python3 scripts/gb.py plugins --action install --source <pasta-ou-url-git> --yes --expect <sha256>
python3 scripts/gb.py plugins --action update --id <id>
python3 scripts/gb.py plugins --action update --id <id> --yes --expect <sha256>
```

- `--source` aceita uma pasta local (copiada sem `.git`/`__pycache__`), uma
  pasta que é repositório git ou uma URL git (`https://…` sem usuário/senha, ou
  `git@host:caminho`). Um repositório nunca é `checkout`ado: o clone usa
  `--no-checkout` e o conteúdo é materializado por nós, um blob por vez, direto
  de `git ls-tree`/`git cat-file blob` — comandos que nunca aplicam filtro
  `clean`/`smudge` nem hook, ao contrário de um `checkout` de verdade — com
  `GIT_TERMINAL_PROMPT=0`; só o que está commitado entra. O clone fica numa
  pasta de staging própria e a árvore é escrita em outra, que nunca tem `.git`.
  É recusado: link simbólico, submódulo (gitlink), qualquer caminho com um
  componente de controle de versão (`.git`, `.hg`, `.svn`, também com ponto ou
  espaço sobrando e os nomes curtos `GIT~1`/`HG~1`/`SVN~1`), `:` ou `\` em
  qualquer componente, colisão de maiúsculas/minúsculas entre dois caminhos,
  mais de 2000 arquivos e mais de 200 MB (no total ou num arquivo só).
- Uma pasta local só é tratada como repositório git quando `.git` é uma pasta
  de verdade (um arquivo `.git` de worktree/submódulo ou um link apontariam
  para outro repositório). Pasta local comum é copiada sem `.git`/`.hg`/`.svn`
  de topo, `__pycache__`/`.pyc` e lixo de SO; uma pasta de controle de versão
  aninhada é recusada.
- No Windows, `https://` usa a configuração de TLS do Git para Windows que está
  no config de **sistema**, que o install ignora de propósito
  (`GIT_CONFIG_NOSYSTEM=1`, para nenhum config de fora redirecionar o clone). Se
  o clone `https://` falhar por certificado, defina `GIT_SSL_CAINFO` (e, se
  preciso, `GIT_SSL_CAPATH`) apontando para o bundle de CAs — essas duas
  variáveis são repassadas ao git; ou instale a partir de uma pasta local.
- Sem `--yes`, nada fica instalado: a resposta mostra id, versão, permissões
  (`network`, `env`, `paths`), contribuições, origem, commit, a lista de
  arquivos (`files`: total e até 50 nomes) e o `sha256` do conteúdo já
  materializado, para revisão.
- `--yes` sozinho não basta: precisa vir junto com `--expect <sha256>`, igual
  ao `sha256` que a prévia (sem `--yes`) mostrou — confirma que a pessoa está
  aprovando o mesmo conteúdo que viu, não um que mudou na origem entre a
  prévia e o `--yes`. Sem `--expect`, ou com um valor que não bate com o
  `sha256` atual, o comando é recusado e pede para rodar a prévia de novo (sem
  `--yes`) e reapresentar o valor atualizado. Batendo, `install` move a pasta
  para `plugins/<id>`, habilita com pin de hash e grava origem/commit em
  `plugins.json` (`sources`); `update --id` troca a pasta e refaz o pin.
- `update --id` exige a origem gravada pelo `install`; sem `--yes` mostra a
  diferença de versão, de permissões e de arquivos (adicionados, removidos,
  alterados) contra a origem gravada. Nenhum código do plugin roda durante
  install/update — só o manifesto é lido.

## Opt-in e confiança

- Plugins só são descobertos dentro de `$GB_HOME/plugins/<id>/` (por padrão,
  `~/.getbrolls/plugins/`). Nenhum outro caminho é varrido.
- Habilitar é sempre em dois passos: `plugins --action enable --id <id>` sem
  `--yes` só mostra o manifesto e as permissões declaradas, para revisão
  humana; `--yes` de fato habilita.
- `enable` × `install`/`update`: o `install` já habilita (com pin) o que
  trouxe da origem; para trazer uma versão nova de um plugin instalado assim,
  use `update --id`, que compara com a origem gravada. O `enable` é para
  plugin copiado à mão para `plugins/` e para religar um plugin que você
  desligou com `disable`.
- **Re-enable de plugin `suspended`.** Quando o conteúdo mudou desde o pin, a
  prévia do `enable` (sem `--yes`) traz o `diff`: versão, permissões
  (`permissions.from`/`to`, com `network`, `env` e `paths`, como no `update`) e
  arquivos adicionados, removidos e alterados, comparados com o que o pin
  guarda em `plugins.json` (`enabled.<id>.permissions` e `.files`). Confirmar exige
  `--yes --expect <sha256>`, o mesmo valor da prévia — `--yes` sozinho é
  recusado, como no install/update. Um `plugins.json` de antes desta versão
  (pin sem `files`) continua válido; a prévia só avisa que o diff é
  desconhecido e pede para conferir a pasta. O primeiro `enable` de um plugin
  nunca pinado segue só com `--yes`.
- O mesmo vale depois de um `disable`: ele tira o plugin de `enabled` mas
  guarda o último pin em `plugins.json` (`last_pins.<id>`). Religar com a pasta
  igual é só `--yes`; com a pasta mudada, a prévia traz o `diff` e confirmar
  exige `--expect`. Um `plugins.json` sem `last_pins` continua válido.
- O mapa por arquivo tem o mesmo teto do `install` (2000 arquivos): acima
  disso o pin guarda só o sha256 total (`files_omitted`) e a prévia diz
  "muitos arquivos, diff omitido" — o `--expect` continua obrigatório.
- `--yes` grava um **pin de hash**: um sha256 sobre todo arquivo da pasta do
  plugin, exceto lixo de SO (`.DS_Store`, `Thumbs.db`, `desktop.ini`) e a pasta
  `.git` **de topo** — só essas ficam fora da conta, e por isso o código do
  plugin **não pode ler nem executar** nada com esses nomes nem de dentro do
  `.git` de topo. O `install` nunca grava esses nomes em `plugins/`. Qualquer
  outra mudança no conteúdo da pasta — mesmo um arquivo que não é código,
  `.hg`/`.svn` de topo inclusos — deixa o plugin `suspended` até um novo
  `enable`. Uma pasta de controle de versão aninhada (`vendor/.hg`,
  `sub/.svn`, `sub/.git`), um link simbólico ou bytecode Python
  (`__pycache__/`, `*.pyc`, `*.pyo`) em qualquer lugar da pasta deixam o
  plugin `invalid` (nunca carrega): seria conteúdo fora do hash ou código
  diferente da fonte revisada. O `install`/`update` recusa os mesmos casos,
  vindos de uma pasta ou de um repositório git.
- O hash é lido em pedaços, com teto de 200 MB por arquivo e 400 MB no total:
  passou disso (um cache largado ao lado do `__file__`, por exemplo), o plugin
  fica `suspended` com o motivo, e o `enable` recusa. Guarde estado em
  `api.data_dir`.
- O arquivo de entrada é sempre executado a partir da fonte (`.py`), e o core
  desliga a escrita de bytecode antes de carregar plugins. Bytecode na pasta
  (`__pycache__`/`.pyc`/`.pyo`) nunca é lido para rodar o plugin porque a pasta
  que o tiver fica `invalid` — apague-o e habilite de novo.
- `GB_PLUGINS=id1,id2` só **filtra**: escolhe, entre os plugins já
  habilitados com pin válido em `plugins.json`, os que esta sessão carrega.
  Nunca carrega um plugin sem pin, nunca habilitado ou com conteúdo mudado
  (esse continua `suspended`); um id da lista que nunca foi habilitado sai
  `disabled` com o motivo pedindo o `enable`. `GB_PLUGINS=off`
  desliga todos os plugins, mesmo os habilitados em `plugins.json`. Com a
  variável no ambiente, `plugins --action list` traz `"selection":
  "GB_PLUGINS"` e cada plugin fora dela sai `disabled` com o motivo
  "desligado por GB_PLUGINS"; a busca por uma fonte dele manda ajustar
  `GB_PLUGINS`, porque `enable` não muda essa seleção.
- `doctor` mostra o resultado real do carregamento em `plugins[]` e, quando
  algum plugin está `failed`/`suspended`/`invalid`/`incompatible`, uma linha
  `plugins` no `summary`. `doctor --live` também busca (limite 1) em cada
  fonte de plugin habilitada: fonte só-metadados (a busca não traz
  `media_url`) sai `search_ok` com `refresh: "no_media_url (fonte
  só-metadados)"`; fonte com rota sai `refresh: "route"` (quem traz o arquivo
  é a rota, e o refresh nem é chamado); um `PluginError` ("Configure
  PASTA_LOCAL_DIR…") aparece saneado no `detail`.
- Um plugin que falha ao carregar (manifesto inválido, exceção em
  `register()`, hash divergente) fica marcado como `failed`/`suspended` e o
  resto do Get B-rolls — built-ins inclusive — continua funcionando normalmente.
- Toda chamada ao código do plugin (`register`, `search`/`resolve`/`refresh`,
  `Route.prepare`, handler de comando, o `plugins check`) passa pelo mesmo
  isolamento: qualquer exceção — inclusive `SystemExit`, `GeneratorExit`,
  `asyncio.CancelledError` e uma classe que herde `BaseException` — vira erro
  do plugin com só o **tipo** (veja [`PluginError`](#mensagens-de-erro-pluginerror)),
  sem cadeia até a exceção original: o texto dela nunca chega ao traceback de
  `diagnostics.jsonl`, ao `getbrolls.log` nem ao motivo em `doctor`. Só o
  `KeyboardInterrupt` de verdade passa (uma subclasse dele levantada pelo
  plugin é falha do plugin). Falhar ao montar o registro de plugins nunca
  derruba `providers`/`doctor`/`rules`/`search` dos built-ins. Tudo o que o
  código do plugin escreve em `sys.stdout` (um `print`) vai para o stderr: o
  stdout da CLI é só o JSON.
- **Não há tempo limite** nas chamadas ao plugin: um `search` ou
  `Route.prepare` que trava segura o comando (e a trava do projeto) até ser
  interrompido com Ctrl+C. Código em processo não tem como ser cortado com
  segurança; isso fica para um modelo de subprocesso futuro.
- Antes de rodar qualquer código de plugin, o core liga
  `sys.dont_write_bytecode`: um plugin com módulo irmão (`sys.path` + `import`)
  não grava `__pycache__` na própria pasta — o que mudaria o hash e o
  suspenderia depois do primeiro uso.
- **O SDK não é uma caixa de areia.** Um plugin habilitado roda com as mesmas
  permissões do processo que executa a CLI. `permissions.network` e
  `permissions.env` limitam o que `api.get_json`/`api.env` aceitam, mas não
  impedem código arbitrário no arquivo de entrada de fazer outra coisa. Só
  habilite plugins cujo código você leu e em que confia.

## Testar seu plugin

Comece pelo scaffold, que já traz manifesto, `plugin.py`, `README.md` e um
teste que passa:

```sh
python3 scripts/gb.py plugins --action new --id meu_banco --kind route --path <pasta>
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<pasta da skill>/scripts python3 -m unittest discover -s <pasta>/meu_banco/tests
```

No PowerShell, defina as variáveis antes do comando (as duas cabem numa linha, separadas por `;`). Um valor em `$env:` vale até fechar aquela sessão do PowerShell:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"; $env:PYTHONPATH = "<pasta da skill>\scripts"
python -m unittest discover -s <pasta>\meu_banco\tests
```

`--kind` é `provider` (fonte), `route` (fonte + rota de `fetch` com token) ou
`command`. Sem `PYTHONDONTWRITEBYTECODE=1`, o `__pycache__` que o teste cria
muda o hash do plugin — o pin cobre todo arquivo da pasta.

`getbrolls.sdk.testing` traz as checagens de contrato: `check_provider`,
`check_route`, `check_command`, `check_exporter`, `check_resolver` (cada uma
levanta `AssertionError` com o que corrigir) e `check_plugin(pasta)`, que roda
tudo. Antes de instalar de
verdade, valide o manifesto e rode `register()` contra um registro descartável
(com os built-ins, para pegar colisão de nome), sem habilitar nada — o `check`
roda as mesmas checagens de contrato e lista o que conferiu em `contracts`:

```sh
python3 scripts/gb.py plugins --action check --path <pasta-do-plugin>
```

Depois de instalado em `$GB_HOME/plugins/<id>/`, use
`plugins --action list` para ver o status (`disabled`, `enabled`, `suspended`,
`invalid`, `incompatible`) e `plugins --action disable --id <id>` para desligar.
`list` nunca executa código do plugin — o status ali é só manifesto + pin de
hash (pré-carga), e o comando devolve uma `note` dizendo isso. Se o
`register()` do plugin estourar uma exceção, `list` continua mostrando
`enabled`; rode `doctor` para o resultado real do carregamento — só ele mostra
`failed`, com o motivo. Pedir busca numa fonte de um plugin instalado mas não
carregado (`search --provider <nome>`) nomeia o plugin e o status atual na
mensagem de erro, em vez de dizer só "fonte desconhecida".

## Evolução do schema

Os schemas de candidato e de brief (`schemas/*.schema.json`) já reservam um
campo `ext` para dados de extensão — reservado para versões futuras e ignorado
pelo runtime atual. Os schemas documentam o formato; o runtime não os aplica e é
permissivo: no `BRIEF.md`, uma chave desconhecida (inclusive `ext`) é aceita e
ignorada, e num candidato do `manifest.json` uma chave fora do schema não é
recusada. Candidato que vem de plugin é diferente: o guarda-corpo descarta todo
campo fora do allowlist, `ext` incluso (veja [Guarda-corpos](#guarda-corpos)).
Também nas próximas versões: suporte aos demais tipos de `contributes`
(`rules`, `hooks`, `themes`, `brief_templates`, `eval_rubrics`, `capturers`,
`engines`, `catalogs`, `roteiro_templates`) e
um caminho para promover um campo nascido em `ext.<id>` de um plugin para o
schema do core, quando fizer sentido para todo mundo. Nada disso está
disponível nesta versão.

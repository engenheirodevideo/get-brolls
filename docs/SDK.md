---
type: documentation
status: current
created: 2026-09-23
updated: 2026-09-24
tags: [get-brolls, sdk, plugins]
---

# SDK de extensões — Get B-rolls

O Get B-rolls aceita extensões locais em Python: **plugins** instalados numa
pasta pessoal, com opt-in explícito, que contribuem fontes de busca
(`providers`), rotas que trazem o arquivo (`routes`), comandos próprios
(`commands`) e presets de licença. Este documento é a referência do SDK; para
exemplos completos e funcionais, veja
[`examples/plugins/pasta_local`](../examples/plugins/pasta_local/README.md)
(acervo local, rota de prévia e comando) e
[`examples/plugins/banco_http`](../examples/plugins/banco_http/README.md)
(API autenticada, rota de `fetch`).

O princípio: **o plugin traz o arquivo; o core decide o resto.** Aprovação,
permit, hash, corte, ledger e entrega continuam só do core.

## O que é um plugin

Um plugin é uma pasta com um manifesto (`getbrolls-plugin.json`) e um arquivo
de entrada em Python que define `register(api)`. Ele roda **como código seu**,
com as mesmas permissões de quem executa a CLI — não é uma extensão isolada
nem carregada de um repositório remoto. Você escreve o plugin, revisa o
manifesto e decide habilitá-lo.

Nesta versão do SDK, um plugin pode contribuir quatro tipos de extensão:

- **`providers`** — uma fonte de busca nova (`search`/`resolve`/`refresh`).
- **`routes`** — como o arquivo de um candidato chega (`prepare(item, workdir)`), para quem precisa de token, URL assinada ou pasta local.
- **`commands`** — rotinas próprias, rodadas com `gb x <plugin> <comando>` (ex.: sincronizar acervo, ver cota).
- **`presets`** — um preset de texto de licença, reaproveitado por `permit`.

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
| `id` | string | sim | 2–32 caracteres `a-z0-9_`, começando por letra; não pode ser `core`. Igual ao nome da pasta. |
| `name` | string | sim | Nome de exibição, até 80 caracteres. |
| `description` | string | não | Até 500 caracteres. |
| `version` | string | sim | `X.Y.Z` do próprio plugin. |
| `sdk_api` | inteiro | sim | Versão do contrato do SDK que o plugin fala; hoje `1`. Diferente da versão instalada do Get B-rolls, o plugin é recusado. |
| `requires_getbrolls` | string | sim | Faixa de compatibilidade, ex.: `">=2.5,<3"`. |
| `entry` | string | sim | Nome do arquivo `.py` de entrada, na raiz da pasta do plugin. |
| `contributes` | objeto | sim | Listas por tipo de contribuição — veja abaixo. |
| `permissions` | objeto | sim | `network` (hosts liberados para `get_json`/`download`), `env` (variáveis liberadas para `env`) e `paths` (pastas liberadas para `local_file`). |

`contributes` aceita as chaves `providers`, `presets`, `routes`, `commands`,
`exporters`, `rules`, `hooks`, `themes`, `brief_templates` e `eval_rubrics`,
mas **só `providers`, `presets`, `routes` e `commands` são suportados nesta
versão do SDK** — declarar qualquer nome nas outras chaves faz o manifesto ser
recusado. Nomes de provider, preset e rota têm que ser iguais ao `id` do plugin
ou começar por `<id>_`; nomes de comando só seguem a regra de nome
(`a-z0-9_`), porque `gb x <plugin> <comando>` já dá o espaço de nomes. Tudo o
que é declarado precisa ser efetivamente registrado em `register(api)`
(conferido por `api.finish()`).

`permissions.paths` lista raízes absolutas ou começando por `~/` (sem `..`),
ex.: `["~/Movies", "/Volumes/NAS/brolls"]`. Só arquivos dentro delas passam por
`api.local_file`.

Os campos `schema` e `signed_fields` existem no formato do manifesto para
versões futuras do SDK; nesta versão eles têm que ficar ausentes ou vazios.

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

`ProviderCapabilities`, campo a campo:

| Campo | Padrão | Significado |
|---|---|---|
| `search` | `False` | A fonte implementa `search()`. |
| `resolve_url` | `False` | A fonte implementa `resolve()` para URLs próprias. |
| `media_kinds` | `("video",)` | Tipos de mídia que a fonte devolve: `video`, `image`, ou os dois. |
| `match_kind` | `"literal"` | Como o core rotula a correspondência: `literal` (entidade nomeada) ou `illustrative` (ideia genérica). |
| `env_key` | `None` | Nome da variável de ambiente cuja presença indica "fonte configurada" (informativo; a leitura real passa por `api.env`). |
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
  `preview --candidate ID --start ... --end ... --reference-only`.
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
Uma rota `stage="preview"` que já deixou a mídia de trabalho pronta não roda de
novo no `fetch`, então a licença dela não é registrada.

## PluginApi

`register(api)` recebe a única porta de entrada no registro:

- `api.provider(provider)` — registra um `Provider`; o `name` tem que estar em `contributes.providers`.
- `api.preset(name, url, text)` — registra um preset de licença; `text` tem que terminar em `"verifique a página da fonte: {url}"`.
- `api.candidate(provider, source_id, title, source_url=None)` — monta um candidato vazio, no formato que o core espera; use isto em vez de montar o dicionário à mão.
- `api.env(key)` — lê uma variável de ambiente; `key` tem que estar em `permissions.env` do manifesto, senão levanta erro.
- `api.get_json(url, params=None, headers=None, cache_ttl=0)` — faz uma requisição HTTP GET com o mesmo transporte validado do core (resolução de IP, HTTPS, sem redirect); o host de `url` tem que estar em `permissions.network`. A resposta passa pela mesma limpeza do core: URL assinada dentro do JSON some.
- `api.route(route)` — registra uma `Route`; o `name` tem que estar em `contributes.routes`.
- `api.command(name, handler, help)` — registra um comando; `name` tem que estar em `contributes.commands`, `help` é a frase que `x --list` mostra.
- `api.download(url, name, headers=None)` — só dentro de `Route.prepare`: baixa `url` (https, host em `permissions.network`, IP público, sem redirect, teto de 512 MB) para `workdir/name` e devolve o caminho. Aceita URL assinada (ex.: um link S3 que o próprio plugin assinou) e headers como `Authorization`; nenhum dos dois vai para log ou mensagem de erro. `name` é só nome de arquivo (`[A-Za-z0-9._-]`, sem `/` nem `..`).
- `api.local_file(path)` — só dentro de `Route.prepare`: copia um arquivo que esteja dentro de `permissions.paths` (resolvido, com link simbólico seguido) para o `workdir`. Sempre cópia, nunca hardlink — o original da pessoa não muda.
- `api.data_dir` — `$GB_HOME/plugin-data/<id>/` (0700), criado na primeira leitura: estado e cache do plugin. Fica fora da pasta do plugin, então escrever ali não muda o pin de hash.
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
`plugin_candidate_sanitized`, com os nomes dos campos removidos. `media_url` e
`source_url` sempre passam pelo mesmo `public_url()` que valida URLs do core.

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
  cada variável declarada em `permissions.env` vira `[REDACTED]` e o texto é
  cortado em 300 caracteres.
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
`plugin_loaded`, `plugin_skipped`, `plugin_failed`, `plugin_enabled`,
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
- `gb x <plugin> <comando> [--project P] [--arg chave=valor]...` carrega os
  plugins, chama `handler(args, ctx)` e imprime `{"plugin", "command",
  "result"}`. `args` é um dicionário de texto; chave repetida é erro.
- `ctx.plugin_id`, `ctx.project` (ou `None`), `ctx.candidates()` (cópias dos
  candidatos do projeto) e `ctx.brief()` (o JSON do BRIEF.md, ou `None`).
  Comando **só lê** o projeto: não há como gravar no ledger, e `x` não toma a
  trava exclusiva nem cria `brolls/`.
- O retorno tem que ser um objeto JSON (dict). Exceção no handler vira erro
  `Plugin <id>: o comando <nome> falhou (<tipo>)`, exit 2; com `PluginError`,
  a mensagem é `Plugin <id>: <texto>` (veja
  [Mensagens de erro](#mensagens-de-erro-pluginerror)).

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
  `GIT_TERMINAL_PROMPT=0`; só o que está commitado entra. É recusado: link
  simbólico, submódulo (gitlink), qualquer caminho com um componente
  equivalente a `.git`, colisão de maiúsculas/minúsculas entre dois caminhos,
  mais de 2000 arquivos e mais de 200 MB (no total ou num arquivo só).
- Sem `--yes`, nada fica instalado: a resposta mostra id, versão, permissões
  (`network`, `env`, `paths`), contribuições, origem, commit e o `sha256` do
  conteúdo já materializado, para revisão.
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
- `--yes` grava um **pin de hash**: um sha256 sobre todo arquivo da pasta do
  plugin, exceto lixo de SO (`.DS_Store`, `Thumbs.db`, `desktop.ini`) e o
  conteúdo de uma pasta de VCS (`.git`, `.hg`, `.svn`) — esses nunca entram na
  conta. Qualquer outra mudança no conteúdo da pasta — mesmo um arquivo que
  não é código, `__pycache__` incluso — deixa o plugin `suspended` até um novo
  `enable`.
- O arquivo de entrada é sempre executado a partir da fonte (`.py`); bytecode
  (`.pyc`/`__pycache__`) nunca é lido para rodar o plugin, mas continua
  contando no hash como qualquer outro arquivo (só o lixo de SO e o VCS
  acima ficam de fora).
- `GB_PLUGINS=id1,id2` seleciona plugins habilitados sem depender do pin de
  hash — pensado para CI/testes, não para uso diário. `GB_PLUGINS=off`
  desliga todos os plugins, mesmo os habilitados em `plugins.json`.
- Um plugin que falha ao carregar (manifesto inválido, exceção em
  `register()`, hash divergente) fica marcado como `failed`/`suspended` e o
  resto do Get B-rolls — built-ins inclusive — continua funcionando normalmente.
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

`--kind` é `provider` (fonte), `route` (fonte + rota de `fetch` com token) ou
`command`. Sem `PYTHONDONTWRITEBYTECODE=1`, o `__pycache__` que o teste cria
muda o hash do plugin — o pin cobre todo arquivo da pasta.

`getbrolls.sdk.testing` traz as checagens de contrato: `check_provider`,
`check_route`, `check_command` (cada uma levanta `AssertionError` com o que
corrigir) e `check_plugin(pasta)`, que roda tudo. Antes de instalar de
verdade, valide o manifesto e rode `register()` contra um registro descartável
(com os built-ins, para pegar colisão de nome), sem habilitar nada — o `check`
roda as mesmas checagens de contrato e lista o que conferiu em `contracts`:

```sh
python3 scripts/gb.py plugins --action check --path <pasta-do-plugin>
```

Depois de instalado em `$GB_HOME/plugins/<id>/`, use
`plugins --action list` para ver o status (`disabled`, `enabled`, `suspended`,
`incompatible`) e `plugins --action disable --id <id>` para desligar.
`list` nunca executa código do plugin — o status ali é só manifesto + pin de
hash (pré-carga), e o comando devolve uma `note` dizendo isso. Se o
`register()` do plugin estourar uma exceção, `list` continua mostrando
`enabled`; rode `doctor` para o resultado real do carregamento (`failed` com o
motivo). Pedir busca numa fonte de um plugin instalado mas não carregado
(`search --provider <nome>`) nomeia o plugin e o status atual na mensagem de
erro, em vez de dizer só "fonte desconhecida".

## Evolução do schema

Os schemas de candidato e de brief já reservam um campo `ext` para dados de
extensão — pensado para as próximas versões do SDK, não usado por este ainda.
Também nas próximas versões: suporte aos demais tipos de `contributes`
(`exporters`, `rules`, `hooks`, `themes`, `brief_templates`, `eval_rubrics`) e
um caminho para promover um campo nascido em `ext.<id>` de um plugin para o
schema do core, quando fizer sentido para todo mundo. Nada disso está
disponível nesta versão.

---
type: documentation
status: current
created: 2026-09-23
updated: 2026-09-23
tags: [get-brolls, sdk, plugins]
---

# SDK de extensões — Get B-rolls

O Get B-rolls aceita extensões locais em Python: **plugins** instalados numa
pasta pessoal, com opt-in explícito, que contribuem fontes de busca
(`providers`) e presets de licença. Este documento é a referência do SDK; para
um exemplo completo e funcional, veja
[`examples/plugins/pasta_local`](../examples/plugins/pasta_local/README.md).

## O que é um plugin

Um plugin é uma pasta com um manifesto (`getbrolls-plugin.json`) e um arquivo
de entrada em Python que define `register(api)`. Ele roda **como código seu**,
com as mesmas permissões de quem executa a CLI — não é uma extensão isolada
nem carregada de um repositório remoto. Você escreve o plugin, revisa o
manifesto e decide habilitá-lo.

Nesta versão do SDK, um plugin pode contribuir dois tipos de extensão:

- **`providers`** — uma fonte de busca nova (`search`/`resolve`/`refresh`).
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
| `permissions` | objeto | sim | `network` (hosts liberados para `get_json`) e `env` (variáveis liberadas para `env`). |

`contributes` aceita as chaves `providers`, `presets`, `exporters`, `rules`,
`hooks`, `themes`, `brief_templates` e `eval_rubrics`, mas **só `providers` e
`presets` são suportados nesta versão do SDK** — declarar qualquer nome nas
outras chaves faz o manifesto ser recusado. Cada nome declarado tem que ser
igual ao `id` do plugin ou começar por `<id>_`, e precisa ser efetivamente
registrado em `register(api)` (conferido por `api.finish()`).

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
| `download` | `True` | Se o core pode baixar o arquivo (`False` para fontes só-metadados, como o exemplo `pasta_local`). |

`search(query, limit, media)` devolve uma lista (ou gerador) de candidatos
construídos com `api.candidate(...)`. `resolve(url)` devolve um candidato para
uma URL que a fonte reconhece, ou `None`. `refresh(item)` recebe o candidato
atual e devolve uma versão com `media_url` renovado (útil para links que
expiram).

## PluginApi

`register(api)` recebe a única porta de entrada no registro:

- `api.provider(provider)` — registra um `Provider`; o `name` tem que estar em `contributes.providers`.
- `api.preset(name, url, text)` — registra um preset de licença; `text` tem que terminar em `"verifique a página da fonte: {url}"`.
- `api.candidate(provider, source_id, title, source_url=None)` — monta um candidato vazio, no formato que o core espera; use isto em vez de montar o dicionário à mão.
- `api.env(key)` — lê uma variável de ambiente; `key` tem que estar em `permissions.env` do manifesto, senão levanta erro.
- `api.get_json(url, params=None, headers=None, cache_ttl=0)` — faz uma requisição HTTP GET com o mesmo transporte validado do core (resolução de IP, HTTPS, sem redirect); o host de `url` tem que estar em `permissions.network`.
- `api.finish()` — chamado automaticamente pelo loader depois de `register()`; confere se tudo declarado em `contributes` foi mesmo registrado.

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
- `acquisition`: `status` e `method`, restritos a valores conhecidos (`available`/`unavailable`, `https`/`yt-dlp`/`None`); `evidence`.

Qualquer campo de topo ou subcampo fora dessas listas é descartado em
silêncio (do ponto de vista do retorno da CLI) e registrado no log estruturado
`plugin_candidate_sanitized`, com os nomes dos campos removidos. `media_url` e
`source_url` sempre passam pelo mesmo `public_url()` que valida URLs do core.

Uma exceção levantada dentro de `search`, `resolve` ou `refresh` nunca derruba
a CLI: ela vira `ProviderError` com a mensagem `Plugin <id>: ...`, e a busca
continua com as outras fontes. `refresh` só pode mudar `media_url` — qualquer
outra diferença entre o candidato atual e o que o plugin devolveu é ignorada.

Eventos do log estruturado relacionados a plugins:
`plugin_loaded`, `plugin_skipped`, `plugin_failed`, `plugin_enabled`,
`plugin_disabled`, `plugin_request_refused`, `plugin_call_failed`,
`plugin_candidate_sanitized`.

## Opt-in e confiança

- Plugins só são descobertos dentro de `$GB_HOME/plugins/<id>/` (por padrão,
  `~/.getbrolls/plugins/`). Nenhum outro caminho é varrido.
- Habilitar é sempre em dois passos: `plugins --action enable --id <id>` sem
  `--yes` só mostra o manifesto e as permissões declaradas, para revisão
  humana; `--yes` de fato habilita.
- `--yes` grava um **pin de hash**: um sha256 sobre todo arquivo da pasta do
  plugin (inclusive `.DS_Store`, `__pycache__` ou `.git`, se existirem ali
  dentro). Qualquer mudança no conteúdo da pasta — mesmo um arquivo que não é
  código — deixa o plugin `suspended` até um novo `enable`.
- O arquivo de entrada é sempre executado a partir da fonte (`.py`); bytecode
  (`.pyc`/`__pycache__`) nunca é lido para rodar o plugin, só entra na conta
  do hash como qualquer outro arquivo.
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

Antes de instalar de verdade, valide o manifesto e rode `register()` contra
um registro descartável (com os built-ins, para pegar colisão de nome), sem
habilitar nada:

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

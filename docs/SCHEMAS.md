---
type: documentation
status: current
created: 2026-09-30
updated: 2026-09-30
tags: [getbrolls, schemas, contrato]
---

# Formatos de arquivo do getbrolls

Este documento lista todo arquivo que o getbrolls grava ou lê como contrato: onde ele
mora, como declara a versão, quem grava, quem lê e onde está o formato. É a
referência para quem escreve plugin, script ou ferramenta que lê um projeto. O
contrato de linha de comando (saída, códigos de saída e de erro) está em
[CLI_CONTRACT.md](CLI_CONTRACT.md).

Os testes do repositório (`tests/test_schemas_doc.py`) conferem que todo
`schemas/*.schema.json` está listado aqui e que os nomes reservados e os estados
citados batem com o código.

## Regra de versão

Cada família de arquivo declara a versão de um de dois jeitos:

- **Famílias novas** (da 2.6 em diante: projeto, análise, cliente, template,
  marketplace) gravam um campo só, autodescritivo:
  `"schema": "getbrolls.<família>/<N>"`, por exemplo `"getbrolls.project/1"`.
- **Famílias que já existiam** mantêm o campo próprio: `schema_version` (manifesto,
  candidatos, referências, fila, biblioteca, estado do roteiro, marcador do export,
  perfil), `version` (`BRIEF.md`, `RULES.md`), `export_version` e `plan_version`
  (plano de export).

A leitura é a mesma para os dois jeitos:

- campo **ausente vale 1** (arquivo gravado antes de o campo existir);
- versão **maior que a suportada é recusada sem tocar no arquivo**, com a frase
  "foi gravado por uma versão mais nova do get-brolls (…); atualize antes de
  continuar.";
- qualquer outro valor (texto, `true`, `1.0`, zero, negativo, outra família) é
  inválido;
- toda gravação nova declara a versão.

`NaN` e `Infinity` são recusados nas famílias novas; os arquivos de análise e o
índice de marketplace recusam também chave repetida. Os arquivos JSONL de log não
têm versão por linha.

### `$id` dos schemas

O `$id` de cada `schemas/*.schema.json` aponta para a tag da versão:
`https://raw.githubusercontent.com/engenheirodevideo/get-brolls/v<versão>/schemas/<arquivo>`.
O endereço-base mora numa constante só (`SCHEMA_ID_BASE`, em
`scripts/bump_version.py`), que o `bump_version.py` usa para reescrever todos os
`$id` a cada versão; quando o repositório mudar de nome, só essa constante muda.

## Famílias e campo de versão

| Família | Campo de versão | Valor atual | Schema |
|---|---|---|---|
| projeto | `schema` | `getbrolls.project/1` | [`project.schema.json`](../schemas/project.schema.json) |
| índice de análise | `schema` | `getbrolls.analysis_index/1` | [`analysis_index.schema.json`](../schemas/analysis_index.schema.json) |
| mídia analisada | `schema` | `getbrolls.media/1` | [`media.schema.json`](../schemas/media.schema.json) |
| transcrição | `schema` | `getbrolls.transcript/1` | [`transcript.schema.json`](../schemas/transcript.schema.json) |
| cenas | `schema` | `getbrolls.scenes/1` | [`scenes.schema.json`](../schemas/scenes.schema.json) |
| silêncio | `schema` | `getbrolls.silence/1` | [`silence.schema.json`](../schemas/silence.schema.json) |
| falantes | `schema` | `getbrolls.speakers/1` | [`speakers.schema.json`](../schemas/speakers.schema.json) |
| análise visual | `schema` | `getbrolls.visual/1` | [`visual.schema.json`](../schemas/visual.schema.json) |
| marcadores | `schema` | `getbrolls.markers/1` | [`markers.schema.json`](../schemas/markers.schema.json) |
| pasta de cliente | `schema` | `getbrolls.client/1` | [`client.schema.json`](../schemas/client.schema.json) |
| registro de clientes | `schema` | `getbrolls.clients/1` | [`clients.schema.json`](../schemas/clients.schema.json) |
| template | `schema` (e `version` = número da versão do template) | `getbrolls.template/1` | [`template.schema.json`](../schemas/template.schema.json) |
| lock do template | `schema` | `getbrolls.template_lock/1` | [`template_lock.schema.json`](../schemas/template_lock.schema.json) |
| índice de marketplace | `schema` | `getbrolls.marketplace_index/1` | [`marketplace_index.schema.json`](../schemas/marketplace_index.schema.json) |
| estado dos marketplaces | `schema` | `getbrolls.marketplaces/1` | validado em código |
| candidato | `schema_version` | `1` | [`candidate.schema.json`](../schemas/candidate.schema.json) |
| brief | `version` | `1` | [`brief.schema.json`](../schemas/brief.schema.json) |
| plano de export | `export_version` (e `plan_version` do plano de cena) | `1` | [`export_plan.schema.json`](../schemas/export_plan.schema.json) |
| manifesto, referências, fila, biblioteca, estado do roteiro, marcador do export, perfil e confiança | `schema_version` | `1` | validado em código |
| `RULES.md` | `version` (`schema_version` aceito como sinônimo) | `1` | validado em código |
| marcador do runtime | `schema` (inteiro) | `1` | validado em código |

`getbrolls.permissions_increase/1` não é um arquivo: é o domínio do sha256 que a
prévia de um `update` de marketplace mostra quando o plugin pede permissão nova.

## Mapa dos contratos

Caminhos relativos à pasta do projeto, salvo `$GB_HOME/…` (pasta pessoal, padrão
`~/.getbrolls`) e os arquivos de plugin, cliente e marketplace.

### Projeto

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `project.json` | `schema` | `init`, `migrate --action apply` | todos os comandos de projeto (`layout`) | [`project.schema.json`](../schemas/project.schema.json) |
| `brolls/manifest.json` | `schema_version` | comandos de coleta (`search`, `resolve`, `preview`, `approve`, `permit`, `fetch`, `verify`, `deliver`…) | `status`, `brief`, `review`, `export`, plugins (`ctx`) | validado em código; cada item segue [`candidate.schema.json`](../schemas/candidate.schema.json) |
| `brolls/candidates/<id>.json` | `schema_version` | os mesmos, a cada gravação do manifesto | quem lê um candidato só | [`candidate.schema.json`](../schemas/candidate.schema.json) |
| `brolls/references.json` | `schema_version` | `remember` | `references`, `learn --from-candidate` | validado em código |
| `brolls/roteiro-state.json` | `schema_version` | `roteiro --action sync` | `roteiro`, `status` | validado em código |
| `work/queue.json` | `schema_version` | `queue` | `queue --action status`, `status` | validado em código |
| `.getbrolls-sources/index.json` | sem campo | `preview`, `inspect`, `fetch` (cache privado de fontes) | os mesmos | validado em código; ilegível vira índice vazio |
| `BRIEF.md` (bloco JSON) | `version` | `init-brief`, `roteiro --action sync` | `brief`, `status`, `search --shot` | [`brief.schema.json`](../schemas/brief.schema.json) |
| `RULES.md` (bloco JSON) | `version` | `init-rules` | todo comando que aplica regras | validado em código; veja [RULES.md](RULES.md) |
| `ROTEIRO.md` (frontmatter) | sem campo; `type: roteiro` | `roteiro --action new`, `init --template` | `roteiro`, `status`, `export`, `template --action freeze` | validado em código; veja [`references/roteiro.md`](../references/roteiro.md) |
| `aroll/<nome>.transcript.json` | sem campo | a pessoa (por exemplo, o `transcribe` do HyperFrames) | `export` | palavras `[{"text", "start", "end"}]` em segundos |
| `assets/<tipo>/<nome>.licenca.json` | `schema_version` opcional | a pessoa | `assets`, `export`, `template` | validado em código |
| `template.lock.json` | `schema` | `init --template` | `status`, `export` | [`template_lock.schema.json`](../schemas/template_lock.schema.json) |

### Análise de mídia (`analysis/`)

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `analysis/index.json` | `schema` | `analysis --action register`, plugins com `project_write` | `analysis`, `export`, plugins | [`analysis_index.schema.json`](../schemas/analysis_index.schema.json) |
| `analysis/media/<media_id>/media.json` | `schema` | só o core (`analysis --action register`) | `analysis`, `export`, plugins | [`media.schema.json`](../schemas/media.schema.json) |
| `analysis/media/<media_id>/transcript.json` | `schema` | plugins com `project_write` | `export` (legenda), plugins | [`transcript.schema.json`](../schemas/transcript.schema.json) |
| `analysis/media/<media_id>/scenes.json` | `schema` | plugins com `project_write` | plugins | [`scenes.schema.json`](../schemas/scenes.schema.json) |
| `analysis/media/<media_id>/silence.json` | `schema` | plugins com `project_write` | plugins | [`silence.schema.json`](../schemas/silence.schema.json) |
| `analysis/media/<media_id>/speakers.json` | `schema` | plugins com `project_write` | plugins | [`speakers.schema.json`](../schemas/speakers.schema.json) |
| `analysis/media/<media_id>/visual.json` | `schema` | plugins com `project_write` | plugins | [`visual.schema.json`](../schemas/visual.schema.json) |
| `analysis/markers.json` | `schema` | plugins com `project_write` (cada um troca só os próprios marcadores) | plugins | [`markers.schema.json`](../schemas/markers.schema.json) |

Nesta versão o core grava só `index.json` e `media.json`; os formatos de
transcrição, cenas, silêncio, falantes, visual e marcadores estão publicados para
os plugins que os produzem. O leitor recusa link, chave repetida, `NaN`, chave com
cara de segredo, caminho absoluto e arquivo acima de 16 MiB.

### Export

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `exports/<exporter>/<NNN>/getbrolls-plan.json` | `export_version` | `export` (o plano exato que o exporter recebeu) | quem audita o export | [`export_plan.schema.json`](../schemas/export_plan.schema.json) |
| `exports/<exporter>/<NNN>/.getbrolls-export.json` | `schema_version` | `export` (marcador do core) | `export` (próxima pasta, varredura) | validado em código |
| `exports/<exporter>/LATEST` | texto | `export` | a pessoa | número da pasta mais nova |

### Instalação e pasta pessoal

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `getbrolls.toml` | `schema_version` | a pessoa | todo comando (perfil de workspace) | validado em código; veja o [MANUAL.md](MANUAL.md) |
| `$GB_HOME/trusted-profiles.json` | `schema_version` | `profile --action trust|untrust` | todo comando | validado em código |
| `$GB_HOME/library/index.json` | `schema_version` | `learn` e `search` (aprendizado automático de busca vazia) | `search`, `library` | validado em código |
| `$GB_HOME/clients.json` | `schema` | `client --action add|remove` | `client`, `assets`, `init`, `migrate`, `template` | [`clients.schema.json`](../schemas/clients.schema.json) |
| `<pasta do cliente>/client.json` | `schema` | `client --action add` | `client`, `assets`, `template` | [`client.schema.json`](../schemas/client.schema.json) |
| `<pasta do cliente>/templates/<slug>/<N>/template.json` | `schema` + `version` | `template --action freeze` | `template --action list|show`, `init --template` | [`template.schema.json`](../schemas/template.schema.json) |
| `<pasta do cliente>/templates/<slug>/<N>/template.sha256` | formato do `sha256sum` | `template --action freeze` | `template --action show`, `init --template` | uma linha: sha256 do `template.json` |
| `$GB_HOME/runtime/<versão>/.getbrolls-runtime-<parte>.json` | `schema` (inteiro) | `setup` | `setup`, `doctor`, toda a CLI | validado em código: `building`, `upgrading` ou `ready` |

### Plugins e marketplaces

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `getbrolls-plugin.json` | `sdk_api` | o autor do plugin (`plugins --action new` gera um) | `plugins`, toda a CLI | validado em código; veja [SDK.md](SDK.md#manifesto) |
| `$GB_HOME/plugins.json` | sem campo | `plugins --action enable|disable|install|update|remove` | toda a CLI | validado em código: pins, últimos pins e origens |
| `$GB_HOME/plugin-data/<id>/` | do plugin | o plugin (`api.data_dir`) | o plugin | livre |
| `getbrolls-marketplace.json` (raiz do repositório do marketplace) | `schema` | o mantenedor do marketplace | `plugins --action marketplace-*`, `search`, `install --id <id>@<marketplace>` | [`marketplace_index.schema.json`](../schemas/marketplace_index.schema.json) |
| `$GB_HOME/marketplaces.json` | `schema` | `plugins --action marketplace-add|update|remove` | `plugins`, `capabilities`, `doctor` | validado em código |
| `$GB_HOME/marketplaces/<nome>/` | do índice | `marketplace-add`, `marketplace-update` | os mesmos | cache conferido por sha256 a cada leitura |

## Layouts de projeto

Um projeto tem layout **0** ou **1**:

- **Layout 0**: sem `project.json`. É o projeto da 2.5 e continua exatamente igual;
  o layout 0 é inferido pela ausência do arquivo e nunca é gravado.
- **Layout 1**: com `project.json` (`layout: 1`). Nasce com `init` ou com
  `migrate --action apply`, que só acrescenta o `project.json`, sem mover nada.

Um `project.json` quebrado nunca é adivinhado: o projeto vale como layout 0 e o
`status` mostra o motivo em `layout.problem`.

**Layout 1 exige getbrolls 2.6 ou mais novo.** A 2.5 não conhece `project.json` nem
`broll/` e trataria o projeto como layout 0, sem achar os clipes finais.

### Layout 0

```text
video-01/
├── RULES.md
├── BRIEF.md
├── ROTEIRO.md               # opcional
├── aroll/  assets/          # só com roteiro --action new
├── entrega/
├── exports/                 # só depois do primeiro export
└── brolls/
    ├── manifest.json
    ├── candidates/
    ├── previews/
    ├── clips/               # clipes finais
    ├── references.json
    ├── roteiro-state.json
    ├── events.jsonl
    ├── roteiro-reviews.jsonl
    ├── diagnostics.jsonl
    ├── getbrolls.log
    ├── credits.md
    └── review.html
```

### Layout 1

```text
video-01/
├── project.json             # schema getbrolls.project/1, layout 1, id, client, template, canvas, fps
├── template.lock.json       # só num projeto criado de template
├── RULES.md
├── BRIEF.md
├── ROTEIRO.md
├── aroll/
├── assets/
│   └── marca/  lettering/  sfx/  musica/  imagem/  composicoes/  outros/
├── broll/                   # clipes finais (fetch grava aqui)
├── analysis/
│   ├── index.json
│   ├── markers.json
│   └── media/<media_id>/media.json, transcript.json, …
├── entrega/
├── exports/
└── brolls/                  # estado: manifesto, candidatos, prévias, logs
    ├── manifest.json
    ├── candidates/
    ├── previews/
    └── …                    # sem clips/
```

No layout 1, o manifesto continua guardando o caminho **lógico** `clips/<arquivo>`
(o schema do candidato não muda); o arquivo físico fica em `broll/<arquivo>`. A pasta
antiga `brolls/clips/`, quando existe, continua sendo lida. O mesmo nome nas duas
pastas nunca vira escolha calada: o `sha256` registrado decide, e a cópia divergente
é nomeada no aviso `CLIP_LEFTOVER_COPY`. `broll/` ou `analysis/` como link são
recusados. No layout 0, uma pasta `broll/` que a pessoa tenha no projeto nunca é
lida.

O `id` do `project.json` é o mesmo `project_id` de `brolls/manifest.json`: o projeto
tem uma identidade só, e quem lê prefere a do `project.json`.

Ferramentas que procuram projetos numa árvore de pastas devem usar o `project.json`
como marcador de projeto e não descer em `broll/` nem em `analysis/`.

## Logs e arquivos de estado

| Arquivo | Papel |
|---|---|
| `brolls/events.jsonl` | Histórico do manifesto: uma linha por gravação (transação). Só acrescenta; a recuperação de uma gravação interrompida confere por ele o que já entrou. |
| `brolls/roteiro-reviews.jsonl` | Revisões humanas do roteiro (quem, canal, frase, sha256 revisado). Só acrescenta. |
| `brolls/diagnostics.jsonl` | Um evento de auditoria por comando no projeto (sucesso ou erro), com traceback e `repr` redigidos quando há erro. Comando só de leitura não cria a pasta só para isso. Sem projeto, `$GB_HOME/diagnostics.jsonl`, só com erros. |
| `brolls/getbrolls.log` | Log do app em `chave=valor`, uma linha por evento, com rotação. Nunca traz caminho da máquina nem texto do roteiro; avisos entram só pelo código. `GB_LOG_LEVEL` controla o nível. |
| `brolls/.pending-transaction.json` | Journal de uma gravação em andamento. Se ficar para trás, o próximo comando a retoma (`recovery_pending`). Não apague. |
| `brolls/.command.lock` | Trava exclusiva do projeto: um comando que grava por vez. |
| `analysis/.lock` | Trava própria das gravações em `analysis/`. |
| `templates/.lock` | Trava da pasta de templates de um cliente durante o `freeze`. |
| `$GB_HOME/.clients.lock` | Trava do registro de clientes. |
| `.getbrolls-setup.lock` | Trava do `setup` na pasta do runtime. |
| `brolls/.serve.pid`, `brolls/.serve.log` (e `.serve.log.1`) | Servidor de fundo do `serve --background`: PID e log da rodada atual e da anterior. |

Os arquivos JSONL não têm versão por linha: cada linha é um objeto independente, e
quem lê ignora chave desconhecida.

## Convenções

### Domínio em português, contrato em inglês

O que a pessoa vê e mexe no dia a dia fica em português: as pastas `aroll/`,
`assets/marca`, `assets/musica`, `entrega/`, o `ROTEIRO.md`, as chaves de frontmatter
(`genero`, `tema`, `cliente`, `direcao`) e as de licença (`origem`, `licenca`,
`credito`). O dado de máquina fica em inglês: chaves de JSON (`media_id`,
`time_unit`, `components`), papéis, estados e códigos. Renomear um lado para o outro
é mudança incompatível e fica para uma versão major.

As pastas de `assets/` são fixas:

| Pasta | Uso |
|---|---|
| `marca` | logos e elementos de marca |
| `lettering` | estilos de texto |
| `sfx` | efeitos sonoros |
| `musica` | trilhas |
| `imagem` | imagens (png, svg, webp, jpg, jpeg) |
| `composicoes` | composições prontas |
| `outros` | só pasta; nada é resolvido nela |

### `media_id` e tempo

- `media_id` são os **16 primeiros hex do sha256 dos bytes** da mídia. Os mesmos
  bytes em qualquer pasta dão o mesmo id; qualquer mudança no arquivo dá outro. Um
  contrato de ingestão externo que chame o mesmo valor de `asset_id`, derivado do
  mesmo jeito, pode usá-lo como chave sem conversão.
- O tempo da análise é em **segundos** (`time_unit: "s"`), número finito, relativo
  ao início do arquivo. Um contrato externo que conte em milissegundos inteiros
  converte com `ms = round(s * 1000)` e `s = ms / 1000`.
- A chave rápida do índice (tamanho, mtime e sha256 de 1 MiB do início e 1 MiB do
  fim) é só cache: o `media_id` é sempre do arquivo inteiro.

### Estados

Estados de componente de análise (`components` do índice e `status` de cada
arquivo):

| Estado | Sentido |
|---|---|
| `done` | Completo. |
| `done_partial` | Parcial; usável com aviso. |
| `unavailable` | Ferramenta ausente (exige `reason`). |
| `failed` | A ferramenta falhou (exige `reason`). |
| `blocked` | Bloqueado por permissão ou política (exige `reason`). |
| `not_run_by_this_script` | Não produzido por este produtor (exige `reason`). |
| `no_speech` | Transcrição sem fala. |

Papéis de mídia (`role`): `aroll`, `broll`, `footage`, `music`, `sfx`,
`narration`, `title`, `animation` e `unknown`.

### Licença de componente

`<nome>.licenca.json`, ao lado do componente, é lido com as chaves em português
(`origem`, `licenca`, `credito`) ou em inglês (`source`, `license_name`,
`attribution`), com `schema_version: 1` opcional. A mesma informação com valores
diferentes nas duas grafias é erro. A gravação e a saída são sempre em português.

**Templates nunca levam licença nem aprovação**: `template --action freeze` nunca
copia `.licenca.json`, aprovações, A-ROLL, `brolls/` nem clipes, e `init --template`
copia componentes sem licença, com o aviso `LICENCE_NOT_TRANSFERRED`.

### Nomes reservados

Reservados para versões futuras, recusados hoje:

- **Chaves de frontmatter do roteiro** `cliente` e `direcao`: aceitas como slug e
  levadas ao plano de export (`meta.cliente`, `meta.direcao`), sem recurso por trás
  ainda.
- **Diretivas do roteiro** `[DIRECAO: …]`, `[TRANSICAO: …]`, `[RITMO: …]` e
  `[VELOCIDADE: …]`: o `roteiro --action check` recusa a linha.
- **Ids de plugin** `core`, `cliente`, `catalogo`, `direcao`, `template` e `projeto`:
  `install`, `enable` e `new` recusam.
- **Tipos de `contributes`** `capturers`, `engines`, `catalogs` e
  `roteiro_templates`: recusados como "ainda não suportado".
- **Refs locais** (`scene:`, `beat:`…) ao lado das refs de catálogo
  `cat:<motor>/<tipo>/<id>@<versão>`.
- **Campo `ext`** nos schemas de candidato e de brief, para dados de extensão.

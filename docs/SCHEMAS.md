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

- **Famílias novas** (da 2.6 em diante: projeto, análise, cliente, receita,
  marketplace) gravam um campo só, autodescritivo:
  `"schema": "getbrolls.<família>/<N>"`, por exemplo `"getbrolls.project/1"`.
- **Famílias que já existiam** mantêm o campo próprio: `schema_version` (manifesto,
  candidatos, referências, fila, biblioteca, estado do roteiro, marcador do export,
  perfil), `version` (`BRIEF.md`, `RULES.md`), `export_version` e `plan_version`
  (plano de export). `plan_version` tem piso (`minimum`) e versão corrente
  (`default`): o piso é o que um plano antigo ainda pode trazer, o `default` é o
  que o core grava.

A leitura é a mesma para os dois jeitos:

- campo **ausente vale 1** (arquivo gravado antes de o campo existir);
- versão **maior que a suportada é recusada sem tocar no arquivo**, com a frase
  "foi gravado por uma versão mais nova do getbrolls (…); atualize antes de
  continuar.";
- qualquer outro valor (texto, `true`, `1.0`, zero, negativo, outra família) é
  inválido;
- toda gravação nova declara a versão.

`NaN` e `Infinity` são recusados nas famílias novas; os arquivos de análise e o
índice de marketplace recusam também chave repetida. Os arquivos JSONL de log não
têm versão por linha.

### A versão mora no schema

Todo JSON que o getbrolls grava ou lê como dado tem um schema em `schemas/`, e o número da
versão sai dele: o código pergunta a `getbrolls.sdk.schemas.version(<nome>)` em vez de repetir
um literal. O schema declara a versão na propriedade do campo de versão da família: um
`maximum` quando a família já teve versões anteriores (fila e biblioteca: `1` e `2`, campo
ausente vale `1`), senão um `const`; nas famílias `getbrolls.<família>/<N>`, o `N` da constante.
Mudar o formato de um arquivo é subir a versão no schema e registrar a mudança no CHANGELOG;
`tests/test_schema_registry.py` confere que não sobrou literal de versão no código nem gravador
sem schema.

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
| receita | `schema` (e `version` = número da versão da receita) | `getbrolls.recipe/1` | [`recipe.schema.json`](../schemas/recipe.schema.json) |
| componentes da receita | `schema` | `getbrolls.components/1` | [`components.schema.json`](../schemas/components.schema.json) |
| lock da receita | `schema` | `getbrolls.recipe_lock/1` | [`recipe_lock.schema.json`](../schemas/recipe_lock.schema.json) |
| rascunho | `schema` | `getbrolls.draft/1` | [`draft.schema.json`](../schemas/draft.schema.json) |
| pacote de entrega | `schema` | `getbrolls.handoff/1` | [`handoff.schema.json`](../schemas/handoff.schema.json) |
| volta do pacote | `schema` | `getbrolls.handoff_return/1` | [`handoff_return.schema.json`](../schemas/handoff_return.schema.json) |
| índice de marketplace | `schema` | `getbrolls.marketplace_index/1` | [`marketplace_index.schema.json`](../schemas/marketplace_index.schema.json) |
| estado dos marketplaces | `schema` | `getbrolls.marketplaces/1` | [`marketplaces.schema.json`](../schemas/marketplaces.schema.json) |
| candidato | `schema_version` | `1` | [`candidate.schema.json`](../schemas/candidate.schema.json) |
| brief | `version` | `1` | [`brief.schema.json`](../schemas/brief.schema.json) |
| plano de export | `export_version` (e `plan_version` do plano de cena, piso `3` e corrente `3`) | `2` | [`export_plan.schema.json`](../schemas/export_plan.schema.json) |
| manifesto | `schema_version` | `1` | [`manifest.schema.json`](../schemas/manifest.schema.json) |
| referências | `schema_version` | `1` | [`references.schema.json`](../schemas/references.schema.json) |
| estado do roteiro | `schema_version` | `1` | [`roteiro_state.schema.json`](../schemas/roteiro_state.schema.json) |
| marcador do export | `schema_version` | `1` | [`export_marker.schema.json`](../schemas/export_marker.schema.json) |
| perfil (`getbrolls.toml`) | `schema_version` | `1` | [`profile_toml.schema.json`](../schemas/profile_toml.schema.json) |
| confiança (`trusted-profiles.json`) | `schema_version` | `1` | [`trusted_profiles.schema.json`](../schemas/trusted_profiles.schema.json) |
| fila (`work/queue.json`) | `schema_version` | `2` (lê `1` e `2`; regrava `1` como `2`) | [`queue.schema.json`](../schemas/queue.schema.json) |
| biblioteca (`library/index.json`) | `schema_version` | `2` (lê `1` e `2`; regrava `1` como `2`) | [`library_index.schema.json`](../schemas/library_index.schema.json) |
| `RULES.md` | `version` | `1` | [`rules_block.schema.json`](../schemas/rules_block.schema.json) |
| marcador do runtime | `schema` (inteiro) | `1` | [`runtime_marker.schema.json`](../schemas/runtime_marker.schema.json) |

`getbrolls.permissions_increase/1` não é um arquivo: é o domínio do sha256 que a
prévia de um `update` de marketplace mostra quando o plugin pede permissão nova.

## Schemas por área

Um schema por arquivo persistido. O campo é o que carrega a versão e o valor é o que o schema
declara hoje (`getbrolls.sdk.schemas.version`). Onde a coluna diz "só a versão", o schema declara
por enquanto apenas o campo de versão e o objeto fica aberto; o restante do formato entra quando o
arquivo passa a ser lido contra o schema.

### Projeto: manifesto, fila e biblioteca

| Arquivo | Schema | Campo | Valor | Estado |
|---|---|---|---|---|
| `brolls/manifest.json` | [`manifest.schema.json`](../schemas/manifest.schema.json) | `schema_version` | `1` | valida o topo na leitura; cada item segue `candidate` ao gravar |
| `brolls/references.json` | [`references.schema.json`](../schemas/references.schema.json) | `schema_version` | `1` | valida na leitura |
| `brolls/roteiro-state.json` | [`roteiro_state.schema.json`](../schemas/roteiro_state.schema.json) | `schema_version` | `1` | valida na leitura |
| `brolls/.pending-transaction.json` | [`pending_transaction.schema.json`](../schemas/pending_transaction.schema.json) | `schema_version` | `1` (sem o campo, `1`) | valida na leitura |
| `work/queue.json` | [`queue.schema.json`](../schemas/queue.schema.json) | `schema_version` | `2` (lê `1` e `2`) | valida na leitura |
| `$GB_HOME/library/index.json` | [`library_index.schema.json`](../schemas/library_index.schema.json) | `schema_version` | `2` (lê `1` e `2`) | valida na leitura |
| `.getbrolls-sources/index.json` | [`source_index.schema.json`](../schemas/source_index.schema.json) | `schema_version` | `1` (sem o campo, `1`) | valida ao gravar; a leitura segue leniente |

### Projeto: export, revisão e relatórios

| Arquivo | Schema | Campo | Valor | Estado |
|---|---|---|---|---|
| `exports/<exporter>/<NNN>/.getbrolls-export.json` | [`export_marker.schema.json`](../schemas/export_marker.schema.json) | `schema_version` | `1` (ausente vale `1`) | formato completo, que o core grava (conferido nos testes); na leitura só `marker` e a versão contam: versão mais nova vale como pasta sem marcador (não é lida e o staging não é apagado) |
| decisões da página de revisão | [`review_decisions.schema.json`](../schemas/review_decisions.schema.json) | `templateVersion` | `2` | envelope no schema, lido contra ele; os itens ficam abertos e o leitor os confere um a um; `templateVersion` maior é recusado com a frase de versão mais nova |
| `--summary-json` do lote do Instagram | [`instagram_summary.schema.json`](../schemas/instagram_summary.schema.json) | `schema_version` | `1` | formato completo; só o core grava (a gravação é conferida contra o schema nos testes) |

### Instalação, pasta pessoal e SDK

| Arquivo | Schema | Campo | Valor | Estado |
|---|---|---|---|---|
| `RULES.md` (bloco JSON) | [`rules_block.schema.json`](../schemas/rules_block.schema.json) | `version` | `1` | lido contra o schema depois de fundir as camadas e das conferências do código; objeto aberto |
| `$GB_HOME/trusted-profiles.json` | [`trusted_profiles.schema.json`](../schemas/trusted_profiles.schema.json) | `schema_version` | `1` | estrito: versão ausente, outra versão ou documento fora do schema é `store_corrupt`; sem a pasta de dados, só a forma e nenhum perfil confiável |
| `getbrolls.toml` | [`profile_toml.schema.json`](../schemas/profile_toml.schema.json) | `schema_version` | `1` | lido contra o schema depois das conferências do código; ausente vale `1` |
| saída de `capabilities --json` | [`capabilities.schema.json`](../schemas/capabilities.schema.json) | `schema_version` | `1` | saída que só cresce: o schema liga quem grava, objetos abertos |
| marcador `.getbrolls-runtime-<parte>.json` | [`runtime_marker.schema.json`](../schemas/runtime_marker.schema.json) | `schema` (inteiro) | `1` | a versão é a constante de inicialização `MARKER_SCHEMA_VERSION`, igual à do schema; marcador mais novo é parte não pronta e de outra versão (conflito de runtime, nunca montada por cima) |
| `$GB_HOME/plugins.json` | [`plugins_state.schema.json`](../schemas/plugins_state.schema.json) | `schema` | `getbrolls.plugins/1` | lido contra o schema; ausente vale `1` (o arquivo da 2.5); `digest: 2` do pin é o formato do hash, não a versão do arquivo |
| `$GB_HOME/marketplaces.json` | [`marketplaces.schema.json`](../schemas/marketplaces.schema.json) | `schema` | `getbrolls.marketplaces/1` | lido contra o schema depois das conferências do código |
| `getbrolls-plugin.json` | [`plugin_manifest.schema.json`](../schemas/plugin_manifest.schema.json) | `sdk_api` | `1` | lido contra o schema quando declara o `sdk_api` desta versão; outro `sdk_api` fica incompatível, com o motivo. `SDK_API` é constante pública do SDK, igual à do schema |

## Mapa dos contratos

Caminhos relativos à pasta do projeto, salvo `$GB_HOME/…` (pasta pessoal, padrão
`~/.getbrolls`) e os arquivos de plugin, cliente e marketplace.

### Projeto

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `project.json` | `schema` | `init`, `migrate --action apply` | todos os comandos de projeto (`layout`) | [`project.schema.json`](../schemas/project.schema.json) |
| `brolls/manifest.json` | `schema_version` | comandos de coleta (`search`, `resolve`, `preview`, `approve`, `permit`, `fetch`, `verify`, `deliver`…) | `status`, `brief`, `review`, `export`, plugins (`ctx`) | [`manifest.schema.json`](../schemas/manifest.schema.json); cada item segue [`candidate.schema.json`](../schemas/candidate.schema.json) |
| `brolls/candidates/<id>.json` | `schema_version` | os mesmos, a cada gravação do manifesto | quem lê um candidato só | [`candidate.schema.json`](../schemas/candidate.schema.json) |
| `brolls/references.json` | `schema_version` | `remember` | `references`, `learn --from-candidate` | [`references.schema.json`](../schemas/references.schema.json) |
| `brolls/roteiro-state.json` | `schema_version` | `roteiro --action sync` | `roteiro`, `status` | [`roteiro_state.schema.json`](../schemas/roteiro_state.schema.json) |
| `work/queue.json` | `schema_version` | `queue` | `queue --action status`, `status` | [`queue.schema.json`](../schemas/queue.schema.json) |
| `.getbrolls-sources/index.json` | `schema_version` (sem o campo, `1`) | `preview`, `inspect`, `fetch` (cache privado de fontes) | os mesmos | [`source_index.schema.json`](../schemas/source_index.schema.json); ilegível, ou de versão mais nova, vira índice vazio |
| `BRIEF.md` (bloco JSON) | `version` | `init-brief`, `roteiro --action sync` | `brief`, `status`, `search --shot` | [`brief.schema.json`](../schemas/brief.schema.json) |
| `RULES.md` (bloco JSON) | `version` | `init-rules` | todo comando que aplica regras | [`rules_block.schema.json`](../schemas/rules_block.schema.json); veja [RULES.md](RULES.md) |
| `ROTEIRO.md` (frontmatter) | sem campo; `type: roteiro` | `roteiro --action new`, `init --recipe` | `roteiro`, `status`, `export`, a selagem da receita | validado em código; veja [`references/roteiro.md`](../references/roteiro.md) |
| `aroll/<nome>.transcript.json` | sem campo | a pessoa (por exemplo, o `transcribe` do HyperFrames) | `export` | palavras `[{"text", "start", "end"}]` em segundos |
| `assets/<tipo>/<nome>.licenca.json` | `schema_version` opcional | a pessoa | `assets`, `export`, a selagem da receita | validado em código |
| `recipe.lock.json` | `schema` | `init --recipe` | `status`, `export` | [`recipe_lock.schema.json`](../schemas/recipe_lock.schema.json) |

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
| `exports/<exporter>/<NNN>/.getbrolls-export.json` | `schema_version` | `export` (marcador do core) | `export` (próxima pasta, varredura) | [`export_marker.schema.json`](../schemas/export_marker.schema.json) |
| `exports/<exporter>/LATEST` | texto | `export` | a pessoa | número da pasta mais nova |

### Instalação e pasta pessoal

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `getbrolls.toml` | `schema_version` | a pessoa | todo comando (perfil de workspace) | [`profile_toml.schema.json`](../schemas/profile_toml.schema.json); veja o [MANUAL.md](MANUAL.md) |
| `$GB_HOME/trusted-profiles.json` | `schema_version` | `profile --action trust|untrust` | todo comando | [`trusted_profiles.schema.json`](../schemas/trusted_profiles.schema.json) |
| `$GB_HOME/library/index.json` | `schema_version` | `learn` e `search` (aprendizado automático de busca vazia) | `search`, `library` | [`library_index.schema.json`](../schemas/library_index.schema.json) |
| `$GB_HOME/clients.json` | `schema` | `client --action add|remove` | `client`, `assets`, `init`, `migrate`, `recipe` | [`clients.schema.json`](../schemas/clients.schema.json) |
| `<pasta do cliente>/client.json` | `schema` | `client --action add` | `client`, `assets`, `recipe` | [`client.schema.json`](../schemas/client.schema.json) |
| `<pasta do cliente>/recipes/<slug>/<N>/getbrolls-recipe.json` | `schema` + `version` | a selagem da receita | `recipe --action list\|show`, `init --recipe` | [`recipe.schema.json`](../schemas/recipe.schema.json) |
| `<pasta do cliente>/recipes/<slug>/<N>/getbrolls-recipe.sha256` | formato do `sha256sum` | a selagem da receita | `recipe --action show`, `init --recipe` | uma linha: sha256 do `getbrolls-recipe.json` |
| `<pasta do cliente>/recipes/<slug>/<N>/components.json` | `schema` | a selagem da receita | `recipe --action show`, `init --recipe` | [`components.schema.json`](../schemas/components.schema.json) |
| `$GB_HOME/runtime/<versão>/.getbrolls-runtime-<parte>.json` | `schema` (inteiro) | `setup` | `setup`, `doctor`, toda a CLI | [`runtime_marker.schema.json`](../schemas/runtime_marker.schema.json): `building`, `upgrading` ou `ready` |

### Plugins e marketplaces

| Arquivo | Versão | Grava | Lê | Formato |
|---|---|---|---|---|
| `getbrolls-plugin.json` | `sdk_api` | o autor do plugin (`plugins --action new` gera um) | `plugins`, toda a CLI | [`plugin_manifest.schema.json`](../schemas/plugin_manifest.schema.json); veja [SDK.md](SDK.md#manifesto) |
| `$GB_HOME/plugins.json` | `schema` (ausente vale `1`) | `plugins --action enable|disable|install|update|remove` | toda a CLI | [`plugins_state.schema.json`](../schemas/plugins_state.schema.json): pins, últimos pins e origens; cada pin grava `digest: 2`, o hash da pasta enquadrado por arquivo, de domínio `getbrolls.plugin_folder/2` (pin sem a chave é do formato antigo e suspende o plugin até novo `enable`) |
| `$GB_HOME/plugin-data/<id>/` | do plugin | o plugin (`api.data_dir`) | o plugin | livre |
| `getbrolls-marketplace.json` (raiz do repositório do marketplace) | `schema` | o mantenedor do marketplace | `plugins --action marketplace-*`, `search`, `install --id <id>@<marketplace>` | [`marketplace_index.schema.json`](../schemas/marketplace_index.schema.json) |
| `$GB_HOME/marketplaces.json` | `schema` | `plugins --action marketplace-add|update|remove` | `plugins`, `capabilities`, `doctor` | [`marketplaces.schema.json`](../schemas/marketplaces.schema.json) |
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
├── project.json             # schema getbrolls.project/1, layout 1, id, client, recipe, canvas, fps
├── recipe.lock.json         # só num projeto criado de receita
├── RULES.md
├── BRIEF.md
├── ROTEIRO.md
├── aroll/
├── assets/
│   └── marca/  lettering/  sfx/  musica/  imagem/  composicao/  outros/
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
| `brolls/getbrolls.log` | Log do app em `chave=valor`, uma linha por evento, com rotação. Nunca traz caminho da máquina nem texto do roteiro; avisos entram só pelo código. `GB_LOG_LEVEL` controla o nível. `init` e `migrate` de um projeto sem `brolls/` só ligam o log depois de criar a pasta. |
| `$GB_HOME/logs/getbrolls.log` | Log do app da instalação: o mesmo formato, a mesma rotação (~1 MB, 3 cópias), a mesma redação e o mesmo `GB_LOG_LEVEL`, para o que acontece fora de um projeto. Só o comando que muda a instalação grava: `plugins` (`install`, `update`, `remove`, `enable`, `disable` e os `marketplace-*` que gravam), `client --action add|remove`, `profile --action trust --yes`, `profile --action untrust` e `setup` (sem `--check` nem `--where`), com `--project` ou não. Comando que só lê (`list`, `show`, `search`, a prévia do `profile --action trust`, `doctor`, `capabilities`…) nunca cria a pasta `logs/` (0700) nem o arquivo (0600). Nada nasce antes da primeira linha; aí um `$GB_HOME` que falta também nasce 0700. Um `logs/` que é link, não é pasta ou é de outro usuário é recusado (a linha não é gravada e o comando segue), um `logs/` seu mais aberto volta a 0700, e o arquivo abre sem seguir link. Cada linha abre, acrescenta e fecha o arquivo, e a rotação acontece sob `logs/.getbrolls.log.lock`. Nunca traz caminho da máquina, URL, nome de cliente ou texto digitado: só ids, contagens, hashes curtos e códigos. O erro de um comando que grava este log aponta o arquivo em `app_log`, e `doctor` e `setup --where` dizem onde ele fica. |
| `brolls/.pending-transaction.json` | Journal de uma gravação em andamento. Se ficar para trás, o próximo comando a retoma (`recovery_pending`). Num `fetch`, ele também guarda o nome final do clipe (`publish`): quem retoma dá o nome ao corte provisório (`<nome>.staged<ext>`) antes de gravar o manifesto. Não apague. |
| `brolls/.command.lock` | Trava exclusiva do projeto: um comando que grava por vez. |
| `analysis/.lock` | Trava própria das gravações em `analysis/`. |
| `recipes/.lock` | Trava da pasta de receitas de um cliente durante a selagem. |
| `$GB_HOME/.clients.lock` | Trava do registro de clientes. |
| `$GB_HOME/logs/.getbrolls.log.lock` | Trava da rotação do log da instalação: tomada só em volta de cada linha, por quem a grava. |
| `.getbrolls-setup.lock` | Trava do `setup` na pasta do runtime. |
| `$GB_HOME/library/index.lock` | Trava do índice da biblioteca pessoal: uma escrita por vez; o `learn` espera até 10 s por ela antes de recusar com `LOCKED`. As linhas `event=library` e `event=library_lock` do `getbrolls.log` estão descritas em [CLI_CONTRACT.md](CLI_CONTRACT.md#linhas-eventlibrary-e-eventlibrary_lock). |
| `brolls/.serve.pid`, `brolls/.serve.log` (e `.serve.log.1`) | Servidor de fundo do `serve --background`: PID e log da rodada atual e da anterior. |

Os arquivos JSONL não têm versão por linha: cada linha é um objeto independente, e
quem lê ignora chave desconhecida.

## Convenções

### Domínio em português, contrato em inglês

O que a pessoa vê e mexe no dia a dia fica em português: as pastas `aroll/`,
`assets/marca`, `assets/musica`, `entrega/`, o `ROTEIRO.md`, as chaves de frontmatter
(`genero`, `tema`, `cliente`, `estilo`) e as de licença (`origem`, `licenca`,
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
| `composicao` | composições prontas |
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
(`origem`, `licenca`, `credito`), com `schema_version: 1` opcional. As chaves em inglês
da versão de testes da 2.6 saíram: o arquivo com elas é recusado na linha do componente,
com a chave em português que vale.

**Receitas nunca levam licença nem aprovação**: a selagem nunca copia `.licenca.json`,
aprovações, A-ROLL, `brolls/` nem clipes, e `init --recipe` copia componentes sem licença,
com o aviso `LICENCE_NOT_TRANSFERRED`.

### Enums abertos e fechados

Um valor que esta versão não conhece num enum **aberto** não é erro: o item não é usado, com o
aviso `ENUM_UNKNOWN`, e o resto segue. Num componente de tipo desconhecido os bytes são conferidos como os
dos outros: a versão selada é verificada inteira. Abertos: tipo de componente (`kind`) e nível (`tier`) das
receitas e do lock; ambiente, `origin.class`, papel de cena, transição, análise, `stages`,
`needs_human.kind` e `reason_code` seguem a mesma regra quando chegam. Fechados (valor
desconhecido é erro): `licence`, `media.role`, o canal de revisão, `exec` e
`rights_basis`.

### Versão de receita: pastas fechadas, lixo do sistema e listagem

Depois de selada, a versão `recipes/<slug>/<N>/` fica somente leitura por inteiro: arquivos em `0444` e
pastas (a da versão, `assets/` e `assets/<tipo>/`) em `0555`, para um Finder, um zip ou um sincronizador não
largarem lixo dentro. Para apagar uma versão, libere a escrita antes (`chmod -R u+w`). No Windows as pastas
continuam graváveis. Falhar em fechar algo é o aviso `RECIPE_FREEZE`, nunca erro.

A conferência (`show`) compara os caminhos em NFC dos dois lados: o disco pode guardar o nome em NFD (HFS+, SMB,
zip) e o selo guarda NFC; o arquivo é aberto pelo nome que está no disco. Um `.DS_Store` e o `._<nome>`
(AppleDouble) de um arquivo da versão são lixo do sistema: viram o aviso `RECIPE_CLUTTER` (e a lista `warnings`
do resultado), não são abertos nem entram em sha256, e a versão segue íntegra. Qualquer outro item a mais é problema.

A listagem (`list`) não refaz o sha256: cada linha de `recipes` traz `owner`, `slug`, `version`, `ref`, `title`,
`problem` (texto em português) e `problem_code` (máquina; `null` sem problema). Os códigos:

| `problem_code` | Quando |
|---|---|
| `unreadable` | O `getbrolls-recipe.json` não é um arquivo comum, é grande demais ou não é JSON legível. |
| `schema` | O JSON está fora do schema `getbrolls.recipe/1` ou é de uma versão que esta não lê. |
| `io_error` | O sistema não deixou olhar a pasta ou o arquivo (permissão, erro de disco). |
| `link` | A pasta da versão (`recipes/<slug>/<N>/`) é um link: o getbrolls não lê receitas através de links. |

`clients_with_problems` lista os clientes cuja pasta `recipes/` (ou a de uma receita) não pôde ser lida, com
`owner`, `problem` e `problem_code`: `io_error` (inclusive a pasta do cliente travada), `link` (a pasta `recipes/<slug>/` é um link) ou `unavailable` quando o cliente não existe ou a pasta `recipes/` é um link. Um cliente com problemas de mais de um tipo aparece uma vez por tipo.
Uma pasta travada nunca derruba a listagem. Se a versão está íntegra, quem diz é o `show` (`intact`, `problems`).

### Nomes reservados

Reservados para versões futuras, recusados hoje:

- **Chaves de frontmatter do roteiro** `cliente` e `estilo`: aceitas como slug e
  levadas ao plano de export (`meta.cliente`, `meta.estilo`), sem recurso por trás
  ainda. `direcao` saiu (agora é `estilo`) e o `check` recusa com o nome novo.
- **Diretivas do roteiro** `[DIRECAO: …]`, `[TRANSICAO: …]`, `[RITMO: …]` e
  `[VELOCIDADE: …]`: o `roteiro --action check` recusa a linha.
- **Ids de plugin** `core`, `cliente`, `catalogo`, `direcao`, `template`, `projeto`,
  `estilo`, `recipe` e `receita`: `install`, `enable` e `new` recusam. Os ids dos
  plugins oficiais (`hyperframes`, `davinci`, `asr`, `analysis_vision`,
  `analysis_audio`, `analysis_ocr`, `analysis_game`) são recusados pelo `new`.
- **Tipos de `contributes`** `environments`, `analyzers`, `templates`, `styles`,
  `recipes`, `rules`, `hooks`, `catalogs` e `eval_rubrics`: recusados como "ainda não
  suportado". `engines`, `brief_templates`, `roteiro_templates`, `themes` e
  `capturers` saíram e recusam com o nome novo.
- **Refs locais** `scene:`, `beat:`, `candidate:`, `clip:`, `aroll:`, `export:`, `doc:` e
  `task:`: recusadas. Já valem `asset:<tipo>:<nome>`, `handoff:<ambiente>/<NNN>`,
  `seg:<id>` e `media:<id>`, ao lado de `cat:<motor>/<tipo>/<id>@<versão>`; a receita é
  `cat:getbrolls/recipe/<dono>.<slug>@<N>`, e `<slug>[@N]` ou `<dono>.<slug>[@N]` também valem
  na linha de comando.
- **Campo `ext`** nos schemas de candidato e de brief, para dados de extensão.

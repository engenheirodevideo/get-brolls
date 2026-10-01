---
type: documentation
status: current
created: 2026-09-30
updated: 2026-09-30
tags: [getbrolls, cli, contrato]
---

# Contrato da CLI do getbrolls

Este documento descreve o que um script, um agente ou um plugin pode esperar de
qualquer comando do `getbrolls`: onde sai o resultado, onde sai o erro, quais são os
códigos de saída e de erro, o que só lê e o que muda entre versões. Os testes do
repositório (`tests/test_cli_contract_doc.py`) conferem as tabelas abaixo contra o
código: um código de saída, de erro ou de aviso novo só entra junto com a linha dele
aqui.

O comando é `getbrolls …` no pacote instalado e `python3 scripts/gb.py …` no checkout
e no plugin do Claude Code; os dois têm o mesmo contrato. `getbrolls capabilities
--json` diz qual das formas vale na instalação atual (veja
[`capabilities`](#capabilities---json)).

Os formatos dos arquivos que a CLI grava estão em [SCHEMAS.md](SCHEMAS.md). O
passo a passo de cada comando está no [MANUAL.md](MANUAL.md).

## Saída: stdout e stderr

- **stdout** carrega só o resultado do comando: um documento JSON, indentado, em
  UTF-8. Nada mais é escrito ali, nem aviso nem progresso. Um comando que deu certo
  sempre deixa um JSON válido em stdout.
- **stderr** carrega o erro (uma linha JSON, veja [Erro de operação](#erro-de-operação))
  e, quando houver, linhas de progresso e o espelho do log:
  - progresso de operação longa sai como uma linha JSON com a chave `progress`
    (por exemplo, `{"progress": "analysis_register", "path": …, "size_bytes": …,
    "message": …}` antes de calcular o sha256 de uma mídia grande);
  - o `setup` escreve o andamento de cada passo em texto (`getbrolls setup: …`), e a
    saída de `pip`/`npm` só vai ao stderr quando ele é um terminal;
  - com `GB_LOG_STDERR=1`, as linhas do `getbrolls.log` são espelhadas no stderr.
- **Avisos** não vão para o stderr: ficam no próprio JSON do resultado (ou do erro),
  na lista `warnings` (veja [Avisos](#avisos-warnings)).
- **Ordem determinística.** Listas do resultado saem numa ordem estável (por id,
  nome ou caminho), para que duas execuções sobre o mesmo estado deem a mesma saída.
- **Sem caminho da máquina onde não precisa.** `capabilities`, `analysis`, `client
  add|list|show` (`root`, `registry` e as mensagens) e `plugins --action list|install`
  (`plugins_dir` como `$GB_HOME/plugins`; `source` local sob a pasta pessoal como
  `~/…`, URL como veio) trocam a pasta pessoal por `~` ou `$GB_HOME/…`. Ficam
  absolutos, porque são dados que a máquina usa (abrir, passar de volta como
  argumento): `log`/`app_log` do erro, `project`, `roteiro`, `brief`, `rules` e
  `backup` dos comandos que os criam, o `path` de cada componente no `roteiro
  check|plan` e no `assets`, `local_path` e `install.*` do `doctor`. O
  `plugins.json` continua guardando a origem com o caminho de verdade.

### `serve` em primeiro plano

`serve` sem `--background` nem `--stop` segura o terminal servindo o Storyboard e
tem um contrato próprio: imprime **uma linha JSON** com as URLs em stdout e mais nada.
Um erro de dados antes de subir o servidor sai também como uma linha JSON
`{"error", "error_code"}` em stdout, com o código de saída do `error_code`. Encerrar
com Ctrl+C sai com `0`: é o jeito normal de parar o servidor.

## Códigos de saída

| Código | Nome | Quando |
|---|---|---|
| `0` | `ok` | Deu certo. O resultado está em stdout. |
| `1` | `operation` | Erro de operação ou de dados: id errado, falta aprovação, link fora do ar, arquivo inválido, trava ocupada (`LOCKED`). Também `analysis --action check` com `"ok": false` e `setup --upgrade` cuja atualização falhou e voltou à versão fixada — nesses dois o relatório sai em stdout do mesmo jeito. |
| `2` | `usage` | Erro de uso: comando, flag ou valor errado, `--env-file`/`GB_ENV_FILE` que não existe, chave que um `.env` não pode definir, perfil não confiável ou inválido. |
| `3` | `internal` | Erro interno (bug). A pessoa vê só o tipo do erro; o traceback fica em `diagnostics.jsonl`. |
| `4` | `prerequisite` | Falta um pré-requisito da instalação: arquivos de dados, ffmpeg, ffprobe, yt-dlp, `requires` do perfil que não bate. `doctor` e `setup` saem com `4` e o resultado em stdout (`"ready": false`); nos outros comandos é um erro `PREREQUISITE_MISSING` no stderr. |
| `130` | `interrupted` | Interrompido (Ctrl+C) fora do `serve` em primeiro plano. |

Quem só confere "diferente de zero" (`set -e`) não precisa distinguir os códigos.
Quem automatiza pode tratar `1` com `LOCKED` como "tente de novo depois" e `4` como
"rode `getbrolls setup`".

Quando quem lê a saída fecha o pipe antes do fim (`| head`), o comando sai com o
próprio código, sem traceback de `BrokenPipeError`.

## Códigos de erro

Todo erro em JSON traz `error_code`. A tabela é estável: um código novo é aditivo e
entra aqui; um código existente não muda de significado nem de código de saída numa
versão minor.

| `error_code` | Saída | Origem |
|---|---|---|
| `INVALID_DATA` | `1` | Qualquer erro de dados ou de operação (`ValueError` depois do parse), inclusive de comando de plugin e de exportador. É o padrão para o que não é dos outros códigos. |
| `IO_ERROR` | `1` | Erro do sistema de arquivos ou de rede (`OSError`): permissão, disco, arquivo que sumiu. |
| `LOCKED` | `1` | Outro processo segura a trava do projeto (`brolls/.command.lock`), de `analysis/` ou do runtime. Repetir depois resolve. |
| `USAGE_ERROR` | `2` | Erro do argparse ou `UsageError` explícito (`.env` recusado, perfil não confiável, combinação de flags inválida). |
| `INTERNAL_ERROR` | `3` | `KeyError`, `TypeError` e `AttributeError` dentro de um comando, ou qualquer exceção fora dele: assinatura de bug. |
| `PREREQUISITE_MISSING` | `4` | `PrerequisiteError`: ferramenta, arquivo de dados ou requisito do perfil ausente. |
| `INTERRUPTED` | `130` | Ctrl+C. |

`ValueError` genérico depois do parse continua sendo `INVALID_DATA` mesmo quando, na
prática, é uso errado: só um `UsageError` explícito vira `USAGE_ERROR`.

## Erro de uso

Erro do argparse (subcomando, flag ou `choices` errado, opção obrigatória faltando)
sai com código `2` no stderr, em um de dois formatos, decidido só pelo stderr:

- **stderr fora de um terminal** (agente, script, `2>` para arquivo): uma linha JSON
  com exatamente estas chaves:

  ```json
  {"error": "faltam argumentos obrigatórios: --project", "error_en": "the following arguments are required: --project", "error_code": "USAGE_ERROR", "usage": "usage: getbrolls status [-h] --project PROJECT", "suggestion": "--project", "prog": "getbrolls status"}
  ```

  `error` é a mensagem em pt-BR: as mensagens comuns do argparse (argumentos
  obrigatórios, um de vários obrigatório, escolha inválida, argumentos não
  reconhecidos, valor que falta, valor inválido, flags que não valem juntas, opção
  ambígua) são traduzidas, com nomes, valores e escolhas como vieram; uma mensagem
  que nenhum padrão reconhece sai como veio. `error_en` é a mensagem original do
  argparse, byte a byte, para ferramentas; `usage` é a linha de uso sem cores;
  `prog` é o comando (`getbrolls` ou `getbrolls <subcomando>`); `suggestion` é o nome
  parecido com o que foi digitado (`serch` → `search`, `--limt` → `--limit`,
  `--projct` → `--project`) ou `null`.
- **stderr num terminal**: a linha de uso do argparse (`usage: …`) e
  `getbrolls: erro: <a mensagem de error>` e, quando há nome parecido, uma linha a mais
  `Você quis dizer: <nome>?`.

A ajuda e os erros chamam a CLI de `getbrolls`, também no checkout.

## Erro de operação

Qualquer outro erro sai como **uma linha JSON no stderr**. Os campos:

| Campo | Conteúdo |
|---|---|
| `error` | Mensagem para a pessoa, em português. |
| `error_code` | Um dos [códigos de erro](#códigos-de-erro). |
| `message` | A mesma mensagem, como gravada no evento de auditoria. |
| `operation` | O subcomando que falhou. |
| `status` | `error` ou `interrupted`. |
| `at` | Data e hora do início do comando. |
| `state_committed` | `true` quando o estado já foi gravado antes da falha (a página de revisão é que falhou). |
| `recovery_pending` | `true` quando ficou uma gravação pendente (`brolls/.pending-transaction.json`) que o próximo comando retoma. |
| `warnings` | Avisos acumulados até a falha (veja [Avisos](#avisos-warnings)). |
| `type` | Nome da classe da exceção, sem o texto dela. |
| `log` | Caminho do `diagnostics.jsonl` que guardou o evento, ou `null`. |
| `app_log` | Caminho do `getbrolls.log` do projeto, ou `null`. |
| `hint` | Só quando há o que fazer além de corrigir e repetir: com `recovery_pending` ou com `state_committed` (rode `review`; nunca no `roteiro`). |

Erro levantado antes do comando começar (`.env` recusado, por exemplo) sai só com
`error` e `error_code`. O erro interno sai com `error`, `error_code`, `type`, `log` e
`app_log`, e a mensagem cita o tipo (`[type: KeyError]`).

**Nunca há traceback nem `repr` no JSON de erro**, em nenhum comando: os dois ficam,
com segredos redigidos, em `brolls/diagnostics.jsonl` do projeto (ou
`$GB_HOME/diagnostics.jsonl` sem projeto).

Nos comandos que não gravam no manifesto do projeto — `plugins`, `x`, `export`,
`assets`, `client`, `analysis` e `template` — o erro sai sem `hint`: a dica de
retomada ou de `review` não se aplica a eles.

## Avisos (`warnings`)

Um aviso é um objeto `{"code": "<CÓDIGO>", "message": "<texto para a pessoa>"}` na
lista `warnings` do resultado (ou do JSON de erro). O comando seguiu; o aviso diz o
que a pessoa ou o agente deveria saber. O `getbrolls.log` registra só o código.

| Código | Quando aparece |
|---|---|
| `APPROVE_ALL_WIDE` | `approve --all` aprovou vários candidatos de uma vez. |
| `CACHE_UNAVAILABLE` | O cache HTTP local não pôde ser lido; a busca foi à fonte. |
| `CLIENT_NOT_REGISTERED` | `migrate`: o cliente do roteiro ou de `--client` não está em `clients.json`. |
| `CLIENT_UNREGISTERED` | O `project.json` cita um cliente que não está registrado; os componentes dele ficam de fora. |
| `CLIP_LEFTOVER_COPY` | O mesmo clipe existe em `broll/` e em `brolls/clips/`; o sha256 registrado decidiu e a cópia divergente é nomeada. |
| `DELIVERY_FREEZE_FAILED` | O arquivo entregue não pôde ficar somente leitura. |
| `DELIVERY_LINK_FAILED` | Os arquivos estão íntegros, mas `entrega/` não pôde ser refeita. |
| `DELIVERY_SWEEP_RMDIR_FAILED` | Uma pasta vazia de `entrega/` não pôde ser apagada. |
| `DEPRECATED` | Algo que ainda funciona, mas sai numa versão futura (veja [Deprecação](#deprecação)). |
| `EMPTY_STORYBOARD` | `review` gerou um Storyboard sem itens. |
| `ENV_FILE_SHADOWED` | Há dois `.env`; só o primeiro da ordem foi lido. |
| `ENV_GB_HOME_IGNORED` | O `.env` lido define `GB_HOME`, mas o `home` do perfil confiável já decidiu a pasta pessoal; a linha do `.env` foi ignorada. |
| `FFMPEG_PROBE_FAILED` | Os filtros do ffmpeg não puderam ser sondados; `drawtext` tratado como indisponível. |
| `LIBRARY_INDEX_UNREADABLE` | A biblioteca de aprendizados não pôde ser lida; as pistas dela ficaram de fora. |
| `LIBRARY_WRITE_FAILED` | Um aprendizado não pôde ser gravado na biblioteca. |
| `LICENCE_NOT_TRANSFERRED` | `init --template` copiou um componente sem a licença dele. |
| `LOG_UNAVAILABLE` | `diagnostics.jsonl` não pôde ser gravado. |
| `MEDIA_PROBE_FAILED` | `analysis --action register`: o ffprobe não leu a mídia (o comando sai com `1`). |
| `MIGRATE_BROLL_EXISTS` | `migrate`: já existe uma `broll/` com arquivos, que vira a pasta dos clipes finais. |
| `PLUGIN_ENV_TOOLCHAIN` | Uma variável de plugin no `.env` tem nome que ferramentas do sistema leem; ela chega só ao plugin. |
| `PLUGIN_PATH_CHANGED` | Uma pasta de `permissions.paths` não confere com a gravada no pin e fica ignorada. |
| `PLUGIN_SELECTION_BLOCKS` | `plugins --action install` instalou e habilitou o plugin, mas a seleção da sessão (o `plugins` do perfil ou o `GB_PLUGINS`) o deixa desligado. |
| `PLUGIN_PIN_OUTDATED` | O pin de um plugin é de uma versão antiga; rode `plugins --action enable` de novo. |
| `PREVIEW_LIMITATION` | A prévia foi gerada com uma limitação (só referência, por exemplo). |
| `PROVIDER_ERROR` | Uma fonte falhou e o comando terminou em erro. |
| `PROVIDER_FAILED` | Uma fonte falhou durante a busca; as outras seguiram. |
| `RECOVERED_WRITE` | Uma gravação interrompida foi retomada antes do comando. |
| `REFERENCE_POSTER_MISSING` | A referência ficou sem imagem de cartaz. |
| `RULES_LAYER_IGNORED` | Uma camada do `RULES.md` foi ignorada. |
| `RULES_UNAVAILABLE` | `queue` seguiu sem o `RULES.md` para o ritmo. |
| `SOURCE_CACHE_STALE` | O cache de fonte de um candidato tem sha divergente e não foi reutilizado. |
| `SOURCE_INDEX_UNREADABLE` | O índice de fontes privadas estava ilegível e foi tratado como vazio. |
| `TEMPLATE_FREEZE` | `template --action freeze` gravou a versão com uma ressalva. |
| `TEMPLATE_LOCK_DRIFT` | Um componente achado hoje não bate com o `template.lock.json`. |
| `YTDLP_WARNING` | Aviso repassado do yt-dlp. |

`analysis --action check` devolve, além disso, uma lista própria de `problems`, cada
um com `code` (`MEDIA_ID_MISMATCH`, `MISSING_FILE`, `ORPHAN_MEDIA`,
`SCHEMA_VIOLATION`, `UNKNOWN_FILE`, `UNSAFE_LINK`) e o arquivo.

## O que só lê

Estes comandos nunca tomam a trava do projeto, nunca criam `brolls/` e nunca gravam
no projeto:

| Comando | Observação |
|---|---|
| `status` | Abre o manifesto sem recuperar pendência. |
| `serve` | Só serve arquivos. |
| `brief` | Lê `BRIEF.md`, `RULES.md` e o manifesto existente. |
| `doctor` | Diagnostica a instalação; `--project` não cria nada. |
| `setup` | Grava só no runtime (`$GB_HOME/runtime` ou `GB_RUNTIME_DIR`), nunca num projeto. |
| `x` | Comando de plugin, que lê o projeto por cópias. |
| `capabilities` | Descreve o parser e os manifestos de plugin. |
| `profile` | `trust`/`untrust` gravam só `$GB_HOME/trusted-profiles.json`. |

E estas ações:

| Comando | Ações que só leem |
|---|---|
| `analysis` | `list`, `check` |
| `assets` | `list`, `where` |
| `client` | `list`, `show` |
| `migrate` | `plan` |
| `plugins` | `marketplace-list`, `search` |
| `queue` | `status` |
| `roteiro` | `check`, `plan` |
| `template` | `list`, `show` |

`analysis --action register` grava só sob a trava própria (`analysis/.lock`), nunca
sob a do projeto. `init` e `migrate` só tomam a trava depois de validar tudo: um
`init` recusado não deixa pasta, `brolls/` nem trava para trás.

`capabilities --json` repete essas listas em `read_only` e `read_only_actions` de
cada comando.

## Sem perguntas: `--yes` e `--expect`

A CLI **nunca abre um prompt**. O que precisa de confirmação humana é feito em dois
passos:

1. sem `--yes`, o comando mostra a prévia (o que seria gravado, permissões, origem) e
   um sha256 do conteúdo mostrado, e não grava nada;
2. depois que a pessoa viu a prévia, o mesmo comando com `--yes --expect <sha256>`
   grava — e recusa se o conteúdo mudou desde a prévia.

Vale para `plugins --action install|update` (sempre `--yes --expect`),
`plugins --action enable` (`--yes`; mais `--expect` quando o conteúdo mudou desde o
pin), `plugins --action remove` (`--yes`), `profile --action trust`
(`--yes --expect`) e `roteiro --action review` (`--expect` com o `review.sha256` que
`check` e `plan` devolvem). `migrate --action plan` e `--dry-run` (em `export`,
`search` e outros) seguem a mesma ideia: mostram sem gravar.

Um agente nunca preenche `--yes` sem mostrar a prévia à pessoa.

## `capabilities --json`

`getbrolls capabilities --json` descreve esta instalação, gerado do próprio parser e
das tabelas do runtime (nada escrito à mão por comando), sem rede e sem caminho da
máquina. Chaves do topo:

| Chave | Conteúdo |
|---|---|
| `schema_version` | Versão deste manifesto (`1`). |
| `name`, `version`, `prog` | `getbrolls`, a versão instalada e o nome do programa. |
| `invocation` | Como chamar esta instalação: `argv` (`python3 scripts/gb.py` no checkout, `getbrolls` no pacote) e `module` (`python3 -P -m getbrolls`, só fora do checkout). |
| `output` | O contrato de streams em uma linha. |
| `global_options` | Opções que valem para todo comando. |
| `commands` | Um objeto por comando: `name`, `summary`, `requires_project`, `read_only` (`true`, `false` ou `"by_action"`), `read_only_actions`, `options` e `positionals`. Cada opção traz `flags`, `dest`, `required`, `takes_value`, `repeatable`, `choices`, `default`, `type`, `help` e `hidden`. |
| `exit_codes` | A [tabela de códigos de saída](#códigos-de-saída): `code`, `name`, `meaning`. |
| `error_codes` | A [tabela de códigos de erro](#códigos-de-erro): `code` e `exit`. |
| `plugin_commands` | Comandos de plugin habilitado: `plugin`, `command`, `status`, `argv`. |
| `plugins_problems` | Plugin que não está habilitado nem desligado de propósito (inválido, com falha, suspenso…), com `status` e `reason`. |
| `plugins_error` | Só quando o inventário de plugins não pôde ser lido. |
| `marketplaces` | Marketplaces fixados: nome, commit do índice, quantos plugins, `allowed` pelo perfil e um eventual problema do cache. |
| `marketplaces_error` | Só quando `$GB_HOME/marketplaces.json` não pôde ser lido. |

`capabilities` só acrescenta chaves; uma chave existente não muda de sentido sem
subir `schema_version`.

## Variáveis de ambiente

Os nomes canônicos são `GB_*`. Cada `GB_X` do core também pode vir como
`GETBROLLS_X` no ambiente do processo: o `GB_X` vence, e um `GB_X` vazio conta como não
definido. O `doctor` mostra os aliases em uso. No `.env`, só o nome `GB_*` é aceito.

| Variável | Para quê |
|---|---|
| `GB_HOME` | Pasta pessoal (`~/.getbrolls`): `RULES.md` global, biblioteca, plugins, clientes, runtime. Defina no ambiente; num `.env` está deprecada. |
| `GB_ENV_FILE` | Qual `.env` ler. Só do ambiente do processo. |
| `GB_PROFILE` | Caminho do `getbrolls.toml` ou `off`. Só do ambiente do processo. |
| `GB_PROFILE_SHA256` | sha256 do perfil que o processo pai conferiu, repassado aos filhos. Só do ambiente do processo. |
| `GB_RULES_FILE`, `GB_BRIEF_FILE` | Caminho alternativo do `RULES.md` e do `BRIEF.md`. |
| `GB_LIBRARY` | `off` desliga a biblioteca global. |
| `GB_PLUGINS` | Lista de ids, entre os habilitados com pin válido, que a sessão carrega; `off` desliga todos. |
| `GB_RUNTIME_DIR` | Pasta com `.venv/` (yt-dlp) e `.tools/` (Playwright). |
| `GB_CACHE_DIR` | Pasta do cache local (o nome antigo `GETBROLLS_CACHE_DIR` continua lido do ambiente). |
| `GB_FFMPEG_PATH`, `GB_FFPROBE_PATH`, `GB_YTDLP_PATH`, `GB_VENV_PATH` | Fixam executáveis e a venv: caminho explícito sempre vence a descoberta, e um pin inválido é erro. |
| `GB_PREVIEW_MODE`, `GB_GIF_SCOPE`, `GB_GIF_WIDTH`, `GB_GIF_FPS`, `GB_GIF_COLORS`, `GB_GIF_MAX_MB`, `GB_PREVIEW_MAX_SECONDS`, `GB_SCAN_MAX_SECONDS`, `GB_STATIC_FRAMES` | Prévias. |
| `GB_FONT_FILE` | Fonte TrueType dos rótulos do contact sheet. |
| `GB_PACE_MIN_S`, `GB_PACE_MAX_S`, `GB_MAX_PER_HOUR`, `GB_MAX_PER_DAY`, `GB_YTDLP_SLEEP` | Ritmo da fila social e pausas do yt-dlp. |
| `GB_DELIVERY_COPY` | `1` faz o `deliver` copiar em vez de criar hardlink. |
| `GB_LOG_LEVEL` | Nível do `getbrolls.log` (`DEBUG`, `INFO`, `WARNING`, `ERROR` ou `off`). |
| `GB_LOG_STDERR` | `1` espelha o `getbrolls.log` no stderr. |
| `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `YOUTUBE_API_KEY` | Chaves opcionais de fonte. |

Os valores e limites de cada uma estão no [MANUAL.md](MANUAL.md).

## Precedência: `.env`, perfil e padrão

Qual `.env` vale (só um é lido):

1. `--env-file <arquivo>`;
2. `GB_ENV_FILE`;
3. o `.env` da pasta da instalação (checkout);
4. `$GB_HOME/.env`.

Com dois `.env` presentes, o primeiro da ordem vale e todo comando avisa
`ENV_FILE_SHADOWED`. Arquivo explícito que não existe é erro de uso (`2`).

Qual perfil `getbrolls.toml` vale: `--profile <arquivo>`, depois `GB_PROFILE`, depois
o primeiro `getbrolls.toml` subindo a partir do `--project` e, por fim, da pasta atual;
`off` desliga. O perfil só vale quando é confiável (veio de `GB_PROFILE`, é o
`$GB_HOME/getbrolls.toml` ou foi confiado com `profile --action trust`).

Qual valor vence, campo a campo:

1. flag da linha de comando;
2. ambiente do processo;
3. o `.env` escolhido acima;
4. o perfil;
5. o padrão.

O `home` do perfil é aplicado antes da escolha do `.env` (ele decide qual
`$GB_HOME/.env` é lido). Por isso, só para `GB_HOME`, o `home` de um perfil confiável
vence o `.env`: um `GB_HOME` num `.env` fora de `$GB_HOME` (checkout, `GB_ENV_FILE`,
`--env-file`) fica ignorado e o comando avisa `ENV_GB_HOME_IGNORED`; o ambiente do
processo continua vencendo o perfil. `doctor` mostra a pasta que valeu em
`install.gb_home` e a origem em `install.gb_home_source` (`env`, `env_file`, `profile`
ou `default`). `plugins` do perfil é um teto: só estreita o `GB_PLUGINS`; quem fica de
fora aparece `disabled` com o motivo "desligado pelo perfil do workspace (getbrolls.toml
`plugins`)" e `plugins --action list` diz `"selection": "profile"`.
`profile --action show` mostra cada valor com a origem (`env`, `env_file`, `profile`,
`default`).

## Deprecação

- Um nome que sai (comando, flag, chave, variável) continua funcionando como alias por
  **pelo menos uma versão minor**, e cada uso dele acrescenta um aviso
  `DEPRECATED` em `warnings` dizendo o que usar no lugar e em que versão o alias some.
- Mudança de código de saída ou de `error_code` é sempre anunciada na seção
  "Migração" do [CHANGELOG](../CHANGELOG.md).
- Campo novo no JSON de resultado, de erro ou de `capabilities` é aditivo: quem lê
  deve ignorar chave desconhecida.

Hoje em deprecação: `GB_HOME` definido num `.env` fora de `$GB_HOME` (vale até a 2.7,
com aviso `DEPRECATED`); o nome `GETBROLLS_CACHE_DIR` (lido do ambiente, nunca do
`.env`).

### Reservado para a 2.7

Um envelope comum para todo resultado, `{"schema_version", "ok", "data", "error"}`,
está reservado e chegará de forma aditiva. Até lá, o resultado é o JSON do próprio
comando, como descrito acima.

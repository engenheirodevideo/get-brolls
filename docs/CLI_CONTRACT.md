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
  - com `GB_LOG_STDERR=1`, as linhas do `getbrolls.log` são espelhadas no stderr; um `.env` ou perfil que para o
    comando antes do log (que nunca cria arquivo) aparece como `command_refused`, com o `reason` do perfil
    (`untrusted`, `changed`, `invalid`, `requires`…) ou do `.env` (`env_missing`, `env_process_only_key`,
    `env_gb_home`, `env_unreadable`, `env_encoding`); uma falha de E/S que não é o `.env` (pasta sem
    permissão no caminho do perfil ou do `$GB_HOME`) sai com `reason=io`.
- **Avisos** não vão para o stderr: ficam no próprio JSON do resultado (ou do erro),
  na lista `warnings` (veja [Avisos](#avisos-warnings)).
- **`--json` e `GB_OUTPUT=json`** valem em todo comando (flag, ambiente ou `.env`): a saída já é
  sempre JSON, então nada muda; os dois ficam reservados para forçar o JSON quando houver modo humano.
  Fora de `doctor`, `setup`, `capabilities` e `profile`, a flag não aparece na ajuda nem no `usage`.
- **Ordem determinística.** Listas do resultado saem numa ordem estável (por id,
  nome ou caminho), para que duas execuções sobre o mesmo estado deem a mesma saída.
- **Sem caminho da máquina.** Em todo comando, toda mensagem para a pessoa (`error`,
  `message`, `hint`, `summary.*`, `warnings[].message`, `problem`, `reason`, `note`…),
  toda linha do `getbrolls.log` e do `diagnostics.jsonl` e o stderr de texto nomeiam uma
  pasta desta máquina pela marca dela: `<projeto>/…` (o projeto do comando), `<cliente>/…`,
  `$GB_HOME/…`, `$GB_CACHE_DIR/…`, `$GB_RUNTIME_DIR/…`, `$TMPDIR/…` ou `~/…` (a mais funda
  que guarda o caminho, nas duas grafias, como escrito e resolvido) e, fora de todas,
  `<pasta fora do projeto>/<nome>`; de um erro do sistema, só o motivo (`Permission
  denied`), nunca o texto do Python que repete o caminho. Vale também com o projeto, a
  pasta do cliente ou o `$GB_HOME` fora da pasta pessoal. A exceção é o caminho que a
  pessoa digitou, devolvido como ela o digitou: o `--project` de uma recusa (projeto que
  não existe, que já existe ou que não é pasta), o `--root` do `client add` (mais
  `/<cliente>`), o `GB_RULES_FILE` e as linhas de comando sugeridas, que repetem o
  `--project`. Ficam absolutos só os campos da tabela [Campos que ficam absolutos](#campos-que-ficam-absolutos),
  dados que a máquina usa (abrir, passar de volta como argumento), mais os caminhos fixados de um
  plugin (`roots_resolved`, o `permissions.paths` resolvido do `plugins --action install` e do
  `enable`). O `plugins.json` continua
  guardando a origem com o caminho de verdade. Na saída do `client`, `registry` é
  `$GB_HOME/clients.json`; a pasta do cliente (`root`) sai no `add`, no `list` e no `show` como a
  pessoa a registrou (o `--root` digitado no `add`, mais `/<cliente>`: dado dela) e, no `remove` e nas
  mensagens, pela marca. O `clients.json` guarda as duas: `root`, resolvida (o que a máquina abre), e
  `root_typed`, como digitada (absoluta ou começando com `~`); um registro de antes de `root_typed` mostra o `root` que tem. Quando um link no caminho digitado passa a levar a outra pasta, o `list` e o `show` mantêm o `root` digitado e dizem isso em `problem`. O teste `tests/test_no_machine_paths.py` roda a
  matriz do ouro e a da 2.6 com a pasta pessoal, o `$GB_HOME`, o projeto e a pasta
  temporária separados e confere esta lista.

### Campos que ficam absolutos

O caminho em cada campo é o do JSON da saída (`[]` é cada item de uma lista); `tests/test_no_machine_paths.py` confere esta tabela contra a guarda que roda a matriz.

<!-- campos-absolutos -->
| Comando | Campos de `stdout` (JSON) que ficam absolutos |
|---|---|
| qualquer comando, no erro | `stderr.app_log`, `stderr.log` |
| `approve` | `local_path` |
| `assets list` | `assets[].path` |
| `brief` | `beats[].commands.inspect`, `beats[].commands.preview`, `beats[].commands.resolve`, `beats[].commands.search`, `brief` |
| `browser-plan` | `cwd`, `output` |
| `client add` | `client.root` |
| `client list` | `clients[].root` |
| `client show` | `client.root` |
| `deliver` | `delivery`, `readme` |
| `doctor` | `install.app_log`, `install.env_file.path`, `install.env_file.searched[]`, `install.gb_home`, `install.profile.trust_file`, `install.profile.values.cache_dir.value`, `install.profile.values.home.value`, `install.profile.values.runtime_dir.value`, `install.runtime.tools.path`, `install.runtime.venv.path`, `venv.path`, `venv.python` |
| `fetch` | `local_path` |
| `import-review` | `file`, `review` |
| `init` | `project`, `summary.do.command` |
| `init-brief` | `brief` |
| `init-rules` | `rules` |
| `migrate plan` | `project` |
| `permit` | `local_path` |
| `preview` | `files.contact_sheet`, `files.gif`, `files.poster`, `files.review`, `local_path` |
| `profile show` | `env_file.path`, `env_file.searched[]`, `profile.trust_file`, `profile.values.cache_dir.value`, `profile.values.home.value`, `profile.values.runtime_dir.value` |
| `queue add` | `queue` |
| `queue mark` | `queue` |
| `queue next` | `queue` |
| `queue resume` | `queue` |
| `queue status` | `queue` |
| `recipe show` | `path` |
| `reject` | `local_path` |
| `resolve` | `local_path` |
| `review` | `review` |
| `roteiro check` | `scenes[].components[].path` |
| `roteiro new` | `roteiro`, `rules`, `summary.do.command` |
| `rules` | `sources.asset_types`, `sources.blocked_domains`, `sources.browser`, `sources.copyright`, `sources.editorial_rules`, `sources.preferred_domains`, `sources.preferred_providers`, `sources.version`, `sources.video_format` |
| `setup` | `app_log`, `gb_home`, `tools.in_use.path`, `tools.path`, `tools.root`, `venv.in_use.path`, `venv.path`, `venv.root` |
| `status` | `log`, `project`, `review_page`, `summary.do.command` |
<!-- /campos-absolutos -->

### `serve` em primeiro plano

`serve` sem `--background` nem `--stop` segura o terminal servindo o Storyboard e
tem um contrato próprio: imprime **uma linha JSON** com as URLs em stdout e mais nada.
Um erro de dados antes de subir o servidor sai também como uma linha JSON
`{"error", "error_code"}` em stdout (mais `reason_code` quando é uma recusa com motivo), com o código de saída do `error_code`. Encerrar
com Ctrl+C sai com `0`: é o jeito normal de parar o servidor.

## Códigos de saída

| Código | Nome | Quando |
|---|---|---|
| `0` | `ok` | Deu certo. O resultado está em stdout. |
| `1` | `operation` | Erro de operação ou de dados: id errado, falta aprovação, link fora do ar, arquivo inválido, trava ocupada (`LOCKED`). Também `analysis --action check` com `"ok": false` e `setup --upgrade` cuja atualização falhou e voltou à versão fixada — nesses dois o relatório sai em stdout do mesmo jeito. |
| `2` | `usage` | Erro de uso: comando, flag ou valor errado, `--env-file`/`GB_ENV_FILE` que não existe, `.env` que não está em UTF-8 (com ou sem BOM), chave que um `.env` não pode definir, perfil não confiável ou inválido. |
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
| `IO_ERROR` | `1` | Erro do sistema de arquivos ou de rede (`OSError`): permissão, disco, arquivo que sumiu. Também um `.env` que existe mas não abre (sem permissão de leitura) ou uma pasta sem permissão no caminho do perfil ou do `$GB_HOME`, antes de o comando começar. Também um `plugins.json` ou um `marketplaces.json` que não dá para ler (sem permissão, ou uma pasta, um FIFO ou um dispositivo no lugar do arquivo): a mensagem diz "não é um arquivo comum" ou o motivo do sistema, não manda apagar nada, e o arquivo pode estar certo. Conteúdo ruim (JSON quebrado, fora do schema) continua `INVALID_DATA`. `plugins --action marketplace-*`, `search` e `install --id <id>@<marketplace>` recusam com `IO_ERROR`; `capabilities` e `doctor` seguem e relatam o motivo em `marketplaces_error`; `plugins --action update --all` segue como prévia e põe o plugin daquele marketplace em `skipped`, com o mesmo motivo. |
| `LOCKED` | `1` | Outro processo segura uma trava: a do projeto (`brolls/.command.lock`), a de `analysis/`, a do runtime (`setup`, `.getbrolls-setup.lock`), a dos plugins (`$GB_HOME/.plugins.lock`), a dos clientes (`$GB_HOME/.clients.lock`), a das receitas de um cliente (`recipes/.lock`), a dos perfis confiáveis (`$GB_HOME/trusted-profiles.json.lock`) ou a do índice da biblioteca pessoal (`$GB_HOME/library/index.lock`). Repetir depois resolve. |
| `USAGE_ERROR` | `2` | Erro do argparse ou `UsageError` explícito (`.env` recusado, perfil não confiável, combinação de flags inválida). |
| `INTERNAL_ERROR` | `3` | Dentro de um comando, qualquer exceção que não seja `ValueError`, `OSError` nem `OverflowError` (`KeyError`, `IndexError`, `TypeError`, `AttributeError`, `RuntimeError`…); fora dele, qualquer exceção: assinatura de bug. |
| `PREREQUISITE_MISSING` | `4` | `PrerequisiteError`: ferramenta, arquivo de dados ou requisito do perfil ausente. |
| `INTERRUPTED` | `130` | Ctrl+C. |

`ValueError` genérico depois do parse continua sendo `INVALID_DATA` mesmo quando, na
prática, é uso errado: só um `UsageError` explícito vira `USAGE_ERROR`.

## Erro de uso

Erro do argparse (subcomando, flag ou `choices` errado, opção obrigatória faltando)
sai com código `2` no stderr, em um de dois formatos, decidido só pelo stderr:

- **stderr fora de um terminal** (agente, script, `2>` para arquivo): uma linha JSON
  com exatamente estas chaves (exemplo de `getbrolls status --projct x`; sem nenhuma
  flag parecida digitada, `suggestion` é `null`):

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
| `reason_code` | Só numa recusa de dados com motivo estável (`error_code` `INVALID_DATA`, saída `1`): o nome do motivo, da seção "Motivos de recusa". Ausente nos outros erros. |
| `message` | A mesma mensagem, como gravada no evento de auditoria. |
| `operation` | O subcomando que falhou. |
| `status` | `error` ou `interrupted`. |
| `at` | Data e hora do início do comando. |
| `state_committed` | `true` quando o estado já foi gravado antes da falha (a página de revisão é que falhou). |
| `recovery_pending` | `true` quando ficou uma gravação pendente (`brolls/.pending-transaction.json`) que o próximo comando retoma. |
| `recovered` | `true` quando o comando, antes de falhar, concluiu a gravação interrompida de um comando anterior (aviso `RECOVERED_WRITE`). Essa gravação não é do comando que falhou: não liga `state_committed`. |
| `warnings` | Avisos acumulados até a falha (veja [Avisos](#avisos-warnings)). |
| `type` | Nome da classe da exceção, sem o texto dela. |
| `log` | Caminho do `diagnostics.jsonl` que guardou o evento, ou `null`. |
| `app_log` | Caminho do `getbrolls.log` que recebe as linhas do comando: o do projeto ou, para quem muda a instalação (com `--project` ou não), o da instalação (`$GB_HOME/logs/getbrolls.log`). Comando que não grava log aponta o do projeto ou, sem projeto, o da instalação. `null` quando o arquivo não existe, não dá para conferir ou é um link. |
| `hint` | Só quando há o que fazer além de corrigir e repetir: com `recovery_pending`, com `state_committed` (rode `review`; nunca no `roteiro`; no `deliver` que falhou nomeando arquivos em conflito, resolva esses arquivos e rode `deliver` de novo) ou com `recovered` sem `state_committed` (a gravação retomada era de outro comando; este falhou sem gravar o estado do projeto). |

Erro levantado antes do comando começar (`.env` recusado ou ilegível, pasta do perfil
ou do `$GB_HOME` sem permissão, perfil não confiável) sai com exatamente `error`, `error_code`, `message`, `operation` e
`status`. O erro interno sai com `error`, `error_code`, `type`, `log` e
`app_log`, e a mensagem cita o tipo (`[type: KeyError]`).

**Nunca há traceback nem `repr` no JSON de erro**, em nenhum comando: os dois ficam,
com segredos redigidos, em `brolls/diagnostics.jsonl` do projeto (ou
`$GB_HOME/diagnostics.jsonl` sem projeto ou numa pasta ainda sem `brolls/`). A
mensagem do erro interno e o campo `log` citam o arquivo que de fato guardou o evento.

Nos comandos que não gravam no manifesto do projeto — `plugins`, `x`, `export`,
`assets`, `client`, `analysis`, `template` e `recipe` — o erro sai sem `hint`: a dica de
retomada ou de `review` não se aplica a eles.

## Motivos de recusa (`reason_code`)

Algumas recusas de dados (`error_code` `INVALID_DATA`, saída `1`) trazem também `reason_code`: um nome
estável em MAIÚSCULAS para quem automatiza. O texto de `error` pode mudar; o motivo, não. É um enum
aberto: um motivo novo é aditivo, e quem não o conhece trata a recusa como as outras. Cada motivo entra
nesta tabela no mesmo commit do comando que o usa.

Onde o motivo aparece: no JSON de erro de operação (`reason_code`, tabela acima), no JSON de erro do
[`serve` em primeiro plano](#serve-em-primeiro-plano), no evento do `diagnostics.jsonl` e na linha
`command_end` do log do app, que o leva como `reason=<MOTIVO>` (só quando existe). Uma recusa com motivo
levantada dentro de um bloco que converte `ValueError` em erro de uso segue como recusa de dados: sai com
`INVALID_DATA`, código `1` e o `reason_code`, nunca como `USAGE_ERROR`.

| Motivo | Comando | Quando |
|---|---|---|
| `UNSAFE_INPUT` | todo comando que lê arquivo de fora pelo leitor único (a volta de um ambiente, um arquivo copiado para o projeto) | o arquivo ou uma pasta do caminho é link ou junção, não é arquivo comum, passa do teto, tem mais de um nome no disco (texto), tem nome recusado, não é texto UTF-8 ou não é um objeto JSON válido |

## Fila (`queue`)

`queue --action next` devolve `item`, `wait_seconds`, `resume_at` e `reason`. Só há `resume_at` quando o que
segura a fila é ritmo; uma parada por recusa nunca traz horário, porque esperar não autoriza nada.

| `reason` de `next` | O que é | O que fazer |
|---|---|---|
| `pace`, `max_per_hour`, `max_per_day` | Ritmo ou teto da fila. | Aguarde `wait_seconds` e chame `next` de novo. |
| `cooldown` | Limite temporário da fonte (429, `rate limit`). É ritmo, não liberação: passar do `resume_at` não devolve acesso nenhum. | Aguarde e volte ao mesmo escopo; se o bloqueio voltar, pare. |
| `active` | Já há um item entregue esperando `mark`. | Feche o item com `mark`. |
| `empty` | Nada pendente. Um provedor parado sem pendentes também dá `empty`; com pendentes, `next` recusa (veja abaixo). | Nada a esperar. |

`queue --action next` com um `RULES.md` inválido (ou ilegível) recusa: sai com `1`, `error_code` `INVALID_DATA`, e nenhum
item é entregue, porque o ritmo do `RULES.md` não pode virar o padrão mais rápido em silêncio. Sem `RULES.md` o ritmo
padrão vale, como sempre. `status`, `queue add`, `queue mark`, `queue resume` e `queue status` seguem com o aviso
`RULES_UNAVAILABLE`.

Um `work/queue.json` com `providers.<provedor>` que não é objeto, um `cooldown_strikes` que não é inteiro ou um
timestamp sem fuso horário é fila inválida em todos os comandos da fila: `INVALID_DATA`, saída `1` (em `status`, o
campo `queue.error`, com saída `0`).

`mark --failed` e `mark --skipped` leem o `--reason` em texto livre e o põem em um de três tipos. Uma recusa
vence uma palavra de limite no mesmo texto: um 403 de causa desconhecida não vira "espere e volte".

| `--reason` contém | Tipo | Efeito na fila |
|---|---|---|
| `429`, `rate limit`, `too many requests`, `limite de requisições` | limite de ritmo | Abre o cooldown de 30 min, dobrando até 4 h; `cooldown` no resultado, `resume_at` em `next`. |
| `login`, `challenge`, `captcha`, `checkpoint`, `401`, `403`, `unauthorized`, `forbidden`, `access denied`, `acesso negado`, `sessão de acesso`, `bloqueou o IP`, `IP bloqueado` | recusa de acesso | Sem cooldown e sem `resume_at`: o resultado traz `cooldown: null` e `stop` (`kind: "access_refused"`, `class`, `at`), e a fila grava `providers.<provedor>.stopped` (`at`, `class`, `item`). |
| qualquer outro texto | falha comum | Só o item falha; nada mais muda. |

Com `stopped` gravado, `next` recusa em vez de esperar quando só restam pendentes desse provedor: sai com `1`,
`error_code` `INVALID_DATA` e sem `reason_code`, com a mensagem "Fila de `<provedor>` parada em `<instante>`", e
nenhum item é entregue desse provedor (nem um item que ficou `active` sob ele). Os outros provedores seguem; a
espera de `next` que vale para um deles traz a chave `stopped` (a lista dos provedores parados) e diz na linha que
a espera não cobre o parado. `status` mostra `providers.<provedor>.stopped` e a linha da fila diz que está parada.
`classe` é um balde grosso (`http_403`, `http_401`, `challenge`, `login`, `ip_block`, `refused`), nunca o texto do
motivo.

A mensagem da recusa mostra só um modelo de retomada, com `NOME`, `"FRASE EXATA"` e `AT_DA_PARADA` por preencher,
nunca um comando pronto. Quem limpa a parada é a pessoa, depois de confirmar no chat que o acesso voltou, com
`queue --action resume --provider PROVEDOR --by NOME --phrase "FRASE EXATA" --expect AT`. Marca de gravação
`P`: sem `--by`, `--phrase` (a frase exata da pessoa, guardada literalmente; não se chama `--statement` porque `--s` abrevia `--skipped` desde a 2.5) ou `--expect`, ou com um `--expect`
que não é o `at` da parada que está gravada agora (o de `status`, em `providers.<provedor>.stopped.at`), recusa
com `INVALID_DATA`, saída `1`, sem `reason_code` e sem mudar nada; assim um comando antigo ou repetido não limpa
uma parada nova. Sem `--provider`, ou com um provedor que não está parado, recusa do mesmo jeito. A ação tira só a
chave `stopped` desse provedor de `work/queue.json` (cooldown, contagens, ritmo e itens ficam como estavam),
grava `providers.<provedor>.last_resume` (`at`, `by`, `statement` (a chave guardada se chama `statement`, embora a flag seja `--phrase`), `stopped_at`, `class`: quem limpou e com que
frase, no `queue.json` privado) e, no log, `event=op` de `fs.write` e `event=queue_resume` com a classe e
`statement_present=True`, nunca a frase. Devolve `action`, `provider`, `by`, `resumed` (`at`, `class`, `item` da
parada que saiu) e `summary.do`, o `queue --action next` do mesmo provedor; `queue` (o caminho do
`work/queue.json`) fica absoluto, como nas outras ações da fila. Nada disso é criptográfico: um agente poderia
inventar a frase, e a regra é que o agente nunca a roda por conta própria; a diferença é que a violação deixa
rastro (quem e com que palavras) em vez de um comando mudo.

O que a parada fecha e o que não fecha. Ela é um registro no `work/queue.json` do projeto que segura o `next` e,
com `--project`, o coletor `instagram_pairs.py` (que lê a fila, sem gravar, antes de qualquer pedido e sai com `1`,
todos os stems `skipped` com motivo `aborted`). Não é uma trava:

| Caminho | Situação |
|---|---|
| Esperar (nenhum `resume_at`), `mark --done` de outro item, `queue add` de um lote novo do mesmo provedor no mesmo projeto, um `stopped` corrompido | Fechado: a parada fica e `next` recusa (um `stopped` corrompido é fila inválida, nunca passe livre). |
| `queue --action resume` | Fechado para quem não declara `--by`, `--phrase` e o `--expect` certo; aberto para quem inventa a frase (regra do agente, com rastro). |
| Editar ou apagar o `work/queue.json` | Aberto: o `add` recria a fila sem a parada. O agente não faz isso. |
| Outro projeto para o mesmo provedor | Aberto: a parada é por projeto. |
| O coletor sem `--project` | Aberto: não há fila para ler, a parada nunca é gravada (só o aviso "parada não registrada"), e o lote sai fora dela. |

O coletor `instagram_pairs.py` usa as mesmas três saídas para um `curl --fail` que volta 401, 403 ou 429, e a
mensagem nunca leva a URL:

| Resposta da CDN | Resultado | Lote | Fila |
|---|---|---|---|
| 429 | limite de ritmo | Para; `stopped_by` `cooldown`, o resto `skipped` com motivo `cooldown`. | Cooldown registrado. |
| 403 com `oe` da URL assinada vencido há mais de 300 s | URL expirada: "recapture pela mesma rota autorizada" | Falha o item e, com `--continue-on-error`, para ali: `stopped_by` `recapture`, o resto `skipped` com motivo `recapture`, sem um pedido por item (sem a flag, termina como qualquer erro fatal). | Nada. |
| 403 sem `oe`, com `oe` ilegível, repetido, zero, no futuro ou vencido há 300 s ou menos, ou 401 | recusa | Termina como qualquer erro fatal (o resto `skipped` com motivo `aborted`, saída `1`), com ou sem `--continue-on-error`. | Parada registrada, sem cooldown. |

O `oe` é o instante de validade em hexadecimal, em segundos Unix. A expiração só vale quando ele é legível,
único, diferente de zero e vencido há mais de 300 s (a margem cobre um relógio adiantado); sem isso o 403 é
recusa, e a conclusão é parar e diagnosticar. Nenhum `error_code` nem
`reason_code` novo.

O coletor com `--project` consulta a fila antes de cada pedido, não só no início: uma parada **ou um cooldown em
vigor** gravado por outro processo, mesmo no meio do lote, o faz terminar (saída `1`, o resto `skipped` com motivo
`aborted` para a parada e `cooldown` para o cooldown; `stopped_by` `cooldown` neste caso). Uma fila ilegível, ou o
módulo da fila indisponível, também recusa: sem saber, não se pede. O `curl` do coletor não tenta de novo
(sem `--retry`): um 429 custa um pedido por item, não três.

O resumo do lote (`--summary-json` e o JSON impresso) ganha o booleano `refusal_not_recorded`. Fica `true` quando um
401/403/429 não pôde ser gravado em `work/queue.json`, porque outro comando segurava a trava do projeto por mais de
5 s ou porque a gravação falhou (qualquer erro do sistema: disco cheio, pasta só de leitura, arquivo preso por outro
programa); o lote então termina com saída `1`, a mensagem diz que a recusa não foi registrada e o próximo lote não saberá dela
(confirme a fila com a pessoa antes de repetir). Sem esse problema o campo é `false`. Nenhum `error_code` nem
`reason_code` novo.

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
| `DELIVERY_COPY_READ_ONLY` | Uma cópia independente em `entrega/` não pôde ficar editável. |
| `DELIVERY_FREEZE_FAILED` | O arquivo entregue não pôde ficar somente leitura. |
| `DELIVERY_LINK_FAILED` | Os arquivos estão íntegros, mas `entrega/` não pôde ser refeita. |
| `DELIVERY_SWEEP_RMDIR_FAILED` | Uma pasta vazia de `entrega/` não pôde ser apagada. |
| `DEPRECATED` | Algo que ainda funciona, mas sai numa versão futura (veja [Deprecação](#deprecação)). |
| `EMPTY_STORYBOARD` | `review` gerou um Storyboard sem itens. |
| `ENUM_UNKNOWN` | Um campo de enum aberto (tipo de componente, nível) trouxe um valor que esta versão não conhece: esse item não é usado (mas o `recipe --action show` confere os bytes dele como os dos outros) e o resto segue. |
| `ENV_FILE_SHADOWED` | Há dois `.env`; só o primeiro da ordem foi lido. |
| `ENV_GB_HOME_IGNORED` | O `.env` lido define `GB_HOME`, mas o `home` do perfil confiável já decidiu a pasta pessoal; a linha do `.env` foi ignorada. |
| `ENV_VALUE_IGNORED` | `GB_OUTPUT` tem um valor que não é `json`; ele é ignorado e a saída continua JSON. |
| `FFMPEG_PROBE_FAILED` | Os filtros do ffmpeg não puderam ser sondados; `drawtext` tratado como indisponível. |
| `LIBRARY_INDEX_UNREADABLE` | A biblioteca de aprendizados não pôde ser lida; as pistas dela ficaram de fora. |
| `LIBRARY_WRITE_FAILED` | Um aprendizado não pôde ser gravado na biblioteca. |
| `LICENCE_NOT_TRANSFERRED` | `init --recipe` copiou um componente sem a licença dele. |
| `LOG_UNAVAILABLE` | `diagnostics.jsonl` não pôde ser gravado. |
| `MARKETPLACE_ENTRY_INCOMPATIBLE` | Uma entrada do índice de um marketplace ficou de fora: esta versão não entende uma chave, um tipo de extensão ou o id dela. As outras entradas seguem. |
| `MEDIA_PROBE_FAILED` | `analysis --action register`: o ffprobe não leu a mídia (o comando sai com `1`). |
| `MIGRATE_BROLL_EXISTS` | `migrate`: já existe uma `broll/` com arquivos, que vira a pasta dos clipes finais. |
| `OLD_SHAPE` | Uma pasta ou arquivo da versão de testes da 2.6 (`assets/composicoes/` do projeto, `components/` e `templates/` do cliente, `template.lock.json`, as variáveis `GETBROLLS_X` do ambiente, só no `doctor`) não é lido; a mensagem traz o conserto à mão (renomear ou mover a pasta, apagar o arquivo, ou trocar `GETBROLLS_X` por `GB_X`); o `migrate` só adota o layout 1 e não move pastas. |
| `PLUGIN_ENV_TOOLCHAIN` | Uma variável de plugin no `.env` tem nome que ferramentas do sistema leem; ela chega só ao plugin. |
| `PLUGIN_PATH_CHANGED` | Uma pasta de `permissions.paths` não confere com a gravada no pin e fica ignorada. |
| `PLUGIN_SELECTION_BLOCKS` | `plugins --action install` instalou e habilitou o plugin, mas a seleção da sessão (o `plugins` do perfil ou o `GB_PLUGINS`) o deixa desligado. |
| `PLUGIN_PIN_OUTDATED` | O pin de um plugin é de uma versão antiga; rode `plugins --action enable` de novo. |
| `PREVIEW_LIMITATION` | A prévia foi gerada com uma limitação (só referência, por exemplo). |
| `PROVIDER_ERROR` | Uma fonte falhou e o comando terminou em erro. |
| `PROVIDER_FAILED` | Uma fonte falhou durante a busca; as outras seguiram. |
| `RECIPE_CLUTTER` | O `recipe --action show` achou lixo do sistema numa versão selada (um `.DS_Store`, ou o `._<nome>` do macOS ao lado de um arquivo da versão): não é aberto nem entra em sha256, e a versão segue íntegra. Qualquer outro item a mais continua sendo problema. |
| `RECIPE_FREEZE` | A selagem da receita gravou a versão com uma ressalva (componente citado e não achado, arquivo ou pasta que não ficou somente leitura). |
| `RECIPE_LOCK_DRIFT` | Um item achado hoje não bate com o `recipe.lock.json` (outro sha256, sumiu, ou o lock não é do `project.json`). |
| `RECOVERED_WRITE` | Uma gravação interrompida foi retomada antes do comando. |
| `REFERENCE_POSTER_MISSING` | A referência ficou sem imagem de cartaz. |
| `RULES_LAYER_IGNORED` | Uma camada do `RULES.md` foi ignorada. |
| `RULES_UNAVAILABLE` | `queue` (`add`, `mark`, `resume`, `status`) seguiu sem o `RULES.md` para o ritmo; `queue next` recusa (veja "Fila"). |
| `SOURCE_CACHE_STALE` | O cache de fonte de um candidato tem sha divergente e não foi reutilizado. |
| `SOURCE_INDEX_UNREADABLE` | O índice de fontes privadas estava ilegível e foi tratado como vazio. |
| `YTDLP_WARNING` | Aviso repassado do yt-dlp. |

`analysis --action check` devolve, além disso, uma lista própria de `problems`, cada
um com `code` (`MEDIA_ID_MISMATCH`, `MISSING_FILE`, `ORPHAN_MEDIA`,
`SCHEMA_VIOLATION`, `UNKNOWN_FILE`, `UNSAFE_LINK`) e o arquivo.

## Linhas `event=op` do `getbrolls.log`

Todo efeito colateral do core — arquivo gravado, trocado ou apagado, pasta criada,
processo rodado ou encerrado, pedido de rede, servidor local, código de plugin
carregado — passa por um só caminho (`getbrolls.effects`) e deixa **uma** linha
`event=op` no `getbrolls.log` que o comando já usa (o do projeto ou o da instalação),
com a mesma redação e rotação das outras linhas. Um teste estrutural recusa a primitiva
chamada por fora desse caminho.

| Campo | O que traz |
|---|---|
| `operation` | Nome curto e fixo `<área>.<verbo>`: `fs.write`, `fs.replace`, `fs.rename`, `fs.unlink`, `fs.rmdir`, `fs.rmtree`, `fs.mkdir`, `fs.copy`, `fs.link`, `proc.run`, `proc.spawn`, `proc.kill`, `net.get`, `net.download`, `plugin.load`, `plugin.install`, `plugin.remove`, `code.import`, `server.start`, `server.stop`, `server.save`, `ledger.commit`, `migrate.apply`, `deliver.place`, `export.place`. |
| `target` | O alvo, redigido (regras abaixo). |
| `outcome` | `ok`, `error` (erro de E/S, ferramenta que falhou ou bug), `refused` (a operação recusou: dado inválido, trava ocupada, uso errado) ou `skipped` (não havia o que fazer, ou uma tentativa falhou do jeito esperado e o comando seguiu por outro caminho: um hardlink entre volumes que vira cópia, um nome já tomado que vira o número seguinte; aí a linha é INFO e traz `fallback` com o motivo, como `exdev` ou `eexist`). |
| `duration_ms` | Duração em milissegundos. |
| `error_code` | Na falha, o código da tabela [Códigos de erro](#códigos-de-erro) que a exceção tem: `IO_ERROR` (arquivo, pasta, rede), `INVALID_DATA` (dado recusado, ou ferramenta externa que falhou ou estourou o tempo, como quando o comando a transforma em erro de provedor), `LOCKED`, `USAGE_ERROR`, `PREREQUISITE_MISSING`, `INTERRUPTED`; `INTERNAL_ERROR` só para bug. `-` no sucesso. |
| `error` | Na falha, só o nome da classe da exceção, nunca a mensagem; `-` no sucesso. |
| `command` | O subcomando que rodou a operação, para juntar com `command_start`/`command_end`. |
| extras | Só estes, quando fazem sentido: `exit` (código de saída do processo), `bytes`, `count`, `errors` (falhas dentro de um conjunto), `fallback`, `sha12`, `pid`, `port`. |

Regras do `target`: um caminho sai relativo ao projeto (`brolls/manifest.json`), ao
`$GB_HOME` (`$GB_HOME/plugins.json`), ao cache (`$GB_CACHE_DIR/…`) ou a um
`$GB_RUNTIME_DIR` explícito. Cada parte do caminho que não é um nome que o próprio core
dá (`brolls`, `entrega`, `manifest.json`, `ORIGEM.md`…) vira `#` e 8 hexadecimais de um
HMAC-SHA256 dela, com uma chave sorteada por instalação (`$GB_HOME/.log-salt`, 0600, criada na
primeira linha que precisa dela e nunca gravada no log): a pasta do beat feita do título, o
clipe com o nome do shot, o id do candidato e a pasta de um cliente nunca aparecem
(`entrega/#3fa2c1d9/#91bc02e4.mp4`), um dicionário de nomes não desfaz o hash sem a chave, e
na mesma máquina as linhas do mesmo arquivo continuam batendo. Só o último nome guarda a
extensão, e só uma conhecida (`mp4`, `mov`, `jpg`, `json`, `md`, `srt`…); uma pasta como
`joao.silva` não guarda nada. A mesma chave dá o hash do nome de um arquivo de fora de toda pasta
conhecida numa mensagem, numa linha de log ou no `diagnostics.jsonl` (`<pasta fora do projeto>/#3fa2c1d9.mp4`),
mas só quando ela já existe: nomear um arquivo nunca cria a chave (um comando que só lê, como `status` numa
pasta travada, não grava nada no `$GB_HOME`). Sem a chave, o processo usa uma só dele, sem gravá-la nem
avisar, e esse hash muda de uma execução para outra. Um arquivo dentro do `--project` digitado (ou uma pasta
acima dele) num erro de E/S sai como a pessoa o digitou (`~/travada/proj/brolls`, `~/travada`).
A chave nasce na primeira linha que precisa dela: se o `$GB_HOME` ainda
não existe, essa linha o cria (0700) só para guardar o `.log-salt`. Uma chave que é link (nunca
seguido), uma pasta só de leitura ou um sistema sem como gravá-la fazem o processo usar uma chave
só dele e avisar, uma vez para cada motivo, com
`event=log_salt_unavailable reason=link|readonly|unreadable|nolink|busy|malformed` (mais `last=` e `errno=`, o que
falhou por último; o campo `reason` é de log, não um `reason_code` do contrato);
uma chave malformada é avisada (`malformed`) e trocada por uma nova (gravada à parte e posta no lugar de uma
vez), sob uma trava (o `flock` do próprio `$GB_HOME`; no Windows, `$GB_HOME/.getbrolls-salt.lock`),
para vários processos nunca ficarem com chaves diferentes: quem perde o conserto (no Windows, uma violação de
compartilhamento ou de trava enquanto outro processo troca o arquivo, ou o arquivo no meio da troca) lê de novo o
arquivo, por cerca de 5 s, e termina com a chave gravada; só se esse prazo acabar (`reason=busy`) usa uma só dele,
e uma falha que não é espera por outro processo desiste com o motivo verdadeiro depois de poucas tentativas
(`readonly`: a pasta não aceita um arquivo; `unreadable`: o arquivo não pode ser lido). Sem hardlink (exFAT, alguns
SMB), a chave é criada direto no nome final com `O_EXCL`. O
`doctor` mostra o estado em `install.log_salt` (`ok`, `missing`, `readonly` — a chave não
existe e a pasta que a guardaria não aceita um arquivo, o que ele confere criando e apagando um arquivo de prova
—, `link`, `malformed`, `unreadable`). Fora dessas pastas (a mídia da pessoa, a
pasta de um cliente, uma pasta temporária), só o hash do caminho inteiro
(`#a3e1d325.mov`). Uma URL sai só pelo host (`api.pexels.com`), como em todo o log: o
caminho dela pode ser a própria credencial (um webhook, um link de compartilhamento, o id de
um arquivo). Um processo sai só com o nome do
executável, sem `.exe` (`yt-dlp`), nunca os argumentos; um comando dado como um texto só
(`shell=True`) sai pela primeira palavra. Plugin sai pelo id, e o endereço do servidor como
`127.0.0.1:<porta>`; um texto que não tem forma de id (com espaço, `=`, longo demais ou com cara
de token, como `ghp_…` e `sk-…`) sai só como hash. Nunca entram
conteúdo de arquivo, token, valor de variável de ambiente nem a linha de comando.

`proc.run` com `outcome=ok` quer dizer que o processo rodou; o resultado dele está em
`exit`. Operação que não muda nada (pasta que já existe, arquivo que já não existe) não
gera linha. Um conjunto de gravações de uma vez (o manifesto e cada candidato) sai como
uma linha `ledger.commit`, com `count`; uma operação que falha dentro do conjunto ganha a própria
linha de aviso e conta em `errors`, mesmo quando o comando segue. O log nunca derruba a operação: se o arquivo não
dá para gravar, a linha cai e o comando segue. Não geram linha `event=op`: a gravação do
próprio `getbrolls.log` (ele não registra a si mesmo), a do `diagnostics.jsonl` (é o log de
auditoria) e a abertura e o travamento dos arquivos de trava (`.command.lock`,
`.plugins.lock`, `index.lock`…; uma trava ocupada já sai como `lock_busy`). Apagar um arquivo
de trava gera `fs.unlink` como qualquer outro: a trava velha do índice da biblioteca
(`$GB_HOME/library/index.lock`) e a `.command.lock` de um `init` recusado.

Duração: toda linha que mede um tempo traz `duration_ms`. A linha `event=op` traz só ela; as
que já traziam `ms` (`command_end`, `request`, `subprocess`, `fetch`, `setup_*`,
`git_*` e as outras com duração) trazem as duas, com o mesmo valor, nesta versão (`ms` sai na
2.7); as de espera por trava (`lock_busy`, `library_lock`) trazem `waited_ms` e também
`duration_ms`. `wait_s` (a pausa planejada antes de repetir um pedido) não é tempo medido e
fica como está; a linha sem tempo nenhum não ganha o campo. `command_end` ganha também `outcome`
(`ok`/`error`), ao lado do `status` de sempre, e `reason` quando o erro traz `reason_code`. `command` é o comando da CLI, também nas linhas
que o servidor do `serve` grava (`command=serve`).

## Linhas `event=library` e `event=library_lock`

A biblioteca pessoal (`$GB_HOME/library`) deixa duas linhas próprias no `getbrolls.log` do
comando (o do projeto ou o da instalação), com a mesma redação e rotação das outras. Elas
não trazem termo de busca, nota, cliente nem caminho.

`event=library` (nível INFO) sai uma vez por uso da biblioteca:

| Campo | O que traz |
|---|---|
| `action` | `learn` (as três formas de `learn` e a busca sem resultado que o `search` guarda sozinho), `search` (`library --search`) ou `hints` (as pistas `library_hints` que o `search` anexa). |
| `entries` | Quantas entradas foram gravadas (`learn`: `1`) ou devolvidas (`search`: assets, buscas e preferências somados; `hints`: o número de pistas). `0` com a biblioteca desligada. |
| `disabled` | `True` quando `GB_LIBRARY=off` desligou a biblioteca e nada foi lido nem gravado; `False` no uso normal. |

`event=library_lock` descreve a trava do índice (`$GB_HOME/library/index.lock`). Só o caso
normal tem nível DEBUG; o resto é aviso que merece olhar:

| Campo | Nível | Quando sai |
|---|---|---|
| `waited_ms`, `timeout=False` | DEBUG | A trava foi tomada; `waited_ms` é a espera (e `duration_ms` repete o valor). |
| `waited_ms`, `timeout=True` | WARNING | Passaram 10 s sem conseguir a trava: o comando termina com `LOCKED`. |
| `stale_swept` | INFO | Quantas sobras `index.lock.stale.*` com mais de 2 minutos foram limpas por quem tinha a trava. |
| `foreign_lock_kept` | WARNING | Na hora de soltar, o arquivo no nome já era a trava de outro processo; foi deixado como está. |
| `fresh_lock_kept` | WARNING | Ao assumir uma trava velha, a de outro processo, nova, tinha sido tirada por engano e outro processo já pegou o nome: a nova dele fica onde está. Traz `reason`. |
| `fresh_lock_restored` | WARNING | A trava nova de outro processo voltou ao nome, recriada com a mesma idade (sistema sem hardlink). Traz `reason`. |
| `fresh_lock_aside` | WARNING | A trava nova de outro processo não pôde voltar e ficou de lado; o comando recusa com `LOCKED` em vez de entrar junto. Traz `reason`. |

`reason` é a descrição curta do erro do sistema, sem caminho. Trava ocupada por mais de
10 s: espere a outra execução terminar e repita; só apague o `index.lock` se nenhuma
execução estiver em andamento.

## O que só lê

Estes comandos nunca tomam a trava do projeto, nunca criam `brolls/` e nunca gravam
no projeto:

| Comando | Observação |
|---|---|
| `status` | Abre o manifesto sem recuperar pendência. |
| `serve` | Só serve arquivos em primeiro plano. `serve --background` grava `brolls/.serve.pid` e `.serve.log`, e `--stop` apaga o `.serve.pid`. |
| `brief` | Lê `BRIEF.md`, `RULES.md` e o manifesto existente. |
| `doctor` | Diagnostica a instalação; `--project` não cria nada. |
| `setup` | Grava só no runtime (`$GB_HOME/runtime` ou `GB_RUNTIME_DIR`), nunca num projeto. |
| `x` | Comando de plugin, que lê o projeto por cópias. A única gravação no projeto é a do próprio plugin em `analysis/` (componentes de análise e marcadores, pela API do SDK), sob a trava de `analysis/` — nunca a trava do projeto, nunca `brolls/`. |
| `capabilities` | Descreve o parser e os manifestos de plugin. |
| `profile` | `trust`/`untrust` gravam só `$GB_HOME/trusted-profiles.json`. |
| `rules` | Mostra as regras em vigor (`RULES.md` global e do projeto). |
| `library` | `--search` consulta só a biblioteca pessoal (`$GB_HOME/library`), pelo contexto do `--project`: num projeto com `client` no `project.json` (ou, sem `project.json`, o `cliente` do `ROTEIRO.md`; se ele não for um slug, `learn` recusa e a busca fica só no contexto da própria pessoa), a resposta traz `scope: "client:<slug>"` e só as entradas guardadas sob esse cliente ou sem cliente; sem `client`, a chave `scope` não aparece e só valem as entradas sem cliente. Entrada de outro cliente nunca aparece, nem em `library_hints` do `search`, nem nos contadores de `providers` (o contador gravado é só o das entradas sem cliente; o de um cliente é refeito das buscas dele). `learn` grava o `client` do projeto na entrada (e recusa um `project.json` ilegível, para a entrada não valer para todos). |
| `references` | Lê as referências já decididas do projeto, sem recuperar pendência. |
| `providers` | Lista as fontes e o que está configurado; não toca projeto. |
| `environment` | `list` lê só o inventário das pontes oficiais (manifesto e pin), sem rodar código delas. |

E estas ações:

| Comando | Ações que só leem |
|---|---|
| `analysis` | `list`, `check` |
| `assets` | `list`, `where` |
| `client` | `list`, `show` |
| `migrate` | `plan` |
| `plugins` | `list`, `marketplace-list`, `search` |
| `queue` | `status` |
| `roteiro` | `check`, `plan` |
| `recipe` | `list`, `show` |
| `template` | `freeze`, `list`, `show` (só recusam com o comando novo) |

`plugins --action check` não está na lista: ele executa o `register()` do plugin (código de terceiros, sem
sandbox), e o core não garante que esse código não grave. A marca dele é `G`.

`analysis --action register` grava só sob a trava própria (`analysis/.lock`), nunca
sob a do projeto. `init` e `migrate` só tomam a trava depois de validar tudo: um
`init` recusado não deixa pasta, `brolls/` nem trava para trás.

Os comandos de etapa — `preview`, `approve`, `permit`, `reject`, `fetch`, `verify`,
`review`, `import-review`, `remember` e `deliver` — nunca criam a pasta do projeto. Com
um `--project` que não existe (um typo), recusam com `INVALID_DATA` ("Projeto não
encontrado em … (a pasta não existe); nenhum arquivo foi criado") antes da trava e do
log, e o evento vai para `$GB_HOME/diagnostics.jsonl`. Numa pasta que já existe, mesmo
sem `brolls/`, rodam como sempre (`review` monta o Storyboard vazio). `search`,
`resolve`, `init`, `init-rules`, `init-brief`, `roteiro` e `queue` continuam criando o
projeto, inclusive a pasta.

`capabilities --json` repete essas listas em `read_only` e `read_only_actions` de
cada comando.

Cada ação tem uma marca de gravação em `scripts/getbrolls/write_marks.py`: `L` (só lê), `0` (só
`$GB_HOME/private`), `G` (grava sem ok, só em área do getbrolls), `OK` (prévia e
`--yes --expect`), `OK1` (o ok só na condição que o comando diz) e `P` (portão humano). Um teste
confere que toda ação da superfície tem uma, e só uma, e que ela concorda com as tabelas de só
leitura do runtime nos dois sentidos. O `browser-plan` toma a trava e grava `brolls/` e
`output/playwright/` (marca `G`), embora o PRD o liste como só leitura; `serve --background` e
`serve --stop` gravam e apagam `brolls/.serve.pid` e `.serve.log` (`G`); `--dry-run` de `deliver`,
`search` e `export` não grava dado nem estado (`L`).

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
| `commands` | Um objeto por comando: `name`, `summary`, `requires_project`, `project_alternatives` (grupos de flags que substituem `--project`, como `[["--client", "--slug"]]` no `init`), `read_only` (`true`, `false` ou `"by_action"`), `read_only_actions`, `options` e `positionals`. Cada opção traz `flags`, `dest`, `required`, `takes_value`, `repeatable`, `choices`, `default`, `type`, `help` e `hidden`. |
| `exit_codes` | A [tabela de códigos de saída](#códigos-de-saída): `code`, `name`, `meaning`. |
| `error_codes` | A [tabela de códigos de erro](#códigos-de-erro): `code` e `exit`. |
| `plugin_commands` | Comandos de plugin habilitado: `plugin`, `command`, `status`, `argv`. |
| `plugins_problems` | Plugin que não está habilitado nem desligado de propósito (inválido, com falha, suspenso…), com `status` e `reason`. |
| `plugins_error` | Só quando o inventário de plugins não pôde ser lido. |
| `marketplaces` | Marketplaces fixados: nome, commit do índice, quantos plugins, `allowed` pelo perfil, `official` (se é o índice oficial) e um eventual problema do cache. |
| `marketplaces_error` | Só quando `$GB_HOME/marketplaces.json` não pôde ser lido. |
| `environments` | As pontes de ambiente que vêm com o getbrolls (hoje `hyperframes`): `id`, `version`, `kind` (`bridge`), `readback` (`files`), `origin` (`official`), `sha256` (o pin do core) e `status`; a mesma lista de `environment --action list`, sem o motivo. |

`capabilities` só acrescenta chaves; uma chave existente não muda de sentido sem
subir `schema_version`.

Sem os arquivos de dados da instalação (schemas, plugins oficiais), `capabilities`
não tem o que descrever: sai com `4` e `error_code: "PREREQUISITE_MISSING"`, como
os outros comandos. `doctor` continua respondendo e diz o que reinstalar.
Com uma venv em uso (`GB_VENV_PATH`, `GB_RUNTIME_DIR`) e sem esses arquivos, o bloco `venv` do `doctor` traz `status: "skipped"` e `reason: "install_data_missing"`: sem os pinos não há o que comparar, e o item "dados da instalação" de `summary.missing` é quem manda.

## Variáveis de ambiente

Os nomes são `GB_*`. O único nome antigo que ainda vale é `GETBROLLS_CACHE_DIR` (da 2.5, só do
ambiente do processo, em deprecação). No `.env`, só o nome `GB_*` é aceito.

| Variável | Para quê |
|---|---|
| `GB_HOME` | Pasta pessoal (`~/.getbrolls`): `RULES.md` global, biblioteca, plugins, clientes, runtime. Defina no ambiente; num `.env` está deprecada. |
| `GB_ENV_FILE` | Qual `.env` ler. Só do ambiente do processo. |
| `GB_PROFILE` | Caminho do `getbrolls.toml` ou `off`. Só do ambiente do processo. |
| `GB_PROFILE_SHA256` | sha256 do perfil que o processo pai conferiu, repassado aos filhos. Só do ambiente do processo. |
| `GB_RULES_FILE`, `GB_BRIEF_FILE` | Caminho alternativo do `RULES.md` e do `BRIEF.md`. |
| `GB_LIBRARY` | `off` desliga a biblioteca global. |
| `GB_PLUGINS` | Lista de ids, entre os habilitados com pin válido e os oficiais, que a sessão carrega; `off` desliga todos, a ponte oficial também (o `--env` dela então sai com `4`). |
| `GB_RUNTIME_DIR` | Pasta com `.venv/` (yt-dlp) e `.tools/` (Playwright). |
| `GB_CACHE_DIR` | Pasta do cache local (o nome antigo `GETBROLLS_CACHE_DIR` continua lido do ambiente). |
| `GB_FFMPEG_PATH`, `GB_FFPROBE_PATH`, `GB_YTDLP_PATH`, `GB_VENV_PATH` | Fixam executáveis e a venv: caminho explícito sempre vence a descoberta, e um pin inválido é erro. |
| `GB_PREVIEW_MODE`, `GB_GIF_SCOPE`, `GB_GIF_WIDTH`, `GB_GIF_FPS`, `GB_GIF_COLORS`, `GB_GIF_MAX_MB`, `GB_PREVIEW_MAX_SECONDS`, `GB_SCAN_MAX_SECONDS`, `GB_STATIC_FRAMES` | Prévias. |
| `GB_FONT_FILE` | Fonte TrueType dos rótulos do contact sheet. |
| `GB_PACE_MIN_S`, `GB_PACE_MAX_S`, `GB_MAX_PER_HOUR`, `GB_MAX_PER_DAY`, `GB_YTDLP_SLEEP` | Ritmo da fila social e pausas do yt-dlp. |
| `GB_LOCK_WAIT_S` | Segundos que um comando espera o projeto ocupado por outro antes de `LOCKED` (0 a 3600; padrão 0, recusa na hora). |
| `GB_DELIVERY_COPY` | `1` faz o `deliver` copiar em vez de criar hardlink. |
| `GB_LOG_LEVEL` | Nível do `getbrolls.log` do projeto e da instalação (`DEBUG`, `INFO`, `WARNING`, `ERROR` ou `off`). |
| `GB_LOG_STDERR` | `1` espelha o `getbrolls.log` no stderr. |
| `GB_OUTPUT` | `json` força a saída de máquina (hoje a saída já é sempre JSON); outro valor não muda nada, mas o comando avisa `ENV_VALUE_IGNORED`. |
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

### Proxy no `doctor`

`doctor` traz `install.proxy`, uma chave aditiva e opcional: só aparece quando há variável de proxy no ambiente (quem lê ignora chave que não conhece):

```json
{"env": ["HTTPS_PROXY", "NO_PROXY"], "honored_by": ["curl", "yt-dlp", "browser"], "ignored_by": ["http"],
 "hint": "curl e yt-dlp usam o proxy do ambiente nas transferências HTTPS; o navegador segue o proxy do sistema, não o do ambiente; o transporte HTTP interno conecta direto"}
```

- `env` lista, em ordem alfabética, os nomes postos (e não vazios) entre `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` e
  `NO_PROXY`, na grafia em que estão no ambiente (`https_proxy` também vale para o `curl` e o `yt-dlp`). Nunca traz o
  valor, que pode levar usuário e senha. Sem nenhuma dessas variáveis, a chave `install.proxy` não existe.
- `honored_by` e `ignored_by` são fixos: o `curl`, o `yt-dlp` e o navegador, que o getbrolls só dispara, são quem pode
  usar um proxy; o transporte HTTP interno (`http`, o das APIs de fonte) ignora o do ambiente de propósito e conecta
  direto ao endereço que conferiu. A lista não promete que cada um use a variável posta: o `curl` e o `yt-dlp` só
  aplicam `HTTPS_PROXY` e `ALL_PROXY` (em qualquer caixa) às transferências HTTPS do getbrolls, e `HTTP_PROXY` só vale
  para endereços `http://`; o navegador segue o proxy do sistema, não o do ambiente.
- `hint` só existe quando `HTTPS_PROXY` ou `ALL_PROXY` (em qualquer caixa) está posto; `HTTP_PROXY` e `NO_PROXY`, sozinhos,
  aparecem em `env` sem o aviso.

O proxy não muda `ready`, `summary` nem o código de saída do `doctor`, e não cria `error_code` nem `warnings`.

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
`.env`; vale até a 2.7, com aviso `DEPRECATED` quando é ele que decide a pasta); a chave `ms` das linhas do `getbrolls.log`, que sai na 2.7 (use `duration_ms`,
já gravada junto com ela; o log não é saída da CLI, então não há aviso `DEPRECATED`).

Nome da versão de testes da 2.6 (que nunca saiu numa versão publicada) não ganha alias: recusa com
o caminho novo (ou, quando o comando novo ainda não existe, diz que não há substituto nesta versão), ou, para o que está em disco, gera `OLD_SHAPE` com o conserto à mão. As
variáveis `GETBROLLS_X` (fora `GETBROLLS_CACHE_DIR`) não valem, como na 2.5; só o `doctor` avisa
`OLD_SHAPE` com o nome de cada uma que ainda estiver no ambiente.

### Reservado para a 2.7

Um envelope comum para todo resultado, `{"schema_version", "ok", "data", "error"}`,
está reservado e chegará de forma aditiva. Até lá, o resultado é o JSON do próprio
comando, como descrito acima.

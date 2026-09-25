---
type: reference
status: current
created: 2026-09-25
updated: 2026-09-25
tags: [get-brolls, roteiro, reels, componentes]
---

# Roteiro — do tema aos beats

Use quando a pessoa pede o conteúdo pronto (um reels com cenas e fala), não só b-roll. O `ROTEIRO.md` é dela: você redige, ela revisa. Com roteiro no projeto, os beats do `BRIEF.md` nascem do roteiro; ninguém os escreve à mão.

Só conta como roteiro do get-brolls o `ROTEIRO.md` cujo frontmatter tem `type: roteiro` (o `new` já escreve assim). Um `ROTEIRO.md` que a pessoa já tinha, sem esse frontmatter, é só um arquivo dela: o brief segue a entrevista completa e exige pelo menos um beat.

> **Caminhos.** Os exemplos escrevem `scripts/gb.py` por brevidade. Rode sempre pelo **caminho absoluto da instalação da skill** (no plugin, `${CLAUDE_PLUGIN_ROOT}/scripts/gb.py`) e passe `--project` com a pasta absoluta do usuário em todo comando. No Windows, use `python` no lugar de `python3`.

## Fluxo

1. Pergunte gênero (hoje só `reels`), tema, público e duração — uma pergunta por mensagem.
2. `python3 "scripts/gb.py" roteiro --action new --genero reels --tema "..." --project <projeto>` cria o esqueleto (Gancho, Problema, Prova, CTA) e as pastas `aroll/` e `assets/`. Se o `ROTEIRO.md` já existe, edite o que está lá; `--force` recomeça do esqueleto e guarda o anterior em `ROTEIRO.md.bak` (ou `ROTEIRO.md.<data>.bak`, quando já há um `.bak`).
3. Só agora, com o `ROTEIRO.md` criado, conduza a entrevista de `/get-brolls-brief`: ela pergunta só vídeo, fontes e direitos e escreve `"beats": []`. Alinhe o aspecto: `"video.delivery.format": "reels"` no brief e `init-rules --format reels --force --project <projeto>`.
4. Escreva a fala de cada cena seguindo [`generos/reels.md`](generos/reels.md). Troque todo `<placeholder>` do esqueleto. Não invente dado, número, nome ou promessa.
5. `roteiro --action check --project <projeto>` até sair sem erro e sem aviso de placeholder (o esqueleto intocado passa no `check`, só com avisos). Leia os outros avisos: componente pendente, licença não registrada, nota de cena, duração. Gravação de `A-ROLL`/`UGC` que ainda não está em `aroll/` não gera aviso: confira com a pessoa o que falta gravar.
6. Mostre o roteiro inteiro à pessoa. Só com a aprovação dela, dita no chat: `roteiro --action review --by NOME --channel chat --statement "frase exata" --project <projeto>`. Nunca revise por conta própria nem edite `status:` à mão; o `review` grava `status: revisado`.
7. `roteiro --action plan --project <projeto>` mostra o que o sync faria, sem gravar (e avisa quando o sync vai recusar por falta de revisão); `roteiro --action sync --project <projeto>` grava ids e beats. Daí em diante a coleta é a de sempre, beat a beat: `brief --beat cNN --project <projeto>`.

Enquanto o `BRIEF.md` não refletir o roteiro — nunca houve sync, há cena sem id, ou as cenas pedem beats diferentes dos beats ativos —, o `status` para no degrau `roteiro-sync` e o `brief` manda revisar e sincronizar: o que falta é a revisão e o sync, não a busca. Roteiro já sincronizado sem nenhuma cena de b-roll não trava nada: `status` e `brief` dizem que o roteiro não pede b-roll e seguem.

## Formato

Frontmatter, uma linha `chave: valor` cada: `type: roteiro`, `genero` e `tema` (obrigatórios); `aspecto` (`"9:16"` ou `"16:9"`; o padrão vem do gênero), `duracao_alvo_s` (inteiro de 5 a 600), `legenda` (`true`/`false`) e `status` (quem muda é o `review`). Lista, bloco e comentário no fim da linha são recusados.

- **Cena**: `## Título`. O sync acrescenta `<!-- cNN -->` no fim do título; no modo de leitura ele não aparece. Ao duplicar uma cena, **não copie o comentário** (id repetido é erro). Nenhum outro comentário HTML vale, em lugar nenhum, nem comentário do Obsidian (`%%...%%`) depois do frontmatter: texto escondido não passa pela revisão.
- **Diretiva**: `[...]` sozinho na linha inteira. Acento, caixa e sinônimos comuns (`[MÚSICA]`, `[TRILHA]`, `[APRESENTADOR]`) são aceitos.
- **Layout**, exatamente um por cena: `[A-ROLL]` ou `[A-ROLL: take]`, `[BROLL: alvo]`, `[SPLIT: esquerda | direita]`, `[FULL: alvo]`, `[UGC: descrição]`. Take: letras, números, `-` ou `_`; `a` e `b`, e take que começa com `a-` ou `b-`, são reservados aos lados do `SPLIT`.
- **SPLIT**: cada lado é um alvo de b-roll, `A-ROLL[: take]`, `UGC: descrição` ou texto entre aspas. Lado de b-roll vira o beat `cNN-a` (esquerda) ou `cNN-b` (direita), sempre pela posição.
- **FULL**: o alvo vira o beat `cNN`. `logo`, `marca`, `cta` ou um arquivo de `assets/marca/` (do projeto ou da biblioteca pessoal) é componente de marca, sem beat. Pôr ou tirar esse arquivo troca beat por marca: a revisão cai, e o sync avisa que o `FULL` virou componente de marca.
- Texto entre aspas num lado do `SPLIT` ou no `FULL` é cartela: não vira busca nem beat.
- **Camadas**: `[LETTERING: "texto" | estilo]` (texto sempre entre aspas, estilo opcional), `[SFX: nome]`, `[MUSICA: nome]`, `[COMP: nome]`. A linha da camada marca onde ela entra: antes da linha de fala seguinte (âncora) ou no fim da cena.
- **Plugin**: `[<plugin>:<nome>]` só de plugin habilitado (`plugins --action list`); prefixo de plugin desligado é erro.
- **Nota de cena**: `[risos]` no meio da fala sai da fala e do tempo. Sozinha na linha (`[pausa]`), também sai, mas vira aviso: confira que não era uma diretiva. Colchete que parece diretiva digitada errado é erro ("quis dizer ...").
- **Tempo**: cerca de 2,5 palavras por segundo, mínimo 1,5 s; cena sem fala vale 2 s; cena acima de 120 s pede divisão. Com `duracao_alvo_s`, o `check` avisa quando a soma passa.

## Componentes

A pasta `assets/` do projeto guarda componentes do vídeo; não confunda com a `assets/` da instalação da skill.

| Diretiva | Pasta | Arquivo |
|---|---|---|
| `[A-ROLL]`, `[UGC]`, lado apresentador do `SPLIT` | `aroll/` | `cNN` ou `cNN-<take>` (mp4, mov, m4v); com os dois lados do `SPLIT` apresentadores, `cNN-a[-take]` e `cNN-b[-take]` |
| `FULL` de marca | `assets/marca/` | png, svg, webp, jpg, jpeg, mp4, mov |
| `LETTERING` (o estilo) | `assets/lettering/` | json, html |
| `SFX`, `MUSICA` | `assets/sfx/`, `assets/musica/` | wav, mp3, m4a, aac, aif, aiff, ogg |
| `COMP` | `assets/composicoes/` | html, json |

`assets/<plugin_id>/` fica reservada para componentes de plugin em versões futuras; a versão atual não lê essa pasta.

O nome é comparado sem extensão, sem acento e sem caixa; vale letra, número, espaço, `-` e `_`. A busca olha a pasta do projeto e depois a biblioteca pessoal `~/.getbrolls/assets/<tipo>/` (A-ROLL e UGC só no projeto), sem subpastas; dois arquivos com o mesmo nome é erro. Não achou = pendente: aviso, nunca erro. A descrição do `[UGC]` é o pedido; o arquivo em `aroll/` é o que a preenche.

`sfx`, `musica` e `marca` levam licença em `<nome>.licenca.json`, ao lado do arquivo, com `origem`, `licenca` e `credito` preenchidos; sem ela, o aviso "licença não registrada". A biblioteca pessoal não transfere licença: cada arquivo carrega a sua. Não invente licença.

`assets --action list --project <projeto>` (com `--kind` opcional) lista o que existe; `assets --action where --kind sfx --name whoosh --project <projeto>` diz onde um nome resolve. Nenhum dos dois cria pasta.

Quem monta o vídeo aponta para os clipes em `brolls/clips/`, nunca para `entrega/`: as pastas da entrega são renumeradas quando a ordem dos beats muda.

## Sync: ids, mudanças e beats aposentados

- **Ids duráveis.** Cada cena ganha `cNN` no primeiro sync, e um id nunca volta para outra cena, nem depois de apagado: `brolls/roteiro-state.json`, o `BRIEF.md` e o manifesto guardam o que já foi usado.
- **Comentário perdido.** Cena sem id que casa exatamente (título, layout e alvo) com uma cena que sumiu recebe o id de volta. Na dúvida, o sync recusa e diz o que fazer: devolver o `<!-- cNN -->` ao título certo ou, se a cena antiga saiu de propósito, dar à nova o id livre que a mensagem sugere.
- **Alvo mudou** num beat com aprovação (ou um id já usado voltou à mão com material no manifesto): o sync para. Explique que essas aprovações voltam a pendente; só com o sim da pessoa, repita com `--confirm-target-change`. O `plan` mostra antes quais seriam, em `affected_approvals`. `queries` e `notes` do beat continuam da pessoa e ficam como estão: quando existem, o aviso diz que ainda são do alvo antigo — revise antes de buscar.
- **Fala mudou**: só aviso, sem portão. Confira se o clipe ainda serve.
- **Cena removida**: o beat fica no `BRIEF.md` com `"retired": true`, e a próxima cena nova ganha id novo sem esbarrar nele. `brief`, `status`, busca e `deliver` o ignoram; candidatos e clipes ficam, e o `deliver` lista os clipes dele em `retired`. Se a cena volta com o mesmo id, o beat volta a valer.
- **Ordem mudou**: aviso; o próximo `deliver` renumera as pastas de `entrega/`.
- **Revisão vencida**: trocar título, palavra da fala, nota de cena, alvo, lugar de camada, qualquer campo do frontmatter (menos `status`) ou o papel de um `FULL` (beat ou marca) exige novo `review`: mostre o roteiro de novo à pessoa. Linha em branco, espaço sobrando e o comentário de id não contam.
- **Aspecto**: `aspecto` diferente do `video_format` do `RULES.md` ou do `video.delivery.format` do `BRIEF.md` faz `check` e `sync` recusarem. Alinhe antes.
- **O que o sync grava**: nos beats `cNN`, só `target`, `narration` e `duration_hint_s`; beat escrito à mão fica como está. Antes de gravar, copia `ROTEIRO.md` e `BRIEF.md` para `ROTEIRO.md.sync.bak` e `BRIEF.md.sync.bak`.
- **Links**: `ROTEIRO.md` pode ser link simbólico; o sync e o `review` gravam no arquivo de verdade e o link continua link. `BRIEF.md` não pode: link, ou `GB_BRIEF_FILE` apontando para qualquer arquivo que não seja o `BRIEF.md` da pasta do projeto, é recusado.
- **Gravação interrompida** (`brolls/.pending-transaction.json`): `check` e `plan` só leem e recusam; um comando que grava conclui a recuperação.

Resumo para a pessoa: [GUIDE — Roteiro e componentes](../docs/GUIDE.md#roteiro-e-componentes).

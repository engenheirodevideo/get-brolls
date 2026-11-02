---
type: reference
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, instagram, reels, playwright]
---

# Instagram — navegador, dois streams e MP4

Esta é a rota mais frágil da skill. Siga o procedimento como está escrito; yt-dlp é outra rota possível, **não substitui** o processo do navegador.

> **Caminhos.** Os exemplos escrevem `scripts/gb.py` por brevidade. Rode sempre pelo **caminho absoluto da instalação da skill** (no plugin, `${CLAUDE_PLUGIN_ROOT}/scripts/gb.py`) e passe `--project` com a pasta absoluta do usuário em todo comando. No Windows, use `python` no lugar de `python3`.

Leia antes a seção [Instagram](../docs/GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4) do guia.

## Um Reel

1. Reutilize o Chrome logado indicado pelo usuário, só com o que as ferramentas e as políticas do host permitem; elas vêm primeiro. Nunca peça senha. Sem sessão existente, as ferramentas e a política do host decidem: uma sessão própria só se o host permitir e a pessoa entrar nela sozinha; senão, explique e aceite o MP4. Se o host não expõe respostas de rede ou proíbe reproduzir as URLs, diga isso e aceite o MP4 que a pessoa fornecer (`resolve --file`) ou outra fonte permitida; nunca leia o perfil, os cookies ou o tráfego por outro caminho.
2. Abra o Reel nesse navegador/Playwright e identifique os streams **do mesmo post**: um de vídeo e um de áudio.
3. Salve os pares privados `_video.conf` / `_audio.conf`.
4. Execute o coletor, que baixa, junta e verifica os dois canais:

```sh
python3 scripts/getbrolls/instagram_pairs.py
```

5. Importe o MP4 resultante como candidato:

```sh
python3 scripts/gb.py resolve --file MP4 --source-url URL_DO_POST --creator AUTOR --shot BEAT_ID --project PROJETO
```

Preserve a sessão e **nunca publique URLs assinadas nem os arquivos `.conf`**.

## Lotes de Reels

Enfileire, respeite o ritmo e feche cada item:

```sh
python3 scripts/gb.py queue --action add --provider instagram --project PROJETO URLS
python3 scripts/gb.py queue --action next --project PROJETO
python3 scripts/gb.py queue --action mark --id ID --done --project PROJETO
python3 scripts/gb.py queue --action mark --id ID --failed --reason "..." --project PROJETO
```

Quando a resposta de `next` trouxer `wait_seconds` sem `item` e `reason` de ritmo (`pace`, `max_per_hour`, `max_per_day`), **aguarde esse tempo** antes de chamar de novo. Com `reason: cooldown` (limite temporário, 429), espere o que a CLI indicar e volte ao mesmo escopo; se o bloqueio voltar, pare. Se `next` recusar dizendo que a fila de `instagram` está parada, não espere nem tente de novo: é a parada descrita abaixo. Capture os pares do item retornado e só então feche com `mark`.

Rode o coletor em lote (`python3 scripts/getbrolls/instagram_pairs.py`) com `--pace 20-60 --max-per-run 25 --continue-on-error --project PROJETO`. Use sempre `--project`, não `--config-output-root`, para o cooldown e a parada irem para a fila certa e o coletor respeitar a parada; sem `--project`, a parada nunca é gravada.

Em batch, use `python3 scripts/getbrolls/instagram_pairs.py --fail-on-duplicate-audio`: é o que pega o erro de colar o áudio de um post noutro.

**Pare. Não insista.** Avise o usuário. Um HTTP 429 (limite de ritmo) abre cooldown na fila: o cooldown é ritmo, não liberação; espere o que a CLI indicar e volte ao mesmo escopo. Um 403 com a URL assinada vencida (`oe` no passado há mais de 5 minutos) é URL expirada: recapture só pela mesma rota autorizada. Login, CAPTCHA, desafio ou outro 401/403 param a fila do provedor, sem cooldown e sem horário para voltar (com causa desconhecida a fila não para sozinha: pare você mesmo e diagnostique), e o coletor com `--project` também não sai enquanto ela estiver parada ou em cooldown (relida antes de cada pedido, então vale também para uma parada gravada no meio do lote); se o resumo do lote trouxer `refusal_not_recorded: true`, a recusa não chegou à fila (projeto ocupado ou falha ao gravar o arquivo): confirme a fila com a pessoa antes de repetir: esperar não autoriza nada, então diagnostique com o usuário. Só a confirmação dele, no chat, libera: peça a frase exata e só então rode `python3 scripts/gb.py queue --action resume --provider instagram --by NOME --phrase "FRASE EXATA" --expect AT --project PROJETO` (o `AT` é o `stopped.at` do `status`). O agente nunca o roda por conta própria nem inventa a frase; editar o `queue.json` ou rodar o coletor sem `--project` para furar a parada também é proibido.

## Recuperação e auditoria

Quando um lote quebrar no meio, a rotina de conferência está em [Instagram — recuperação e auditoria](../docs/GUIDE.md#instagram--recuperação-e-auditoria).

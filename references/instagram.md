---
type: reference
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, instagram, reels, playwright]
---

# Instagram — navegador, dois streams e MP4

Esta é a rota mais frágil da skill. Siga o procedimento como está escrito; yt-dlp é outra rota possível, **não substitui** o processo do navegador.

Leia antes a seção [Instagram](../docs/GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4) do guia.

## Um Reel

1. Reutilize o Chrome logado indicado pelo usuário. Nunca peça senha nem crie sessão nova.
2. Abra o Reel nesse navegador/Playwright e identifique os streams **do mesmo post**: um de vídeo e um de áudio.
3. Salve os pares privados `_video.conf` / `_audio.conf`.
4. Execute o coletor, que baixa, junta e verifica os dois canais:

```sh
python3 scripts/getbrolls/instagram_pairs.py
```

5. Importe o MP4 resultante como candidato:

```sh
python3 scripts/gb.py resolve --file <MP4> --source-url <URL do post> --creator <@autor> --shot <beat.id> --project <projeto>
```

Preserve a sessão e **nunca publique URLs assinadas nem os arquivos `.conf`**.

## Lotes de Reels

Enfileire, respeite o ritmo e feche cada item:

```sh
python3 scripts/gb.py queue --action add --provider instagram --project <projeto> <URLs>
python3 scripts/gb.py queue --action next --project <projeto>
python3 scripts/gb.py queue --action mark --id <ID> --done --project <projeto>
python3 scripts/gb.py queue --action mark --id <ID> --failed --reason "..." --project <projeto>
```

Quando a resposta de `next` trouxer `wait_seconds` sem `item`, **aguarde esse tempo** antes de chamar de novo. Capture os pares do item retornado e só então feche com `mark`.

Rode o coletor em lote com `--pace 20-60 --max-per-run 25 --continue-on-error --project <projeto>`. Use sempre `--project`, não `--config-output-root`, para o cooldown ir para a fila certa.

Em batch, use `--fail-on-duplicate-audio`: é o que pega o erro de colar o áudio de um post noutro.

Um HTTP 403 ou 429 abre cooldown na fila. **Pare. Não insista.** Avise o usuário e volte depois do tempo indicado.

## Recuperação e auditoria

Quando um lote quebrar no meio, a rotina de conferência está em [Instagram — recuperação e auditoria](../docs/GUIDE.md#instagram--recuperação-e-auditoria).

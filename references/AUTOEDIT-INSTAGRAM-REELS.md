---
name: instagram-reels
description: This skill should be used when the user asks to "baixar vídeos do Instagram", "baixar Reels", "redownload Instagram", "corrigir áudio de Reels", "usar curl configs do Instagram", or needs to merge Instagram video/audio CDN streams into verified MP4 files for the autoedit pipeline.
version: 0.1.0
type: reference
status: reference
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, autoedit, original]
---

> Referência de origem do processo autoedit. Para execução nesta versão, siga SKILL.md e os guias de rotas atuais; nomes específicos de ferramentas e limitações históricas abaixo não substituem a capacidade da sessão atual.

# instagram-reels (autoedit)

Baixar e organizar Reels do Instagram para a pipeline `autoedit` usando o método validado na campanha Codex de 2026-07-08: capturar URLs diretas de CDN como pares `*_video.conf` + `*_audio.conf`, baixar as partes separadas, mesclar com `ffmpeg`, e validar que o MP4 final tem vídeo + áudio corretos.

## Quando usar

Usar quando:

- `yt-dlp` falhar no Instagram com `empty media response` mesmo com cookies.
- Existirem curl configs gerados por navegador/devtools para Reels.
- Houver suspeita de áudio duplicado/trocado em MP4 local.
- For necessário redownload organizado de Reels antes de transcrever, fazer curated/tagged ou aprender formato/motion.

## Fonte preservada

A fonte que originou este método está documentada em:

[Processo de captura e download](INSTAGRAM-BROWSER.md)

Consultar a referência antes de mudar o script. Ela preserva os comandos originais da pasta:

`<projeto>/work`

Não copiar signed CDN URLs para a skill. Elas expiram, podem carregar sessão/assinatura e pertencem ao material de trabalho, não ao runtime do plugin.

## Script principal

Usar:

`$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py`

Dependências:

- `python3`
- `curl`
- `ffmpeg`
- `ffprobe`

O script nunca imprime a URL assinada; ele só mostra o arquivo `.conf` de origem e o destino.

## Fluxo single reel

Para um par de configs:

```bash
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py" \
  --video-config /path/to/01_CODE_video.conf \
  --audio-config /path/to/01_CODE_audio.conf \
  --output /path/to/output/01_CODE.mp4 \
  --parts-dir /path/to/work/instagram_parts \
  --config-output-root /path/to/root_that_resolves_conf_output_lines \
  --summary-json /path/to/output/01_CODE.summary.json
```

## Fluxo batch

Para uma pasta de configs:

```bash
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py" \
  --config-dir /path/to/curl_configs \
  --output-dir /path/to/outputs \
  --parts-dir /path/to/work/instagram_parts \
  --config-output-root /path/to/root_that_resolves_conf_output_lines \
  --layout auto \
  --fail-on-duplicate-audio \
  --summary-json /path/to/outputs/instagram-download-summary.json
```

Layouts:

- `auto`: mantém subpasta de username para stems no formato `<username>_<rank>_<code>` e usa output plano para stems `<rank>_<code>`.
- `student`: força output `<output-dir>/<username>/<rank>_<code>.mp4`.
- `flat`: escreve sempre `<output-dir>/<stem>.mp4`.

## Reuso dos outputs dos configs

O método original gravava `output = "work/..."` dentro de cada `.conf`. Por padrão, o script tenta reaproveitar esse arquivo se ele já existir, resolvendo o caminho via `--config-output-root`. Se não existir, baixa pela URL assinada do `.conf`.

Usar `--force-download` quando for obrigatório redownloadar a partir da URL assinada.

Usar `--no-prefer-config-output` (para invalidar também partes já salvas, use `--force-download` ou nova parts-dir) quando o arquivo apontado por `output =` for suspeito e não deve ser reaproveitado.

## Gate anti-áudio-duplicado

Sempre usar `--fail-on-duplicate-audio` em batch. Esse gate extrai o AAC dos outputs e falha se dois MP4 finais tiverem o mesmo SHA-256 de áudio. Foi exatamente esse o bug encontrado na track `07-15-2026/03-metodo-audience2-review`: quatro MP4s tinham vídeo diferente e o mesmo stream de áudio.

Para auditoria manual:

```bash
for f in /path/to/videos/*.mp4; do
  tmp="$(mktemp -d /tmp/autoedit-audiohash-XXXXXX)"
  ffmpeg -nostdin -v error -i "$f" -map 0:a:0 -c copy "$tmp/audio.aac"
  shasum -a 256 "$tmp/audio.aac"
  rm -rf "$tmp"
done
```

## Organização recomendada no projeto

Guardar a mídia final em uma pasta de fonte, nunca sobrescrever sem backup:

```text
<projeto>/sources/instagram/<perfil>/<rank>_<code>.mp4
<projeto>/sources/instagram/<perfil>/<rank>_<code>.summary.json
<projeto>/work/instagram_parts/<stem>_video.mp4
<projeto>/work/instagram_parts/<stem>_audio.mp4
```

Quando a mídia substituir uma versão bugada, primeiro criar backup com timestamp/slug dentro da track ou ao lado do arquivo:

```text
<arquivo>.backup-audio-bug-YYYYMMDD-HHMMSS.mp4
```

## Depois do download

Executar nesta ordem:

1. `ffprobe`/summary JSON: confirmar stream de vídeo e áudio.
2. Gate de hash: confirmar que áudios que deveriam ser distintos não duplicaram.
3. Transcrição: `autoedit:transcribe` ou fluxo específico do corpus.
4. Curadoria/tagging: recriar derivados a partir do transcript correto.
5. Registro: salvar summary, comandos e evidências no journal da track.

## Segurança

Não ler nem imprimir cookies, `.env`, browser credential stores ou tokens. Usar signed CDN URLs já capturadas em `.conf` como fonte operacional temporária. A solicitação de coleta autoriza a captura das URLs do post na sessão indicada. Reutilize essa autorização; peça acesso somente se faltar sessão/autorização necessária. A captura é temporária e operada pelo agente, conforme INSTAGRAM-BROWSER.md.

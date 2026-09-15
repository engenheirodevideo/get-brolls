---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# Instagram — navegador/Playwright, dois streams e MP4

Processo portado de autoedit:instagram-reels. O motor de download/merge é uma cópia do script existente, em `scripts/instagram/ig_curl_pair_downloader.py`. A captura acontece na sessão do navegador operada pelo agente; o script Python consome os pares capturados. [Instruções originais portadas](AUTOEDIT-INSTAGRAM-REELS.md).

## 1. Abrir o Reel e capturar as fontes

Use a URL real do Reel na sessão autorizada. **Se há Chrome logado indicado pelo usuário, reutilize esse Chrome pelo plugin do agente.** Não abra outra sessão sem necessidade. Com a extensão oficial Playwright disponível, a alternativa executável é:

```sh
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram attach --extension=chrome
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram tab-list
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram tab-select INDICE_OBSERVADO
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram goto "$REEL_URL"
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram snapshot
```

`INDICE_OBSERVADO` vem de `tab-list`. A extensão Playwright não é a extensão do plugin Codex; quando só esta estiver conectada, controle o Chrome por ela. Sem sessão existente, `open "$REEL_URL" --headed` cria uma sessão própria. Login necessário é realizado pelo humano nessa sessão.

### Captura pela rede da página

1. Confira no snapshot a URL/código do Reel, perfil e legenda. Inspecione apenas esse post.
2. Registre as respostas antes de reproduzir/recarregar o Reel. No plugin com CDP: obtenha a capacidade `cdp`, leia sua documentação, envie `Network.enable`, guarde o cursor de `readEvents` e observe `Network.responseReceived` após a reprodução. No Playwright CLI instalado, os comandos são `requests` e `response-body` (não `network`).
3. Examine as respostas da página e do manifesto DASH que contêm o **mesmo código/ID do Reel**. Use a resposta/documento observado; não invente endpoints. Se o manifesto estiver em JSON, decodifique o campo de manifesto e depois seu XML. Identifique `AdaptationSet` de vídeo/áudio por `mimeType`/`contentType`, selecione suas `Representation` e `BaseURL`, preservando os parâmetros assinados. Prefira vídeo até 1080p quando disponível.
4. Se só houver requests de segmentos, relacione as representações ao mesmo Reel. No ensaio real, o parâmetro `efg` em base64 JSON identificou o mesmo `xpv_asset_id`, duração e `vencode_tag` de vídeo/áudio; isso distinguiu o Reel ativo de recomendações pré-carregadas. Compare também duração/dimensões do player e inspecione o conteúdo baixado.
5. Os URLs observados nesse formato tinham `bytestart`/`byteend`, seletores explícitos de faixa. Para obter o arquivo completo, remova **somente esses dois parâmetros de faixa**, preservando todos os demais parâmetros e assinatura exatamente como capturados. Não remova `oh`, `oe` ou parâmetros desconhecidos. Valide duração e decodificação completa; um fragmento/HTTP 206 não comprova download integral. Se a CDN rejeitar a URL completa, recapture a representação pelo manifesto; não altere assinatura nem credenciais.
6. Grave somente as duas URLs selecionadas em configs privados. Nunca exporte cookies, headers de autenticação ou todo o perfil para o pacote.

Exemplo de inspeção CLI, com saída sensível retida no projeto:

```sh
umask 077
mkdir -p "$GB_PROJECT/work/instagram-configs"
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram requests > "$GB_PROJECT/work/instagram-configs/requests.private.txt"
# O agente inspeciona o arquivo local e escolhe o índice da resposta do Reel.
bash "$GB_SKILL_DIR/scripts/playwright.sh" -s=getbrolls-instagram --raw response-body INDICE_OBSERVADO > "$GB_PROJECT/work/instagram-configs/reel-response.private.txt"
```

`response-body` salva corpos binários em arquivo e informa o caminho. Em resposta textual, o agente analisa JSON/XML observado e escreve os configs a seguir; não há parser de captura automática embutido. Se a ferramenta não oferece respostas/manifesto, informe essa limitação e use a integração autorizada que ofereça, sem substituir a origem por stock.

Escolha as duas representações **do mesmo Reel** pelo manifesto/identificador e conteúdo, não simplesmente os dois primeiros MP4 da página. Recomendações e pré-carregamento podem pertencer a outros vídeos. URL `blob:` é referência interna do player e não serve ao curl; use a URL HTTPS real de CDN que a página requisitou. Preserve query assinada necessária à requisição.

Salve os configs em `<projeto>/work/instagram-configs`, com permissão privada; capture URL de vídeo e URL de áudio separadamente. Este exemplo descreve o formato, não contém URLs utilizáveis:

```text
# 01_REEL_video.conf
url = "URL_HTTPS_REAL_DO_STREAM_DE_VIDEO"
# 01_REEL_audio.conf
url = "URL_HTTPS_REAL_DO_STREAM_DE_AUDIO"
```

São **dois arquivos**, com o mesmo prefixo e sufixos `_video.conf` / `_audio.conf`. O coletor lê `url` e opcionalmente `output`; não repassa headers arbitrários do config ao curl. Se a fonte exigir headers/cookies além da URL, não invente suporte: registre a necessidade e use a ferramenta de navegador autorizada para obter as partes, registrando `output` no config para reaproveitá-las. Não exponha cookies/URLs assinadas nos relatórios, HTML ou ZIP.

## 2. Baixar os dois canais, juntar e verificar

```sh
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py"   --video-config "$GB_PROJECT/work/instagram-configs/01_REEL_video.conf"   --audio-config "$GB_PROJECT/work/instagram-configs/01_REEL_audio.conf"   --output "$GB_PROJECT/sources/instagram/01_REEL.mp4"   --parts-dir "$GB_PROJECT/work/instagram-parts"   --config-output-root "$GB_PROJECT"   --summary-json "$GB_PROJECT/work/instagram-summary.json"
```

O script baixa com curl, mapeia vídeo do primeiro input e áudio do segundo, normaliza H.264/yuv420p + AAC e verifica streams via ffprobe. Se a URL expirou, recapture no navegador. Um erro de acesso não significa que a plataforma é somente referência.

## 3. Batch e áudio duplicado

```sh
python3 "$GB_SKILL_DIR/scripts/instagram/ig_curl_pair_downloader.py"   --config-dir "$GB_PROJECT/work/instagram-configs"   --output-dir "$GB_PROJECT/sources/instagram"   --parts-dir "$GB_PROJECT/work/instagram-parts"   --config-output-root "$GB_PROJECT"   --layout flat --fail-on-duplicate-audio   --summary-json "$GB_PROJECT/work/instagram-summary.json"
```

Em batch, mantenha o gate de hash de áudio. Dois Reels podem ter áudio igual legitimamente; uma colisão exige conferir o par correto, não ignorar a detecção automaticamente. `--force-download` obtém novamente; `--no-prefer-config-output` evita somente o arquivo apontado no config; ainda pode reutilizar `parts-dir`. Para descartar uma parte suspeita, use `--force-download` após recapturar os URLs ou um novo diretório de partes. Preserve arquivos anteriores antes de substituir.

## 4. Entrar no fluxo comum de B-roll

```sh
python3 "$GB_SKILL_DIR/scripts/gb.py" resolve --file "$GB_PROJECT/sources/instagram/01_REEL.mp4" --source-url "$REEL_URL" --creator "$CREATOR" --shot instagram-01 --project "$GB_PROJECT"
python3 "$GB_SKILL_DIR/scripts/gb.py" preview --candidate "$LOCAL_ID" --start 0 --end 5 --reason "Trecho do Reel selecionado para revisão" --project "$GB_PROJECT"
python3 "$GB_SKILL_DIR/scripts/gb.py" review --project "$GB_PROJECT"
```

Use o ID local retornado e um intervalo que caiba no vídeo. Confira visualmente sincronização, identidade e conteúdo; áudio presente não comprova que é o áudio correto. O candidato fica pendente; não se autoaprove. O download das partes para inspecionar a mídia é preparação, distinta do corte final aprovado.

## Teste de instalação

`python3 scripts/instagram/ig_curl_pair_downloader.py --help` precisa funcionar a partir da pasta da skill, sem autoedit instalado. A suíte testa pares locais distintos, junção real e detecção de áudio duplicado. Teste local não prova captura/login/CDN ao vivo; o QA identifica separadamente essa evidência.

## Evidência ao vivo desta versão

Em 15/09/2026, a sessão Chrome indicada pelo usuário abriu o Reel `DcMXl1IPNtB`. Vídeo e áudio compartilhavam o asset `1474399414721222`; o coletor desta pasta baixou ambos por curl, mesclou e verificou MP4 de 50,226009 s, 1076×1912, H.264/yuv420p e AAC. Decodificação integral FFmpeg passou, quadro visual foi inspecionado e a CLI gerou prévia de 5 s com decisão pendente. URLs assinadas/configs ficaram somente em temporário privado, fora da skill. Essa evidência substitui a pendência anterior causada pela UI de extensão.

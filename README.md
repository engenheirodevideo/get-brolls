---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, recovery]
---

# Get B-rolls 2.3.4

Busca, prévia, revisão e coleta de trechos. YouTube usa **yt-dlp sem API key**. Instagram preserva o fluxo **navegador/Playwright → vídeo + áudio → curl → FFmpeg**. TikTok recebe URL completa pelo extrator yt-dlp. Pexels/Pixabay são fontes adicionais via API.

Instale conforme [INSTALL](INSTALL.md). Esta pasta reúne a skill, os comandos/scripts próprios, documentação e UI. Dependências são instaladas pelo destinatário conforme INSTALL. [QA](QA.md) separa testes reais de fixtures.

## Começar

1. Copie a pasta da skill para o destino do seu agente conforme [INSTALL](INSTALL.md).
2. Instale os pré-requisitos do sistema e execute os comandos abaixo na pasta da skill.
3. Invoque `$get-brolls` no Codex ou `/get-brolls` no Claude Code, informando roteiro/objetivo e pasta do projeto.

```sh
bash scripts/install.sh --check
bash scripts/install.sh
python3 scripts/gb.py doctor
```

O instalador obtém yt-dlp/EJS pelo PyPI e Playwright CLI pelo npm. Não distribua os ambientes gerados `.venv/` e `.tools/`. Para Instagram, use a sessão de navegador autorizada; a instalação não transfere login de outra pessoa.

## Fontes

| Fonte | Descoberta | Obtenção da mídia |
|---|---|---|
| YouTube | Busca da CLI ou URL | yt-dlp + FFmpeg, sem API key |
| Instagram | Navegador e URL do Reel | Captura dos dois canais e coletor próprio |
| TikTok | Navegador e URL completa | yt-dlp + FFmpeg, sem API key |
| Pexels / Pixabay | API com chave do próprio banco | HTTPS, original em cache para prévia |
| Commons / NASA | API sem chave | HTTPS |
| Arquivo local | `resolve --file` | Leitura local, sem upload |

YouTube, Instagram, TikTok, Pexels e Pixabay tiveram aquisição real testada; Commons/NASA tiveram busca, refresh e HEAD de mídia. Resultados e limites estão no [QA](QA.md). Disponibilidade de uma URL pode mudar.

## Fluxo principal

```sh
python3 scripts/gb.py doctor
python3 scripts/gb.py rules --project /caminho/video
python3 scripts/gb.py references --project /caminho/video
python3 scripts/gb.py search --provider youtube --query "NASA Artemis launch" --limit 3 --intent literal --project /caminho/video
```

Para URL direta, `resolve --url URL --shot insert-01 --project /caminho/video`. Para bancos, troque o provider por pexels/pixabay e configure a chave correspondente. Use o ID retornado:

```sh
python3 scripts/gb.py preview --candidate ID --start 0 --end 5 --reason "Ação citada na fala" --project /caminho/video
python3 scripts/gb.py review --project /caminho/video
python3 -m http.server 8767 --bind 127.0.0.1 --directory /caminho/video
```

Abra `http://127.0.0.1:8767/brolls/review.html`. Preview remoto obtém mídia de trabalho em `.getbrolls-sources/`, gera GIF/contact sheet e mantém aprovação pendente. Na rota yt-dlp de YouTube/TikTok, baixa o intervalo selecionado; em bancos, obtém o original disponível. Instagram segue primeiro o fluxo de captura e junção abaixo. O cache liga os bytes ao ID/fonte e intervalo. Não é aprovação nem entrega final.

O usuário aprova, pede ajuste ou sugere fonte e exporta o JSON. Registre a decisão real:

```sh
python3 scripts/gb.py import-review --file /caminho/revisao.json --by "Nome de quem revisou" --project /caminho/video
python3 scripts/gb.py permit --candidate ID --evidence "Evidência real das condições de uso deste material" --project /caminho/video
python3 scripts/gb.py fetch --candidate ID --project /caminho/video
python3 scripts/gb.py verify --project /caminho/video
```

`approve --candidate ID --start 0 --end 5 --by "Nome" --project /caminho/video` registra aprovação explícita já recebida. Não invente decisões. Os exemplos de evidência e nomes precisam ser substituídos pelos dados reais. `fetch` conserva o gate de aprovação/condições do projeto e usa o arquivo de trabalho revisado. Mudança de intervalo ou contexto exige nova revisão. `--reference-only` no preview é escolha explícita para referência estática sem aquisição.

## Instagram — processo completo preservado

Leia [INSTAGRAM-BROWSER](references/INSTAGRAM-BROWSER.md). Capture no navegador os streams separados do mesmo Reel, gere os dois configs privados e execute `scripts/instagram/ig_curl_pair_downloader.py`. Ele baixa/junta vídeo e áudio, verifica codecs/streams e detecta áudio duplicado em batch. Importe o MP4 resultante com `resolve --file --source-url --creator --shot`; depois preview/review seguem o fluxo comum.

O coletor original não captura sozinho a sessão do navegador: a skill orienta a etapa pelo agente/Playwright. yt-dlp pode ser uma rota alternativa para um URL público que funcione, mas não substitui o método de captura de pares.

## Compatibilidade com os comandos originais

Ative `.venv` conforme INSTALL e use `bash`:

```sh
bash scripts/broll/gb_search.sh "NASA Artemis launch" 3
bash scripts/broll/gb_contact.sh VIDEO_ID 00:00-00:05 /caminho/contact.jpg
# Após aprovação do trecho:
bash scripts/broll/gb_fetch.sh VIDEO_ID 00:00-00:05 01_nasa_launch /caminho/video/brolls
bash scripts/broll/gb_verify.sh /caminho/video/brolls
```

Esses helpers foram copiados do autoedit e preservam a interface YouTube por ID. Não passe URL Instagram/TikTok no campo VIDEO_ID. Eles não gravam automaticamente o ledger Python; importe o resultado no CLI para integrar a revisão. `gb_vertical.sh` é a conversão opcional para fundo borrado. A CLI aceita URLs sociais; os helpers mantêm os argumentos originais e recebem apenas a resolução da venv/runtime.

## Arquivos locais, contexto e formatos

`resolve --file /original.mp4 --source-url URL_REAL --creator "Autor" --shot insert-01 --project /caminho/video` preserva a procedência. Um insert isolado não exige roteiro inteiro; omita `--narration` quando não houver fala. Para contexto estático, acrescente `--context-image /pessoa.png`. `GB_GIF_SCOPE=full` exige `--full-preview-file` com composição pronta do mesmo insert; a skill não monta vídeo automaticamente.

GIF padrão: 360 px, 8 fps, 128 cores, até 10 s e 5 MB; preserve proporção. Intervalos acima de 10 s são recusados. Se o GIF exceder 5 MB, entrega prévia estática com aviso. `GB_PREVIEW_MODE=static` usa poster/contact sheet. `.env.example` contém as opções; `--help` mostra argumentos. Imagens e notícias entram por arquivo/captura: [ASSETS](references/ASSETS.md), [BROWSER](references/BROWSER.md).

## Regras e memória

`init-rules --project` cria RULES editável; `rules` mostra padrões ou regras do projeto. Fonte/tipo/domínios bloqueados continuam bloqueados. `remember --candidate ID --decision approved|rejected --reason TEXTO --by NOME --project /caminho/video` guarda referências do projeto; decisão positiva exige aprovação vigente. Declaração de condições preenchida pelo usuário é diferente de licença verificada.

## Entrega, recuperação e limites

Compartilhe `brolls/` inteiro para visualização. Para continuar editando/regerar prévias, preserve também `.getbrolls-sources/` e originais; caminhos locais são absolutos. Cortes finais ficam em `brolls/clips/`; MP4 de trabalho não integra a pasta distribuída da skill.

Execute um comando por projeto. Ledger usa journal recuperável e trava; não apague originais/eventos/journal. `diagnostics.jsonl` registra operação/erro/avisos sem argumentos completos. Se o estado foi salvo e a página falhou, execute `review`. Projetos são locais confiáveis, sem autenticação de revisor. `verify` checa hash e decodificação, não qualidade editorial.

Suporte de sites depende do conteúdo, disponibilidade e sessão. Não há busca global Instagram/TikTok implementada: descubra a URL pelo navegador. Escolha fonte 1080p quando disponível; nunca declare HD só porque o seletor pediu até 1080p. Veja o resultado ffprobe.

## Documentação e manutenção

| Documento | Conteúdo |
|---|---|
| [SKILL](SKILL.md) | Instruções de execução para o agente |
| [INSTALL](INSTALL.md) | Dependências, instalação e conexão do navegador |
| [AGENTS](AGENTS.md) | Contratos do produto e manutenção do código |
| [Instagram](references/INSTAGRAM-BROWSER.md) | Captura e junção de vídeo/áudio |
| [Storyboard](references/STORYBOARD.md) | Revisão visual, retorno JSON e impressão |
| [QA](QA.md) / [Rotas](references/API-STATUS.md) | Evidências e limites dos testes |
| [Compatibilidade](COMPATIBILITY.md) | Sistemas, agentes e migração de projetos |
| [CHANGELOG](CHANGELOG.md) | Histórico de mudanças |
| [Contribuição](CONTRIBUTING.md) / [Segurança](SECURITY.md) | Manutenção e arquivos privados |

Para contribuir, execute `python3 -m unittest discover -s tests -v` e siga AGENTS/CONTRIBUTING. A versão atual é a desta pasta. `dist/` contém snapshots históricos; não faz parte da entrega atual. Bibliotecas, fontes tipográficas externas, configurações pessoais e mídias de ensaio não integram os arquivos distribuídos da skill. Consulte [avisos de dependências](THIRD_PARTY_NOTICES.md).

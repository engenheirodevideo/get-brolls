---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, product, getting-started]
---

<div align="center">
  <img src="assets/brand-logo.png" alt="Engenheiro de vídeo" width="104">
  <h1>Get B-rolls</h1>
  <p><strong>Da ideia ao trecho certo para a sua edição.</strong></p>
  <p>Encontre imagens de apoio, veja o movimento e revise cada escolha<br>antes de receber os cortes finais com suas fontes.</p>
  <p>v2.3.4 · Codex e Claude Code · macOS e Linux</p>
  <p><a href="#comece-aqui">Comece aqui</a> · <a href="#storyboard-v2">Storyboard V2</a> · <a href="#fontes">Fontes</a> · <a href="GUIDE.md#instalação">Guia completo</a></p>
</div>

---

Get B-rolls é uma skill para coletar os vídeos e imagens que apoiam uma fala, ilustram uma ideia ou mostram exatamente a pessoa, o produto e o acontecimento citados no roteiro. Você descreve o que precisa; o agente pesquisa, prepara as prévias e reúne as escolhas em um storyboard para sua revisão.

- **Escolha com contexto.** Cada trecho pode reunir fala, motivo da escolha, intervalo, autor e fonte original.
- **Veja antes de decidir.** GIFs e sequências de quadros ajudam a avaliar ação, enquadramento e textos sobrepostos.
- **Receba uma coleta organizada.** Os cortes finais ficam junto de um registro de origem, revisão e condições de uso.

## Como funciona

```text
Seu roteiro ou pedido → Pesquisa → Prévias → Sua revisão → Cortes finais
```

O agente prioriza fontes literais quando você cita uma entidade real. Para ideias ilustrativas, também pode pesquisar bancos de vídeo. Um único insert funciona sem roteiro completo: basta explicar o que precisa aparecer.

A prévia pode baixar mídia de trabalho para mostrar o movimento. A entrega final depende da decisão humana e do registro das condições de uso da fonte. Se o intervalo ou o contexto mudar, o trecho volta para revisão.

## Comece aqui

### 1. Coloque a skill no seu agente

Copie a pasta completa `get-brolls/` para **um** destes destinos:

| Agente | Instalação pessoal | Dentro de um projeto | Como chamar |
|---|---|---|---|
| Codex | `~/.agents/skills/get-brolls/` | `.agents/skills/get-brolls/` | `$get-brolls` |
| Claude Code | `~/.claude/skills/get-brolls/` | `.claude/skills/get-brolls/` | `/get-brolls` |

Ao copiar uma pasta de desenvolvimento, exclua `dist/`, `.venv/`, `.tools/`, caches, projetos e arquivos privados. Instale as dependências no destino final e abra uma nova sessão do agente. [Veja instalação, atualização e compatibilidade.](GUIDE.md#instalação)

### 2. Prepare o ambiente

Pré-requisitos: Python 3.11+, FFmpeg/ffprobe, Node 22+, npm/npx, curl e Bash. Em macOS com Homebrew, comece com `brew install python ffmpeg node`. Para Linux e conexão do navegador, siga o [guia de instalação](GUIDE.md#instalação).

Na pasta instalada da skill:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
python3 scripts/gb.py doctor
```

O instalador cria os ambientes locais e obtém yt-dlp/EJS e Playwright CLI. `doctor` confere a disponibilidade das ferramentas; o acesso a cada fonte depende da URL e, quando necessário, da sua sessão de navegador.

**YouTube funciona sem API key.** Pexels e Pixabay usam suas próprias chaves opcionais, configuradas no ambiente ou no `.env` privado da skill. As opções estão em [.env.example](.env.example).

### 3. Faça seu primeiro pedido

No Codex:

```text
$get-brolls Preciso de três inserts para um vídeo sobre o lançamento Artemis.
Procure imagens reais do foguete e da decolagem, com trechos de 3 a 5 segundos.
Prepare as prévias e um storyboard para eu revisar.
Use a pasta /caminho/meu-video para guardar o projeto.
```

No Claude Code, troque a primeira chamada por `/get-brolls`. Substitua a pasta pelo caminho real do seu projeto, fora da instalação da skill. Você também pode fornecer uma URL específica ou um arquivo local.

## Storyboard V2

O comando `review` gera `brolls/review.html`: uma página local para avaliar a coleta, navegar entre os trechos e devolver decisões ao agente.

| Na revisão | O que você faz |
|---|---|
| Quadro selecionado | Alterna entre imagem estática e GIF, mantendo a proporção original. |
| Contexto e origem | Consulta fala fornecida, intervalo, motivo da escolha, autor e link da fonte. |
| Decisão por trecho | Aprova, pede ajuste com comentário ou sugere outra fonte. |
| Exportar revisão | Salva um JSON para o agente importar no projeto. |
| Imprimir / PDF | Gera uma versão estática com quadros, fontes e comentários. |

A galeria permanece estática; a animação acontece no quadro selecionado e respeita a preferência por movimento reduzido. Um print opcional da pessoa serve de contexto e permanece estático. Para avaliar uma composição pronta do mesmo insert, use `GB_GIF_SCOPE=full` com `--full-preview-file`.

Compartilhe a pasta **`brolls/` completa**, para manter as imagens e os GIFs acessíveis. Para continuar editando ou regenerar prévias, preserve também os originais e `.getbrolls-sources/`. [Detalhes da revisão.](GUIDE.md#storyboard-v2)

## Fontes

| Fonte | Como encontrar | Como obter |
|---|---|---|
| **YouTube** | Busca por palavras ou URL | yt-dlp + FFmpeg; sem API key. |
| **Instagram** | Reel encontrado no navegador | Captura de vídeo e áudio do mesmo Reel; coletor incluído une os canais. |
| **TikTok** | URL completa descoberta no navegador | yt-dlp + FFmpeg; sem API key. |
| **Pexels / Pixabay** | Busca nas APIs dos bancos | Chave do respectivo banco; download HTTPS. |
| **Wikimedia Commons / NASA** | Busca nas APIs públicas | Download HTTPS; sem chave. |
| **Arquivo local** | Vídeo, imagem ou captura fornecida | Importação local com origem e autoria, quando informadas. |

Para Instagram, o agente opera o navegador autorizado e entrega os dois streams ao coletor; o script não captura a sessão sozinho. O [guia Instagram](GUIDE.md#instagram--navegadorplaywright-dois-streams-e-mp4) cobre seleção dos pares, download, áudio e recuperação. Instagram e TikTok dependem da descoberta da URL no navegador; não há busca global por palavra-chave na CLI.

Os ensaios registrados incluem aquisição real de YouTube, Instagram, TikTok, Pexels e Pixabay. Para Commons/NASA, a evidência cobre busca e disponibilidade do arquivo, sem download integral naquele ensaio. Consulte os resultados e seus limites em [Qualidade e evidências](QUALITY.md).

## Usar pelo terminal

Execute os exemplos abaixo na pasta da skill. Troque `/caminho/meu-video` pelo seu projeto e `ID` pelo identificador retornado na busca.

```sh
python3 scripts/gb.py rules --project /caminho/meu-video
python3 scripts/gb.py references --project /caminho/meu-video
python3 scripts/gb.py search --provider youtube --query "NASA Artemis launch" --limit 3 --intent literal --project /caminho/meu-video
python3 scripts/gb.py preview --candidate ID --start 0 --end 5 --reason "Mostrar a decolagem citada no vídeo" --project /caminho/meu-video
python3 scripts/gb.py review --project /caminho/meu-video
python3 -m http.server 8767 --bind 127.0.0.1 --directory /caminho/meu-video/brolls
```

Abra [o storyboard local](http://127.0.0.1:8767/review.html), revise os trechos e exporte as decisões. Depois, em outro terminal na pasta da skill:

```sh
python3 scripts/gb.py import-review --file /caminho/revisao.json --by "Nome de quem revisou" --project /caminho/meu-video
python3 scripts/gb.py permit --candidate ID --evidence "Evidência real das condições de uso" --project /caminho/meu-video
python3 scripts/gb.py fetch --candidate ID --project /caminho/meu-video
python3 scripts/gb.py verify --project /caminho/meu-video
```

Substitua o nome, o arquivo exportado e a evidência pelos dados reais. Repita `permit` e `fetch` para cada candidato aprovado. `approve` também pode registrar uma decisão explícita já recebida. `verify` confere integridade e decodificação dos arquivos; a avaliação editorial é sua.

<details>
<summary>URLs, arquivos locais e configurações</summary>

- URL específica: `resolve --url URL_REAL --shot insert-01 --project /caminho/meu-video`.
- Arquivo local: `resolve --file /caminho/original.mp4 --source-url URL_REAL --creator "Autor" --shot insert-01 --project /caminho/meu-video`. Use metadados reais; uma fonte sem URL pode omitir `--source-url`.
- Fala exata: acrescente `--narration` à prévia quando houver roteiro fornecido.
- Regras do projeto: `init-rules --project /caminho/meu-video` cria um [RULES.md](RULES.md) editável com formatos, fontes preferidas e bloqueios.
- Memória de escolhas: `remember` registra referências aprovadas ou rejeitadas; `references` consulta o histórico daquele projeto.
- Imagens e notícias: importe o arquivo ou a captura com sua procedência. Veja [tipos de mídia](GUIDE.md#tipos-de-assets-e-formatos) e [capturas pelo navegador](GUIDE.md#captura-de-notícias-e-páginas-pelo-navegador).
- GIF padrão: 360 px, 8 fps, 128 cores, até 10 segundos e 5 MB. Intervalo acima do limite de duração é recusado; tamanho excessivo gera prévia estática com aviso. `GB_PREVIEW_MODE=static` usa poster e sequência de quadros.
- `--reference-only` prepara uma referência estática sem obter mídia remota; um poster isolado não comprova movimento.
- Os helpers em `scripts/broll/` oferecem busca, quadros, cortes e verificação de YouTube por `VIDEO_ID`. Seus resultados precisam ser importados pela CLI para integrar o registro e a revisão do projeto.

Use `python3 scripts/gb.py --help` e `python3 scripts/gb.py preview --help` para consultar os argumentos. Fora da pasta da skill, use o caminho absoluto de `scripts/gb.py`.

</details>

## Limites e privacidade

Os arquivos de projeto e os originais importados ficam locais. Pesquisa e aquisição remotas se conectam aos provedores escolhidos. Guarde chaves, sessão do navegador, configs Instagram e URLs assinadas em ambiente privado; esses dados não pertencem à pasta distribuída da skill.

O storyboard é destinado a projetos locais confiáveis e não possui autenticação de revisor. Compartilhe apenas o material necessário à revisão. As condições de uso pertencem a cada fonte; o registro de uma decisão não verifica automaticamente sua licença.

A resolução final depende da fonte: prefira 1080p quando disponível e confira as dimensões reais. A ferramenta preserva a proporção e sinaliza incompatibilidades de formato. Ela não monta automaticamente o vídeo completo, não faz busca de imagens via API nem entrega áudio isolado como asset final.

Execute um comando por projeto de cada vez. Preserve originais, cache e histórico de eventos. Se o registro for salvo e a geração da página falhar, execute `review` novamente. Windows nativo ainda não foi validado; veja [compatibilidade](GUIDE.md#compatibilidade).

## Dentro do repositório

| Entrada | Para que serve |
|---|---|
| **[README.md](README.md)** | Visão do produto e primeiro uso. |
| [GUIDE.md](GUIDE.md) · [SKILL.md](SKILL.md) | Manual completo e instruções de execução para o agente. |
| [QUALITY.md](QUALITY.md) | Testes, evidências reais e limites conhecidos. |
| [RULES.md](RULES.md) · [.env.example](.env.example) | Regras editoriais e opções de configuração. |
| `scripts/` | CLI, busca, aquisição, revisão e utilitários. |
| `assets/` | Arquivos da interface e artefato visual de storyboard. |
| `agents/` · `schemas/` | Apresentação no agente e contrato de dados. |
| `tests/` · `.github/workflows/` | Testes e automação de qualidade. |

Para manter o projeto, comece por [CONTRIBUTING](CONTRIBUTING.md) e [AGENTS](AGENTS.md). Consulte [QUALITY](QUALITY.md), [CHANGELOG](CHANGELOG.md) e [SECURITY](SECURITY.md) para evidências, mudanças e tratamento de dados privados. `dist/` guarda pacotes gerados em momentos específicos; confira a versão e o conteúdo antes de distribuí-los.

Código sob [licença MIT](LICENSE). Dependências externas mantêm suas próprias condições, descritas nos [avisos de terceiros](THIRD_PARTY_NOTICES.md).

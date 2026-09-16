---
type: documentation
status: current
created: 2026-09-16
updated: 2026-09-16
tags: [get-brolls]
---

# GET B-ROLLS — Gemini CLI

O Gemini CLI carrega arquivos `GEMINI.md` de forma hierárquica a partir do diretório em que você está trabalhando (e de `~/.gemini/GEMINI.md`), não da pasta desta skill. Por isso este arquivo sozinho não é descoberto quando você edita um vídeo em outra pasta: importe o roteador a partir do seu próprio contexto.

## Configuração

Acrescente ao seu `~/.gemini/GEMINI.md` (crie o arquivo se ele não existir), trocando o caminho pelo local real do clone:

```md
## Get B-rolls
Use para coletar B-roll, cutaways, inserts ou imagens de apoio para um vídeo ou Reel — pesquisa em YouTube, Instagram, TikTok e bancos, prévias em Storyboard para revisão humana e entrega com origem e licença registradas.
@/caminho/absoluto/para/get-brolls/SKILL.md
```

A linha `@/caminho/...` importa o conteúdo do [SKILL.md](SKILL.md) no contexto da sessão; a frase acima dela diz ao modelo quando usar a skill. Use sempre caminho absoluto. Para importar apenas em um projeto, coloque o mesmo trecho no `GEMINI.md` da pasta desse projeto.

Alternativa sem importação: cole o conteúdo de [SKILL.md](SKILL.md) diretamente no seu `GEMINI.md`. Nesse caso, repita a cópia a cada atualização da skill — a importação por caminho não precisa disso.

Depois de configurar, prepare o ambiente pela seção de [instalação do guia](GUIDE.md#instalação) e confirme com `python3 scripts/gb.py doctor`. Use o caminho absoluto do CLI e `--project` ao trabalhar de outra pasta.

## Manutenção

Para manter código ou documentação deste repositório, siga [AGENTS.md](AGENTS.md). O guia operacional único do produto é [GUIDE.md](GUIDE.md).

Não duplique instruções aqui: este arquivo apenas roteia e explica a importação. As regras de aprovação humana, licença e proveniência de [SKILL.md](SKILL.md) valem integralmente no Gemini CLI.

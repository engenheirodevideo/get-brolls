---
type: reference
status: current
created: 2026-09-17
updated: 2026-09-17
tags: [get-brolls, direitos, permit, licenca]
---

# Condições de uso — o que registrar antes de coletar

Nenhum corte final sai sem que as condições de uso do trecho estejam registradas. **Não invente licença.** A responsabilidade pelas condições de uso do material é de quem produz o vídeo; a skill responde pela fidelidade do trecho e pelo registro de origem de cada asset.

> **Caminhos.** Os exemplos escrevem `scripts/gb.py` por brevidade. Rode sempre pelo **caminho absoluto da instalação da skill** (no plugin, `${CLAUDE_PLUGIN_ROOT}/scripts/gb.py`) e passe `--project` com a pasta absoluta do usuário em todo comando. No Windows, use `python` no lugar de `python3`.

## As três rotas do `permit`

Condições reais que você leu na página da fonte:

```sh
python3 scripts/gb.py permit --candidate <ID> --evidence "condições reais, com o que a página diz" --project <projeto>
```

Texto genérico da fonte, quando ela tem termos padrão:

```sh
python3 scripts/gb.py permit --candidate <ID> --preset youtube|nasa|commons|pexels|pixabay --project <projeto>
```

O preset preenche as condições genéricas da fonte e pede que se verifique a página real; `--evidence` junto concatena o texto específico.

Declaração falada pela pessoa responsável, no chat:

```sh
python3 scripts/gb.py permit --candidate <ID> --declared-by "NOME" --declaration-text "frase exata que a pessoa disse" --project <projeto>
```

Exige nome real (não vale "usuário") e texto com pelo menos 20 caracteres. Grava `rights.basis = user_declaration` e a pessoa responsável.

## Modo do projeto

`RULES.md` define o modo: `per_item_evidence` (cada item precisa da sua evidência) ou `user_declaration` (a declaração do responsável cobre o projeto). Crie ou ajuste com:

```sh
python3 scripts/gb.py init-rules --mode per_item_evidence --responsible "NOME" --declaration "TEXTO" --project <projeto>
```

`copyright` nunca é herdado de camada fora do projeto: responsabilidade e declaração valem só no projeto em que o vídeo é feito.

## Depois do permit

```sh
python3 scripts/gb.py fetch --candidate <ID> --project <projeto>
python3 scripts/gb.py verify --project <projeto>
python3 scripts/gb.py deliver --project <projeto>
```

O corte usa os bytes revisados e mantém origem e autor. `brolls/credits.md` guarda origem, autor e decisão; `entrega/` sai com uma pasta por beat e um `ORIGEM.md` em cada uma.

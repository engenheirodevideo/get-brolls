---
type: documentation
status: current
created: 2026-09-15
updated: 2026-11-06
tags: [get-brolls, quality, qa, evidence]
---

# Qualidade e evidências — GET B-ROLLS 2.6.2

Este documento diz como a qualidade da versão atual é conferida: os portões automáticos, as garantias que eles cobrem, o que fica fora deles e onde ficam os ensaios reais. Resultado ao vivo é registro datado, não promessa de disponibilidade futura nem aprovação editorial. O que cada versão anterior entregou e corrigiu está no [CHANGELOG](../CHANGELOG.md).

## Portões da 2.6.0

A 2.6.0 é medida por portões automáticos, todos offline e sem chave de API:

- **Suíte inteira em Python 3.11 e 3.14.** `python3 -m unittest discover -s tests`, com `GB_HOME` numa pasta temporária. As duas versões precisam passar, porque falhas já apareceram só na 3.14. O CI roda a mesma suíte em macOS, Windows e Linux, com Python 3.11, 3.13 e 3.14, conforme a matriz de `.github/workflows/test.yml`.
- **Lint e tipos em zero erro.** `ruff check`, `ruff format --check`, `pyright`, `pylint scripts plugins/examples` e `pylint tests` (régua em `tests/pylintrc`). `bash scripts/check.sh` roda a bateria que o job `quality` do CI roda.
- **Espelho e âncoras.** `scripts/gen_skill_mirror.py --check` confirma que `skills/get-brolls/SKILL.md` espelha o `SKILL.md` da raiz, e `scripts/check_anchors.py` confirma que toda âncora e link relativo citados nos documentos resolvem.
- **Golden da 2.5.** `tests/test_golden_v25.py` repete byte a byte a matriz de saídas gravada da 2.5; só a lista fechada de diferenças aceitas passa. Assim quem não migra não vê mudança.
- **Cobertura dos módulos estritos.** `scripts/check_coverage.py` roda a suíte sob `coverage` e falha se alguma meta de `[tool.getbrolls.coverage]` (em `pyproject.toml`) ficar de fora, dizendo o arquivo e a lacuna.

O que a 2.6.0 não mede: disponibilidade de fonte, Windows em máquina real (só a matriz do CI) e o comportamento do agente. Esse último fica nos testes cegos abaixo, cuja última rodada completa é a da 2.4.0; não houve rodada nova para a 2.6.0.

## Garantias de proveniência

A suíte prova as guardas que impedem coleta sem decisão humana: `require_fetch` exige aprovação humana, assinatura conferida e direitos permitidos com evidência; `signature()` invalida a revisão quando o intervalo ou o contexto muda; `validate_manifest` restringe os caminhos de mídia do manifesto a `previews/` e `clips/`. Nenhum comando preenche `rights.evidence` ou `approval` sem ação explícita de uma pessoa, e a biblioteca entre projetos responde com `rights_not_transferable: true`.

## Testes cegos

A suíte automatizada responde se o programa funciona; o **teste cego** responde se a skill entrega o que promete a um criador de conteúdo. O processo, os papéis (executor cego, juiz com gabarito, amostragem humana), a cadência e o corpus de 16 casos estão em [eval/README.md](../eval/README.md); a pontuação por beat e as metas, em [eval/rubric.md](../eval/rubric.md). É medição editorial, fora do CI de propósito: precisa de rede, sessão e tempo de agente, e todo relatório separa **ambiente** (URL fora do ar, sessão, quota) de **comportamento** (stock sem pedido, licença inventada, aprovação pelo próprio agente).

Os relatórios datados ficam em [eval/runs/](../eval/runs/), um arquivo por execução: a linha de base é a da 2.3.7, e a execução completa mais recente, com os 16 casos, é a da 2.4.0.

## Ensaios reais

Ensaio contra fonte real (YouTube, Instagram, TikTok, bancos de imagem) fica fora da suíte padrão e fora da pasta da skill: registra a URL pública, a versão usada, o resultado, a dimensão e a duração, e os limites do ensaio. Ensaio datado não certifica a plataforma inteira nem versões futuras, e falha de URL ou de sessão não é prova de indisponibilidade permanente.

- **Rede opt-in.** `tests/test_inspect_network.py` pede um vídeo real ao YouTube e só roda com `GB_EVAL_NETWORK=1`; sem a variável, o yt-dlp da suíte é um substituto offline (`tests/_offline.py`).
- **`doctor --live`** faz buscas e refresh limitados nas fontes configuradas e pode consumir quota; não testa a captura do Instagram nem do TikTok.
- **Limites conhecidos.** O ritmo e o cooldown de lote do Instagram são cobertos só por mocks. Windows é exercitado pela matriz do CI, não por máquina real. A descoberta automática da skill em novas sessões de agente depende da instalação do agente destinatário.

Para repetir a verificação local na pasta da skill:

```sh
bash scripts/install.sh --check
python3 -m unittest discover -s tests -v
bash scripts/check.sh
```

No Windows PowerShell, use `scripts/install.ps1 -Check` e `python` no lugar de `python3`.

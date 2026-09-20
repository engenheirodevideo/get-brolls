---
name: Relatar um bug
about: Falha reproduzível na CLI, no instalador, na revisão ou na entrega
title: ''
labels: bug
assignees: ''
---

## Ambiente

- Sistema operacional e versão:
- Versão do Python (`python3 --version`):
- Instalação: clone como skill (Codex) ou plugin do Claude Code:

## Saída do doctor

Cole a saída **completa** de `python3 scripts/gb.py doctor` (no Windows, `python scripts/gb.py doctor`). Ela traz versão da skill, Python, caminhos fixados e o veredito `summary`.

```json

```

## Log do projeto

A partir da 2.5.0 cada projeto grava `brolls/getbrolls.log`. Cole as linhas do comando que falhou (o envelope de erro traz o caminho em `app_log`). O log não registra nomes, frases de aprovação, texto de busca nem URLs, só o host; mesmo assim, releia antes de colar. Para mais detalhe, repita o comando com `GB_LOG_LEVEL=DEBUG`.

```text

```

## Passos para reproduzir

1.
2.
3.

Inclua os comandos exatos, sem chaves, URLs assinadas, cookies ou dados de clientes.

## Resultado esperado

## Resultado obtido

Mensagem de erro completa, quando houver. Se a falha for de uma fonte, informe se a URL abre no navegador na mesma máquina.

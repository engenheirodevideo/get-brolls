---
type: documentation
status: current
created: 2026-09-26
updated: 2026-09-26
tags: [get-brolls, code-style, python, review]
---

# Guia de código

A base é o [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) (CC BY 3.0), com as adaptações abaixo para o stack do getbrolls. Quem escreve código segue este guia, e quem revisa confere contra ele. Uma regra daqui vence a do guia do Google, e o código vizinho vence as duas quando o assunto não está coberto: **seja consistente**.

## Stack

- **Runtime:** Python 3.11+ e **só a biblioteca padrão**, sem dependência de runtime; os plugins de exemplo seguem a mesma regra. Ferramentas externas (FFmpeg, yt-dlp, Playwright, CLIs de engine) são chamadas como processo, nunca importadas.
- **Qualidade:** `ruff check` (regras em `pyproject.toml`), `ruff format` (o formatador; não use Black nem yapf), `pyright` em `basic` e `pylint` (régua em `[tool.pylint]` do `pyproject.toml`; testes com `tests/pylintrc`). Tudo pinado em `requirements-dev.txt`.
- **Testes:** `unittest`, sem rede, com mídia sintética. `bash scripts/check.sh` roda a bateria inteira.

## Regras da linguagem

- **Lint:** o código entra com ruff e pylint limpos. Uma supressão fica **na linha**, nomeia a regra e diz o motivo: `# pylint: disable=broad-exception-caught  # isola o código do plugin` e `# noqa: BLE001 - …`. Supressão no módulo só vale para código legado já documentado.
- **Imports:** `from .modulo import nome` é a convenção do pacote. Import dentro de função é permitido de propósito, porque mantém a CLI rápida para iniciar, carrega módulo de plataforma (`msvcrt`) só onde precisa e, nos testes, deixa `import _isolation` vir antes de tudo. Proibidos: `import *`, import não usado sem motivo, reimport.
- **Exceções:** use as do core (`ValueError`, `PluginError` e as da CLI) com mensagem em pt-BR para a pessoa. `ValueError` é o dado inválido (a CLI o devolve como `INVALID_DATA`, saída 1) e `TypeError` é bug (`INTERNAL_ERROR`, saída 3): o ruff pede `TypeError` para tipo errado (TRY004), então onde o dado inválido é um tipo errado a linha leva `# noqa: TRY004` com o motivo. `except:` nu é proibido. `except Exception`/`BaseException` só com motivo na linha, como o isolamento de plugin de terceiro. Relance com `raise … from` quando a causa importa e com `from None` quando a cadeia vazaria detalhe interno.
- **Estado global:** nada de global mutável. Constante de módulo em `MAIUSCULAS_COM_SUBLINHADO`.
- **Valor padrão:** nunca mutável (`[]`, `{}`); use `None` ou tupla.
- **Verdadeiro/falso:** falso implícito em coleções (`if not itens:`), `is None` para `None`. `type(x) is dict` vale quando a intenção é rejeitar `bool` e subclasses ao validar JSON; fora disso, `isinstance`.
- **Comprehension:** para casos simples. Com mais de um `for` ou condição que exija leitura, escreva o laço.
- **Tipos:** anotação obrigatória na API pública do SDK (`getbrolls.sdk`) e recomendada no resto; o `pyright` precisa passar.
- **Recursos avançados:** sem metaclasse, sem mexer em `__dict__` alheio, sem `exec`. A exceção documentada é o loader de plugin, que existe para isso.
- **Processos:** sempre lista de argv, sem `shell=True`, com timeout quando a espera puder travar, e um ambiente mínimo quando o processo não precisa do nosso.
- **Imports entre módulos do pacote:** `scripts/getbrolls` é organizado em camadas (base, núcleo, serviços, contratos de plugin, domínio, anfitrião de plugin, funcionalidades, comandos, entrada). Um import de módulo aponta para a mesma camada ou para uma abaixo, e o grafo não tem ciclo. Subir de camada só vale dentro de função, como adiamento deliberado, e fica registrado com motivo na allowlist. `tests/test_import_rules.py` confere as regras com a tabela e a allowlist de `tests/import_rules/`: módulo novo entra em `layers.txt`, a allowlist e o catraca só diminuem, e dois módulos que se importam precisam estar em `pairs_accepted.txt`. Plugins importam só `getbrolls.sdk`, e `tests/test_sdk_surface_frozen.py` congela o que o SDK e a CLI expõem.

## Regras de estilo

- **Linha:** até 120 colunas, igual no ruff e no pylint. Quebre a linha, não suprima a regra; a exceção é URL ou literal que não pode ser quebrado.
- **Docstring:** `"""aspas triplas"""`, em pt-BR, com **um resumo de uma linha** do que a função faz ou devolve. `Args:`/`Returns:`/`Raises:` só quando o contrato não é óbvio pelo nome e pelos tipos, e isso é obrigatório na API pública do SDK. Evite a docstring que repete o nome ("Função que…").
- **Comentário:** em pt-BR, curto, explica o **porquê**. Não use `TODO` solto: pendência vira issue.
- **Mensagem para a pessoa:** em pt-BR, dizendo o que aconteceu e o próximo passo. Nunca inclua caminho absoluto da máquina, token nem trecho de exceção em inglês.
- **Nomes:** `snake_case` em módulo, função e variável; `PascalCase` em classe; `_privado` no que é interno. Arquivo sem prefixo numérico. Identificadores de domínio seguem o vizinho: `roteiro`, `cena` e `beat` continuam como estão.
- **Complexidade:** função nova cabe nos limites do pylint (ramos, variáveis, instruções, argumentos). Passou do limite, extraia funções auxiliares pequenas e com nome. Para código legado vale a exceção documentada por regra e módulo.
- **`main()`:** script executável tem `main()` chamado por `if __name__ == "__main__":`.

## Contratos que o estilo não pode quebrar

- **API pública congelada entre versões:** nomes e assinaturas de `PluginApi`, o que `getbrolls.sdk` exporta, flags e subcomandos da CLI, campos JSON de saída, nomes de arquivo gerados. Se uma regra de lint pedir mudança aí, a supressão fica na linha com motivo.
- **Contrato do plano de export:** siga a política de evolução do [SDK](SDK.md). Mudança aditiva não sobe `export_version`; remover, renomear ou mudar o significado de um campo sobe.
- **Projeto sem roteiro e sem plugin gera a mesma saída byte a byte** depois de um refactor. Quem refatora comprova isso.
- **Nada de material interno** no repositório: ids de revisão, caminhos de máquina, planejamento. `tests/test_repository.py` confere.
- **O que entra no log** (`brolls/getbrolls.log` e `$GB_HOME/logs/getbrolls.log`): evento novo sai por `logs.event` (ou `raise logs.refusal(...)`, que põe o `error_code` que a CLI dá ao mesmo erro; quem converte o `ValueError` em `UsageError` usa `runtime.usage_errors()`, e quem o captura e segue o envolve em `logs.handled()`, para a linha não citar um código que a CLI não dá), com nome em `snake_case` e campos de baixa cardinalidade: ids de plugin e de marketplace, contagens, duração em `duration_ms` (o `ms` antigo sai junto só na 2.6), hash curto (12 hex) e um `reason` estável. Nunca caminho, URL (só o host), nome ou slug de cliente, texto digitado, mensagem de erro nem linha de comando; a única exceção é o `target` da linha `event=op`, redigido por `effects.redact_target` (caminho relativo ao projeto ou ao `$GB_HOME`, com hash em todo nome que não é do core; fora deles, só um hash; URL só pelo host; processo só pelo executável). Recusa sem `reason` é bug; sem `error_code`, só dentro de `logs.handled()`. Cada evento novo tem teste que confere os campos e que nada sensível vazou. Efeito colateral (gravar, trocar, apagar, criar pasta, rodar processo, rede, servidor, carregar código) passa por `getbrolls.effects` — um helper ou um bloco `with effects.op(...)` —, que grava a linha `event=op`; o `test_effects_guard` recusa a primitiva chamada fora dele, e a exceção (recursão no log ou nas travas, leitura com `os.open`) leva `# effect-exempt: <motivo>`.

## Testes

- **Nome:** descreve o comportamento (`test_export_refuses_a_machine_path`).
- **Imports:** `import _isolation` vem primeiro e define `GB_HOME`, com supressão na linha porque o import tem efeito.
- **Asserção:** não se muda asserção para um teste passar. Bug corrigido ganha teste de regressão.
- **Isolamento:** sem rede, sem tocar em `~/.getbrolls` e sem depender de ordem de arquivos (o Windows ordena diferente).
- As exceções de pylint próprias dos testes (docstring, acesso a interno, assinatura de mock) ficam em `tests/pylintrc`, cada uma com o motivo.

## Checklist de revisão

1. `bash scripts/check.sh` verde, com ruff, format, pyright, espelho, âncoras e suíte; `pylint` com zero mensagens nos arquivos tocados.
2. Cada supressão nomeia a regra e diz o motivo.
3. Mensagem para a pessoa em pt-BR, acionável e sem caminho da máquina.
4. API pública e contrato do plano intactos, ou a mudança é aditiva e está documentada.
5. Refactor sem mudança de comportamento, provado por teste e comparação de saída.
6. Docs (GUIDE, SDK, references) e CHANGELOG atualizados junto com o código.

## Comando novo

Toda ação nova da CLI, ou flag nova de uma ação, passa por esta lista no mesmo commit:

1. `write_marks.MARKS` tem a marca dela (`L`, `0`, `G`, `OK`, `OK1` ou `P`); flag de modo (`--dry-run`) entra
   em `write_marks.MODE_FLAGS` pelo comando.
2. `cli.SUMMARIES` descreve o comando; quem só lê entra em `command_policy.READ_ONLY_COMMANDS` (ou
   `READ_ONLY_ACTIONS`; o `runtime` só reexporta); `capabilities --json` mostra o que mudou.
3. `tests/fixtures/cli_surface.json` regravado (`GB_UPDATE_CLI_SURFACE=1`) e o espelho da skill em
   sincronia (`python3 scripts/gen_skill_mirror.py --check`).
4. `docs/CLI_CONTRACT.md`: nenhum `error_code` novo (são sete); recusa com motivo estável usa
   `errors.Refusal(…, reason_code="NOME")` e ganha a linha em "Motivos de recusa"; aviso novo entra em
   "Avisos (`warnings`)". JSON novo gravado em disco tem schema em `schemas/` e linha em `docs/SCHEMAS.md`.
5. Toda dica que ele mostra (`do.command`, `hint`, a cauda de um `renamed`, comando entre crases numa
   mensagem) cita só subcomando, flag e escolha que existem: lugar para preencher em MAIÚSCULAS
   (`NOME`, `PROJETO`) ou `"…"` entre aspas; nunca `<nome>` nem reticências soltas; flag com lista de
   `choices` leva uma escolha real, nunca `a|b`. Vale também para comando montado com `+`, para o prefixo da
   CLI, para comando em prosa sem crases e para `SKILL.md`, `commands/` e `references/`
   (`tests/test_hints_parse.py`).

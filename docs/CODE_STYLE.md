---
type: documentation
status: current
created: 2026-09-26
updated: 2026-09-26
tags: [get-brolls, code-style, python, review]
---

# Guia de código

A base é o [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) (CC BY 3.0), com as adaptações abaixo para o stack do get-brolls. Quem escreve código segue este guia, e quem revisa confere contra ele. Uma regra daqui vence a do guia do Google, e o código vizinho vence as duas quando o assunto não está coberto: **seja consistente**.

## Stack

- **Runtime:** Python 3.11+ e **só a biblioteca padrão**, sem dependência de runtime; os plugins de exemplo seguem a mesma regra. Ferramentas externas (FFmpeg, yt-dlp, Playwright, CLIs de engine) são chamadas como processo, nunca importadas.
- **Qualidade:** `ruff check` (regras em `pyproject.toml`), `ruff format` (o formatador; não use Black nem yapf), `pyright` em `basic` e `pylint` (régua em `[tool.pylint]` do `pyproject.toml`; testes com `tests/pylintrc`). Tudo pinado em `requirements-dev.txt`.
- **Testes:** `unittest`, sem rede, com mídia sintética. `bash scripts/check.sh` roda a bateria inteira.

## Regras da linguagem

- **Lint:** o código entra com ruff e pylint limpos. Uma supressão fica **na linha**, nomeia a regra e diz o motivo: `# pylint: disable=broad-exception-caught  # isola o código do plugin` e `# noqa: BLE001 - …`. Supressão no módulo só vale para código legado já documentado.
- **Imports:** `from .modulo import nome` é a convenção do pacote. Import dentro de função é permitido de propósito, porque mantém a CLI rápida para iniciar, carrega módulo de plataforma (`msvcrt`) só onde precisa e, nos testes, deixa `import _isolation` vir antes de tudo. Proibidos: `import *`, import não usado sem motivo, reimport.
- **Exceções:** use as do core (`ValueError`, `PluginError` e as da CLI) com mensagem em pt-BR para a pessoa. `except:` nu é proibido. `except Exception`/`BaseException` só com motivo na linha, como o isolamento de plugin de terceiro. Relance com `raise … from` quando a causa importa e com `from None` quando a cadeia vazaria detalhe interno.
- **Estado global:** nada de global mutável. Constante de módulo em `MAIUSCULAS_COM_SUBLINHADO`.
- **Valor padrão:** nunca mutável (`[]`, `{}`); use `None` ou tupla.
- **Verdadeiro/falso:** falso implícito em coleções (`if not itens:`), `is None` para `None`. `type(x) is dict` vale quando a intenção é rejeitar `bool` e subclasses ao validar JSON; fora disso, `isinstance`.
- **Comprehension:** para casos simples. Com mais de um `for` ou condição que exija leitura, escreva o laço.
- **Tipos:** anotação obrigatória na API pública do SDK (`getbrolls.sdk`) e recomendada no resto; o `pyright` precisa passar.
- **Recursos avançados:** sem metaclasse, sem mexer em `__dict__` alheio, sem `exec`. A exceção documentada é o loader de plugin, que existe para isso.
- **Processos:** sempre lista de argv, sem `shell=True`, com timeout quando a espera puder travar, e um ambiente mínimo quando o processo não precisa do nosso.

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

---
type: documentation
status: current
created: 2026-09-15
updated: 2026-10-22
tags: [get-brolls, documentation]
---

# Contribuição

Leia [AGENTS](AGENTS.md) antes de alterar o código e [GUIDE](docs/GUIDE.md#instalação) para preparar dependências. O fluxo YouTube sem API key e o processo Instagram de dois canais fazem parte do contrato do produto.

## Alterações

Descreva o problema, o comportamento resultante e a validação realizada. Para bugs, reproduza a falha e adicione regressão relevante. Atualize a referência da rota afetada e o CHANGELOG. Comandos de um mesmo projeto devem ser executados serialmente.

Testes automatizados usam mídia sintética e mocks, sem segredos ou conteúdo privado. Ensaios reais de plataforma ficam fora da pasta da skill e registram resultado técnico em [QUALITY](docs/QUALITY.md); falha de rede não deve ser escondida por fixture.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/gb.py doctor
```

No checkout, `python3 scripts/gb.py` é o comando; no pacote instalado (`uv tool install` ou `pipx install`) é `getbrolls`, com os mesmos subcomandos. Desenvolvedor trabalha no checkout.

Para rodar um arquivo de teste só, use o mesmo `discover` com `-p`:
`python3 -m unittest discover -s tests -p "test_x.py"`. A forma
`python3 -m unittest tests.test_x` falha no import (`_isolation`), porque os
módulos de `tests/` se importam pelo nome, com `tests/` no `sys.path` — o que
o `discover -s tests` faz.

Mudanças na revisão visual exigem conferir aprovação/ajuste/sugestão, exportação/importação, impressão e largura móvel conforme o impacto. Revisões apenas documentais precisam validar frontmatter, links e exemplos de CLI, sem refazer downloads desnecessariamente. Mudança no contrato da CLI (código de saída, `error_code`, aviso) ou num formato de arquivo atualiza [`docs/CLI_CONTRACT.md`](docs/CLI_CONTRACT.md) ou [`docs/SCHEMAS.md`](docs/SCHEMAS.md) no mesmo commit; `tests/test_cli_contract_doc.py` e `tests/test_schemas_doc.py` falham quando o código e o documento divergem.

## Lint e type check

O estilo de código (base Google Python Style Guide, adaptada ao nosso stack) e o checklist de revisão estão em [`docs/CODE_STYLE.md`](docs/CODE_STYLE.md). Toda mudança segue, em ordem, o [`docs/CHECKLIST.md`](docs/CHECKLIST.md).

`ruff`, `pyright`, `pylint` e `coverage` são as únicas dependências de desenvolvimento e ficam pinadas em `requirements-dev.txt`; o runtime da CLI continua sem dependência nenhuma. A configuração das três está em `pyproject.toml`: `ruff` com o conjunto padrão do próprio ruff mais E, F, W, I, B, UP, ISC, ICN, G, LOG, PLE, ERA, RSE, C4, PIE, RET, FURB, PERF, TC, A, N, DTZ, SIM, RUF, BLE, PLW, PTH, S, T20, ARG, C90, PLC e PLR em 120 colunas (`extend-select`: a lista completa, as exceções propositais e o porquê dos `# noqa: TRY004` ficam em `[tool.ruff.lint]`), `pyright` em `basic` sobre `scripts/` e `tests/`, e `pylint` na régua de `[tool.pylint]` (`tests/pylintrc` para os testes).

```sh
python3 -m pip install -r requirements-dev.txt
bash scripts/check.sh
```

O job `quality` do CI faz o `pylint` em Python 3.11 (o `pyproject.toml` fixa `py-version = "3.11"`, mas o pylint infere assinatura de stdlib pelo interpretador que roda o lint, não por esse campo); um `.venv` criado com `python3.11` reproduz exatamente o que o CI vê, então rode `bash scripts/check.sh` também sob 3.11 antes de empurrar uma mudança que toque código de plataforma condicional.

No Windows, `./scripts/check.ps1` roda a mesma bateria. Os dois executam, em ordem, `ruff check`, `ruff format --check`, `pyright`, `pylint scripts examples`, `pylint` nos testes (com `PYTHONPATH=scripts:tests` e `--rcfile tests/pylintrc`), `python3 scripts/gen_skill_mirror.py --check` (o espelho da skill em `skills/get-brolls/SKILL.md` é gerado a partir do `SKILL.md` da raiz — nunca edite o espelho à mão), `python3 scripts/check_anchors.py` e a suíte de testes — o mesmo que o CI cobre: o job `quality` roda `ruff`, `pyright` e as duas passadas de `pylint` no Ubuntu, e a matriz `Tests` roda a suíte (que inclui a conferência de âncoras) e o `--check` do espelho nos três sistemas.

No Windows o `ruff` não aplica EXE001/EXE002 (shebang sem bit de execução e o contrário), e um arquivo novo entra no git como `100644`. Quem confere nos três sistemas é a suíte (`tests/test_repository.py`), pelo modo gravado no índice: um `.py` com `#!` na primeira linha precisa de `git update-index --chmod=+x <arquivo>`, e um sem shebang, de `--chmod=-x`.

## Cobertura

O gate de cobertura roda a suíte inteira sob `coverage`, medindo também cada CLI que os testes sobem como subprocesso, e falha quando uma meta não é atingida, com o arquivo e as linhas ou ramos que faltam:

```sh
python3 -m pip install -r requirements-dev.txt   # traz o coverage pinado
python3 scripts/check_coverage.py                 # --keep DIR guarda os dados e o coverage.json
```

As metas ficam num lugar só, `[tool.getbrolls.coverage]` do `pyproject.toml`, e valem sem arredondamento: no total de `scripts/` (o pacote e os scripts avulsos, como `bump_version.py`), 95% das linhas e 90% dos ramos; cada arquivo de `scripts/getbrolls/` (inclusive `sdk/`), 90% e 85%; e os módulos de contrato (`cli`, `runtime`, `errors`, `_paths`, `ledger`, `effects`, `logs`), 100% das linhas e dos ramos. Saída 0 com tudo atingido; 1 com a suíte vermelha ou uma meta de fora; 2 para erro de ambiente (sem `coverage`, configuração inválida, nenhum dado gravado).

A medição de subprocesso depende de `COVERAGE_PROCESS_START` chegar ao filho: teste que monta um ambiente fechado repassa essa variável, como `hermetic_env()` em `tests/_golden.py` (ela não muda nada do que o ouro grava). Código só de outro sistema (ramos de Windows ou Linux) é testado simulando a plataforma, como os testes de `serve`, `runtime` e `logs` fazem; `# pragma: no cover` só onde a simulação é impossível, com o motivo na linha. Teste de cobertura confere comportamento (saída, código de saída, arquivo, evento de log), nunca só executa a linha.

No CI, só a célula `macos-latest` / 3.11 da matriz `Tests` roda a suíte pelo gate (no lugar da rodada simples): é o sistema em que as metas são medidas localmente, então um ramo só de macOS ou só de Linux não muda o resultado entre a máquina e o CI. As outras células seguem com `python -m unittest discover -s tests -v`.

## Dependências e releases

Revise `requirements.txt` e `package-lock.json` junto com mudanças nas dependências. O `requirements.txt` tem hashes (o `setup` instala com `--require-hashes --only-binary :all:`) e não se edita à mão: mude o pino em `requirements.in` e regenere, da raiz, guardando o cabeçalho escrito à mão (o `uv` o descarta): `sed '/^# This file was autogenerated/,$d' requirements.txt > header.tmp && uv pip compile --universal --generate-hashes --python-version 3.11 requirements.in -o requirements.txt -q && cat header.tmp requirements.txt > merged.tmp && mv merged.tmp requirements.txt && rm header.tmp`; sem mudar nenhum pino, o resultado é idêntico ao arquivo versionado. O pip da venv vem de `requirements-bootstrap.txt`: ao subir a versão, copie os dois sha256 (roda e sdist) de `https://pypi.org/pypi/pip/<versão>/json`. Os instaladores usam o conjunto registrado; não faça atualização global nem incorpore bibliotecas no repositório. O Dependabot propõe atualizações por PR; elas exigem testes e, quando afetarem aquisição, ensaio da rota correspondente. `ruff`, `pylint` e `astroid` ficam fora dele: um bump muda a régua de lint (o `extend-select` do ruff acompanha o conjunto padrão, que cresce nos minors 0.x), então sobe à mão, num PR só dele, junto com a re-baseline das supressões. As GitHub Actions ficam fixadas por SHA.

A branch `main` é produção: o plugin instalado por quem usa a skill acompanha essa branch, e cada release baixada por um usuário sai de um commit dela. A branch principal deve exigir a matriz `Tests` antes do merge — essa proteção é uma configuração do GitHub feita pelo mantenedor, o arquivo do workflow não a ativa; verifique os nomes dos checks no PR ao configurar a regra. Mudança de comportamento entra por PR com CI verde nos três sistemas operacionais, nunca por push direto em `main`, e vem acompanhada de entrada no CHANGELOG e de compatibilidade retroativa.

Uma correção de código incrementa a versão com `python3 scripts/bump_version.py X.Y.Z --date AAAA-MM-DD`, que escreve numa passada só `scripts/getbrolls/__init__.py`, `package.json`/`package-lock.json`, `.claude-plugin/plugin.json`/`marketplace.json`, `SKILL.md` (o espelho é regerado junto), READMEs, `docs/QUALITY.md` e o stub do CHANGELOG; `--check` confere as mesmas fontes sem escrever. Antes de empurrar a tag, rode `bash scripts/preflight.sh --version X.Y.Z`: o mesmo portão que o release roda contra o commit da tag — versão coerente, frontmatter, ausência de material interno, espelho da skill, âncoras, suíte completa e seção do CHANGELOG no formato esperado — e sai 0 com `PREFLIGHT OK` ou nomeia o passo que reprovou. Só depois disso empurre uma tag `vX.Y.Z` apontando para o commit aprovado; nunca mova uma tag já distribuída para outro código. **O ato manual do mantenedor é o push da tag.** A partir dele, `.github/workflows/release.yml` publica sozinho: instala FFmpeg, roda `bash scripts/preflight.sh --version "$VERSION"` contra o commit da própria tag, extrai as notas da seção correspondente do CHANGELOG — que precisa começar exatamente com `## <versão> — ` (travessão em em dash, não hífen) — e cria a release com `gh`, marcada como pré-lançamento quando a tag tem hífen (por exemplo `v2.4.0-rc1`). Sem o preflight verde ou com a seção fora do formato esperado, nada é publicado.

## Arquivos publicados

O próprio repositório é a entrega. Inclua skill, AGENTS, documentação, código e interface necessários para um clone funcional. Dependências são instaladas pelos comandos do GUIDE; não incorpore bibliotecas, fontes externas, binários, ambientes virtuais, cookies, configs CDN, mídia de clientes ou exemplos preenchidos com dados reais.

Código próprio sob MIT, conforme LICENSE. Dependências mantêm suas licenças, descritas em THIRD_PARTY_NOTICES. Publicação, push e atualização de instalação pessoal são ações separadas da revisão local.

---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-17
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

Mudanças na revisão visual exigem conferir aprovação/ajuste/sugestão, exportação/importação, impressão e largura móvel conforme o impacto. Revisões apenas documentais precisam validar frontmatter, links e exemplos de CLI, sem refazer downloads desnecessariamente.

## Lint e type check

`ruff` e `pyright` são as únicas dependências de desenvolvimento e ficam pinadas em `requirements-dev.txt`; o runtime da CLI continua sem dependência nenhuma. A configuração das duas está em `pyproject.toml` (`ruff` com E/F/W/I/B/UP em 120 colunas, `pyright` em `basic` sobre `scripts/` e `tests/`).

```sh
python3 -m pip install -r requirements-dev.txt
bash scripts/check.sh
```

No Windows, `./scripts/check.ps1` roda a mesma bateria. Os dois executam, em ordem, `ruff check`, `ruff format --check`, `pyright` e a suíte de testes — exatamente o que o job `quality` do CI cobre no Ubuntu, ao lado da matriz `Tests` nos três sistemas.

## Dependências e releases

Revise `requirements.txt` e `package-lock.json` junto com mudanças nas dependências. Os instaladores usam o conjunto registrado; não faça atualização global nem incorpore bibliotecas no repositório. O Dependabot propõe atualizações por PR; elas exigem testes e, quando afetarem aquisição, ensaio da rota correspondente. As GitHub Actions ficam fixadas por SHA.

A branch principal deve exigir a matriz `Tests` antes do merge. Essa proteção é uma configuração do GitHub feita pelo mantenedor; o arquivo do workflow não a ativa. Verifique os nomes dos checks no PR ao configurar a regra.

Uma correção de código incrementa a versão em `scripts/getbrolls/__init__.py`, SKILL, READMEs, manifestos npm, GUIDE, QUALITY e CHANGELOG. Após a aprovação e o merge, empurre uma tag `vX.Y.Z` apontando para o commit aprovado; nunca mova uma tag já distribuída para outro código. **O ato manual do mantenedor é o push da tag.** A partir dele, `.github/workflows/release.yml` publica sozinho: confere que `__version__` é igual à versão da tag, roda `python3 -m unittest discover -s tests`, extrai as notas da seção correspondente do CHANGELOG — que precisa começar exatamente com `## <versão> — ` (travessão em em dash, não hífen) — e cria a release com `gh`, marcada como pré-lançamento quando a tag tem hífen (por exemplo `v2.4.0-rc1`). Sem a seção no formato esperado, sem a suíte verde ou com a versão divergente, nada é publicado.

## Arquivos publicados

O próprio repositório é a entrega. Inclua skill, AGENTS, documentação, código e interface necessários para um clone funcional. Dependências são instaladas pelos comandos do GUIDE; não incorpore bibliotecas, fontes externas, binários, ambientes virtuais, cookies, configs CDN, mídia de clientes ou exemplos preenchidos com dados reais.

Código próprio sob MIT, conforme LICENSE. Dependências mantêm suas licenças, descritas em THIRD_PARTY_NOTICES. Publicação, push e atualização de instalação pessoal são ações separadas da revisão local.

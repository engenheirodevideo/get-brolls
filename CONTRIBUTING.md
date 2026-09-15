---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, documentation]
---

# Contribuição

Leia [AGENTS](AGENTS.md) antes de alterar o código e [GUIDE](GUIDE.md#instalação) para preparar dependências. O fluxo YouTube sem API key e o processo Instagram de dois canais fazem parte do contrato do produto.

## Alterações

Descreva o problema, o comportamento resultante e a validação realizada. Para bugs, reproduza a falha e adicione regressão relevante. Atualize a referência da rota afetada e o CHANGELOG. Comandos de um mesmo projeto devem ser executados serialmente.

Testes automatizados usam mídia sintética e mocks, sem segredos ou conteúdo privado. Ensaios reais de plataforma ficam fora da pasta da skill e registram resultado técnico em [QUALITY](QUALITY.md); falha de rede não deve ser escondida por fixture.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/gb.py doctor
```

Mudanças na revisão visual exigem conferir aprovação/ajuste/sugestão, exportação/importação, impressão e largura móvel conforme o impacto. Revisões apenas documentais precisam validar frontmatter, links e exemplos de CLI, sem refazer downloads desnecessariamente.

## Arquivos de distribuição

Inclua skill, AGENTS, documentação, scripts próprios e interface. Dependências são instaladas pelos comandos do GUIDE; não incorporar bibliotecas, fontes externas, binários, ambientes virtuais, cookies, configs CDN ou exemplos preenchidos. Novos arquivos necessários devem entrar na seleção mantida em `scripts/package_release.py`, mesmo quando a entrega atual for somente a pasta. Não execute esse empacotador sem pedido de empacotamento.

Código próprio sob MIT, conforme LICENSE. Dependências mantêm suas licenças, descritas em THIRD_PARTY_NOTICES. Publicação, push e atualização de instalação pessoal são ações separadas da revisão local.

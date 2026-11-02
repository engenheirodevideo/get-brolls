# Exemplo de exportador (`exporter_example`)

Plugin de exemplo do SDK do getbrolls: o menor exportador útil. O exportador `exporter_example`
transforma o roteiro revisado e sincronizado numa lista de cortes:

- `cortes.csv`: planilha CSV com `;` e BOM (abre no Excel em português), uma linha por cena, com início e
  duração em segundos, o layout, o título e a fala;
- `EXPORT.md`: o tema, as cenas e as pendências do plano.

É uma função pura: recebe o plano de export do core (`export_version` 2) e devolve texto. Não pede
mídia, não usa rede, não lê pasta nenhuma e não tem permissão nenhuma; quem grava a pasta em
`exports/exporter_example/NNN/` é o core. Use como ponto de partida para um exportador do seu editor
(veja [`SDK.md#exportadores`](../../../docs/SDK.md#exportadores)).

Todo texto do plano é dado não confiável: no CSV, uma célula que começaria por `=`, `+`, `-` ou `@`
ganha um apóstrofo na frente (a planilha não a lê como fórmula) e nunca quebra a linha (sequências de
espaço, tab e quebra de linha na `fala` e no título viram um espaço só); no
`EXPORT.md`, a marcação sai escapada.

## Instalação

O exemplo vem dentro da instalação do getbrolls, em `examples/plugins/exporter_example` da pasta de dados
dela: o `install.data_root` que o `doctor` mostra (num clone ou no plugin do Claude Code, a própria raiz da
instalação; no pacote, a pasta `_data` dele). Passe o caminho absoluto em `--source`, de qualquer pasta. No
pacote:

```sh
getbrolls plugins --action install --source <install.data_root>/examples/plugins/exporter_example
```

Num clone ou no plugin, rode o script da própria instalação pelo caminho absoluto dela (e troque
`getbrolls` por ele nos comandos abaixo):

```sh
python3 <raiz da instalação>/scripts/gb.py plugins --action install --source <raiz da instalação>/examples/plugins/exporter_example
```

Sem `--yes`, o `install` só mostra o que chegaria, com o `sha256` do conteúdo. Confira e confirme com
`--yes --expect` desse valor; isso já habilita o plugin.

## Uso

Com o roteiro revisado e sincronizado:

```sh
getbrolls export --to exporter_example --project <projeto>
```

O resultado fica em `exports/exporter_example/NNN/`, com o plano exato que o exportador recebeu
(`getbrolls-plan.json`).

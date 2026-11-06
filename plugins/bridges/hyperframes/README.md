# Ponte HyperFrames (plugin oficial)

## O que é

A ponte entre o getbrolls e o HyperFrames. O getbrolls coleta, mostra a prévia e entrega os clipes com
origem e condições de uso; quem monta e renderiza o vídeo é o próprio HyperFrames. A ponte só faz a passagem
entre os dois.

## O que ela faz por você

- Vem instalada com o getbrolls: você não instala nem habilita nada.
- `getbrolls environment --action list` mostra a versão dela e se está ligada (`status`).
- Ela traz duas funções: uma escreve o pedido para o lado do HyperFrames (um `LEIA-ME.md` que manda
  preservar a fala revisada e não publicar nem renderizar sem o ok da pessoa) e a outra diz, pelos arquivos,
  em que pé está a pasta do projeto HyperFrames (esperando, projeto pronto ou quebrado). Nesta versão,
  nenhum comando do getbrolls chama essas funções: a ponte vem conferida e listada.
- Para desligá-la, use `GB_PLUGINS=off` ou `plugins = []` no perfil `getbrolls.toml`.

## O que ela não faz

- Não monta timeline, não compõe e não renderiza: isso é do HyperFrames.
- O getbrolls não exporta uma timeline do HyperFrames, e isso é de propósito. `getbrolls export --to
  hyperframes` é recusado (saída `2`); o `export` é para exportadores de plugin.
- Não toca disco, rede nem processo, e não pede permissão nenhuma.

## Confiança

O core fixa o hash desta pasta: um byte mudado, um arquivo a mais, um link ou bytecode fora do que o
instalador compila deixa a ponte `invalid`, e ela nunca roda. Uma cópia com o id `hyperframes` em
`$GB_HOME/plugins/` é recusada; detalhes em [SECURITY.md](../../../docs/SECURITY.md#plugins).

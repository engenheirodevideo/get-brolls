# Ponte HyperFrames (plugin oficial)

Vem com o getbrolls: no checkout em `plugins_official/hyperframes/`, no pacote instalado em
`getbrolls/_data/plugins_official/hyperframes/`. A confiança é o `folder_digest` desta pasta, fixado no
core (`scripts/getbrolls/sdk/official_pins.py`, gerado por `scripts/gen_official_pins.py`): um byte mudado,
um arquivo a mais, um link ou bytecode fora do que o instalador compila deixa a ponte `invalid`, e ela
nunca roda.

Não copie esta pasta para `$GB_HOME/plugins/`: um plugin de terceiros com o id `hyperframes` é recusado.

A ponte é pura: `package` devolve texto e `readback` lê só a lista que o core montou. Ela não toca disco,
rede nem processo, e não pede permissão nenhuma.

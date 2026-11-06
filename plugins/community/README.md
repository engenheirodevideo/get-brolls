# Plugins da comunidade

Um plugin da comunidade é um plugin do SDK escrito e mantido por terceiros, não pelo getbrolls. Ele mora no
repositório git de quem o escreveu e chega até você por um marketplace: um repositório git com um
`getbrolls-marketplace.json` na raiz que lista os plugins, cada um com a origem, o commit e o `sha256` do
conteúdo (formato em [`docs/SDK.md`](../../docs/SDK.md#marketplaces)). Qualquer pessoa pode publicar um
marketplace; o `tier` de cada entrada é o que o dono do índice declara, não uma verificação. Esta pasta é o
lugar reservado a eles na árvore `plugins/` do repositório (`plugins/community/<id>/`); hoje ela não traz
nenhum.

As outras pastas de `plugins/` têm outro papel: `plugins/bridges/` guarda as pontes oficiais para
ferramentas externas, `plugins/getbrolls/` os plugins oficiais feitos para o próprio getbrolls (as duas vêm
no pacote e só rodam com o pin do core) e `plugins/examples/` os exemplos do SDK.

## Instalar

Nenhum plugin da comunidade roda sozinho. Na sua máquina, todo plugin instalado mora em `$GB_HOME/plugins/`
(padrão `~/.getbrolls/plugins/`), e chega lá em dois passos:

```sh
getbrolls plugins --action install --source <pasta ou URL git do plugin>
getbrolls plugins --action install --source <pasta ou URL git do plugin> --yes --expect <sha256 da prévia>
```

O primeiro só mostra o manifesto, as permissões, a origem e o `sha256` do conteúdo. Depois de ler o código e
as permissões, o segundo instala e habilita. De um marketplace adicionado com `plugins --action
marketplace-add --source <url-git-do-marketplace>`, o mesmo vale para
`getbrolls plugins --action install --id <id>@<marketplace>`, também em dois passos.

`plugins --action list` mostra a origem de cada plugin instalado. Um plugin com o id de uma ponte ou de um
plugin oficial é recusado.

O plugin roda como código local de quem usa a CLI: não há caixa de areia. Antes de instalar, passe pelo
checklist de segurança do [guia rápido de plugins](../../docs/PLUGIN_DEV_QUICKSTART.md); a referência
completa está em [`docs/SDK.md`](../../docs/SDK.md).

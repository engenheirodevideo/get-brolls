# Pasta local de B-rolls

Plugin de exemplo do SDK do getbrolls: um `Provider` que busca vídeos pelo
nome do arquivo numa pasta do seu computador, sem chamar nenhuma API externa.
Serve como ponto de partida para escrever sua própria fonte — o código inteiro
está em `plugin.py` e usa só o que o [SDK.md](../../../docs/SDK.md) documenta.

## O que faz

`search` varre `PASTA_LOCAL_DIR` recursivamente, procurando arquivos `.mp4`,
`.mov`, `.m4v` ou `.webm` cujo nome (sem extensão) contenha todas as palavras
da busca. Cada arquivo encontrado vira um candidato só com metadados.

A rota `pasta_local` (`stage="preview"`) entrega o arquivo quando você pede a
prévia: `preview --candidate <ID> --start ... --end ...` funciona direto, sem
`resolve --file`. A rota acha o arquivo pelo id da busca e pede ao core uma
cópia de trabalho com `api.local_file` — o core recusa qualquer arquivo fora
de `permissions.paths` e nunca mexe no original. O plugin não declara host em
`permissions.network`, então `api.get_json`/`api.download` recusam qualquer
chamada — isso não é caixa de areia: o código do plugin roda com as suas
permissões.

O comando `recentes` lista os vídeos mais novos da pasta e quantos candidatos
dela já estão no projeto:

```sh
getbrolls x pasta_local recentes --arg limite=5 --project <projeto>
```

## Instalação

O exemplo vem dentro da instalação do getbrolls: `<install.data_root>` é a pasta de dados que o `doctor`
mostra em `install.data_root` (num clone ou no plugin, a raiz da instalação; no pacote, a pasta `_data`
dele). Com o caminho absoluto, o comando vale de qualquer pasta. Confira antes
`permissions.paths` (veja abaixo).

```sh
getbrolls plugins --action install --source <install.data_root>/plugins/examples/pasta_local
getbrolls plugins --action install --source <install.data_root>/plugins/examples/pasta_local --yes --expect <sha256 da prévia>
```

O primeiro só mostra o manifesto, as permissões, a origem e o `sha256` do conteúdo. Depois de ler o código e
as permissões, o segundo instala e habilita. O plugin vai para `$GB_HOME/plugins/pasta_local/` (`GB_HOME`
padrão: `~/.getbrolls`).

Se a pasta do plugin mudar depois, ele fica `suspended`: `getbrolls plugins --action enable --id pasta_local`
mostra o que mudou (arquivos adicionados, removidos e alterados) e o novo `sha256`; para religar, confirme
com `--yes --expect <sha256>` desse valor.

## Variável de ambiente e raízes

- `PASTA_LOCAL_DIR`: caminho absoluto da pasta com os vídeos a buscar. Sem essa
  variável (ou apontando para algo que não é pasta), `search` falha com uma
  mensagem pedindo para configurá-la.
- `permissions.paths` no manifesto vem com `["~/Movies"]` (a pasta de vídeos do
  macOS). No Windows, a pasta equivalente é `~/Videos`: troque a entrada por
  `"~/Videos"`. Se a sua pasta de B-rolls fica em outro lugar (um NAS, outro
  disco), edite essa lista **antes**
  do `install` — o pin de hash cobre o manifesto. `PASTA_LOCAL_DIR`
  tem que ficar dentro de uma dessas raízes para a prévia funcionar.
- Fora das raízes, a **busca funciona** (ela só lê nomes de arquivo com o
  código do próprio plugin), mas a **prévia é recusada**: quem copia o arquivo
  é o `api.local_file` do core, e ele só aceita caminhos dentro de
  `permissions.paths`. Achou o candidato e a prévia falhou com "fora de
  permissions.paths"? Ajuste a lista (e confirme de novo o `enable` do plugin suspenso) ou mova os vídeos.

## Testar antes de habilitar

```sh
getbrolls plugins --action check --path <install.data_root>/plugins/examples/pasta_local
```

`check` valida o manifesto e roda `register()` contra um registro descartável,
sem instalar nada — útil para pegar erro de nome ou de manifesto antes do
`install`. Ele **executa** o código da pasta: rode só em plugin que você leu.

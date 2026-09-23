# Pasta local de B-rolls

Plugin de exemplo do SDK do Get B-rolls: um `Provider` que busca vídeos pelo
nome do arquivo numa pasta do seu computador, sem chamar nenhuma API externa.
Serve como ponto de partida para escrever sua própria fonte — o código inteiro
está em `plugin.py` e usa só o que o [SDK.md](../../../docs/SDK.md) documenta.

## O que faz

`search` varre `PASTA_LOCAL_DIR` recursivamente, procurando arquivos `.mp4`,
`.mov`, `.m4v` ou `.webm` cujo nome (sem extensão) contenha todas as palavras
da busca. Cada arquivo encontrado vira um candidato com só metadados
(`id`, `title`); nenhum arquivo é copiado, movido ou lido além do nome.

Depois de aprovado, o candidato entra no fluxo comum com `resolve --file`,
apontando para o caminho real na pasta — exatamente como qualquer original
local que você já importaria manualmente. O plugin não baixa, não copia e não
tem acesso à rede (`permissions.network` vazio no manifesto).

## Instalação

Copie a pasta para dentro da sua instalação pessoal do Get B-rolls:

```sh
cp -r examples/plugins/pasta_local ~/.getbrolls/plugins/
```

Habilite em dois passos. O primeiro só mostra o manifesto e as permissões
declaradas — confira com a pessoa antes de continuar:

```sh
python3 scripts/gb.py plugins --action enable --id pasta_local
```

Depois de revisar, confirme com `--yes` para de fato habilitar (isso grava um
pin de hash da pasta inteira; qualquer mudança no conteúdo suspende o plugin
até um novo `enable`):

```sh
python3 scripts/gb.py plugins --action enable --id pasta_local --yes
```

## Variável de ambiente

- `PASTA_LOCAL_DIR`: caminho absoluto da pasta com os vídeos a buscar. Sem essa
  variável (ou apontando para algo que não é pasta), `search` falha com uma
  mensagem pedindo para configurá-la.

## Testar antes de habilitar

```sh
python3 scripts/gb.py plugins --action check --path examples/plugins/pasta_local
```

`check` valida o manifesto e roda `register()` contra um registro descartável,
sem habilitar nada — útil para pegar erro de nome ou de manifesto antes do
`enable`.

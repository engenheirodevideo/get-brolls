# Pasta local de B-rolls

Plugin de exemplo do SDK do Get B-rolls: um `Provider` que busca vídeos pelo
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
de `permissions.paths` e nunca mexe no original. O plugin não tem acesso à
rede (`permissions.network` vazio).

O comando `recentes` lista os vídeos mais novos da pasta e quantos candidatos
dela já estão no projeto:

```sh
python3 scripts/gb.py x pasta_local recentes --arg limite=5 --project <projeto>
```

## Instalação

Copie a pasta para dentro da sua instalação pessoal do Get B-rolls — a pasta
`plugins/` fica em `$GB_HOME` (por padrão, `~/.getbrolls`) e pode ainda não
existir, por isso o `mkdir -p` antes: com `plugins/` existindo, o `cp` copia a
pasta `pasta_local` para dentro dela (em vez de espalhar os arquivos soltos), e
rodar de novo só sobrescreve a mesma `plugins/pasta_local`:

```sh
mkdir -p "${GB_HOME:-$HOME/.getbrolls}/plugins"
cp -r examples/plugins/pasta_local "${GB_HOME:-$HOME/.getbrolls}/plugins/"
```

Ou deixe o `plugins --action install --source examples/plugins/pasta_local`
copiar e registrar a origem (sem `--yes` ele só mostra o que chegaria,
inclusive o `sha256` do conteúdo; com `--yes --expect <sha256>` — o mesmo
valor da prévia — já instala e habilita, dispensando o `enable` abaixo).

Habilite em dois passos. O primeiro só mostra o manifesto e as permissões
declaradas — confira com a pessoa antes de continuar:

```sh
python3 scripts/gb.py plugins --action enable --id pasta_local
```

Depois de revisar, confirme com `--yes` para de fato habilitar (isso grava um
pin de hash da pasta inteira, exceto lixo de SO como `.DS_Store` e o
conteúdo de uma pasta de VCS como `.git`; qualquer outra mudança no conteúdo
suspende o plugin até um novo `enable`):

```sh
python3 scripts/gb.py plugins --action enable --id pasta_local --yes
```

Se o plugin ficar `suspended` porque a pasta mudou, o `enable` sem `--yes`
mostra o que mudou (arquivos adicionados, removidos e alterados) e o novo
`sha256`; para religar, confirme com `--yes --expect <sha256>` desse valor.

## Variável de ambiente e raízes

- `PASTA_LOCAL_DIR`: caminho absoluto da pasta com os vídeos a buscar. Sem essa
  variável (ou apontando para algo que não é pasta), `search` falha com uma
  mensagem pedindo para configurá-la.
- `permissions.paths` no manifesto vem com `["~/Movies"]`. Se a sua pasta de
  B-rolls fica em outro lugar (um NAS, outro disco), edite essa lista **antes**
  do `enable`/`install` — o pin de hash cobre o manifesto. `PASTA_LOCAL_DIR`
  tem que ficar dentro de uma dessas raízes para a prévia funcionar.
- Fora das raízes, a **busca funciona** (ela só lê nomes de arquivo com o
  código do próprio plugin), mas a **prévia é recusada**: quem copia o arquivo
  é o `api.local_file` do core, e ele só aceita caminhos dentro de
  `permissions.paths`. Achou o candidato e a prévia falhou com "fora de
  permissions.paths"? Ajuste a lista (e habilite de novo) ou mova os vídeos.

## Testar antes de habilitar

```sh
python3 scripts/gb.py plugins --action check --path examples/plugins/pasta_local
```

`check` valida o manifesto e roda `register()` contra um registro descartável,
sem habilitar nada — útil para pegar erro de nome ou de manifesto antes do
`enable`.

# Banco HTTP (exemplo)

Plugin de exemplo do SDK do Get B-rolls: um banco de vídeos com API
autenticada. Mostra o caminho completo de um arquivo que **consome licença**:
a busca devolve metadados, e o original só é baixado no `fetch`, depois da
aprovação humana e do `permit`, pela rota `banco_http` (`stage="fetch"`).

`api.banco.example` é um host de exemplo: para usar de verdade, troque a URL em
`plugin.py` e o host em `permissions.network` pelos da API do seu banco.

## O que faz

- `search` chama `GET /v1/search` com `Authorization: Bearer $BANCO_HTTP_TOKEN`
  e devolve um candidato por item (título, página pública, duração, miniatura
  `thumb_url` e player `embed_url`). Sem miniatura nem player, não há o que
  mostrar à pessoa e o `approve` recusa o candidato.
- A rota `banco_http` chama `GET /v1/videos/<id>/license` e registra o texto
  devolvido como licença; depois baixa `GET /v1/videos/<id>/file` com
  `api.download`, que grava o arquivo na pasta de trabalho criada pelo core.
- O core confere o arquivo (dentro da pasta de trabalho, até 512 MB, legível
  pelo `ffprobe`), corta o trecho aprovado e grava em `clips/`. A licença entra
  em `rights.evidence` como `Licença registrada pelo plugin banco_http: …`,
  **depois** do permit humano.
- O token vai só no header: nunca aparece na URL, no log nem em mensagem de erro.

## Fluxo

```sh
python3 scripts/gb.py search --provider banco_http --query "praia" --project <projeto>
python3 scripts/gb.py preview --candidate banco_http:<id> --start 0 --end 4 --reference-only --project <projeto>
python3 scripts/gb.py approve --candidate banco_http:<id> --by NOME --channel chat --statement "frase exata" --project <projeto>
python3 scripts/gb.py permit --candidate banco_http:<id> --evidence "condições reais do plano" --project <projeto>
python3 scripts/gb.py fetch --candidate banco_http:<id> --project <projeto>
```

`preview` sem `--reference-only` é recusado com uma mensagem que diz isso:
baixar a prévia gastaria a licença antes da decisão humana.

## Instalação

```sh
python3 scripts/gb.py plugins --action install --source examples/plugins/banco_http
python3 scripts/gb.py plugins --action install --source examples/plugins/banco_http --yes --expect <sha256-da-prévia>
```

O primeiro comando só mostra manifesto, permissões, origem e o `sha256` do
conteúdo; repita com `--yes --expect <sha256>` — o mesmo valor que a prévia
mostrou — para instalar e habilitar de fato. Configure `BANCO_HTTP_TOKEN` no
`.env` ou no ambiente.

---
type: documentation
status: current
created: 2026-09-29
updated: 2026-09-30
tags: [get-brolls, sdk, plugins, quickstart]
---

# Quickstart — criar plugins do Get B-rolls

Este guia é o caminho curto para uma pessoa externa criar um plugin com segurança. A referência completa continua em [`SDK.md`](SDK.md); os exemplos funcionais ficam em [`../examples/plugins/`](../examples/plugins/).

## O modelo mental

Um plugin é uma pasta local com:

```text
meu_plugin/
├── getbrolls-plugin.json
├── plugin.py
├── README.md
└── tests/
```

O plugin roda como código local do usuário. **Não é sandbox.** Leia o código antes de habilitar, confira permissões e use `--expect <sha256>` para garantir que o conteúdo instalado é o mesmo revisado. O comando `plugins --action check` também **executa o código do plugin** (`register()`), então revise a fonte e o manifesto antes de usá-lo em qualquer pasta que você não escreveu.

## Tipos que existem hoje

```text
provider   busca candidatos e devolve metadados
route      traz o arquivo para preview/fetch quando o core pedir
command    adiciona comandos em gb x <plugin> <comando>
exporter   experimental: exporta o roteiro revisado para outro formato/app
resolver   experimental: resolve mídia faltante no export, como SFX/música
```

Para um plugin de motion/editor, comece por **exporter**. O exporter recebe o plano de roteiro do core e devolve arquivos de projeto, HTML, JSON, EDL, manifestos ou pedidos de mídia. O core continua dono de revisão, direitos, hash, entrega e escrita em disco.

## Crie um plugin mínimo

Crie o scaffold em uma pasta de trabalho **fora da instalação do Get B-rolls**, para não misturar rascunho com a release:

```sh
mkdir -p "$HOME/getbrolls-plugin-lab"
python3 scripts/gb.py plugins --action new --id meu_motion --kind exporter --path "$HOME/getbrolls-plugin-lab"
```

No Windows PowerShell:

```powershell
$lab = Join-Path $env:USERPROFILE "getbrolls-plugin-lab"
New-Item -ItemType Directory -Force $lab | Out-Null
python scripts\gb.py plugins --action new --id meu_motion --kind exporter --path $lab
```

O scaffold `new` gera **provider**, **route**, **command** ou **exporter**. O `--kind exporter` traz um exportador puro que devolve um `index.html` com o título de cada cena, escapado com `html.escape`, sem pedir mídia, e recusa `export_version` diferente de 1. Depois de revisar o código gerado, valide:

```sh
python3 scripts/gb.py plugins --action check --path "$HOME/getbrolls-plugin-lab/meu_motion"
```

No PowerShell:

```powershell
python scripts\gb.py plugins --action check --path (Join-Path $lab "meu_motion")
```

Para ir além do exporter gerado (pedir mídia, gravar outros formatos), veja [`SDK.md#exportadores`](SDK.md#exportadores) e use [`../examples/plugins/hyperframes`](../examples/plugins/hyperframes/) como referência, reduzindo ao menor caso possível. Lembre: `check` executa `register()` e, para exporter, roda o exportador contra um plano sintético; não use em código não revisado.

## O manifesto em uma tela

O `getbrolls-plugin.json` gerado já traz o obrigatório. Os campos opcionais que valem a pena preencher antes de publicar:

```json
{
  "homepage": "https://example.com/meu_motion",
  "license": "MIT",
  "author": "Seu nome",
  "keywords": ["motion", "export"],
  "platforms": ["darwin", "linux", "windows"],
  "requires": {"python": ["jinja2>=3.1"], "binaries": ["node"], "runtimes": {"node": ">=18"}},
  "permissions": {"network": [], "env": [], "paths": [], "project_write": []},
  "metadata": {}
}
```

- `platforms` ausente vale todos os sistemas; fora da lista, o plugin fica `incompatible`.
- `requires` só informa: o get-brolls não instala nada nem executa binários do plugin para conferir.
- `permissions.project_write` aceita só `"analysis"` nesta versão; deixe `[]` se o plugin não grava no projeto.
- `metadata` é livre para ferramentas de terceiros e o core o ignora.
- `engines` no topo foi aposentado: a faixa de versão de um motor vai em `requires.runtimes`.

Os campos de topo e as chaves de `permissions` estão congelados em `sdk_api` 1: campo novo só dentro de `metadata` ou com `sdk_api` 2. Qualquer outro campo recusa o manifesto. Referência completa em [`SDK.md#manifesto`](SDK.md#manifesto).

## Instale em dois passos

Nunca instale às cegas. Primeiro prévia:

```sh
python3 scripts/gb.py plugins --action install --source "$HOME/getbrolls-plugin-lab/meu_motion"
```

Revise id, versão, permissões e `sha256`. Depois confirme exatamente aquele hash:

```sh
python3 scripts/gb.py plugins --action install --source "$HOME/getbrolls-plugin-lab/meu_motion" --yes --expect <sha256-da-previa>
```

Se a pasta mudar, o plugin fica suspenso até nova revisão e novo `--expect`.

## Checklist de segurança antes de enviar ou habilitar

- [ ] sem `.env`, token, cookie, chave, URL assinada ou credencial;
- [ ] sem `.git`, `.hg`, `.svn`, symlink, `__pycache__`, `.pyc`, `.pyo` dentro do plugin;
- [ ] sem mídia, projeto de cliente, render, cache, output ou log privado;
- [ ] `permissions.network` contém só hosts necessários;
- [ ] `permissions.env` contém só variáveis do próprio plugin;
- [ ] `permissions.paths` não aponta para home inteira, raiz do disco ou pasta ampla demais;
- [ ] `permissions.project_write` fica `[]`, a menos que o plugin grave análises no projeto;
- [ ] você leu `plugin.py` e `getbrolls-plugin.json` antes do `check`;
- [ ] `plugins --action check --path <plugin>` retorna `"ok": true`;
- [ ] README do plugin explica o que faz, permissões, instalação e teste;
- [ ] se exportar projeto, não gravar caminhos absolutos nem dados privados nos arquivos gerados.

## Exemplos incluídos

| Exemplo | Use para aprender |
|---|---|
| [`examples/plugins/pasta_local`](../examples/plugins/pasta_local/) | provider + route + command, sem rede |
| [`examples/plugins/banco_http`](../examples/plugins/banco_http/) | API autenticada, token via `api.env`, download em `fetch` |
| [`examples/plugins/hyperframes`](../examples/plugins/hyperframes/) | exporter + resolver experimental para projeto de motion/edição |

## Caminho recomendado para plugin de motion

1. Gere o exporter mínimo com `plugins --action new --kind exporter` (só `index.html`) e adapte para o formato do seu editor.
2. Revise a fonte e rode `plugins --action check` até passar.
3. Instale/habilite com `install --yes --expect <sha256>` depois da prévia.
4. Exporte com `gb export --to <id> --dry-run --project <projeto-teste>` somente em projeto revisado/sincronizado.
5. Só depois peça mídia via `MediaRequest`.
6. Registre no README quais arquivos o editor/motion tool espera abrir.
7. Trate tudo como experimental até rodar com um roteiro revisado real.

## O que o plugin não deve fazer

- Não aprovar candidato por conta própria.
- Não declarar licença inventada.
- Não baixar mídia fora de rota/consentimento do core.
- Não escrever em pastas do projeto fora do que o core fornece.
- Não vazar caminho absoluto do computador do usuário.
- Não depender de estado local secreto para funcionar sem documentar.

## Referências

- [`SDK.md`](SDK.md) — contrato completo do SDK.
- [`SECURITY.md#plugins`](SECURITY.md#plugins) — modelo de segurança.
- [`MANUAL.md`](MANUAL.md) — comandos de operação.
- [`examples/plugins/hyperframes/README.md`](../examples/plugins/hyperframes/README.md) — referência mais próxima de plugin de motion/export.

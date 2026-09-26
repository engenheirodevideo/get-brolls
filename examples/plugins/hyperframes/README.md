# HyperFrames

Plugin de exemplo do SDK do Get B-rolls com os dois contratos experimentais de
export:

- o **exporter** `hyperframes`, que transforma o roteiro revisado e
  sincronizado num projeto HyperFrames editável: `index.html` com o tempo global, uma sub-composição por cena,
  legendas, voz, música com ducking, SFX, cartelas para o que falta gravar e um
  `EXPORT.md` com pendências, créditos e próximos passos;
- o **resolvedor** `hyperframes_media`, que acha `[SFX: nome]` e
  `[MUSICA: nome]` no acervo do `media-use` quando o projeto e a biblioteca
  pessoal não têm o arquivo.

O código inteiro está em `plugin.py` e usa só o que o
[SDK.md](../../../docs/SDK.md) documenta. O exporter é uma função pura: recebe o
plano de export do core e devolve textos e pedidos de mídia por id lógico; quem
grava a pasta e põe a mídia em `assets/` é o core.

## Instalação

```sh
python3 scripts/gb.py plugins --action install --source examples/plugins/hyperframes
```

Sem `--yes`, o `install` só mostra o que chegaria, com o `sha256` do conteúdo.
Confira com a pessoa e confirme com `--yes --expect <sha256>` desse valor; isso
já habilita o plugin. Para habilitar depois, ou de novo:

```sh
python3 scripts/gb.py plugins --action enable --id hyperframes
```

O `enable` sem `--yes` mostra o manifesto e as permissões (`permissions.paths`
com `~/.media`, nenhuma rede, nenhuma variável); `--yes` grava o pin.

## Uso

Com o roteiro revisado e sincronizado:

```sh
python3 scripts/gb.py export --to hyperframes --dry-run --project <projeto>
python3 scripts/gb.py export --to hyperframes --project <projeto>
```

Cada export é uma pasta nova em `exports/hyperframes/NNN/`. O `EXPORT.md` dela
traz os comandos da CLI HyperFrames, sempre da raiz do projeto
(`npx --yes hyperframes@0.8.77 lint|check|preview|render exports/hyperframes/NNN`).
Versão testada da CLI: **0.8.77** (`HYPERFRAMES_VERSION` no `plugin.py`); o
`check` e o `render` baixam GSAP e a fonte Inter na primeira vez.

Diretivas `[hyperframes:<bloco>]` no roteiro viram um comentário na cena e um
item no `EXPORT.md` com o `hyperframes add <bloco>`; ligue o bloco com a skill
`/hyperframes-registry`.

## Acervo do media-use

O resolvedor só lê `manifest.jsonl` — nunca escreve em `.media/`, nunca chama a
rede nem o `media-use resolve`:

1. projetos listados em `media_projects` no `settings.json` do plugin
   (`$GB_HOME/plugin-data/hyperframes/settings.json`, lista de pastas absolutas):
   `<projeto>/.media/manifest.jsonl`, arquivo em `<projeto>/<path>`;
2. o acervo global `~/.media/manifest.jsonl` (registros `reusable: true`),
   arquivo em `cached_path`, só com o sentinela `.hf-complete` ao lado.

O nome casa, nessa ordem, com `id`, `entity` e `provenance.prompt` ou
`provenance.library_key` (sem caixa e com espaços colapsados). Dois registros
diferentes no mesmo nível é ambiguidade: o core mostra o aviso e segue sem o
arquivo. `[SFX]` procura `type: sfx`; `[MUSICA]`, `type: bgm`.

Só entram pastas dentro de `permissions.paths` (`~/.media`). Para usar um projeto
em outro lugar, acrescente a raiz ao manifesto do plugin instalado: com o
conteúdo mudado, ele fica `suspended`. Rode a prévia do `enable` (sem `--yes`),
confira a mudança com a pessoa e confirme com `--yes --expect <sha256>` da
prévia. A licença que o plugin informa é só informativa: aparece nos créditos
como "Licença informada pelo plugin hyperframes: …" e nunca vale como `permit`.

Um `manifest.jsonl` que é link, FIFO ou pasta, ou que passa de 16 MB, não é lido:
o core mostra o aviso e segue sem o arquivo daquele nome.

## Limites

- Uma gravação por cena em `aroll/`; take contínuo não é suportado.
- `COMP` e blocos do registro não são ligados automaticamente.
- O estilo do `LETTERING` vira só uma classe CSS (`lettering--<estilo>`).
- Áudio em `.aif`, `.aiff`, `.ogg` ou `.m4a` não toca no Chrome do Studio: o
  `EXPORT.md` pede a conversão para `.wav` ou `.mp3`.

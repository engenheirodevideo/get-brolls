"""`entrega/`: o texto que o `deliver` escreve (`ORIGEM.md` de cada trecho e `README.md`).

Só monta texto a partir do candidato e das linhas da entrega, sem tocar em disco (a
`created` de um arquivo já gerado é a única leitura). `inert` e `evidence_line` fecham o
Markdown de plugin: nada vindo de fora vira imagem, link ou ênfase. Também é onde `rendering`,
`sdk.exporters` e `sdk.resolvers` buscam esses ajudantes, sem depender de `delivery`.
"""

import re
from datetime import date

from .runtime import one_line

# Aviso do ORIGEM.md quando o arquivo é uma cópia independente.
COPY_NOTE = (
    "**Esta é uma cópia independente** (`GB_DELIVERY_COPY`, ou porque o sistema não "
    "deixou criar link): edite à vontade, o original em `brolls/` não é afetado. Rodar "
    "`deliver` de novo não mexe numa cópia que você editou — ele para e diz o nome dela."
)

# Aviso que vai no README.md e em cada ORIGEM.md: hardlink é o mesmo arquivo.
EDIT_WARNING = (
    "**Editar aqui é editar o original.** Estes arquivos são o mesmo arquivo de "
    "`brolls/` com outro nome (hardlink), e por isso vêm marcados como somente-leitura: "
    "copie antes de mexer. Se você quiser editar dentro de `entrega/`, rode o `deliver` "
    "com `GB_DELIVERY_COPY=1` e receba cópias independentes."
)


def _frontmatter(kind, created, tags):
    today = date.today().isoformat()  # noqa: DTZ011 - local date; tz-aware would shift day near midnight
    return [
        "---",
        f"type: {kind}",
        "status: current",
        f"created: {created or today}",
        f"updated: {today}",
        f"tags: [{', '.join(tags)}]",
        "---",
        "",
    ]


def created_in(path):
    """Preserva o `created` de um arquivo já gerado: repetir não muda o histórico."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[:8]:
            if line.startswith("created:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return None


def _segment_label(c):
    start = (c.get("segment") or {}).get("start_s")
    end = (c.get("segment") or {}).get("end_s")
    if start is None or end is None:
        return "não definido"
    return f"{start:g}s → {end:g}s"


def _author(c):
    """Quem assina a fonte: `creator.name` quando existe, senão o canal/uploader bruto.

    Provedores de vídeo costumam devolver só `channel`/`uploader`; dizer "não
    informado" com o nome do canal na mão esconde crédito que existe.
    """
    for value in (
        (c.get("creator") or {}).get("name"),
        c.get("channel"),
        c.get("uploader"),
        (c.get("media") or {}).get("channel"),
        (c.get("media") or {}).get("uploader"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "não informado"


def plugin_label(c):
    """Id do plugin dono da fonte do candidato, ou `None` para as fontes embutidas.

    Só lê manifesto/pin (`loader.declared_by`, nunca roda código de plugin); plugin
    que saiu da pasta desde a busca cai no nome da fonte gravado no candidato."""
    from .providers import BUILTIN_CAPABILITIES

    provider = c.get("provider")
    if not isinstance(provider, str) or provider in BUILTIN_CAPABILITIES:
        return None
    from .sdk import loader

    row = loader.declared_by(provider)
    return row["id"] if row else provider


# Só os caracteres que abrem marcação dentro de uma linha: `<img>`, `[link](url)`,
# `**ênfase**`, `` `código` ``, `|` de tabela, `%%comentário%%` e `==realce==` do
# Obsidian, `~~riscado~~`, `#tag`, `$matemática$`, `{atributo}`. Ponto, vírgula, hífen e
# dois-pontos ficam como estão: o texto continua legível colado numa descrição.
_MARKDOWN_SIGNIFICANT = re.compile(r"([\\`*_\[\]()<>!|%~=#${}])")
# Autolink sem `<>`: só o gatilho é quebrado (`https\://`, `www\.`).
_AUTOLINK_SCHEME = re.compile(r"://")
_AUTOLINK_WWW = re.compile(r"(?i)\b(www)\.")
# Prefixos que o core escreve na frente de um texto de plugin guardado como evidência.
_PLUGIN_EVIDENCE_PREFIXES = ("Licença registrada pelo plugin ", "Condições informadas pelo plugin ")


def inert(value, plugin):
    """`value` numa linha só e, se veio de um plugin (`plugin` não é `None`), com todo
    caractere que Markdown/HTML interpreta escapado por barra invertida: um título com
    `<img>`, `[link](url)` ou `**negrito**` sai como texto em ORIGEM.md/credits.md, nunca
    como imagem remota, link ou ênfase. Fonte embutida: só `one_line`, texto idêntico."""
    text = one_line(value)
    if plugin is None:
        return text
    text = _MARKDOWN_SIGNIFICANT.sub(r"\\\1", text)
    text = _AUTOLINK_SCHEME.sub(r"\\://", text)
    return _AUTOLINK_WWW.sub(r"\1\\.", text)


def evidence_line(evidence, plugin):
    """Itens de evidência juntos por `"; "`. Num candidato de plugin, o texto depois do
    prefixo que o core pôs num registro do plugin (licença, condições do preset) sai
    inerte; o que a pessoa disse sai como ela disse."""
    items = []
    for item in evidence or []:
        text = one_line(item)
        if plugin is not None and text.startswith(_PLUGIN_EVIDENCE_PREFIXES):
            head, sep, tail = text.partition(": ")
            text = head + sep + inert(tail, plugin) if sep else inert(text, plugin)
        items.append(text)
    return "; ".join(items)


def source_label(c, plugin):
    """Linha "Fonte:" de ORIGEM.md/credits.md. Fonte embutida: a URL ou "original
    local", como sempre; fonte de plugin: o plugin e a URL (ou "arquivo local") —
    antes saía "original local" sem dizer de onde o arquivo veio.
    `plugin` é o `plugin_label(c)` que quem chama já calculou."""
    url = c.get("source_url")
    if plugin is None:
        return url or "original local"
    return f"plugin {plugin} ({inert(url, plugin) if url else 'arquivo local'})"


def render_origin(c, media_name, created=None, method="hardlink", canonical=None):
    """`ORIGEM.md` do trecho: fonte, autor, intervalo, direitos e sha256 do arquivo.

    `canonical` é onde o original está, relativo à raiz do projeto (`broll/…` no
    layout 1); sem ele, `brolls/` + o caminho lógico do manifesto, como sempre.
    """
    rights = c.get("rights") or {}
    approval = c.get("approval") or {}
    plugin = plugin_label(c)
    lines = _frontmatter("delivery-origin", created, ["get-brolls", "entrega"])
    # pylint: disable-next=fixme
    # Todo valor vindo do candidato passa por `one_line`: um título/licença/evidência
    # com quebra de linha (plugin ou ledger editado à mão) nunca forja outra linha
    # "- Direitos:"/"- Aprovado por:" neste registro. Texto normal sai idêntico.
    lines += [
        f"# Origem de {one_line(media_name)}",
        "",
        f"- Arquivo: `{one_line(media_name)}`",
        f"- Candidato: `{one_line(c['id'])}`",
        f"- Título na fonte: {inert(c.get('title') or 'não informado', plugin)}",
        f"- Fonte: {one_line(source_label(c, plugin))}",
        f"- Autor: {inert(_author(c), plugin)}",
        f"- Trecho usado: {_segment_label(c)}",
        f"- Direitos: {one_line(rights.get('status') or 'unknown')}",
        f"- Licença: {inert(rights.get('license_name') or 'ver evidência', plugin)}",
        f"- Licença URL: {inert(rights.get('license_url') or 'não informada', plugin)}",
        "- Evidência: " + (evidence_line(rights.get("evidence"), plugin) or "não registrada"),
        f"- Aprovado por: {one_line(approval.get('by') or 'não registrado')}"
        + (f" ({one_line(approval.get('channel'))})" if approval.get("channel") else ""),
        f"- sha256 do arquivo coletado: `{one_line((c.get('output') or {}).get('sha256') or 'não calculado')}`",
        "- Original canônico: `"
        + (one_line(canonical) if canonical else f"brolls/{one_line((c.get('output') or {}).get('path'))}")
        + "`",
        "",
        EDIT_WARNING if method in ("hardlink", "symlink") else COPY_NOTE,
        "",
        (
            "Este arquivo é gerado por `deliver`. A pasta `entrega/` inteira pode ser apagada "
            "e refeita: o que vale é `brolls/`."
        ),
        "",
    ]
    return "\n".join(lines)


# Numa entrega misturada (parte link, parte cópia), nenhum aviso global é verdade: um
# diz "editar aqui é editar o original" sobre arquivos que são cópias independentes, o
# outro diz o contrário sobre hardlinks. A resposta vira coluna, linha a linha.
MIXED_NOTE = (
    "**Esta entrega tem os dois casos.** Parte dos arquivos é o mesmo arquivo de "
    "`brolls/` com outro nome (hardlink) e parte é cópia independente — o sistema não "
    "deixou criar link para todos. A coluna **Edição** diz qual é qual: "
    "`original compartilhado` vem somente-leitura e editar ali edita o original "
    "(copie antes de mexer); `editável` você muda à vontade. Para receber tudo como "
    "cópia, rode o `deliver` com `GB_DELIVERY_COPY=1`."
)
# Rótulo por linha quando a entrega é misturada.
EDIT_LABELS = {"copy": "editável", "hardlink": "original compartilhado", "symlink": "original compartilhado"}


def _mixed(rows):
    """A entrega tem cópia e link ao mesmo tempo? Aí nenhum aviso global serve."""
    kinds = {"copy" if row.get("method") == "copy" else "link" for row in rows if row.get("method")}
    return len(kinds) > 1


def render_index(rows, for_human=None, created=None, conflicts=(), copies=False):
    """`entrega/README.md`: a tabela que responde “onde estão meus arquivos”."""
    mixed = _mixed(rows)
    columns = ["beat", "narration", "target", "file", "state", "rights"]
    headers = ["Beat", "Narração", "Alvo", "Arquivo", "Estado", "Direitos"]
    if mixed:
        columns.append("edit")
        headers.append("Edição")
    lines = _frontmatter("delivery-index", created, ["get-brolls", "entrega"])
    lines += [
        "# Seus trechos",
        "",
        (
            "Cada pasta é um trecho do vídeo, na ordem do BRIEF.md. É só arrastar o `.mp4` "
            "para o seu editor; o `ORIGEM.md` ao lado diz de onde ele veio e o que você me "
            "disse sobre poder usar."
        ),
        "",
        MIXED_NOTE if mixed else (COPY_NOTE if copies else EDIT_WARNING),
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]
    for row in rows:
        cells = dict(row)
        if mixed:
            cells["edit"] = EDIT_LABELS.get(row.get("method") or "", "—")
        lines.append(
            "| " + " | ".join(str(cells.get(key) or "—").replace("|", "/").replace("\n", " ") for key in columns) + " |"
        )
    if not rows:
        # A linha vazia sai das mesmas colunas do cabeçalho: contar células à mão
        # deixava a tabela torta (5 células para 6 colunas) em todo projeto sem clipe.
        empty = dict.fromkeys(columns, "—")
        empty["file"] = "nenhum trecho coletado ainda"
        lines.append("| " + " | ".join(empty[key] for key in columns) + " |")
    if conflicts:
        # Texto puro é arquivo editado pela pessoa; `(arquivo, motivo)` é outro motivo.
        edited = [item for item in conflicts if isinstance(item, str)]
        other = [item for item in conflicts if not isinstance(item, str)]
        lines += ["", "## Conflitos", ""]
        if edited:
            lines += [
                (
                    "Estes trechos não foram refeitos porque o arquivo em `entrega/` tem conteúdo "
                    "diferente do que está em `brolls/` — parece edição sua e eu não sobrescrevo. "
                    "Renomeie ou apague o arquivo e rode `deliver` de novo:"
                ),
                "",
            ]
            lines += [f"- `{item}`" for item in edited]
        if other:
            lines += [*([""] if edited else []), "Estes trechos ficaram de fora pelo motivo dito em cada linha:", ""]
            lines += [f"- `{path}`: {note}" for path, note in other]
    lines += ["", "## Próximo passo", "", for_human or "Nada pendente por aqui.", ""]
    return "\n".join(lines)

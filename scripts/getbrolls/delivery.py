"""`entrega/`: a mesma coleta, organizada por beat, em nomes que uma pessoa lê.

`brolls/` continua canônico — nada aqui apaga, renomeia ou move o que está lá. Esta
camada é derivada e regenerável: cada arquivo de `clips/` aparece em `entrega/` por
hardlink (ou symlink, ou cópia, nessa ordem), com o contact sheet ao lado e um
`ORIGEM.md` dizendo de onde veio. Rodar de novo não estraga nada, e um arquivo que a
pessoa editou à mão nunca é sobrescrito: o comando termina o trabalho e então falha
nomeando todos os arquivos em conflito.

Hardlink é o padrão porque vídeo pesa e duas cópias de cada corte dobram o projeto —
mas hardlink é o *mesmo* arquivo, com dois nomes: editar em `entrega/` editaria o
original em `brolls/clips/`. Por isso a mídia entregue nasce somente-leitura (a
proteção vale para o inode compartilhado, e nenhum comando reescreve esse arquivo no
lugar: `fetch` grava um `-r<N>` novo e `verify` só lê) e tanto o `README.md` quanto o
`ORIGEM.md` dizem isso em uma frase. Quem quiser editar dentro de `entrega/` define
`GB_DELIVERY_COPY=1` e recebe cópias independentes.

`c["delivery"]["path"]` é relativo à **raiz do projeto** (começa em `entrega/`), ao
contrário de `c["output"]["path"]`, que é relativo a `brolls/`.
"""

import logging
import os
import re
import shutil
import stat
import unicodedata
from collections import namedtuple
from datetime import date
from pathlib import Path

from . import logs
from .runtime import record_warning

log = logs.get("delivery")

# Pasta derivada, na raiz do projeto — irmã de `brolls/`, nunca dentro dela.
DELIVERY_DIR = "entrega"
# Onde ficam os trechos coletados que nenhum beat reivindicou.
NO_BEAT = "00-sem-beat"
INDEX = "README.md"
ORIGIN = "ORIGEM.md"
SHEET = "contact-sheet"
# Nome de pasta que ainda cabe num Finder/Explorer sem virar reticências.
MAX_NAME = 60
# Só o que o gerador escreve pode ser apagado quando vira órfão.
GENERATED = re.compile(r"^(ORIGEM(-\d+)?\.md|contact-sheet(-\d+)?\.[a-z0-9]+)$")
BEAT_DIR_RE = re.compile(r"^\d{2}-[a-z0-9-]*$")
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


def slug(text):
    """Texto humano vira pedaço de caminho: ASCII, minúsculo, só letras/números/hífen."""
    normal = unicodedata.normalize("NFKD", str(text or ""))
    ascii_only = normal.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_only)).strip("-")


def _safe(value, field):
    """Recusa qualquer coisa que tentaria sair da pasta de entrega."""
    text = str(value or "")
    if ".." in text or "/" in text or "\\" in text or os.sep in text:
        raise ValueError(
            f'Valor inválido para o nome da pasta de entrega ({field}): "{text}". '
            "Ids de beat usam só letras minúsculas, números e hífen; corrija o BRIEF.md "
            "ou o --shot do candidato."
        )
    return text


def beat_dir_name(nn, beat_id, target):
    """`NN-<beat.id>-<slug(alvo)>`: estável entre execuções e seguro como caminho."""
    _safe(beat_id, "id do beat")
    _safe(target, "alvo do beat")
    prefix = f"{int(nn):02d}"
    parts = [prefix, slug(beat_id), slug(target)]
    name = "-".join(p for p in parts if p)
    name = re.sub(r"-+", "-", name).strip("-")[:MAX_NAME].rstrip("-")
    if not name or name in (".", "..") or not BEAT_DIR_RE.match(name):
        raise ValueError(
            f'Não consegui montar um nome de pasta seguro para o beat "{beat_id}". '
            "Use um id com letras minúsculas, números e hífen."
        )
    return name


class _CompareError(Exception):
    """Raised when a comparison could not complete because of an OSError.

    Kept distinct from a real content difference: `link_or_copy` must never report
    "parece edição sua" when it simply failed to read one of the two files.
    """


def _same_file(a, b):
    try:
        return os.path.samestat(Path(a).stat(), Path(b).stat())
    except OSError as exc:
        raise _CompareError(str(exc)) from exc


def _same_bytes(a, b, block=1024 * 1024):
    """Compara em blocos, nunca carregando um vídeo inteiro na memória."""
    try:
        if Path(a).stat().st_size != Path(b).stat().st_size:
            return False
        with Path(a).open("rb") as left, Path(b).open("rb") as right:
            while True:
                chunk = left.read(block)
                if chunk != right.read(block):
                    return False
                if not chunk:
                    return True
    except OSError as exc:
        raise _CompareError(str(exc)) from exc


def _compared(compare, dest, src):
    """Run one comparison; an I/O failure is reported as such, never as a user edit."""
    try:
        return compare(dest, src)
    except _CompareError as exc:
        raise ValueError(
            f"Não consegui comparar {dest} com o arquivo coletado ({exc}): confira "
            "permissão ou disponibilidade do arquivo e rode `deliver` de novo."
        ) from exc


def copies_forced():
    """`GB_DELIVERY_COPY=1`: cópias independentes para quem quer editar em `entrega/`."""
    return (os.environ.get("GB_DELIVERY_COPY") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _freeze(path, method):
    """Tira a escrita só de quem compartilha bytes com o original.

    Hardlink e symlink são o mesmo arquivo com outro nome: editar ali editaria
    `brolls/`. Uma cópia é independente — congelá-la quebraria a promessa de
    `GB_DELIVERY_COPY=1`, que existe justamente para quem quer editar em `entrega/`.

    Uma falha aqui fica não fatal (o resto da entrega continua), mas nunca some: vai
    para `record_warning`, o mesmo canal de aviso que o resto do projeto usa, para que
    a promessa de "somente-leitura" não fique silenciosamente falsa.
    """
    if method not in ("hardlink", "symlink"):
        return
    try:
        mode = Path(path).stat().st_mode
        Path(path).chmod(mode & ~0o222)
    except OSError as exc:
        record_warning(
            "DELIVERY_FREEZE_FAILED",
            f"Não consegui deixar {path} somente-leitura ({exc}): o arquivo entregue "
            "compartilha o inode com o original em brolls/, mas ficou editável.",
        )


def _thaw_unlink(path):
    """Apaga o que este gerador congelou, inclusive no Windows.

    `_freeze` tira o bit de escrita; no Windows isso vira o atributo somente-leitura
    e `unlink` levanta `PermissionError`. Devolver a escrita antes é o único jeito de
    `deliver` regenerar `entrega/` depois de um beat renomeado.
    """
    path = Path(path)
    try:
        path.unlink()
        return
    except PermissionError:
        pass
    path.chmod(stat.S_IWRITE | stat.S_IREAD)
    path.unlink()


def link_or_copy(src, dest, read_only=False):  # noqa: C901 - existing size; hardlink/reflink/copy fallback ladder across OSes
    """Liga `dest` a `src` pelo jeito mais barato que o sistema aceitar.

    Hardlink primeiro (não ocupa disco e não quebra ao mover a pasta de dentro),
    symlink depois (no Windows pode faltar privilégio) e cópia por último. Devolve o
    método usado. Se `dest` já é o mesmo arquivo, não faz nada; se é um arquivo comum
    com conteúdo diferente, é obra da pessoa e o erro nomeia o arquivo.
    """
    src, dest = Path(src), Path(dest)
    methods = (
        (("copy", shutil.copy2),)
        if copies_forced()
        else (
            ("hardlink", os.link),
            ("symlink", os.symlink),
            ("copy", shutil.copy2),
        )
    )
    if dest.is_symlink():
        try:
            if dest.resolve() == src.resolve():
                return "symlink"
        except OSError:
            pass
        _thaw_unlink(dest)
    elif dest.exists():
        if _compared(_same_file, dest, src):
            if read_only:
                _freeze(dest, "hardlink")
            return "hardlink"
        if _compared(_same_bytes, dest, src):
            return "copy"
        raise ValueError(
            f"{dest} já existe com conteúdo diferente do arquivo coletado: parece edição "
            "sua e eu não sobrescrevo. Renomeie ou apague esse arquivo e rode `deliver` "
            "de novo."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    for method, make in methods:
        try:
            make(str(src), str(dest))
            if read_only:
                _freeze(dest, method)
            return method
        except (OSError, NotImplementedError, AttributeError):
            if dest.is_symlink() or dest.exists():
                _thaw_unlink(dest)
    raise OSError(
        f"Não consegui ligar nem copiar {src} para {dest}. Confira espaço e permissão de escrita na pasta do projeto."
    )


def _frontmatter(kind, created, tags):
    today = date.today().isoformat()  # noqa: DTZ011 - local date in the frontmatter; timezone-aware would shift the day near midnight
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


def _created_in(path):
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


def render_origin(c, media_name, created=None, method="hardlink"):
    """`ORIGEM.md` do trecho: fonte, autor, intervalo, direitos e sha256 do arquivo."""
    rights = c.get("rights") or {}
    approval = c.get("approval") or {}
    lines = _frontmatter("delivery-origin", created, ["get-brolls", "entrega"])
    lines += [
        f"# Origem de {media_name}",
        "",
        f"- Arquivo: `{media_name}`",
        f"- Candidato: `{c['id']}`",
        f"- Título na fonte: {c.get('title') or 'não informado'}",
        f"- Fonte: {c.get('source_url') or 'original local'}",
        f"- Autor: {_author(c)}",
        f"- Trecho usado: {_segment_label(c)}",
        f"- Direitos: {rights.get('status') or 'unknown'}",
        f"- Licença: {rights.get('license_name') or 'ver evidência'}",
        f"- Licença URL: {rights.get('license_url') or 'não informada'}",
        "- Evidência: " + ("; ".join(rights.get("evidence") or []) or "não registrada"),
        f"- Aprovado por: {approval.get('by') or 'não registrado'}"
        + (f" ({approval.get('channel')})" if approval.get("channel") else ""),
        f"- sha256 do arquivo coletado: `{(c.get('output') or {}).get('sha256') or 'não calculado'}`",
        f"- Original canônico: `brolls/{(c.get('output') or {}).get('path')}`",
        "",
        EDIT_WARNING if method in ("hardlink", "symlink") else COPY_NOTE,
        "",
        "Este arquivo é gerado por `deliver`. A pasta `entrega/` inteira pode ser apagada "
        "e refeita: o que vale é `brolls/`.",
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
        "Cada pasta é um trecho do vídeo, na ordem do BRIEF.md. É só arrastar o `.mp4` "
        "para o seu editor; o `ORIGEM.md` ao lado diz de onde ele veio e o que você me "
        "disse sobre poder usar.",
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
        lines += [
            "",
            "## Conflitos",
            "",
            "Estes trechos não foram refeitos porque o arquivo em `entrega/` tem conteúdo "
            "diferente do que está em `brolls/` — parece edição sua e eu não sobrescrevo. "
            "Renomeie ou apague o arquivo e rode `deliver` de novo:",
            "",
        ]
        lines += [f"- `{item}`" for item in conflicts]
    lines += ["", "## Próximo passo", "", for_human or "Nada pendente por aqui.", ""]
    return "\n".join(lines)


def _brief_beats(project):
    """Beats do BRIEF.md quando ele existe e é válido; lista vazia quando não dá."""
    try:
        from .brief import load_brief, validate_brief

        data, _ = validate_brief(load_brief(project))
        return [b["resolved"] for b in data["beats"]]
    except (ValueError, OSError):
        return []


def _plan(project, items):
    """Um grupo por beat, na ordem do brief; sem brief, na ordem do manifesto.

    Fica de fora quem não tem `output.path`, quem tem a chave `output.verified`
    presente com um valor falso (`False`, `0`, `None` — verificação de sha256 que
    falhou ou nunca rodou) e quem foi rejeitado na aprovação — mesmo que `reject` já
    limpe `output` nesses casos, a checagem aqui é defesa extra. Um item sem a chave
    `verified` (manifesto antigo) segue endereçado normalmente; a mesma regra que
    `STAGE_TESTS["verified"]` usa em `commands.py`.
    """
    collected, skipped = [], []
    for c in items:
        output = c.get("output") or {}
        if not output.get("path"):
            continue
        if "verified" in output and not output["verified"]:
            skipped.append(
                {
                    "id": c["id"],
                    "reason": (
                        "o arquivo em brolls/ não confere mais com o que foi coletado: restaure o "
                        "original, ou apague esse arquivo e rode `fetch` de novo; depois, `verify`."
                    ),
                }
            )
            logs.event(log, logging.INFO, "deliver_skipped", candidate=c["id"], reason="unverified")
            continue
        if (c.get("approval") or {}).get("status") == "rejected":
            skipped.append({"id": c["id"], "reason": "candidato foi rejeitado: não entra na entrega."})
            logs.event(log, logging.INFO, "deliver_skipped", candidate=c["id"], reason="rejected")
            continue
        collected.append(c)
    beats = _brief_beats(project)
    order = [b["id"] for b in beats]
    meta = {b["id"]: b for b in beats}
    for c in collected:
        shot = c.get("shot")
        if shot and shot not in order:
            order.append(shot)
    groups = []
    for position, beat_id in enumerate(order, start=1):
        beat = meta.get(beat_id) or {}
        members = [c for c in collected if c.get("shot") == beat_id]
        if not members:
            continue
        target = beat.get("target") or members[0].get("title") or beat_id
        groups.append(
            {
                "beat": beat_id,
                "narration": beat.get("narration"),
                "target": target,
                "dir": beat_dir_name(position, beat_id, target),
                "items": members,
            }
        )
    orphans = [c for c in collected if not c.get("shot")]
    if orphans:
        groups.append(
            {
                "beat": None,
                "narration": None,
                "target": "trechos sem beat no brief",
                "dir": NO_BEAT,
                "items": orphans,
            }
        )
    return groups, skipped


def _names(group_dir, index, source_suffix, sheet_suffix):
    tail = "" if index == 1 else f"-{index}"
    return {
        "media": f"{group_dir}{tail}{source_suffix}",
        "sheet": f"{SHEET}{tail}{sheet_suffix}",
        "origin": ORIGIN if index == 1 else f"ORIGEM{tail}.md",
    }


def _symlink_targets_brolls(path, brolls_root):
    """Um symlink só é nosso quando aponta para um clipe dentro do `brolls/` deste projeto.

    Um link que a pessoa criou (para a própria mídia, um atalho, outra pasta) não bate
    com isso e não pode ser tratado como órfão do gerador.
    """
    try:
        target = path.resolve()
        brolls_real = Path(brolls_root).resolve()
    except OSError:
        return False
    return target == brolls_real or brolls_real in target.parents


def _ours(rel, path, owned, brolls_root):
    """Só é órfão o que este gerador escreveu; o resto é da pessoa e fica.

    Reconhecemos três assinaturas: um caminho que o próprio manifesto registra em
    `c["delivery"]`, os nomes que o gerador usa (`README.md`, `ORIGEM*.md`,
    `contact-sheet*.*` e a mídia, que repete o nome da pasta do beat) e, só quando o
    nome já bateu com uma dessas assinaturas, um symlink que resolve para dentro de
    `brolls/`. Um bilhete — ou um link — que a pessoa deixou dentro da pasta não casa
    com nada disso e é preservado.
    """
    if rel in owned or rel == INDEX:
        return True
    name = path.name
    parent = path.parent.name
    name_matches = bool(GENERATED.match(name)) or (
        bool(BEAT_DIR_RE.match(parent)) and re.fullmatch(re.escape(parent) + r"(-\d+)?\.[A-Za-z0-9]+", name) is not None
    )
    if not name_matches:
        return False
    if path.is_symlink():
        return _symlink_targets_brolls(path, brolls_root)
    return True


def _confined(path, real_root):
    """A exclusão só é segura quando o local real de `path` está dentro de `real_root`.

    `entrega/` em si já foi recusada como symlink em `build_delivery`, mas uma pasta de
    beat marcada à mão como link ainda poderia levar `rglob` para fora do projeto — esta
    checagem confirma o caminho físico antes de qualquer `unlink`/`rmdir`.
    """
    try:
        parent_real = path.parent.resolve()
    except OSError:
        return False
    return parent_real == real_root or real_root in parent_real.parents


_SweepContext = namedtuple("_SweepContext", "expected dry_run owned brolls_root real_root")


def _sweep_empty_directory(path, rel, ctx):
    """One orphaned-beat-directory candidate for `_sweep`: `"removed"`, `"kept"`, or
    `None` (not a candidate at all — same gate `_sweep` used to apply inline).

    A symlinked "directory" needs its own removal path: `rmdir` on a symlink to an
    empty directory raises `NotADirectoryError` on POSIX, and blindly removing it
    could take someone else's folder with it. It is only `unlink()`'d (never
    `rmdir()`'d) when the same rules that already cover a symlinked FILE — `_ours`
    and `_symlink_targets_brolls` — recognize it as this generator's own; otherwise
    it is foreign and stays. A real empty directory that fails to `rmdir`
    (permissions, a race) is left in place and reported as a warning, never an
    aborted delivery.
    """
    if not (
        BEAT_DIR_RE.match(path.name)
        and not any(path.iterdir())
        and rel not in ctx.expected
        and _confined(path, ctx.real_root)
    ):
        return None
    if path.is_symlink():
        if not _ours(rel, path, ctx.owned, ctx.brolls_root):
            return "kept"
        if not ctx.dry_run:
            _thaw_unlink(path)
        return "removed"
    if not ctx.dry_run:
        try:
            path.rmdir()
        except OSError as exc:
            record_warning(
                "DELIVERY_SWEEP_RMDIR_FAILED",
                f"Não consegui apagar a pasta vazia {path} ({exc}); ela continua em entrega/.",
            )
            return None
    return "removed"


def _sweep(root, expected, dry_run, owned=(), brolls_root=None):
    """Apaga só o que este gerador escreveu e que deixou de existir no plano."""
    removed, kept = [], []
    owned = set(owned)
    if not root.is_dir():
        return removed, kept
    ctx = _SweepContext(expected, dry_run, owned, brolls_root, root.resolve())
    for path in sorted(root.rglob("*"), reverse=True):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            outcome = _sweep_empty_directory(path, rel, ctx)
            if outcome == "removed":
                removed.append(rel)
            elif outcome == "kept":
                kept.append(rel)
            continue
        if rel in expected:
            continue
        if _ours(rel, path, owned, brolls_root) and _confined(path, ctx.real_root):
            if not dry_run:
                _thaw_unlink(path)
            removed.append(rel)
        else:
            kept.append(rel)
    logs.event(log, logging.INFO, "sweep", removed=len(removed), kept_foreign=len(kept))
    return removed, kept


def build_delivery(project, dry_run=False, ledger=None, for_human=None):  # noqa: C901, PLR0912, PLR0915 - existing size; rebuilds `entrega/` through every item/state combination
    """Refaz `entrega/` a partir do que já está coletado em `brolls/`.

    Não toca em `brolls/`, não decide nada e não inventa direito de uso: só reorganiza
    o que `fetch` já produziu. Com `dry_run`, devolve o mesmo relatório sem escrever
    um byte — nem na pasta, nem no manifesto, e o método de cada item sai como
    `"planned"`, porque só a escrita diz qual o sistema aceita.

    `for_human` pode ser um texto ou uma função sem argumentos: ela é chamada **depois**
    da materialização, para que o "próximo passo" do índice fale do estado novo e não do
    passo que acabou de ser executado.

    Um arquivo que a pessoa editou não interrompe o trabalho pela metade: o conflito é
    anotado, os demais itens são materializados, o índice e o manifesto são gravados, a
    varredura roda, e só então o comando falha nomeando todos os arquivos em conflito.

    `entrega/` precisa ser uma pasta real: se o caminho já existe como link simbólico,
    seguir ele poderia varrer e apagar arquivos de outro lugar (o alvo do link), então o
    comando recusa antes de tocar em qualquer arquivo, mesmo em `dry_run`.
    """
    from .ledger import Ledger, atomic_write

    ledger = ledger or Ledger(project, recover=False)
    items = ledger.data["items"]
    root = Path(project).expanduser().resolve() / DELIVERY_DIR
    if root.is_symlink():
        logs.event(log, logging.WARNING, "deliver_refusal", reason="entrega_is_symlink")
        raise ValueError(
            f"{root} é um link simbólico: apague o link antes de rodar `deliver`. A pasta "
            "de entrega precisa ser uma pasta real dentro do projeto, nunca um atalho para "
            "outro lugar."
        )
    groups, skipped = _plan(project, items)
    expected, listed, rows, changed = set(), [], [], []
    conflicts, conflicted = [], []
    for group in groups:
        expected.add(group["dir"])
        for index, c in enumerate(group["items"], start=1):
            source = ledger.root / c["output"]["path"]
            sheet_rel = (c.get("preview") or {}).get("contact_sheet_path")
            sheet = ledger.root / sheet_rel if sheet_rel else None
            names = _names(
                group["dir"],
                index,
                Path(c["output"]["path"]).suffix or ".mp4",
                Path(sheet_rel).suffix if sheet_rel else ".jpg",
            )
            media_rel = f"{group['dir']}/{names['media']}"
            expected.add(media_rel)
            method = "planned" if dry_run else None
            sheet_rel_out = f"{group['dir']}/{names['sheet']}" if sheet else None
            if sheet_rel_out:
                expected.add(sheet_rel_out)
            conflict = None
            if not dry_run:
                try:
                    if source.is_file():
                        # Só hardlink/symlink são congelados: cópia é independente.
                        method = link_or_copy(source, root / media_rel, read_only=True)
                        logs.event(
                            log,
                            logging.INFO,
                            "deliver_item",
                            beat=group["beat"] or "no_beat",
                            mode=method,
                            # `link_or_copy` doesn't report a per-attempt failure reason
                            # (its signature is shared with tests that stub it out with a
                            # 3-arg fake), so only the one fallback cause this call site
                            # can know for certain — a forced copy — is named here.
                            reason="env_copy" if copies_forced() else None,
                        )
                    if sheet and sheet_rel_out and sheet.is_file():
                        # O contact sheet não é congelado: `preview` regrava o arquivo
                        # de origem no mesmo caminho quando a pessoa muda o intervalo.
                        link_or_copy(sheet, root / sheet_rel_out)
                except ValueError as exc:
                    conflict = str(exc)
                    conflicts.append(conflict)
                    conflicted.append(media_rel)
                    logs.event(
                        log,
                        logging.WARNING,
                        "deliver_conflict",
                        beat=group["beat"] or "no_beat",
                        kind="foreign_file" if "parece edição sua" in conflict else "io_error",
                    )
            origin_rel = f"{group['dir']}/{names['origin']}"
            expected.add(origin_rel)
            if not dry_run and not conflict:
                target = root / origin_rel
                atomic_write(
                    target,
                    render_origin(c, names["media"], _created_in(target), method or "hardlink"),
                )
            record = {"path": f"{DELIVERY_DIR}/{media_rel}", "method": method}
            if conflict:
                # Não entregamos este: manter o registro antigo (ou nenhum) é o que
                # mantém o item fora de "entregue" no `status` e fora da tabela.
                continue
            if not dry_run and c.get("delivery") != record:
                c["delivery"] = record
                changed.append(c)
            listed.append(
                {
                    "id": c["id"],
                    "beat": group["beat"],
                    "path": f"{DELIVERY_DIR}/{media_rel}",
                    "method": method,
                }
            )
            rows.append(
                {
                    "beat": group["beat"] or "sem beat",
                    "narration": group["narration"],
                    "target": group["target"],
                    "file": f"{group['dir']}/{names['media']}",
                    "state": c.get("state"),
                    "rights": (c.get("rights") or {}).get("status"),
                    # Como este arquivo chegou aqui: é o que decide se editar nele
                    # editaria o original.
                    "method": method,
                }
            )
    expected.add(INDEX)
    if not dry_run:
        root.mkdir(parents=True, exist_ok=True)
        index = root / INDEX
        # O "próximo passo" é lido agora, com `c["delivery"]` já preenchido: senão o
        # índice mandaria a pessoa rodar exatamente o comando que acabou de rodar.
        text = for_human() if callable(for_human) else for_human
        atomic_write(
            index,
            render_index(
                rows,
                text,
                _created_in(index),
                conflicted,
                # Quando tudo virou cópia, o aviso de "é o mesmo arquivo" seria mentira.
                copies=bool(listed) and all(i["method"] == "copy" for i in listed),
            ),
        )
    owned = {
        (c.get("delivery") or {}).get("path", "")[len(DELIVERY_DIR) + 1 :]
        for c in items
        if (c.get("delivery") or {}).get("path", "").startswith(DELIVERY_DIR + "/")
    }
    removed, kept = _sweep(root, expected, dry_run, owned, ledger.root)
    if changed:
        ledger.save_many("deliver", changed)
    if conflicts:
        raise ValueError(" ".join(conflicts))
    return {
        "delivery": str(root),
        "readme": str(root / INDEX),
        "dry_run": bool(dry_run),
        "items": listed,
        "rows": rows,
        "removed": removed,
        "kept": kept,
        "skipped": skipped,
    }

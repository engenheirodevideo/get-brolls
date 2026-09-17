"""`entrega/`: a mesma coleta, organizada por beat, em nomes que uma pessoa lê.

`brolls/` continua canônico — nada aqui apaga, renomeia ou move o que está lá. Esta
camada é derivada e regenerável: cada arquivo de `clips/` aparece em `entrega/` por
hardlink (ou symlink, ou cópia, nessa ordem), com o contact sheet ao lado e um
`ORIGEM.md` dizendo de onde veio. Rodar de novo não estraga nada, e um arquivo que a
pessoa editou à mão nunca é sobrescrito: o comando para e diz o nome dele.
"""

import os
import re
import shutil
import unicodedata
from datetime import date
from pathlib import Path

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


def _same_file(a, b):
    try:
        return os.path.samestat(os.stat(a), os.stat(b))
    except OSError:
        return False


def _same_bytes(a, b):
    try:
        return Path(a).read_bytes() == Path(b).read_bytes()
    except OSError:
        return False


def link_or_copy(src, dest):
    """Liga `dest` a `src` pelo jeito mais barato que o sistema aceitar.

    Hardlink primeiro (não ocupa disco e não quebra ao mover a pasta de dentro),
    symlink depois (no Windows pode faltar privilégio) e cópia por último. Devolve o
    método usado. Se `dest` já é o mesmo arquivo, não faz nada; se é um arquivo comum
    com conteúdo diferente, é obra da pessoa e o erro nomeia o arquivo.
    """
    src, dest = Path(src), Path(dest)
    if dest.is_symlink():
        try:
            if dest.resolve() == src.resolve():
                return "symlink"
        except OSError:
            pass
        dest.unlink()
    elif dest.exists():
        if _same_file(dest, src):
            return "hardlink"
        if _same_bytes(dest, src):
            return "copy"
        raise ValueError(
            f"{dest} já existe com conteúdo diferente do arquivo coletado: parece edição "
            "sua e eu não sobrescrevo. Renomeie ou apague esse arquivo e rode `deliver` "
            "de novo."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    for method, make in (
        ("hardlink", os.link),
        ("symlink", lambda s, d: os.symlink(s, d)),
        ("copy", shutil.copy2),
    ):
        try:
            make(str(src), str(dest))
            return method
        except (OSError, NotImplementedError, AttributeError):
            if dest.is_symlink() or dest.exists():
                dest.unlink()
    raise OSError(
        f"Não consegui ligar nem copiar {src} para {dest}. Confira espaço e permissão "
        "de escrita na pasta do projeto."
    )


def _frontmatter(kind, created, tags):
    today = date.today().isoformat()
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


def render_origin(c, media_name, created=None):
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
        f"- Autor: {(c.get('creator') or {}).get('name') or 'não informado'}",
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
        "Este arquivo é gerado por `deliver`. A pasta `entrega/` inteira pode ser apagada "
        "e refeita: o que vale é `brolls/`.",
        "",
    ]
    return "\n".join(lines)


def render_index(rows, for_human=None, created=None):
    """`entrega/README.md`: a tabela que responde “onde estão meus arquivos”."""
    lines = _frontmatter("delivery-index", created, ["get-brolls", "entrega"])
    lines += [
        "# Seus trechos",
        "",
        "Cada pasta é um trecho do vídeo, na ordem do BRIEF.md. É só arrastar o `.mp4` "
        "para o seu editor; o `ORIGEM.md` ao lado diz de onde ele veio e o que você me "
        "disse sobre poder usar.",
        "",
        "| Beat | Narração | Alvo | Arquivo | Estado | Direitos |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                str(row.get(key) or "—").replace("|", "/").replace("\n", " ")
                for key in ("beat", "narration", "target", "file", "state", "rights")
            )
            + " |"
        )
    if not rows:
        lines.append("| — | — | — | nenhum trecho coletado ainda | — | — |")
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
    """Um grupo por beat, na ordem do brief; sem brief, na ordem do manifesto."""
    collected = [c for c in items if (c.get("output") or {}).get("path")]
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
    return groups


def _names(group_dir, index, source_suffix, sheet_suffix):
    tail = "" if index == 1 else f"-{index}"
    return {
        "media": f"{group_dir}{tail}{source_suffix}",
        "sheet": f"{SHEET}{tail}{sheet_suffix}",
        "origin": ORIGIN if index == 1 else f"ORIGEM{tail}.md",
    }


def _sweep(root, expected, dry_run):
    """Apaga só o que este gerador escreveu e que deixou de existir no plano."""
    removed, kept = [], []
    if not root.is_dir():
        return removed, kept
    for path in sorted(root.rglob("*"), reverse=True):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            if BEAT_DIR_RE.match(path.name) and not any(path.iterdir()) and rel not in expected:
                if not dry_run:
                    path.rmdir()
                removed.append(rel)
            continue
        if rel in expected:
            continue
        generated = path.is_symlink() or GENERATED.match(path.name) or rel == INDEX
        if generated or path.parent != root:
            if not dry_run:
                path.unlink()
            removed.append(rel)
        else:
            kept.append(rel)
    return removed, kept


def build_delivery(project, dry_run=False, ledger=None, for_human=None):
    """Refaz `entrega/` a partir do que já está coletado em `brolls/`.

    Não toca em `brolls/`, não decide nada e não inventa direito de uso: só reorganiza
    o que `fetch` já produziu. Com `dry_run`, devolve o mesmo relatório sem escrever
    um byte — nem na pasta, nem no manifesto.
    """
    from .ledger import Ledger, atomic_write

    ledger = ledger or Ledger(project, recover=False)
    items = ledger.data["items"]
    root = Path(project).expanduser().resolve() / DELIVERY_DIR
    groups = _plan(project, items)
    expected, listed, rows, changed = set(), [], [], []
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
            method = None
            if dry_run:
                method = "hardlink"
            elif source.is_file():
                method = link_or_copy(source, root / media_rel)
            if sheet and sheet.is_file():
                sheet_rel_out = f"{group['dir']}/{names['sheet']}"
                expected.add(sheet_rel_out)
                if not dry_run:
                    link_or_copy(sheet, root / sheet_rel_out)
            origin_rel = f"{group['dir']}/{names['origin']}"
            expected.add(origin_rel)
            if not dry_run:
                target = root / origin_rel
                atomic_write(
                    target, render_origin(c, names["media"], _created_in(target))
                )
            record = {"path": f"{DELIVERY_DIR}/{media_rel}", "method": method}
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
                }
            )
    expected.add(INDEX)
    if not dry_run:
        root.mkdir(parents=True, exist_ok=True)
        index = root / INDEX
        atomic_write(index, render_index(rows, for_human, _created_in(index)))
    removed, kept = _sweep(root, expected, dry_run)
    if changed:
        ledger.save_many("deliver", changed)
    return {
        "delivery": str(root),
        "readme": str(root / INDEX),
        "dry_run": bool(dry_run),
        "items": listed,
        "rows": rows,
        "removed": removed,
        "kept": kept,
    }

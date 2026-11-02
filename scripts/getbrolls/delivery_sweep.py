"""Varredura de `entrega/`: apaga só o que o gerador escreveu e saiu do plano.

Separado de `delivery` para caber num módulo só o que decide "isto é nosso": os nomes
que o gerador usa (e o `.part` de uma cópia que não terminou), o symlink que aponta
para uma pasta de clipes do projeto, a confinação física dentro de `entrega/` antes de
qualquer `unlink`/`rmdir` e, para a mídia, a prova de que o arquivo ainda é um clipe de
`brolls/` (mesmo inode ou mesmos bytes): uma cópia que a pessoa editou nunca é apagada,
e o `ORIGEM.md` dela fica junto.
"""

import logging
import os
import re
import stat
from collections import namedtuple
from pathlib import Path

from . import effects, labels, logs
from ._paths import strict_is_dir
from .runtime import record_warning

log = logs.get("delivery")

INDEX = "README.md"
# Só o que o gerador escreve pode ser apagado quando vira órfão.
GENERATED = re.compile(r"^(ORIGEM(-\d+)?\.md|contact-sheet(-\d+)?\.[a-z0-9]+)$")
BEAT_DIR_RE = re.compile(r"^\d{2}-[a-z0-9-]*$")
# Sobra de uma cópia por `<destino>.part` que um kill à força (ou uma queda de energia)
# não deixou terminar: nunca é obra da pessoa e nunca é o arquivo inteiro.
PART_SUFFIX = ".part"
# Só no Windows o próprio link tem atributo somente-leitura que barra o `unlink`; no POSIX o modo de um link não conta.
LINK_ATTRIBUTES = os.name == "nt"


def thaw_unlink(path):
    """Apaga o que este gerador congelou, inclusive no Windows.

    `_freeze` tira o bit de escrita; no Windows isso vira o atributo somente-leitura
    e `unlink` levanta `PermissionError`. Devolver a escrita antes é o único jeito de
    `deliver` regenerar `entrega/` depois de um beat renomeado.
    """
    path = Path(path)
    with effects.op("fs.unlink", path) as first:
        try:
            path.unlink()
            return
        except PermissionError:
            # Somente leitura (Windows): a tentativa falha como esperado e a de baixo, degelada, apaga.
            first.skip(fallback="eacces")
    with effects.op("fs.unlink", path):
        # `chmod` segue o link: num link, devolveria a escrita ao clipe de `brolls/` e ninguém a tiraria
        # de novo. O link é degelado nele mesmo (`_thaw_link`), sem chegar ao clipe.
        # lenient: link test of the path being thawed; a stat failure falls to the chmod/unlink below
        if not path.is_symlink():
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
        elif LINK_ATTRIBUTES:
            _thaw_link(path)
        path.unlink()


def _thaw_link(path):
    """Tira o somente-leitura do próprio link no Windows, sem seguir até o clipe de `brolls/`.

    No Windows antes da 3.13 o `chmod` age no próprio link (é assim que `_freeze` o deixou somente-leitura,
    e o atributo barra o `unlink`); da 3.13 em diante ele segue o link, e `follow_symlinks=False` o mantém no link.
    """
    if os.chmod in os.supports_follow_symlinks:
        path.chmod(stat.S_IWRITE | stat.S_IREAD, follow_symlinks=False)
    else:
        path.chmod(stat.S_IWRITE | stat.S_IREAD)


def unlink_keeping_mode(dest, src):
    """Apaga o nome `dest` do mesmo inode de `src` sem destravar o clipe.

    No Windows, `unlink` de um somente-leitura falha e `thaw_unlink` devolve a escrita
    ao inode antes de apagar — e o inode é o do clipe em `brolls/`. O modo dele volta
    ao que era; sem isso, o clipe deixaria de estar congelado e o `export` passaria a
    cloná-lo em vez de ligar. Volta também quando o `unlink` falha de novo depois do
    degelo (o editor segura o arquivo aberto) ou um Ctrl+C cai no meio: o nome em
    `entrega/` que ficou é o mesmo inode, e nada pode deixá-lo editável.
    """
    try:
        mode = stat.S_IMODE(src.stat().st_mode)
    # lenient: mode to restore after a thaw is optional: without it the sweep restores nothing
    except OSError:
        mode = None
    try:
        thaw_unlink(dest)
    finally:
        if mode is not None:
            _restore_mode(src, mode)


def _restore_mode(src, mode):
    """Devolve `mode` a `src` se ele mudou; a falha vira aviso, nunca a exceção de quem chamou."""
    try:
        if stat.S_IMODE(src.stat().st_mode) != mode:
            src.chmod(mode)
    except OSError as exc:
        record_warning(
            "DELIVERY_FREEZE_FAILED",
            f"Não consegui devolver o modo somente-leitura de {labels.shown(src)} ({labels.reason(exc)}): o clipe em "
            "brolls/ ficou editável.",
        )


def _symlink_targets_brolls(path, brolls_root):
    """Um symlink só é nosso quando aponta para um clipe dentro do `brolls/` deste projeto.

    `brolls_root` é uma pasta ou uma lista delas: no layout 1 o clipe mora em `broll/`,
    irmã de `brolls/`, e o link para lá também é do gerador. Um link que a pessoa criou
    (para a própria mídia, um atalho, outra pasta) não bate com isso e não pode ser
    tratado como órfão do gerador.
    """
    roots = (brolls_root,) if isinstance(brolls_root, (str, Path)) else tuple(brolls_root or ())
    try:
        target = path.resolve()
        reals = [Path(root).resolve() for root in roots]
    except OSError:
        return False
    return any(target == real or real in target.parents for real in reals)


def media_tail(name, folder):
    """O `-N` de uma mídia do gerador (`<pasta do beat>[-N].<ext>`): `""` na primeira, `None` se não é mídia."""
    if not BEAT_DIR_RE.match(folder):
        return None
    found = re.fullmatch(re.escape(folder) + r"(-\d+)?\.[A-Za-z0-9]+", name)
    return None if found is None else found.group(1) or ""


def _ours(rel, path, owned, brolls_root):
    """Só é órfão o que este gerador escreveu; o resto é da pessoa e fica.

    Reconhecemos três assinaturas: um caminho que o próprio manifesto registra em
    `c["delivery"]`, os nomes que o gerador usa (`README.md`, `ORIGEM*.md`,
    `contact-sheet*.*` e a mídia, que repete o nome da pasta do beat — também com o
    `.part` de uma cópia interrompida) e, só quando o nome já bateu com uma dessas
    assinaturas, um symlink que resolve para dentro de `brolls/`. Um bilhete — ou um
    link — que a pessoa deixou dentro da pasta não casa com nada disso e é preservado.
    """
    if rel in owned or rel == INDEX:
        return True
    name = path.name.removesuffix(PART_SUFFIX)
    name_matches = bool(GENERATED.match(name)) or media_tail(name, path.parent.name) is not None
    if not name_matches:
        return False
    # lenient: link test of a swept entry, which the sweep already stat'ed with strict_is_dir
    if path.is_symlink():
        return _symlink_targets_brolls(path, brolls_root)
    return True


def confined(path, real_root):
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


class Pristine:
    # Um predicado com cache: só lê disco quando há o que conferir.
    """Diz se um arquivo de `entrega/` ainda é um dos arquivos coletados (`sources`).

    Mesmo inode (hardlink) ou mesmos bytes (sha256) que algum deles: apagar não perde
    nada, o original continua em `brolls/`. `sources` só é percorrido na primeira
    pergunta (pode ser um gerador que lista as pastas de clipe), o sha de cada original
    só é calculado quando o tamanho bate, e uma vez só. A varredura pergunta pelos
    órfãos; o `deliver`, pelo arquivo planejado que ainda é de outra revisão.
    """

    def __init__(self, sources):
        self._sources = sources or ()
        self._stats = None
        self._digests = {}

    def _known(self):
        if self._stats is None:
            self._stats = []
            for path in dict.fromkeys(Path(p) for p in self._sources):
                try:
                    self._stats.append((path, path.stat()))
                # lenient: a source it cannot stat is not offered as pristine, so its mirror is kept
                except OSError:
                    continue
        return self._stats

    def _digest_of(self, path):
        from .ledger import digest

        if path not in self._digests:
            self._digests[path] = digest(path)
        return self._digests[path]

    def __call__(self, path):
        return self.match(path) is not None

    def match(self, path):
        """`(original, mesmo_inode)` do arquivo coletado que `path` ainda é; senão `None`."""
        from .ledger import digest

        try:
            info = path.stat()
            known = self._known()
            for source, other in known:
                if os.path.samestat(info, other):
                    return source, True
            same_size = [source for source, other in known if other.st_size == info.st_size]
            if not same_size:
                return None
            mine = digest(path)
            return next(((source, False) for source in same_size if mine == self._digest_of(source)), None)
        # lenient: without reading the file there is no proof it is ours: kept, never removed
        except OSError:
            # Sem conseguir ler, não há prova de que é nosso: fica.
            return None


# `linked`: {caminho: clipe} da mídia fora do plano que ainda é hardlink de um clipe de `brolls/`.
_SweepContext = namedtuple("_SweepContext", "expected dry_run owned brolls_root real_root pristine linked gone")


def _is_empty(path, rel, ctx):
    """A pasta está vazia, ou ficará: no ensaio nada sai do disco, então vale o que o ensaio já marcou para sair."""
    if not ctx.dry_run:
        return not any(path.iterdir())
    return all(f"{rel}/{child.name}" in ctx.gone for child in path.iterdir())


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
        and _is_empty(path, rel, ctx)
        and rel not in ctx.expected
        and confined(path, ctx.real_root)
    ):
        return None
    # lenient: link test of a swept entry, which the sweep already stat'ed with strict_is_dir
    if path.is_symlink():
        if not _ours(rel, path, ctx.owned, ctx.brolls_root):
            return "kept"
        if not ctx.dry_run:
            thaw_unlink(path)
        return "removed"
    if not ctx.dry_run:
        try:
            effects.rmdir(path)
        except OSError as exc:
            record_warning(
                "DELIVERY_SWEEP_RMDIR_FAILED",
                f"Não consegui apagar a pasta vazia {labels.shown(path)} ({labels.reason(exc)}); "
                "ela continua em entrega/.",
            )
            return None
    return "removed"


def _mirrors_a_clip(rel, path, owned):
    """Mídia entregue (arquivo comum, não link): espelha um clipe de `brolls/` e só sai se ainda for igual a ele.

    Contact sheet e `ORIGEM.md` são derivados que o gerador refaz — o `preview` regrava o
    sheet no mesmo caminho, com inode novo, e o antigo em `entrega/` vira só uma prévia
    velha: saem pelo nome, como sempre.
    """
    # lenient: link test of a swept entry, which the sweep already stat'ed with strict_is_dir
    if path.is_symlink():
        return False
    return rel in owned or media_tail(path.name, path.parent.name) is not None


def _verdict(path, rel, ctx):
    """Um arquivo fora do plano, só lendo: `"remove"`, `"kept"` (da pessoa) ou `"edited"` (nosso, mas mudado)."""
    if not (_ours(rel, path, ctx.owned, ctx.brolls_root) and confined(path, ctx.real_root)):
        return "kept"
    if path.name.endswith(PART_SUFFIX):
        # Cópia que não terminou: apagar não perde nada, o arquivo inteiro nunca chegou aqui.
        return "remove"
    if _mirrors_a_clip(rel, path, ctx.owned):
        match = ctx.pristine.match(path)
        if match is None:
            return "edited"
        clip, same_inode = match
        if same_inode:
            # Apagar este nome não pode destravar o clipe que compartilha o inode.
            ctx.linked[rel] = clip
    return "remove"


def _origins_beside(edited):
    """O `ORIGEM[-N].md` de cada mídia editada: fica com ela, com a origem e as condições de uso."""
    found = set()
    for rel in edited:
        folder, _, name = rel.rpartition("/")
        tail = media_tail(name, folder.rpartition("/")[2])
        if tail is not None:
            found.add(f"{folder}/ORIGEM{tail}.md")
    return found


def _verdicts(paths, ctx):
    """{caminho: veredito} de cada arquivo fora do plano, só lendo.

    Vem antes de qualquer `unlink`: na ordem reversa o `ORIGEM.md` aparece antes da
    mídia, e ele fica (`"kept"`) quando a mídia ao lado é uma cópia editada.
    """
    found = {
        rel: _verdict(path, rel, ctx) for path, rel in paths if rel not in ctx.expected and not strict_is_dir(path)
    }
    beside = _origins_beside(rel for rel, verdict in found.items() if verdict == "edited")
    return {rel: "kept" if verdict == "remove" and rel in beside else verdict for rel, verdict in found.items()}


def _remove(path, linked):
    """Apaga um arquivo fora do plano; o hardlink de um clipe (`linked`) sai sem destravar o clipe."""
    if linked is None:
        thaw_unlink(path)
    else:
        unlink_keeping_mode(path, linked)


def sweep(  # noqa: PLR0913 - one input per rule the sweep applies
    root, expected, dry_run, owned=(), brolls_root=None, *, sources=(), edited=None
):  # pylint: disable=too-many-arguments
    """Apaga só o que este gerador escreveu e que deixou de existir no plano.

    `sources` são os clipes de `brolls/` que a mídia de `entrega/` pode espelhar. Mídia
    com nome do gerador só sai se ainda for um deles, pelo inode ou pelos bytes; a que a
    pessoa editou fica em `kept`, com o `ORIGEM.md` ao lado, e, se `edited` for uma
    lista, entra nela também, para quem chama tratar como conflito.
    """
    removed, kept = [], []
    if not strict_is_dir(root):
        return removed, kept
    ctx = _SweepContext(expected, dry_run, set(owned), brolls_root, root.resolve(), Pristine(sources), {}, set())
    paths = [(path, path.relative_to(root).as_posix()) for path in sorted(root.rglob("*"), reverse=True)]
    verdicts = _verdicts(paths, ctx)
    for path, rel in paths:
        if rel in verdicts:
            outcome = verdicts[rel]
            if outcome == "remove":
                if not dry_run:
                    _remove(path, ctx.linked.get(rel))
                outcome = "removed"
        elif rel in expected or not strict_is_dir(path):
            continue
        else:
            outcome = _sweep_empty_directory(path, rel, ctx)
        if outcome == "removed":
            removed.append(rel)
            ctx.gone.add(rel)
        elif outcome in ("kept", "edited"):
            kept.append(rel)
            if outcome == "edited" and edited is not None:
                edited.append(rel)
    logs.event(log, logging.INFO, "sweep", removed=len(removed), kept_foreign=len(kept))
    return removed, kept

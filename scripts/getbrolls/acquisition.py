"""Private working sources for review; final clips remain approval-gated."""

import contextlib
import copy
import json
import logging
import os
import re
import shlex
import tempfile
import time
import uuid
from pathlib import Path
from typing import NamedTuple

from . import logs
from .errors import PrerequisiteError
from .http import DOWNLOAD_MAX_BYTES
from .ledger import digest
from .media import probe
from .models import id_stem
from .runtime import force_rmtree, record_warning

INDEX_NAME = "index.json"
ROUTE_PREFIX = "plugin:"
# Mesmo teto do download do core (uma fonte só: `http.DOWNLOAD_MAX_BYTES`): rota de
# plugin não traz arquivo maior do que o core baixaria.
ROUTE_MAX_BYTES = DOWNLOAD_MAX_BYTES
# Chave do índice de fontes para o arquivo que uma rota `stage="fetch"` já trouxe:
# separada da chave do candidato, então `inspect`/`preview` nunca reaproveitam por
# engano o arquivo licenciado como mídia de trabalho — só o `fetch` o lê.
FETCH_INDEX_SUFFIX = "#fetch"
_CACHE_SUFFIX_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")

log = logs.get("acquisition")


def _load_index(cache):
    path = cache / INDEX_NAME
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        record_warning(
            "SOURCE_INDEX_UNREADABLE",
            f"Índice de fontes ilegível ({type(error).__name__}); tratado como vazio.",
        )
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        record_warning(
            "SOURCE_INDEX_UNREADABLE",
            "Índice de fontes corrompido (JSON inválido); renomeado para .bad e tratado como vazio.",
        )
        with contextlib.suppress(OSError):
            path.replace(path.with_name(path.name + ".bad"))
        return {}
    return data if isinstance(data, dict) else {}


def _save_index(cache, index):
    """Atomic write via a unique temp file (mkstemp, not a fixed name shared by every writer)."""
    if not isinstance(index, dict):
        raise ValueError("Índice de fontes inválido: esperado objeto {candidato: [entradas]}.")
    path = cache / INDEX_NAME
    fd, temp_name = tempfile.mkstemp(dir=str(cache), prefix=".index-", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(index, ensure_ascii=False))
        temp.chmod(0o600)
        temp.replace(path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def _ensure_private_cache_dir(cache):
    """Create/reuse `.getbrolls-sources/` as 0700, same as `social.py`'s cache.

    `mkdir(exist_ok=True)` only applies `mode` to a directory it actually creates;
    an already-existing (looser) directory from before this fix would stay as-is
    without the explicit `chmod` below.
    """
    cache.mkdir(mode=0o700, exist_ok=True)
    cache.chmod(0o700)


def _covers(entry, start, end):
    entry_start = entry.get("start")
    entry_duration = entry.get("duration")
    if entry_start is None or entry_duration is None:
        return False
    try:
        return entry_start <= start and end <= entry_start + entry_duration + 0.05
    except TypeError:
        return False


def _reuse_from_index(cache, candidate_id, start, end):
    """A prior segment for this candidate — any of them, not just the last one — that already
    covers [start, end]; None when nothing qualifies or the file no longer checks out."""
    index = _load_index(cache)
    for entry in index.get(candidate_id, []):
        if not isinstance(entry, dict) or not _covers(entry, start, end):
            continue
        path = Path(entry.get("path") or "")
        if not path.is_file():
            continue
        if digest(path) != entry.get("sha"):
            record_warning(
                "SOURCE_CACHE_STALE",
                f"Cache de fonte para {candidate_id} tem sha divergente; ignorado, não reutilizado.",
            )
            logs.event(log, logging.INFO, "source_cache", candidate=candidate_id, result="stale", reason="sha_mismatch")
            continue
        logs.event(log, logging.INFO, "source_cache", candidate=candidate_id, result="hit", reason="covers_range")
        return entry
    logs.event(log, logging.INFO, "source_cache", candidate=candidate_id, result="miss", reason="no_match")
    return None


class RoutedFile(NamedTuple):
    """Arquivo que uma rota de plugin trouxe, já verificado pelo core."""

    path: Path
    license: str | None
    plugin: str


def route_name(candidate):
    """Nome da rota de plugin que entrega o arquivo deste candidato; None nas fontes do core.

    Só lê o candidato: fontes do core nunca montam o registro (nem rodam plugin) aqui.
    """
    method = (candidate.get("acquisition") or {}).get("method")
    if isinstance(method, str) and method.startswith(ROUTE_PREFIX) and len(method) > len(ROUTE_PREFIX):
        return method[len(ROUTE_PREFIX) :]
    return None


def candidate_arg(candidate):
    """Id do candidato pronto para um comando sugerido: candidato de plugin vai
    por `shlex.quote` quando tem caractere inseguro; fonte embutida sai como sempre."""
    from .providers import BUILTIN_CAPABILITIES

    ident = str(candidate.get("id"))
    return ident if candidate.get("provider") in BUILTIN_CAPABILITIES else shlex.quote(ident)


def fetch_stage_message(candidate):
    """Frase para um candidato cuja rota só entrega o arquivo no `fetch`: como revisar agora."""
    # Foto não tem trecho: a prévia de referência dela vai sem `--start/--end`.
    span = "" if (candidate.get("media") or {}).get("kind") == "image" else " --start <INICIO> --end <FIM>"
    return (
        f"A fonte {candidate.get('provider')} só entrega o arquivo no `fetch`, depois da aprovação e do "
        "permit (baixar consome licença ou cota). Para revisar agora, use "
        f"`preview --candidate {candidate_arg(candidate)}{span} --reference-only`; "
        "depois approve, permit e fetch."
    )


def license_evidence(plugin, text):
    """Evidência de licença registrada pela rota do plugin, com o prefixo que diz de onde veio."""
    from .sdk.guard import LICENSE_EVIDENCE_LABEL, plugin_evidence

    return plugin_evidence(LICENSE_EVIDENCE_LABEL, plugin, text)


def fetch_only(candidate):
    """O candidato só tem arquivo no `fetch` (rota `stage="fetch"`)? Lido do próprio
    candidato — `preview.route_stage`, gravado pelo core na busca —, sem montar o
    registro: `status`/guidance nunca rodam código de plugin para responder isto."""
    return route_name(candidate) is not None and (candidate.get("preview") or {}).get("route_stage") == "fetch"


def _route_for(candidate):
    from .sdk.registry import get_registry

    name = route_name(candidate) or ""
    registry = get_registry()
    route = registry.route(name)
    owner = registry.owner("route", name)
    if route is None or owner is None:
        raise ValueError(
            f"A rota {name} não está carregada: o plugin dela está desligado, suspenso ou falhou. "
            "Rode plugins --action list / doctor."
        )
    if registry.owner("provider", candidate.get("provider")) != owner:
        raise ValueError(f"A rota {name} não pertence à fonte {candidate.get('provider')}; refaça a busca.")
    return owner, name, route, registry.route_stage(name)


def verified_route_file(raw_path, workdir, owner, route):
    """O core confere o que a rota devolveu: arquivo real, dentro do workdir, com
    tamanho no teto e que o ffprobe lê como vídeo ou imagem."""
    from .http import ProviderError

    root = Path(workdir).resolve()
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    if path.is_symlink():
        raise ProviderError(f"Plugin {owner}: a rota {route} devolveu um link simbólico, não o arquivo.")
    real = path.resolve()
    if not real.is_relative_to(root) or not real.is_file():
        raise ProviderError(f"Plugin {owner}: a rota {route} devolveu um arquivo fora da pasta de trabalho.")
    stat = real.stat()
    if stat.st_nlink != 1:
        raise ProviderError(
            f"Plugin {owner}: a rota {route} devolveu um hardlink; copie o arquivo para a pasta de trabalho."
        )
    size = stat.st_size
    if not size:
        raise ProviderError(f"Plugin {owner}: a rota {route} devolveu um arquivo vazio.")
    if size > ROUTE_MAX_BYTES:
        raise ProviderError(
            f"Plugin {owner}: a rota {route} devolveu {size / (1024 * 1024):.1f} MB; "
            f"o teto é {ROUTE_MAX_BYTES // (1024 * 1024)} MB."
        )
    try:
        probe(real)
    except PrerequisiteError:
        raise  # ffprobe ausente é da instalação (saída 4), não culpa do plugin
    except ValueError as exc:
        raise ProviderError(f"Plugin {owner}: a rota {route} devolveu algo que não é vídeo nem imagem.") from exc
    return real


@contextlib.contextmanager
def plugin_source(ledger, candidate, stage):
    """Roda a rota do plugin numa pasta própria em `.getbrolls-sources/` e entrega o
    arquivo verificado; a pasta some ao sair, com ou sem erro.

    Rota `stage="fetch"` consome licença ou cota: fora do `fetch` ela nem é chamada.
    """
    from .sdk import guard

    owner, name, route, route_stage = _route_for(candidate)
    if route_stage == "fetch" and stage != "fetch":
        raise ValueError(fetch_stage_message(candidate))
    cache = ledger.root.parent / ".getbrolls-sources"
    _ensure_private_cache_dir(cache)
    workdir = cache / f"plugin-{owner}-{uuid.uuid4().hex}"
    workdir.mkdir(mode=0o700)
    started = time.monotonic()
    try:
        raw_path, license_text = guard.route_call(owner, name, route, copy.deepcopy(candidate), workdir)
        try:
            path = verified_route_file(raw_path, workdir, owner, name)
        except ValueError as exc:
            logs.event(log, logging.WARNING, "plugin_call_failed", plugin=owner, route=name, error=type(exc).__name__)
            raise
        logs.event(
            log,
            logging.INFO,
            "plugin_route",
            plugin=owner,
            route=name,
            stage=stage,
            bytes=path.stat().st_size,
            ms=round((time.monotonic() - started) * 1000),
        )
        yield RoutedFile(path, license_text, owner)
    finally:
        force_rmtree(workdir)


class FetchedSource(NamedTuple):
    """Arquivo que a rota de `fetch` trouxe, já no cache privado do projeto."""

    path: Path
    license: str | None
    plugin: str
    duration_s: float | None
    reused: bool


def _cache_suffix(path):
    suffix = Path(path).suffix.lower()
    if 1 < len(suffix) <= 9 and set(suffix[1:]) <= _CACHE_SUFFIX_CHARS:  # noqa: PLR2004 - ponto + 1 a 8 caracteres
        return suffix
    return ".bin"


_CACHE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


def _fetched_in_cache(cache, raw):
    """Arquivo de uma entrada de `fetch` dentro da pasta de cache DESTE projeto.

    A entrada guarda só o nome do arquivo (relativo ao cache). Uma entrada antiga
    com caminho absoluto vale pelo nome do arquivo, procurado aqui: um projeto
    movido continua achando o próprio arquivo, e uma cópia do projeto nunca lê o
    cache do projeto original."""
    if not isinstance(raw, str) or not raw:
        return None
    name = re.split(r"[\\/]", raw)[-1]
    # Só o formato de nome que o próprio core grava (`<stem>-fetch-<sha><ext>`): nada de
    # `C:x.mp4`, `.`/`..` ou outro caractere; e nunca um link simbólico plantado no cache.
    if name in (".", "..") or not _CACHE_NAME_RE.fullmatch(name):
        return None
    path = cache / name
    if path.is_symlink():
        return None
    return path


def _reuse_fetched(cache, candidate):
    key = candidate["id"] + FETCH_INDEX_SUFFIX
    for entry in _load_index(cache).get(key, []):
        if not isinstance(entry, dict):
            continue
        path = _fetched_in_cache(cache, entry.get("path"))
        if path is not None and path.is_file() and digest(path) == entry.get("sha"):
            logs.event(log, logging.INFO, "source_cache", candidate=candidate["id"], result="hit", reason="route_fetch")
            license_text = entry.get("license")
            duration = entry.get("duration")
            return FetchedSource(
                path,
                license_text if isinstance(license_text, str) else None,
                str(entry.get("plugin") or ""),
                duration if isinstance(duration, (int, float)) else None,
                True,
            )
    return None


def route_consumed_message(candidate, consumed_at):
    """Frase para uma licença já consumida cujo arquivo sumiu do cache: nunca roda a rota de novo."""
    return (
        f"A licença da fonte {candidate.get('provider')} para {candidate.get('id')} já foi consumida em "
        f"{consumed_at}, e o arquivo licenciado não está mais no cache privado do projeto "
        "(.getbrolls-sources/). Não rodei a rota de novo: isso consumiria outra licença ou cota. "
        "Restaure a pasta .getbrolls-sources/ do projeto; ou, se a pessoa confirmar uma nova aquisição, "
        f"rode `fetch --candidate {candidate_arg(candidate)} --reacquire`."
    )


def fetch_routed_source(ledger, candidate, reacquire=False):
    """O arquivo da rota de `fetch`, trazido uma única vez.

    A rota consome licença ou cota: o arquivo verificado vai para o cache privado
    `.getbrolls-sources/` (índice por candidato + sha, chave separada da mídia de
    trabalho) e a licença fica guardada junto — ANTES de devolver, então qualquer
    recusa posterior (extensão de imagem, corte, cópia) não perde esse registro.
    Um `fetch` que falha depois é repetido e reaproveita esse arquivo — a rota não
    é chamada de novo (a extensão da imagem roteada só é conferida por quem chama,
    depois que o cache e a licença já foram gravados)."""
    cache = ledger.root.parent / ".getbrolls-sources"
    _ensure_private_cache_dir(cache)
    reused = _reuse_fetched(cache, candidate)
    if reused is not None:
        return reused
    consumed_at = (candidate.get("acquisition") or {}).get("route_consumed_at")
    if consumed_at and not reacquire:
        # Licença já consumida e cache perdido (pasta apagada, projeto movido):
        # nunca chama a rota de novo sem o `--reacquire` explícito.
        logs.event(log, logging.WARNING, "route_refused", candidate=candidate["id"], reason="license_consumed")
        raise ValueError(route_consumed_message(candidate, consumed_at))
    with plugin_source(ledger, candidate, "fetch") as routed:
        suffix = _cache_suffix(routed.path)
        info = probe(routed.path)
        sha = digest(routed.path)
        final = cache / (id_stem(candidate["id"]) + "-fetch-" + sha + suffix)
        if not final.exists():
            routed.path.replace(final)
            final.chmod(0o600)
        elif digest(final) != sha:
            raise ValueError("Cache de mídia inconsistente; não foi sobrescrito.")
        plugin, license_text = routed.plugin, routed.license
    index = _load_index(cache)
    entries = index.setdefault(candidate["id"] + FETCH_INDEX_SUFFIX, [])
    entries[:] = [e for e in entries if isinstance(e, dict) and e.get("sha") != sha]
    entries.append(
        {
            "path": final.name,
            "sha": sha,
            "duration": info["duration_s"],
            "plugin": plugin,
            "license": license_text,
        }
    )
    _save_index(cache, index)
    return FetchedSource(final, license_text, plugin, info["duration_s"], False)


def direct_media(candidate):
    """A fonte publica o arquivo direto (mp4/jpg) em vez de uma página para o yt-dlp?

    É o caso do acervo da NASA e dos bancos de imagem: `source_url` é a página do
    item, e mandá-la ao yt-dlp devolve "Unsupported URL". Quem tem `media_url` e não
    é rota de yt-dlp se lê pelo próprio arquivo — e quem tem rota de plugin também:
    o arquivo vem pela rota, nunca pelo yt-dlp.
    """
    if route_name(candidate) is not None:
        return True
    return bool(candidate.get("media_url")) and (candidate.get("acquisition") or {}).get("method") != "yt-dlp"


def _log_reused(candidate_id, reused_path):
    """Registra `source_materialized` de uma fonte reaproveitada do cache (`kind=local`)."""
    logs.event(
        log,
        logging.INFO,
        "source_materialized",
        candidate=candidate_id,
        bytes=reused_path.stat().st_size if reused_path.is_file() else None,
        ms=0,
        kind="local",
    )


def _move_into_cache(cache, candidate_id, target, sha, suffix):
    """Leva `target` para o cache com o nome `<id>-<sha><suffix>` e devolve esse caminho.

    Um arquivo com esse nome e outro conteúdo nunca é sobrescrito: vira erro."""
    final = cache / (id_stem(candidate_id) + "-" + sha + suffix)
    if not final.exists():
        target.replace(final)
        final.chmod(0o600)
    elif digest(final) != sha:
        logs.event(
            log, logging.WARNING, "source_cache", candidate=candidate_id, result="stale", reason="digest_mismatch"
        )
        raise ValueError("Cache de mídia inconsistente; não foi sobrescrito.")
    return final


def _remember_in_cache(cache, candidate_id, final, entry, started):
    """Registra `source_materialized` (`kind=remote`) e guarda `final` no índice do cache.

    `entry` traz `sha`, `start` e `duration` do arquivo, em tempo da fonte."""
    logs.event(
        log,
        logging.INFO,
        "source_materialized",
        candidate=candidate_id,
        bytes=final.stat().st_size,
        ms=round((time.monotonic() - started) * 1000),
        kind="remote",
    )
    index = _load_index(cache)
    entries = index.setdefault(candidate_id, [])
    entries[:] = [e for e in entries if e.get("sha") != entry["sha"]]
    entries.append({"path": str(final.resolve()), **entry})
    _save_index(cache, index)


def _direct_download(candidate, refresh):
    """O que `cache_direct_media` baixa: o item atualizado (com as versões menores da
    foto da NASA em `media_url_fallbacks`) ou `{"media_url": ...}` do próprio candidato."""
    url = candidate.get("media_url")
    fresh = {}
    if refresh:
        from .providers import refresh as refresh_candidate

        fresh = refresh_candidate(candidate) or {}
        url = fresh.get("media_url") or url
    if not url:
        raise ValueError("Arquivo do provedor não está mais disponível.")
    return fresh if fresh.get("media_url") else {"media_url": url}


def _download_direct(rendition, target):
    """Baixa `rendition` para `target` e devolve `target`.

    Como no `fetch`: a foto da NASA traz as versões menores em `media_url_fallbacks`,
    e acima do teto de download vale a próxima."""
    from .http import download_rendition

    download_rendition(rendition, target)
    return target


def cache_direct_media(ledger, candidate, refresh=True, stage="inspect"):
    """Baixa uma vez o arquivo direto no cache privado e devolve o caminho local.

    Só mexe no cache: nada é gravado no candidato nem em `brolls/`, então `inspect`
    continua somente leitura sobre decisão, intervalo e direitos.
    """
    cache = ledger.root.parent / ".getbrolls-sources"
    _ensure_private_cache_dir(cache)
    reused = _reuse_from_index(cache, candidate["id"], 0, 0)
    if reused is not None:
        _log_reused(candidate["id"], Path(reused["path"]))
        return Path(reused["path"])
    rendition = None if route_name(candidate) is not None else _direct_download(candidate, refresh)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(dir=cache) as work, contextlib.ExitStack() as route_stack:
        if rendition is None:
            target = route_stack.enter_context(plugin_source(ledger, candidate, stage)).path
        else:
            target = _download_direct(rendition, Path(work) / "source.bin")
        # Antes do ffprobe: foto de formato desconhecido é recusada com a razão certa.
        suffix = _cached_suffix(candidate, target)
        info = probe(target)
        sha = digest(target)
        final = _move_into_cache(cache, candidate["id"], target, sha, suffix)
    _remember_in_cache(cache, candidate["id"], final, {"sha": sha, "start": 0, "duration": info["duration_s"]}, started)
    return final


# Contêineres de vídeo que as fontes de arquivo direto publicam (o Commons serve webm/ogv).
_VIDEO_SUFFIXES = (".mp4", ".webm", ".ogv")


def _cached_suffix(candidate, path):
    """Extensão da cópia no cache, pelo conteúdo: a foto guarda a dela, que `fetch`
    leva até `clips/`, e formato de foto desconhecido é recusado; o vídeo só deixa
    de chamar webm de `.mp4` (o corte final continua saindo em `.mp4` pelo FFmpeg)."""
    from .media import image_suffix, sniff_suffix

    if (candidate.get("media") or {}).get("kind") != "image":
        found = sniff_suffix(path, ".mp4")
        return found if found in _VIDEO_SUFFIXES else ".mp4"
    return image_suffix(path)


def prepare_image_source(ledger, candidate, *, stage="preview"):
    """Traz a foto remota inteira para o cache privado: é ela a mídia de trabalho.

    Imagem estática não tem intervalo; a prévia é o cartaz da própria foto, e o
    `fetch` depois copia exatamente os bytes que a pessoa viu e aprovou (o hash entra
    na assinatura da aprovação por `local_sha256`).
    """
    c = candidate
    path = c.get("local_path")
    if path and Path(path).is_file():
        if digest(path) != c["local_sha256"]:
            raise ValueError("Fonte de trabalho alterada; importe novamente antes de revisar.")
        return
    final = cache_direct_media(ledger, c, stage=stage)
    info = probe(final)
    c.update(local_path=str(Path(final).resolve()), local_sha256=digest(final))
    c["media"].update(width=info["width"], height=info["height"])


def _ready_locally(candidate, start, end):
    """A mídia de trabalho já guardada no candidato cobre [start, end]?

    Uma mídia de trabalho alterada desde que foi guardada vira erro."""
    path = candidate.get("local_path")
    if not (path and Path(path).is_file()):
        return False
    if digest(path) != candidate["local_sha256"]:
        raise ValueError("Fonte de trabalho alterada; importe novamente antes de revisar.")
    offset = candidate.get("local_start_s", 0)
    duration = candidate.get("local_duration_s")
    return duration is not None and start >= offset and end <= offset + duration + 0.05


def _reused_for_range(cache, candidate, start, end):
    """Aproveita do cache um arquivo que já cobre [start, end]; `True` se aproveitou."""
    reused = _reuse_from_index(cache, candidate["id"], start, end)
    if reused is None:
        return False
    info = probe(reused["path"])
    candidate.update(
        local_path=str(Path(reused["path"]).resolve()),
        local_sha256=reused["sha"],
        local_start_s=reused["start"],
        local_duration_s=reused["duration"],
    )
    candidate["media"].update(width=info["width"], height=info["height"], fps=info["fps"])
    _log_reused(candidate["id"], Path(reused["path"]))
    return True


def _acquire(candidate, target, span, route_stack, source):
    """Traz o arquivo da fonte para `target`; devolve `(caminho, extensão, início)`.

    `span` é `(start, end)`; `source` é `(ledger, stage)`, para a rota de plugin."""
    method = candidate["acquisition"].get("method")
    if method == "yt-dlp":
        from .social import download_segment

        download_segment(candidate["source_url"], target, span[0], span[1])
        return target, ".mp4", span[0]
    if method == "https":
        from .http import download_rendition
        from .providers import refresh

        fresh = refresh(candidate)
        if not fresh.get("media_url"):
            raise ValueError("Arquivo do provedor não está mais disponível.")
        download_rendition(fresh, target)
        return target, _cached_suffix(candidate, target), 0
    if route_name(candidate) is not None:
        # Rota de plugin traz o arquivo inteiro (preview) para a pasta de trabalho;
        # o resto — ffprobe, sha256, cache, índice — segue igual às outras fontes.
        ledger, stage = source
        return route_stack.enter_context(plugin_source(ledger, candidate, stage)).path, ".mp4", 0
    raise ValueError(f"Esta fonte requer importação do original local (method={method!r}).")


def _acquire_into_cache(cache, candidate, span, tolerant, source):
    """Traz o arquivo da fonte e o guarda no cache; devolve `(final, sha, início, probe)`.

    Sem `tolerant`, um arquivo mais curto que `span` (`(start, end)`) vira erro."""
    with tempfile.TemporaryDirectory(dir=cache) as work, contextlib.ExitStack() as route_stack:
        target, suffix, offset = _acquire(candidate, Path(work) / "source.mp4", span, route_stack, source)
        info = probe(target)
        if span[1] - offset > info["duration_s"] + 0.1 and not tolerant:
            raise ValueError("Original não contém o intervalo solicitado.")
        sha = digest(target)
        return _move_into_cache(cache, candidate["id"], target, sha, suffix), sha, offset, info


# Seis parâmetros: é a assinatura que preview, scan e fetch chamam.
def prepare_source(  # noqa: PLR0913  # pylint: disable=too-many-arguments  # assinatura chamada pelo core
    ledger,
    candidate,
    start,
    end,
    tolerant=False,
    *,
    stage="preview",
):
    """Deixa a mídia de trabalho pronta para [start, end] em tempo da fonte.

    `tolerant=True` aceita que o arquivo baixado seja mais curto do que o pedido — é
    o caso da varredura, que pede o vídeo inteiro e não pode falhar porque a fonte
    entregou alguns segundos a menos do que a duração anunciada. Quem chama com
    `tolerant` precisa reler `local_duration_s` antes de montar a grade.
    """
    c = candidate
    if c["provider"] == "local" or _ready_locally(c, start, end):
        return
    cache = ledger.root.parent / ".getbrolls-sources"
    _ensure_private_cache_dir(cache)
    if _reused_for_range(cache, c, start, end):
        return
    started = time.monotonic()
    final, sha, offset, info = _acquire_into_cache(cache, c, (start, end), tolerant, (ledger, stage))
    c.update(
        local_path=str(final.resolve()), local_sha256=sha, local_start_s=offset, local_duration_s=info["duration_s"]
    )
    c["media"].update(width=info["width"], height=info["height"], fps=info["fps"])
    _remember_in_cache(cache, c["id"], final, {"sha": sha, "start": offset, "duration": info["duration_s"]}, started)

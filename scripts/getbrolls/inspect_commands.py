"""`inspect` e `preview --scan`: o que a fonte conta sobre si, as janelas candidatas e a varredura de trechos."""

import logging
import re
from pathlib import Path

from . import logs
from .acquisition import candidate_arg
from .config import CAP_EPSILON
from .fetch_commands import preview_files
from .media import probe
from .models import id_stem
from .rendering import render
from .summaries import _count

_log = logs.get("commands")


def _inspect_windows_source(windows):
    """Which kind of source data produced the candidate windows, for the `inspect` event."""
    sources = {(w or {}).get("source") for w in windows}
    if "subtitle" in sources:
        return "subtitles"
    if "chapter" in sources:
        return "chapters"
    if "description_timestamp" in sources:
        return "description"
    return "none"


def _validate_scan_flags(cmd, args):
    """`preview --scan` não combina com --start/--end nem --reference-only."""
    if not (cmd == "preview" and args.scan):
        return
    if args.start is not None or args.end is not None:
        raise ValueError(
            "--scan varre o vídeo inteiro: não combine com --start/--end. "
            "Escolha o intervalo depois, olhando a varredura."
        )
    if args.reference_only:
        raise ValueError("--scan precisa da mídia de trabalho; não use com --reference-only.")


# Fonte acima disto já é vídeo de evento inteiro/livestream: o trecho existe, mas
# achar onde ele está custa caro, e vale avisar antes de pedir mídia.
LONG_SOURCE_S = 1800


# Folga de ponto flutuante ao comparar tempos de mídia (start/end/duração) em segundos.
TIME_TOLERANCE_S = 0.1


# "360" solto no título ou nas tags do vídeo; `360p` e `1360` não contam.
_THREE_SIXTY = re.compile(r"(?<![0-9a-zA-Z])360(?![0-9a-zA-Z])", re.IGNORECASE)


def clamp_windows(windows, cap):
    """Encurta a janela sugerida até o teto de prévia: sugerir o que `preview` recusa é pior que sugerir menos.

    Mexe só no fim, e no lugar: a janela continua começando onde a fonte disse que o
    assunto começa, e o contrato de chaves de `candidate_windows` fica igual.
    """
    if not cap or cap <= 0:
        return windows
    for window in windows:
        if window["end_s"] - window["start_s"] > cap + CAP_EPSILON:
            window["end_s"] = round(window["start_s"] + cap, 3)
    return windows


LANGUAGE_NAMES = {"pt": "PT", "en": "EN", "es": "ES", "fr": "FR", "de": "DE", "it": "IT", "ja": "JA"}


def language_warning(probe, query):  # pylint: disable=redefined-outer-name
    # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).
    """Aviso de idioma: a frase da pessoa e a legenda da fonte não se falam."""
    from .inspecting import language_mismatch

    found = language_mismatch(probe, query)
    if not found:
        return None
    spoken, asked = found
    return (
        f"legenda em {LANGUAGE_NAMES.get(spoken, spoken.upper())}, sua --query está em "
        f"{LANGUAGE_NAMES.get(asked, asked.upper())}: traduza a fala ao idioma da fonte"
    )


def _byte_size(size):
    """MB com uma casa; abaixo de 0,1 MB, KB inteiro — "0.0 MB" não diz nada."""
    megabytes = size / (1024 * 1024)
    if megabytes >= 0.1:  # noqa: PLR2004 - 0,1 MB: abaixo disso "0.0 MB"
        return f"{megabytes:.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def inspect_warnings(probe_data, query=None):
    """Avisos sobre a fonte em si — o que costuma virar retrabalho depois da prévia."""
    found = []
    language = language_warning(probe_data, query)
    if language:
        found.append(language)
    duration = probe_data.get("duration_s")
    if duration and float(duration) > LONG_SOURCE_S:
        found.append(f"fonte longa: {round(float(duration) / 60)} min")
    haystack = " ".join([str(probe_data.get("title") or ""), *(probe_data.get("tags") or [])])
    if _THREE_SIXTY.search(haystack):
        found.append("vídeo 360°")
    downloaded = probe_data.get("downloaded_bytes")
    if downloaded and probe_data.get("local_copy"):
        # Rota de plugin: o arquivo chegou pela rota (cópia local ou download do próprio
        # plugin), não por um download do core — "baixar ... (0.0 MB)" confundia.
        found.append(
            f"esta fonte não tem metadados públicos, então a análise usou a cópia local do arquivo "
            f"({_byte_size(downloaded)}), trazida pela rota {probe_data['local_copy']} para o cache privado"
        )
    elif downloaded:
        # Esta fonte não tem página de metadados: a análise só existe porque o arquivo
        # veio inteiro. Dizer o preço evita repetir a conta sem perceber.
        found.append(
            f"esta fonte não tem metadados públicos, então analisá-la exigiu baixar o "
            f"arquivo inteiro ({downloaded / (1024 * 1024):.1f} MB) para o cache privado"
        )
    return found


def _inspect_target(ledger, args):
    """Resolve o alvo do `inspect`: o candidato (ou `None`), a URL e a fonte que será lida."""
    from .acquisition import direct_media

    c = None
    if args.candidate:
        c = ledger.get(args.candidate)
        if (c.get("media") or {}).get("kind") == "image":
            raise ValueError(
                "Imagem estática não tem duração nem trecho para analisar: gere a prévia "
                f"dela direto com `preview --candidate {candidate_arg(c)}`, sem `--start` e `--end`."
            )
        url = c.get("source_url")
        if not url and not direct_media(c):
            raise ValueError(
                "Este candidato não tem URL pública para analisar; use `inspect --url` "
                "ou importe o original local com `resolve --file`."
            )
        source = c
    else:
        url = args.url
        if not (url or "").strip():
            raise ValueError("--url não pode ser vazio: informe a URL pública da fonte.")
        # `probe_remote` resolveria a mesma URL logo abaixo: resolver aqui não custa
        # pedido a mais e revela a fonte de arquivo direto antes de chamar o yt-dlp.
        from getbrolls import providers

        source = providers.resolve(url)
        if (source.get("media") or {}).get("kind") == "image":
            raise ValueError(
                "Esta URL é uma imagem estática, sem trecho para analisar: registre com "
                "`resolve --url` e gere a prévia com `preview --candidate ID`, sem `--start` e `--end`."
            )
    return c, url, source


def inspect_source(ledger, args, config=None):
    """O que a fonte já conta sobre si, antes de escolher intervalo.

    Na rota normal (página que o yt-dlp lê) nada de mídia é pedido: só metadados e
    legenda. Numa fonte de arquivo direto (NASA, Commons, bancos), a duração que a
    própria fonte publicou vale e nada é baixado. Sem ela (vídeo da NASA), a duração
    só sai do arquivo, então o `inspect` **baixa o arquivo inteiro** uma vez para o
    cache privado — e diz isso, com o tamanho, no resumo e em `warnings[]`.

    Somente leitura sobre decisão e intervalo em qualquer rota: com `--candidate`, o
    único campo que passa a existir no projeto é `media.duration_s` — nada de
    aprovação, segmento, prévia ou arquivo em `clips/`.
    """
    from .acquisition import direct_media
    from .inspecting import candidate_windows
    from .social import probe_remote

    max_windows = args.max_windows
    if max_windows is not None and not 1 <= max_windows <= 20:  # noqa: PLR2004 - ints do msg abaixo
        raise ValueError("Use --max-windows entre 1 e 20.")
    c, url, source = _inspect_target(ledger, args)
    if direct_media(source):
        # NASA, Commons e os bancos publicam o arquivo; `source_url` é a página do
        # item, e o yt-dlp responde "Unsupported URL" para ela. A duração sai do
        # ffprobe do próprio arquivo, e legenda não existe nessa rota.
        probe_data = probe_direct(ledger, source, url)
    else:
        probe_data = probe_remote(url, cache=ledger.root.parent / ".getbrolls-sources")
    cap = float((config or {}).get("max_seconds") or 0)
    windows = clamp_windows(candidate_windows(probe_data, args.query, args.max_windows or 3), cap)
    if c is not None and probe_data["duration_s"]:
        # Único efeito no projeto: agora `set_segment` sabe recusar o que não cabe.
        c["media"]["duration_s"] = probe_data["duration_s"]
        ledger.save("inspect", c)
    logs.event(
        _log,
        logging.INFO,
        "inspect",
        candidate=c["id"] if c is not None else None,
        windows=len(windows),
        source=_inspect_windows_source(windows),
    )
    return {
        # Veredito primeiro, como nos outros comandos: quantas janelas e qual a melhor.
        "summary": inspect_summary(windows, probe_data, cap, args.query),
        "warnings": inspect_warnings(probe_data, args.query),
        "candidate": c["id"] if c is not None else None,
        "url": url,
        "title": probe_data.get("title"),
        "duration_s": probe_data["duration_s"],
        "chapters": probe_data["chapters"],
        "subtitle_langs": probe_data["subtitle_langs"],
        "subtitle_langs_total": probe_data.get("subtitle_langs_total", len(probe_data["subtitle_langs"])),
        "candidate_windows": windows,
    }


def probe_direct(ledger, source, url=None, stage="inspect"):
    """O mesmo contrato de `social.probe_remote`, lido do arquivo direto da fonte.

    Sem capítulo e sem legenda: um mp4 servido por URL não traz nenhum dos dois. O
    que ele traz é a duração real, que é o que separa a janela do palpite. Quando a
    fonte já publicou a duração (Commons, bancos), ela vale e nada é baixado; só a
    fonte sem esse metadado (vídeo da NASA) custa o download do arquivo inteiro.
    """
    from .acquisition import cache_direct_media, route_name

    known = (source.get("media") or {}).get("duration_s")
    # Rota de plugin segue pelo `cache_direct_media`, que recusa a rota de `fetch` e diz
    # o estágio: o atalho da duração publicada vale só para as fontes embutidas.
    if route_name(source) is None and isinstance(known, (int, float)) and not isinstance(known, bool) and known > 0:
        # A fonte já publicou a duração nos metadados (o Commons manda no `imageinfo`):
        # baixar o arquivo inteiro só para medir o que já se sabe custava dezenas de MB
        # antes de a pessoa confirmar qualquer coisa.
        return {
            "downloaded_bytes": 0,
            "local_copy": None,
            "url": url or source.get("source_url") or source.get("media_url"),
            "title": source.get("title"),
            "duration_s": float(known),
            "chapters": [],
            "subtitle_langs": [],
            "subtitle_langs_total": 0,
            "description": "",
            "tags": [],
            "subtitles": {},
        }
    path = cache_direct_media(ledger, source, stage=stage)
    info = probe(path)
    duration = info.get("duration_s")
    try:
        downloaded = Path(path).stat().st_size
    # lenient: size for the report only: zero when the downloaded file cannot be read
    except OSError:
        downloaded = 0
    return {
        # Analisar esta fonte custou o arquivo inteiro; quem lê o resumo precisa saber.
        "downloaded_bytes": downloaded,
        # Rota de plugin: nome da rota que trouxe a cópia local (o aviso muda de frase).
        "local_copy": route_name(source),
        "url": url or source.get("source_url") or source.get("media_url"),
        "title": source.get("title"),
        "duration_s": float(duration) if duration else None,
        "chapters": [],
        "subtitle_langs": [],
        "subtitle_langs_total": 0,
        "description": "",
        "tags": [],
        "subtitles": {},
    }


def _clock(seconds):
    total = round(float(seconds or 0))
    return f"{total // 60}:{total % 60:02d}"


def _has_cues(probe):  # pylint: disable=redefined-outer-name
    # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).
    """Alguma legenda chegou de fato, com falas dentro? Anunciar idioma não é ter legenda."""
    return any((entry or {}).get("cues") for entry in (probe.get("subtitles") or {}).values())


def inspect_summary(windows, probe, cap=0.0, query=None):  # pylint: disable=redefined-outer-name
    # Legado: ocorrência pré-existente (corpo idêntico ao código anterior à 2.6.0).
    """`{line, next}` em PT-BR: quantas janelas saíram, qual a melhor e o que fazer com ela."""
    # Sem isto, "nenhuma janela casou" parece resposta sobre o conteúdo da fonte
    # quando o que houve foi a frase e a legenda estarem em idiomas diferentes.
    mismatch = language_warning(probe, query)
    if not windows:
        return {
            "line": "A fonte não deu capítulo, legenda nem marcação de tempo: não tenho por onde começar."
            + (f" Atenção: {mismatch}." if mismatch else ""),
            "next": "Rode `preview --scan` para ver a grade do vídeo inteiro e escolher o trecho olhando.",
        }
    best = windows[0]
    found = best["score"] > 0
    # Janela de relógio sem uma única fala lida: são pontos igualmente espaçados, e
    # chamá-los de "ponto de partida" deixaria parecer que alguém leu o vídeo.
    blind = not found and not _has_cues(probe) and best.get("source") == "even_spacing"
    where = f"{_clock(best['start_s'])}–{_clock(best['end_s'])}"
    if found:
        middle = f"A mais parecida com o que você pediu está em {where}"
    elif blind:
        middle = (
            f"Sem legendas obtidas para esta fonte: não li nenhuma fala, e {where} é só "
            "um ponto igualmente espaçado no relógio, não um trecho encontrado"
        )
    else:
        middle = f"Nenhuma casou com a frase, então a primeira é só um ponto de partida: {where}"
    line = (
        f"Analisei a fonte e separei {_count(len(windows), 'janela', 'janelas')}. "
        + middle
        + (f" ({best['source']})." if best.get("source") else ".")
    )
    if probe.get("duration_s"):
        line += f" O vídeo tem {_clock(probe['duration_s'])}."
    if cap:
        # A janela já sai cortada no teto; dizer o teto evita pedir um intervalo que
        # o `preview` recusaria logo depois.
        line += f" A prévia aceita no máximo {cap:g} s por vez (GB_PREVIEW_MAX_SECONDS)."
    for warning in inspect_warnings(probe, query):
        line += f" Atenção — {warning}."
    return {
        "line": line,
        "next": (
            f"Confirme olhando: `preview --start {best['start_s']} --end {best['end_s']}`"
            if found
            else "Confirme antes de baixar: `preview --scan` mostra a grade do vídeo "
            f"inteiro, ou gere a prévia de {where} e olhe o contact sheet."
        ),
    }


def _scan_note(start, end, duration, ceiling, has_segment=False):
    """Frase em PT-BR dizendo que trecho da fonte entrou na grade, e por quê.

    `start`/`end` são tempo da fonte, os mesmos números dos rótulos: dizer "os
    primeiros N s" quando a mídia de trabalho começa no minuto 2 mandaria a pessoa
    procurar no lugar errado.
    """
    span = end - start
    ignored = (
        " A varredura ignora o intervalo já escolhido neste candidato: ela é "
        "exploratória, não define nem invalida segmento."
        if has_segment
        else ""
    )
    if end >= duration - TIME_TOLERANCE_S and start <= TIME_TOLERANCE_S:
        return f"Baixei e varri o vídeo inteiro ({span:.0f} s) para montar a grade." + ignored
    if span >= float(ceiling) - 0.1 and duration > float(ceiling):
        return (
            f"Varri de {_clock(start)} a {_clock(end)} ({span:.0f} s) de um vídeo de "
            f"{duration:.0f} s: o teto GB_SCAN_MAX_SECONDS está em {int(ceiling)} s. Para "
            "ver o resto, aumente o teto ou varra o candidato de novo depois de escolher "
            "um trecho." + ignored
        )
    return (
        f"Varri de {_clock(start)} a {_clock(end)} ({span:.0f} s) de um vídeo de "
        f"{duration:.0f} s: a mídia de trabalho que tenho aqui não cobre o resto. Os "
        "rótulos da grade são tempo da fonte, não do arquivo baixado." + ignored
    )


def _scan_known_duration(ledger, c):
    """Duração conhecida do candidato, consultando a fonte se ela ainda não tiver sido inspecionada."""
    duration = c["media"].get("duration_s")
    if duration or c["provider"] == "local":
        return duration
    from .acquisition import direct_media

    if direct_media(c):
        # Fonte de arquivo direto: o yt-dlp não lê a página dela, mas o ffprobe lê
        # o arquivo — e é o mesmo arquivo que a varredura vai usar logo em seguida.
        probe_data = probe_direct(ledger, c, stage="scan")
    else:
        from .social import probe_remote

        probe_data = probe_remote(c["source_url"], cache=ledger.root.parent / ".getbrolls-sources")
    duration = probe_data["duration_s"]
    if duration:
        c["media"]["duration_s"] = duration
    return duration


def _scan_prepare_span(ledger, c, config, duration):
    """Garante a mídia de trabalho e devolve {source, offset, local_start, span, duration}."""
    # O teto vale sobre a duração real: pedir 900 s de um vídeo de 126 s faz a fonte
    # devolver menos do que o pedido, e a grade sairia rotulada com tempos que não existem.
    span = min(duration, float(config["scan_max_seconds"]))
    if c["provider"] != "local":
        from .acquisition import prepare_source

        # `tolerant`: varrer o vídeo inteiro não pode falhar porque a fonte entregou
        # alguns segundos a menos do que anunciou.
        prepare_source(ledger, c, 0, span, tolerant=True, stage="scan")
    source = c.get("local_path")
    if not source:
        raise ValueError("A varredura precisa da mídia de trabalho; esta fonte só permite referência estática.")
    offset = c.get("local_start_s", 0)
    # Tempo do arquivo de trabalho para o ffmpeg; tempo da fonte nos rótulos.
    local_start = max(0, -offset)
    # A grade se mede pelo que existe no arquivo de trabalho, não pelo que foi pedido:
    # senão os últimos quadros vêm vazios e os rótulos apontam para o nada.
    available = c.get("local_duration_s")
    if available is None:
        from .media import probe as probe_media

        available = probe_media(source).get("duration_s")
    if available:
        span = min(span, max(0.0, float(available) - local_start))
    if span <= 0:
        raise ValueError("A mídia de trabalho não tem quadros para varrer; gere uma prévia do trecho que te interessa.")
    return {"source": source, "offset": offset, "local_start": local_start, "span": span, "duration": duration}


def _scan_record(ledger, c, config, result, span_info):
    """Grava o resultado da varredura no candidato e loga o evento de preview."""
    # pylint: disable=duplicate-code
    # O evento `preview` repete o formato de `_log_preview`; os campos vêm de lugares diferentes.
    offset, local_start, span, duration = (
        span_info["offset"],
        span_info["local_start"],
        span_info["span"],
        span_info["duration"],
    )
    # `scan` fica fora de `preview`/`segment`: varrer não decide nem invalida nada.
    # Os rótulos saem em tempo da fonte, e é esse mesmo trecho que a nota descreve.
    covered_start = float(offset) + local_start
    covered_end = covered_start + span
    capped = span < duration - 0.1
    c["scan"] = {
        **result,
        "capped": capped,
        "duration_s": duration,
        # Trecho da fonte que a grade cobre, no mesmo relógio de `frame_times_s`.
        "start_s": round(covered_start, 3),
        "end_s": round(covered_end, 3),
        # O que realmente entrou na grade, em segundos de mídia baixada.
        "downloaded_seconds": round(span, 3),
        "note": _scan_note(
            covered_start,
            covered_end,
            duration,
            config["scan_max_seconds"],
            has_segment=c["segment"]["start_s"] is not None,
        ),
    }
    ledger.save("preview", c)
    render(ledger)
    logs.event(
        _log,
        logging.INFO,
        "preview",
        candidate=c["id"],
        start_s=c["scan"]["start_s"],
        end_s=c["scan"]["end_s"],
        revision=c["segment"]["revision"],
        mode="scan",
    )


def scan_candidate(ledger, c, config):
    """Contact sheet de baixa resolução do vídeo inteiro; não escolhe intervalo nenhum."""
    from .media import scan_sheet

    if c.get("media", {}).get("kind") == "image":
        raise ValueError(
            "Imagem estática não tem o que varrer: gere a prévia dela com "
            f"`preview --candidate {candidate_arg(c)}`, sem `--start` e `--end`."
        )
    duration = _scan_known_duration(ledger, c)
    if not duration:
        raise ValueError("Duração desconhecida: rode `inspect --candidate " + candidate_arg(c) + "` antes de varrer.")
    span_info = _scan_prepare_span(ledger, c, config, float(duration))
    stem = id_stem(c["id"])
    result = scan_sheet(
        span_info["source"],
        ledger.root / "previews",
        stem,
        span_info["local_start"],
        span_info["span"],
        source_offset=span_info["offset"],
    )
    _scan_record(ledger, c, config, result, span_info)
    return {
        **c,
        # Mesmo contrato das outras rotas de `preview`: caminho absoluto de tudo o que
        # existe para olhar, e não só da varredura recém-gerada.
        "files": {**preview_files(ledger, c), "scan": str((ledger.root / result["scan_path"]).resolve())},
    }

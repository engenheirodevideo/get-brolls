"""Leitor de página com player embutido: acha o vídeo público do player, sem executar JavaScript.

Nenhum player é reconhecido pelo nome: o que se procura na página já lida é a forma. Um elemento
personalizado cujo nome tem `player` (`<x-player id="…">`, com um id do player num atributo) e/ou um
script HTTPS público, na 443, que a página carrega (`<script src>` ou o `.src = "…"` de um carregador inline;
URL só citada no texto não conta), cujo caminho parece o pacote de um player (`/players/<id>/…/x.js`, ou um
arquivo `.js` com `player` no nome). Com id no elemento, vale o script que traz esse id; sem ele, o script
com pasta `/players/<id>/` vem antes do achado só pelo nome do arquivo. Achado só pelo nome, sem elemento
nem pasta de player, o candidato sai com aviso (`guessed`): uma biblioteca de player pode citar um link de
exemplo. No máximo um script é lido (com teto de tamanho e de prazo, pelo
`web_text`, sem sair do host dele); dele saem os links `.m3u8`/`.mp4` HTTPS públicos na 443, conferidos
depois pelo `stream_check` antes de ir ao `direct_stream` com a página como referer. A segurança não
vem de uma lista de hosts: vem das travas de destino (`http.public_url`/`public_destination`, só 443),
do proxy de saída conferida, das leituras com teto, do `stream_check`, do downloader nativo do yt-dlp e
do corte só em arquivo local. A chave `drm` do player não decide nada: só o DRM de CDM declarado na
playlist para (`stream_check`). Funil ofuscado, que não põe o player no HTML, fica de fora, com o
`suggested_route` do que a página mostra (`route_hint`).
"""

import itertools
import logging
import re
from typing import NamedTuple
from urllib.parse import urlsplit

from . import logs, route_hint, web_text
from .errors import ProviderError, RouteRefusal
from .route_hint import referer_for

_log = logs.get("page_video")

PAGE_MAX_BYTES = 2 * 1024 * 1024
PLAYER_MAX_BYTES = 4 * 1024 * 1024

# `re.ASCII` junto de `IGNORECASE` em todos os padrões: sem ele, `ſ` (U+017F) casa `s` e `K` (U+212A) casa `k`.
_ID = r"[A-Za-z0-9_-]{8,64}"
# A abertura de um elemento cujo nome tem `player`; os limites fixos de cada lado deixam o custo por
# posição constante. Só a abertura é procurada; os atributos são lidos num trecho curto (`_element_ids`):
# uma regex única até o `>` voltava a varrer o resto da página a cada tag sem `>`, em tempo quadrático.
_ELEMENT = re.compile(r"<([a-z][a-z0-9-]{0,40}player[a-z0-9-]{0,40})(?![a-z0-9-])", re.IGNORECASE | re.ASCII)
# Atributo que leva o id do player: `id`, `player-id`/`playerid` ou um `data-…id`.
_ELEMENT_ID = re.compile(
    r"(?<![A-Za-z0-9_-])(?:id|player-?id|data-[a-z0-9-]{0,32}id)\s*=\s*[\"']([A-Za-z0-9_-]{1,96})[\"']",
    re.IGNORECASE | re.ASCII,
)
ELEMENT_LIMIT = 64
ATTRIBUTES_MAX = 2048
# Onde a página carrega um script: o `src` de uma tag `<script>` (lida só até o `>` dela, com teto) ou o
# `.src = "…"` de um carregador inline (`s=document.createElement("script"); s.src="…"`). URL só citada no
# texto (comentário, JSON, link, `data-src`) não é candidata.
_SCRIPT_OPEN = re.compile(r"<script\b", re.IGNORECASE | re.ASCII)
_SCRIPT_SRC = re.compile(
    r"(?<![A-Za-z0-9_:.-])src\s*=\s*(?:\"([^\"\s<>]{1,2048})\"|'([^'\s<>]{1,2048})'|([^\s\"'<>]{1,2048}))",
    re.IGNORECASE | re.ASCII,
)
_LOADER_SRC = re.compile(r"\.src\s*=\s*[\"']([^\"'\s<>\\]{1,2048})[\"']", re.IGNORECASE | re.ASCII)
SCRIPT_SCAN_LIMIT = route_hint.SCAN_LIMIT
# Pasta `player`/`players` seguida do id, no caminho do pacote do player.
_PLAYERS_DIR = re.compile(r"players?", re.IGNORECASE | re.ASCII)
MEDIA_SUFFIXES = (".m3u8", ".mp4")

# A chave `drm` (ou `isDrm`, `drmEnabled`) ligada: `true`, `"true"`, `!0` do JavaScript minificado, `1`,
# `"1"` ou um objeto não vazio (`drm:{widevine:…}`). `false`, `!1`, `0`, `null` e `{}` não ligam. Só vai para
# o log (`drm_flag`): o que para a rota é o DRM de CDM na playlist, não a chave do player.
_DRM = re.compile(
    r"[\"']?\b(?:is_?)?drm(?:_?enabled)?[\"']?\s*:\s*"
    r"(?:true\b|[\"'](?:true|1)[\"']|!0(?![0-9])|1(?![0-9.])|\{\s*[^}\s])",
    re.IGNORECASE | re.ASCII,
)
# Sinal da proteção anti-download (o vídeo alternativo que o player entrega a quem baixa): só uma chave
# genérica da configuração do player, com um desses nomes, ligada ou com um endereço. Os nomes não vêm de um
# player conferido; player que usa outro nome, ou servidor que troca o vídeo sem marca no script, não é
# detectável sem rede (docs/SECURITY.md).
_DECOY = re.compile(
    r"[\"']?\b(?:anti_?download|alternat(?:e|ive)_?video|fake_?video|decoy(?:_?video)?)[\"']?\s*:\s*"
    r"(?:true\b|[\"'](?:true|1|https?:|//)|!0(?![0-9])|1(?![0-9.])|\{\s*[^}\s])",
    re.IGNORECASE | re.ASCII,
)
DECOY_NOTICE = (
    "O player embutido desta página dá sinal de proteção anti-download, que pode entregar um vídeo alternativo "
    "no lugar do original. Compare a prévia com o vídeo da página antes de aprovar."
)

GUESSED_NOTICE = (
    "O player desta página foi achado só pelo nome do arquivo do script, sem elemento de player nem pasta de "
    "player no caminho: o link pode ser de exemplo de uma biblioteca, e não o vídeo da página. Compare a "
    "prévia com o vídeo da página antes de aprovar."
)

DIRECT_LINK_HINT = (
    "Se a página mostra um vídeo, passe o link direto dele (.m3u8 ou .mp4) no lugar da página: no navegador, "
    "abra DevTools > Network, filtre por m3u8 (ou mp4), dê play e copie a URL da requisição. Sem vídeo na "
    "página, use a busca de um banco (`search --provider FONTE`) ou o original local."
)
UNSUPPORTED = "Fonte de URL não suportada"
DRM_REFUSAL = (
    "Este vídeo usa DRM de navegador (Widevine, FairPlay ou PlayReady), e o getbrolls não contorna DRM. "
    "Peça o arquivo original a quem publicou e importe com `resolve --file`, ou escolha outra fonte."
)


class PageVideo(NamedTuple):
    """O vídeo achado na página: o link, a página que vai como referer, os ids e o sinal de vídeo alternativo.

    `player_id` é o id do player tirado do caminho do script ou do elemento (`None` sem id). `decoy` é o
    motivo (`anti_download_flag`) quando a configuração do player tem a chave de vídeo alternativo da
    proteção anti-download; `None` sem sinal (várias rendições no script não contam). `guessed` diz que o
    script foi achado só pelo nome do arquivo, sem elemento de player na página nem pasta `/players/<id>/`
    no caminho.
    """

    media_url: str
    referer: str
    video_id: str | None
    player_id: str | None
    decoy: str | None = None
    guessed: bool = False


def _unescape(text):
    """Texto com `\\/` (barra escapada de JSON) trocado por `/`."""
    return text.replace("\\/", "/")


def _id_tokens(value):
    """O valor do atributo e cada pedaço depois de um `-` (`player-abc…` dá ele mesmo e `abc…`), com forma de id."""
    pieces = [value] + [value.split("-", n)[n] for n in range(1, value.count("-") + 1)]
    return [piece for piece in pieces if re.fullmatch(_ID, piece, re.ASCII)]


def _element_ids(text):
    """`(achou_elemento, ids)`: se há elemento de player e os ids das primeiras `ELEMENT_LIMIT` tags, em ordem.

    Só conta o elemento personalizado (nome com `-`) cujo nome tem `player`. Cada tag é lida só até o
    `>` dela, e no máximo `ATTRIBUTES_MAX` caracteres: o custo fica linear no tamanho da página.
    """
    seen, ids = False, {}
    matches = (m for m in _ELEMENT.finditer(text) if "-" in m.group(1))
    for match in itertools.islice(matches, ELEMENT_LIMIT):
        seen = True
        attributes = text[match.end() : match.end() + ATTRIBUTES_MAX].partition(">")[0]
        for found in itertools.islice(_ELEMENT_ID.finditer(attributes), 8):
            # Dicionário como conjunto ordenado: sem repetir e na ordem, sem varrer uma lista a cada id.
            ids.update(dict.fromkeys(_id_tokens(found.group(1))))
    return seen, list(ids)


def _script_player_id(path):
    """`(é_pacote_de_player, id_ou_None)` do caminho de um script.

    Pacote de player: termina em `.js` e tem uma pasta `player`/`players` seguida de um id, ou o nome do
    arquivo tem `player`. O id é o da pasta, quando há.
    """
    segments = path.split("/")
    if not segments[-1].lower().endswith(".js"):
        return False, None
    for index, segment in enumerate(segments[:-2]):
        following = segments[index + 1]
        if _PLAYERS_DIR.fullmatch(segment) and re.fullmatch(_ID, following, re.ASCII):
            return True, following
    return "player" in segments[-1].lower(), None


def _script_sources(text):
    """Os endereços HTTPS públicos na 443 que a página carrega como script, na ordem do texto e sem repetir.

    Só o `src` das primeiras `SCRIPT_SCAN_LIMIT` tags `<script>` e dos primeiros `.src = "…"`; o valor
    inteiro precisa ser o endereço (com `https:` ou sem esquema), conferido por `route_hint.https_urls`.
    """
    placed = []
    for match in itertools.islice(_SCRIPT_OPEN.finditer(text), SCRIPT_SCAN_LIMIT):
        attributes = text[match.end() : match.end() + ATTRIBUTES_MAX].partition(">")[0]
        src = _SCRIPT_SRC.search(attributes)
        if src:
            placed.append((match.start(), next(group for group in src.groups() if group)))
    placed.extend(
        (match.start(), match.group(1)) for match in itertools.islice(_LOADER_SRC.finditer(text), SCRIPT_SCAN_LIMIT)
    )
    found = {}
    for _, value in sorted(placed):
        expected = "https:" + value if value.startswith("//") else value
        if expected not in found and route_hint.https_urls(value, (".js",)) == [expected]:
            found[expected] = None
    return list(found)


def _scripts(text):
    """`(url, id_ou_None, pastas)` de cada script de player que a página carrega, na ordem."""
    found = []
    for url in _script_sources(text):
        path = urlsplit(url).path
        is_player, player_id = _script_player_id(path)
        if is_player:
            found.append((url, player_id, set(path.split("/"))))
    return found


def _pick_player(html):
    """`(achou_elemento, (url_do_script, player_id) ou None)` do player da página.

    Com ids no elemento, vale o primeiro script cujo caminho traz um deles; senão, o primeiro com pasta
    `/players/<id>/`; senão, o primeiro script (achado só pelo nome do arquivo).
    """
    text = _unescape(html)
    element, wanted = _element_ids(text)
    scripts = _scripts(text)
    if not scripts:
        return element, None
    for player_id in wanted:
        for url, script_id, segments in scripts:
            if player_id in segments:
                return element, (url, script_id or player_id)
    url, script_id, _ = next((entry for entry in scripts if entry[1] is not None), scripts[0])
    return element, (url, script_id or (wanted[0] if wanted else None))


def _video_id(path):
    """O id do vídeo no caminho do link: a pasta logo acima do arquivo, ou o nome do arquivo, quando têm forma de id."""
    segments = path.rstrip("/").split("/")
    candidates = [segments[-2]] if len(segments) > 1 else []
    candidates.append(segments[-1].rsplit(".", 1)[0])
    return next((c for c in candidates if re.fullmatch(_ID, c, re.ASCII)), None)


def _media_list(player_js):
    """`(url, video_id)` de cada link `.m3u8`/`.mp4` HTTPS público na 443 do script; HLS primeiro, sem repetir."""
    found = route_hint.https_urls(player_js, MEDIA_SUFFIXES)[:ELEMENT_LIMIT]
    found.sort(key=lambda url: not urlsplit(url).path.lower().endswith(".m3u8"))
    return [(url, _video_id(urlsplit(url).path)) for url in found]


def _decoy(player_js):
    """O motivo do sinal de vídeo alternativo (a chave genérica na configuração do player), ou `None`."""
    return "anti_download_flag" if _DECOY.search(player_js) else None


def _refuse(host, reason, message, suggested_route):
    """Linha `page_video` da recusa e o erro pronto para levantar, com o próximo passo para o agente."""
    logs.event(_log, logging.WARNING, "page_video", host=host, result="refused", reason=reason)
    return RouteRefusal(message, suggested_route=suggested_route)


def _read_script(url):
    """O texto do único script de player lido: com teto, prazo e conferência do `web_text`, sem sair do host dele."""
    script_host = (urlsplit(url).hostname or "").lower()
    return web_text.get_text(url, PLAYER_MAX_BYTES, allowed_hosts=(script_host,))[1]


def find(page_url, source_for_host=None):
    """O vídeo do player embutido da página.

    Levanta `RouteRefusal` (com `suggested_route`) quando a página não abre, não traz player embutido ou
    o script do player não traz o link do vídeo. `source_for_host(host)` é o nome da fonte do getbrolls que
    atende um host (para o `<iframe>` do `suggested_route`). A chave `drm` do player não recusa nada (vai
    para o log).
    """
    host = (urlsplit(page_url).hostname or "-").lower()
    try:
        final_url, html = web_text.get_text(page_url, PAGE_MAX_BYTES)
    except ProviderError as error:
        raise _refuse(
            host,
            "page_unreadable",
            f"{UNSUPPORTED}: não consegui ler a página ({error}). {DIRECT_LINK_HINT}",
            route_hint.page_unreadable(),
        ) from None
    element, player = _pick_player(html)
    if player is None:
        logs.event(_log, logging.INFO, "page_video", host=host, result="none", reason="no_player")
        raise RouteRefusal(
            f"{UNSUPPORTED}: a página não traz um player que o getbrolls saiba ler. {DIRECT_LINK_HINT}",
            suggested_route=route_hint.for_page(final_url, html, element, source_for_host),
        )
    player_url, player_id = player
    guessed = not element and _script_player_id(urlsplit(player_url).path)[1] is None
    try:
        player_js = _read_script(player_url)
    except ProviderError as error:
        raise _refuse(
            host,
            "player_unreadable",
            f"{UNSUPPORTED}: achei um player embutido na página, mas não consegui ler o script dele ({error}). "
            + DIRECT_LINK_HINT,
            route_hint.for_page(final_url, html, True, source_for_host),
        ) from None
    videos = _media_list(player_js)
    if not videos:
        raise _refuse(
            host,
            "no_media",
            f"{UNSUPPORTED}: achei um player embutido na página, mas o endereço do vídeo não está no script "
            f"público dele. {DIRECT_LINK_HINT}",
            route_hint.for_page(final_url, html, True, source_for_host),
        )
    video = _page_video(videos[0], final_url, player_js, player_id, guessed)
    logs.event(
        _log,
        logging.INFO,
        "page_video",
        host=host,
        result="found",
        player="embedded_player",
        drm_flag=bool(_DRM.search(player_js)),
        decoy=video.decoy or "-",
        guessed=guessed,
    )
    return video


def _page_video(media, page_url, player_js, player_id, guessed):
    """O `PageVideo` do vídeo `media` (link e id) do script do player, com a página como referer."""
    media_url, video_id = media
    return PageVideo(media_url, referer_for(page_url), video_id, player_id, _decoy(player_js), guessed)

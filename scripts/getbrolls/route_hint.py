"""O `suggested_route` de uma recusa do `resolve`: o que a página mostra de vídeo e o próximo comando.

Só o HTML já lido entra (nenhum pedido a mais), com teto de ocorrências e de tamanho por trecho:
o custo fica linear no tamanho da página. O que se procura: link direto `.m3u8`/`.mp4` (HTTPS ou
sem esquema), `<iframe>` de player e o player embutido. O link direto e o `<iframe>` de um host que
uma fonte do getbrolls já atende (`url_hosts` do registro, perguntado por `source_for_host`) viram
`next_command`; o resto vira dica em `alternatives`. Nada daqui vai para log.
"""

import itertools
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .http import public_url

# Quantas ocorrências de cada tipo são olhadas, e o tamanho máximo de um trecho (URL ou atributos).
SCAN_LIMIT = 256
SNIPPET_MAX = 2048
# Quantos endereços candidatos (já com o sufixo pedido) são conferidos, aceitos ou não.
EXAMINE_LIMIT = 4 * SCAN_LIMIT

DEVTOOLS_HINT = (
    "Abra a página no navegador, DevTools > Network, filtre por m3u8 (ou mp4), dê play e passe o link direto "
    "da requisição com `resolve --url LINK --referer PÁGINA`."
)
SEARCH_HINT = "Sem vídeo na página, use a busca de um banco (`search --provider FONTE`) ou o original local."
FILE_HINT = "Peça o arquivo original a quem publicou e importe com `resolve --file`."
OFFICIAL_CLIP_HINT = (
    "Busque o trailer ou um trecho oficial do mesmo vídeo no YouTube ou no Instagram e passe esse link "
    "com `resolve --url`."
)

# Endereço citado no texto: o esquema (opcional) e o resto até um separador de atributo ou de texto.
_URL = re.compile(
    r"(https?:)?//([A-Za-z0-9.-]{1,253}(?::[0-9]{1,5})?/[^\s\"'<>\\()]{0,2048})", re.IGNORECASE | re.ASCII
)
# Metacaractere de shell num link sugerido: o `next_command` é argv, mas um agente pode juntá-lo numa
# linha de shell sem aspas. Link com um desses não é sugerido (o `&` também: o getbrolls não aceita query).
_SHELL_META = frozenset("`$;&|!*{}^[]~")
_ASCII_URL = re.compile(r"[\x21-\x7e]+")
_IFRAME = re.compile(r"<iframe\b", re.IGNORECASE | re.ASCII)
# O atributo `src` da tag, e não o `data-src` (carregamento preguiçoso) nem outro nome que termina em `src`.
_SRC = re.compile(r"(?<![A-Za-z0-9_:.-])src\s*=\s*[\"']([^\"']{1,2048})[\"']", re.IGNORECASE | re.ASCII)
_ID = r"[A-Za-z0-9_-]"
_YOUTUBE_EMBED = re.compile(rf"/embed/({_ID}{{11}})(?:[/?#]|$)", re.ASCII)
_INSTAGRAM_EMBED = re.compile(rf"/(p|reel|tv)/({_ID}{{1,64}})/embed", re.ASCII)

# Um `<iframe>` de host que nenhuma fonte atende só conta quando o endereço parece um player de vídeo.
_PLAYER_WORDS = ("player", "embed", "video")


def route(kind, reason, next_command=None, alternatives=()):
    """O objeto `suggested_route`: `kind`, `reason` (pt-BR), `next_command` (argv ou `None`) e `alternatives`."""
    return {
        "kind": kind,
        "reason": reason,
        "next_command": list(next_command) if next_command else None,
        "alternatives": list(alternatives),
    }


def drm():
    """DRM de CDM (Widevine, FairPlay, PlayReady): não há comando; a saída é outra fonte do mesmo vídeo."""
    return route(
        "drm",
        "O vídeo usa DRM de navegador (Widevine, FairPlay ou PlayReady), e o getbrolls não contorna DRM.",
        alternatives=(OFFICIAL_CLIP_HINT, FILE_HINT),
    )


def unknown_host():
    """Host que nenhuma fonte reclama, numa chamada que não abre páginas (a fila)."""
    return route(
        "unknown_host",
        "Nenhuma fonte do getbrolls reconhece este host, e esta chamada não abre páginas.",
        alternatives=(DEVTOOLS_HINT, SEARCH_HINT),
    )


def page_unreadable():
    """A página não abriu: só as dicas, sem comando."""
    return route(
        "page_unreadable",
        "Não consegui ler a página para procurar o vídeo.",
        alternatives=(DEVTOOLS_HINT, SEARCH_HINT),
    )


def referer_for(page_url):
    """A página como referer: esquema, host e caminho, sem query nem fragmento."""
    parts = urlsplit(page_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))


def _https(scheme, rest):
    """O endereço com `https:`; `None` quando veio com `http:` (o getbrolls só lê HTTPS)."""
    if scheme and scheme.lower() == "http:":
        return None
    return "https://" + rest


def https_urls(text, suffixes=None):
    """Endereços HTTPS públicos na 443 citados no texto (com ou sem esquema), na ordem e sem repetir.

    Só ASCII visível; `http:` fica de fora (o getbrolls só lê HTTPS) e `\\/` de JSON vale como `/`. Com
    `suffixes`, só os caminhos que terminam num deles (sem diferenciar maiúsculas). Confere no máximo
    `EXAMINE_LIMIT` candidatos e devolve no máximo `SCAN_LIMIT` endereços.
    """
    found, examined = [], 0
    for match in _URL.finditer(text.replace("\\/", "/")):
        path = match.group(2).partition("?")[0].partition("#")[0]
        if suffixes is not None and not path.lower().endswith(suffixes):
            continue
        examined += 1
        if examined > EXAMINE_LIMIT:
            break
        url = _https(match.group(1), match.group(2))
        if url is None or not _ASCII_URL.fullmatch(url):
            continue
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            continue
        # Só a 443: o `resolve` recusa outra porta.
        if port not in {None, 443} or not public_url(url) or url in found:
            continue
        found.append(url)
        if len(found) >= SCAN_LIMIT:
            break
    return found


def direct_links(text):
    """Links `.m3u8`/`.mp4` HTTPS públicos na 443, sem metacaractere de shell, na ordem e sem repetir."""
    return [url for url in https_urls(text, (".m3u8", ".mp4")) if not _SHELL_META.intersection(url)]


def iframe_sources(text):
    """`src` dos primeiros `SCAN_LIMIT` `<iframe>`, cada um lido só até o `>` da tag."""
    sources = []
    for match in itertools.islice(_IFRAME.finditer(text), SCAN_LIMIT):
        attributes = text[match.end() : match.end() + SNIPPET_MAX].partition(">")[0]
        src = _SRC.search(attributes)
        if src:
            sources.append(src.group(1).replace("&amp;", "&"))
    return sources


# Um nome de DNS tem no máximo 253 caracteres: host maior nem é perguntado ao registro.
HOST_MAX = 253


def _source_of(host, source_for_host):
    """A fonte do getbrolls que atende `host` ou um domínio acima dele (`a.b.exemplo` → `b.exemplo`), ou `None`.

    Cada sufixo é um corte do próprio host (sem `join` por rótulo), e host acima de `HOST_MAX` não é
    perguntado: o custo por iframe fica limitado.
    """
    if source_for_host is None or not host or len(host) > HOST_MAX:
        return None
    start = 0
    while True:
        dot = host.find(".", start)
        if dot < 0:
            return None
        name = source_for_host(host[start:])
        if name:
            return name
        start = dot + 1


def _once_per_host(source_for_host):
    """`source_for_host` com a resposta guardada por host: a mesma página não pergunta duas vezes."""
    answers = {}

    def lookup(host):
        if host not in answers:
            answers[host] = source_for_host(host)
        return answers[host]

    return lookup


def _canonical(name, parts):
    """A URL que a fonte `name` resolve a partir do endereço do `<iframe>`, ou `None`."""
    if name == "youtube":
        found = _YOUTUBE_EMBED.search(parts.path)
        ident = found.group(1) if found else (parse_qs(parts.query).get("v") or [None])[0]
        if ident and re.fullmatch(_ID + "{11}", ident):
            return "https://www.youtube.com/watch?v=" + ident
    elif name == "instagram":
        found = _INSTAGRAM_EMBED.search(parts.path)
        if found:
            return f"https://www.instagram.com/{found.group(1)}/{found.group(2)}/"
    return None


def _embed(src, source_for_host):
    """`(fonte_ou_None, url_que_o_getbrolls_resolve_ou_None)` de um `src` de iframe; `None` se não parece player.

    A fonte vem do registro (`source_for_host`); um host que nenhuma fonte atende só conta quando o
    endereço parece um player de vídeo (`_PLAYER_WORDS`), e nunca vira comando.
    """
    text = src if not src.startswith("//") else "https:" + src
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    name = _source_of(host, source_for_host)
    if name is not None:
        return name, _canonical(name, parts)
    if host and any(word in (host + parts.path).lower() for word in _PLAYER_WORDS):
        return None, None
    return None


def for_page(page_url, html, embedded_player=False, source_for_host=None):
    """O `suggested_route` de uma página sem rota: link direto, `<iframe>` de fonte, player embutido ou nada.

    `embedded_player` diz que a página tem um player embutido cujo vídeo não foi achado no script público.
    `source_for_host(host)` diz o nome da fonte do getbrolls que atende o host (o registro), ou `None`.
    Link direto vem antes de tudo (é o próprio vídeo); depois o `<iframe>` que uma fonte resolve.
    """
    links = direct_links(html)
    if links:
        return route(
            "direct_link",
            "A página cita um link direto de vídeo; o getbrolls resolve esse link com a página como referer.",
            ["resolve", "--url", links[0], "--referer", referer_for(page_url)],
            [f"Outros links diretos na página: {len(links) - 1}."] if len(links) > 1 else [],
        )
    if source_for_host is not None:
        source_for_host = _once_per_host(source_for_host)
    embeds = [embed for embed in (_embed(src, source_for_host) for src in iframe_sources(html)) if embed]
    for name, supported in embeds:
        if supported:
            return route(
                "player_embed",
                f"A página embute um vídeo da fonte {name}, que o getbrolls resolve pela URL dele.",
                ["resolve", "--url", supported],
            )
    if embedded_player:
        return route(
            "embedded_player",
            "A página tem um player embutido, mas o endereço do vídeo não está no script público dele.",
            alternatives=(DEVTOOLS_HINT,),
        )
    for name, _ in embeds:
        if name is not None:
            how = (
                "Abra o vídeo no TikTok e passe a URL completa @usuario/video/ID com `resolve --url`."
                if name == "tiktok"
                else f"Abra o vídeo na própria fonte ({name}) e passe a URL da página dele com `resolve --url`."
            )
            return route(
                "player_embed",
                f"A página embute um player da fonte {name}, que o getbrolls não lê pelo endereço do player.",
                alternatives=(how, SEARCH_HINT),
            )
    if embeds:
        return route(
            "player_embed",
            "A página embute um player de outro site, que o getbrolls não lê pela página.",
            alternatives=(DEVTOOLS_HINT, SEARCH_HINT),
        )
    return route(
        "no_video",
        "A página não traz link de vídeo nem player que o getbrolls reconheça no HTML.",
        alternatives=(DEVTOOLS_HINT, SEARCH_HINT),
    )

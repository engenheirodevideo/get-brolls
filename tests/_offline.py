"""Ambiente para `resolve --url` de YouTube sem rede nos testes.

`resolve` pede metadados ao yt-dlp; aqui o "yt-dlp" é o próprio Python (existe e é
executável em qualquer SO), que recusa as flags e sai com erro na hora: os
metadados viram `{}` — o mesmo caminho de uma página sem metadados — e nada sai
da máquina. `GB_YTDLP_SLEEP=0,0,0` tira a pausa entre pedidos.

`_isolation` aplica este yt-dlp a toda a suíte (só `GB_EVAL_NETWORK=1` o deixa de fora) e, com
`GB_TEST_OFFLINE=1`, também recusa a consulta de nomes ao DNS: a máquina de quem roda pode estar
sem rede e a suíte inteira passa do mesmo jeito.
"""

import ipaddress
import socket
import sys
from pathlib import Path

OFFLINE_YTDLP = {"GB_YTDLP_PATH": sys.executable, "GB_YTDLP_SLEEP": "0,0,0"}


def _needs_no_dns(host):
    """`localhost` e endereço literal (IPv4 ou IPv6) se resolvem sem perguntar a ninguém."""
    if host in (None, "", "localhost"):
        return True
    text = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)
    try:
        ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    return True


def install_dns_guard(log=None):
    """Troca `socket.getaddrinfo` por uma que recusa todo nome (`socket.gaierror`), como uma máquina sem rede.

    Endereço literal e `localhost` passam para o original: os servidores locais dos testes seguem funcionando.
    Com `log` (um caminho), cada nome recusado entra nele, um por linha. Devolve o `getaddrinfo` original."""
    real = socket.getaddrinfo

    def refuse(host, *args, **kwargs):
        if _needs_no_dns(host):
            return real(host, *args, **kwargs)
        if log is not None:
            with Path(log).open("a", encoding="utf-8") as stream:
                stream.write(f"{host}\n")
        raise socket.gaierror(socket.EAI_NONAME, "sem rede (GB_TEST_OFFLINE)")

    socket.getaddrinfo = refuse
    return real

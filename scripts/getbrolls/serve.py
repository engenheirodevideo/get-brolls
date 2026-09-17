"""Servidor HTTP local, somente leitura, para abrir o Storyboard (brolls/review.html) no navegador.

Serve apenas `<project>/brolls/`, vinculado a 127.0.0.1 (nunca 0.0.0.0): nada aqui é
exposto fora desta máquina. Sem cache — o Storyboard e suas prévias podem mudar a
qualquer revisão local. Não cria a árvore do projeto nem toma a trava exclusiva
(comando somente leitura, como `status`); se `brolls/review.html` não existir, falha
cedo com uma mensagem clara em vez de servir um diretório vazio.
"""
from __future__ import annotations

import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8767


class _NoCacheHandler(SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler servindo um diretório fixo, sem cache e sem log no console."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format, *args):  # noqa: A002 - assinatura exigida pela stdlib
        # Silencioso: o processo comunica estado só via a linha JSON impressa em run().
        pass


def start(project, port: int = DEFAULT_PORT):
    """Sobe o servidor em 127.0.0.1, tentando `port` e caindo para uma porta livre se ocupada.

    Retorna (server, port). O chamador decide quando chamar `serve_forever()`/fechar.
    """
    directory = Path(project).expanduser().resolve() / "brolls"
    review = directory / "review.html"
    if not review.is_file():
        raise ValueError(
            f"Storyboard não encontrado em {review}. Gere-o antes com o comando `review`."
        )
    handler = partial(_NoCacheHandler, directory=str(directory))
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        # Porta pedida ocupada: cai para uma porta efêmera livre.
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    return server, server.server_address[1]


def run(project, port: int = DEFAULT_PORT) -> int:
    """Sobe o servidor, imprime a URL em JSON e serve até Ctrl+C (saída limpa, exit 0)."""
    server, bound_port = start(project, port)
    directory = Path(project).expanduser().resolve() / "brolls"
    payload = {
        "urls": [
            f"http://localhost:{bound_port}/review.html",
            f"http://127.0.0.1:{bound_port}/review.html",
        ],
        "port": bound_port,
        "directory": str(directory),
    }
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

"""Exemplo do SDK do Get B-rolls: fonte que procura vídeos por nome numa pasta local.

Aponte a pasta em PASTA_LOCAL_DIR; ela tem que ficar dentro de uma das raízes de
`permissions.paths` do manifesto. A busca devolve só metadados. A rota
`pasta_local` (stage="preview") copia o arquivo escolhido para a pasta de trabalho
do core com `api.local_file` — `preview --start/--end` funciona sem `resolve --file`.
O comando `recentes` lista os vídeos mais novos da pasta (`gb x pasta_local recentes`).
Mensagens para quem usa saem em `PluginError` (o core mostra só o tipo das outras).
"""

import hashlib
from pathlib import Path

from getbrolls.sdk import PluginError, ProviderCapabilities, RouteResult

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm")
DEFAULT_RECENT = 5
MAX_RECENT = 50


def _ident(path):
    return hashlib.sha256(path.as_posix().encode()).hexdigest()[:16]


class PastaLocal:
    """Provider da pasta local: busca vídeos pelo nome do arquivo e lista os mais recentes."""

    name = "pasta_local"
    capabilities = ProviderCapabilities(search=True, transport="local", seek="local", route="pasta_local")

    def __init__(self, api):
        self.api = api

    def _folder(self):
        raw = self.api.env("PASTA_LOCAL_DIR")
        if not raw or not Path(raw).is_dir():
            raise PluginError("Configure PASTA_LOCAL_DIR com a pasta dos seus B-rolls.")
        return Path(raw)

    def videos(self):
        """Vídeos da pasta configurada, em ordem estável, pelas extensões de `VIDEO_SUFFIXES`."""
        return [p for p in sorted(self._folder().rglob("*")) if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]

    def search(self, query, limit, media):  # pylint: disable=unused-argument  # noqa: ARG002 - assinatura fixa de Provider.search; esta fonte só tem vídeo
        """Candidatos cujo nome do arquivo contém todas as palavras de `query`."""
        words = [w for w in query.lower().split() if w]
        found = [path for path in self.videos() if all(w in path.stem.lower() for w in words)]
        return [self.api.candidate(self.name, _ident(path), path.stem) for path in found[:limit]]

    def resolve(self, url):  # pylint: disable=unused-argument  # noqa: ARG002 - assinatura fixa de Provider.resolve; esta fonte não reconhece URL
        """Esta fonte não reconhece URL colada por fora: sempre `None`."""
        return

    def refresh(self, item):
        """Devolve `item` sem mudança: esta fonte não precisa atualizar metadados depois."""
        return item

    def recentes(self, args, ctx):
        """`gb x pasta_local recentes [--arg limite=N] [--project P]`: só leitura."""
        try:
            limit = min(max(int(args.get("limite", DEFAULT_RECENT)), 1), MAX_RECENT)
        except ValueError:
            raise PluginError("--arg limite=N espera um número inteiro.") from None
        newest = sorted(self.videos(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        in_project = sum(1 for item in ctx.candidates() if item.get("provider") == self.name)
        return {"arquivos": [p.name for p in newest], "candidatos_no_projeto": in_project}


class CopiaDaPasta:  # pylint: disable=too-few-public-methods  # contrato do SDK: rota é só um `prepare`
    """Rota de prévia: acha o arquivo pelo id da busca e pede ao core uma cópia de trabalho."""

    name = "pasta_local"
    stage = "preview"

    def __init__(self, fonte, api):
        self.fonte = fonte
        self.api = api

    def prepare(self, item, workdir):  # pylint: disable=unused-argument  # noqa: ARG002 - o core cuida do workdir; api.local_file grava nele
        """Copia o arquivo já achado na busca para a pasta de trabalho do core."""
        for path in self.fonte.videos():
            if _ident(path) == item["source_id"]:
                return RouteResult(self.api.local_file(path))
        raise PluginError("O arquivo não está mais na pasta; rode a busca de novo.")


def register(api):
    """Ponto de entrada do SDK: registra o provider `pasta_local`, a rota de prévia e o comando `recentes`."""
    fonte = PastaLocal(api)
    api.provider(fonte)
    api.route(CopiaDaPasta(fonte, api))
    api.command("recentes", fonte.recentes, "Lista os vídeos mais recentes da pasta (--arg limite=N)")

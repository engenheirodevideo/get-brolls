"""Exemplo do SDK do Get B-rolls: fonte que procura vídeos por nome numa pasta local.

Aponte a pasta em PASTA_LOCAL_DIR. O candidato sai só com metadados; o arquivo
entra no fluxo comum com `resolve --file`, como qualquer original local.
"""

import hashlib
from pathlib import Path

from getbrolls.sdk import ProviderCapabilities

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm")


class PastaLocal:
    name = "pasta_local"
    capabilities = ProviderCapabilities(search=True, transport="local", seek="local", download=False)

    def __init__(self, api):
        self.api = api

    def _folder(self):
        raw = self.api.env("PASTA_LOCAL_DIR")
        if not raw or not Path(raw).is_dir():
            raise ValueError("Configure PASTA_LOCAL_DIR com a pasta dos seus B-rolls.")
        return Path(raw)

    def search(self, query, limit, media):  # noqa: ARG002 - assinatura fixa de Provider.search; esta fonte só tem vídeo
        words = [w for w in query.lower().split() if w]
        found = []
        for path in sorted(self._folder().rglob("*")):
            if path.suffix.lower() in VIDEO_SUFFIXES and all(w in path.stem.lower() for w in words):
                ident = hashlib.sha256(path.as_posix().encode()).hexdigest()[:16]
                found.append(self.api.candidate(self.name, ident, path.stem))
            if len(found) >= limit:
                break
        return found

    def resolve(self, url):  # noqa: ARG002 - assinatura fixa de Provider.resolve; esta fonte não reconhece URL
        return None

    def refresh(self, item):
        return item


def register(api):
    api.provider(PastaLocal(api))

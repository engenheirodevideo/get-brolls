"""Exemplo do SDK do Get B-rolls: banco de vídeos com API autenticada.

`search` pede a lista à API com o token de BANCO_HTTP_TOKEN. Baixar o original
consome licença da conta, então a rota `banco_http` tem `stage="fetch"`: o core só
a chama no `fetch`, depois da aprovação humana e do `permit`. A rota registra a
licença que a API devolve (vira evidência extra em `rights.evidence`) e baixa o
arquivo com `api.download`, com o token no header — nunca na URL nem no log.

Orientação para quem usa vai em `PluginError`: é a única exceção cujo texto o core
mostra (`Plugin banco_http: ...`); qualquer outra aparece só pelo tipo.
"""

from getbrolls.sdk import PluginError, ProviderCapabilities, RouteResult

API = "https://api.banco.example/v1"


class BancoHttp:
    """Provider do banco de vídeos: busca via API e monta o cabeçalho autenticado das chamadas."""

    name = "banco_http"
    capabilities = ProviderCapabilities(
        search=True, match_kind="illustrative", env_key="BANCO_HTTP_TOKEN", transport="banco_http", route="banco_http"
    )

    def __init__(self, api):
        self.api = api

    def headers(self):
        """Cabeçalho `Authorization` com o token da conta, ou `PluginError` se ele não estiver configurado."""
        token = self.api.env("BANCO_HTTP_TOKEN")
        if not token:
            raise PluginError("Configure BANCO_HTTP_TOKEN com o token da sua conta no banco.")
        return {"Authorization": f"Bearer {token}"}

    def search(self, query, limit, media):  # pylint: disable=unused-argument  # noqa: ARG002 - assinatura fixa de Provider.search; este banco só tem vídeo
        """Candidatos de vídeo da API para `query`, limitados a `limit`."""
        data = self.api.get_json(f"{API}/search", {"q": query, "limit": limit}, headers=self.headers())
        found = []
        for row in data.get("items", [])[:limit]:
            item = self.api.candidate(self.name, row["id"], row["title"], row.get("page_url"))
            item["media"].update(duration_s=row.get("duration"), kind="video")
            # O que a pessoa vê antes de aprovar: sem miniatura nem player, o core
            # recusa aprovar um candidato de plugin (não há nada para mostrar).
            item["preview"].update(poster_url=row.get("thumb_url"), embed_url=row.get("embed_url"))
            found.append(item)
        return found

    def resolve(self, url):  # pylint: disable=unused-argument  # noqa: ARG002 - assinatura fixa de Provider.resolve; este exemplo não reconhece URL
        """Este banco não reconhece URL colada por fora: sempre `None`."""
        return

    def refresh(self, item):
        """Devolve `item` sem mudança: este banco não precisa atualizar metadados depois."""
        return item


class DownloadLicenciado:  # pylint: disable=too-few-public-methods  # contrato do SDK: rota é só um `prepare`
    """Rota de fetch: registra a licença na API e baixa o original com Authorization."""

    name = "banco_http"
    stage = "fetch"

    def __init__(self, fonte, api):
        self.fonte = fonte
        self.api = api

    def prepare(self, item, workdir):  # pylint: disable=unused-argument  # noqa: ARG002 - o core cuida do workdir; api.download grava nele
        """Baixa o vídeo licenciado da API e devolve o caminho e o texto da licença."""
        ident = item["source_id"]
        license_info = self.api.get_json(f"{API}/videos/{ident}/license", headers=self.fonte.headers())
        path = self.api.download(f"{API}/videos/{ident}/file", "original.mp4", headers=self.fonte.headers())
        text = license_info.get("text")
        return RouteResult(path, license=text if isinstance(text, str) and text.strip() else None)


def register(api):
    """Ponto de entrada do SDK: registra o provider `banco_http` e a rota de fetch licenciado."""
    fonte = BancoHttp(api)
    api.provider(fonte)
    api.route(DownloadLicenciado(fonte, api))

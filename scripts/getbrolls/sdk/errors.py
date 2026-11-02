"""Recusas escritas pelo próprio core do SDK, num módulo folha.

`api`, `registry` e `guard` importam daqui: o `guard` precisa reconhecer estes
tipos (o texto deles é do core e pode chegar à pessoa) sem importar os módulos que
o importam. Os nomes continuam acessíveis em `api.ApiError` e `registry.RegistryError`.
"""


class ApiError(ValueError):
    """Recusa escrita pelo próprio `PluginApi` (uso errado da API): o texto é do core,
    então chega à pessoa mesmo quando o plugin deixa a exceção subir."""


class RegistryError(ValueError):
    """Registro recusado pelo `Registry` (nome inválido, repetido ou fora do contrato)."""

"""Condições genéricas por fonte para `permit --preset`.

Um preset **não é licença**: ele grava o que a fonte costuma dizer e obriga quem
assina a conferir a página do item, porque só ela diz a condição real daquele
vídeo. Por isso todo texto termina em "verifique a página da fonte: <url>" e
`fetch` continua exigindo a aprovação humana separada.
"""

PERMIT_PRESETS = {
    "youtube": {
        "url": "https://www.youtube.com/t/terms",
        "text": (
            "Condições gerais do YouTube: cada vídeo pode estar sob a licença padrão "
            "(todos os direitos reservados) ou Creative Commons CC BY; a licença real "
            "aparece na página do próprio vídeo — verifique a página da fonte: "
            "https://www.youtube.com/t/terms"
        ),
    },
    "nasa": {
        "url": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
        "text": (
            "Condições gerais da NASA: o material costuma ser de domínio público para "
            "uso não comercial, mas há exceções (logotipos, marcas, imagens de pessoas "
            "e material de terceiros) — verifique a página da fonte: "
            "https://www.nasa.gov/nasa-brand-center/images-and-media/"
        ),
    },
    "commons": {
        "url": "https://commons.wikimedia.org/wiki/Commons:Licensing",
        "text": (
            "Condições gerais do Wikimedia Commons: cada arquivo tem sua própria "
            "licença livre (CC BY, CC BY-SA, domínio público) com exigência de crédito "
            "e, às vezes, de compartilhar igual — verifique a página da fonte: "
            "https://commons.wikimedia.org/wiki/Commons:Licensing"
        ),
    },
    "pexels": {
        "url": "https://www.pexels.com/license/",
        "text": (
            "Condições gerais do Pexels: uso gratuito, inclusive comercial, sem crédito "
            "obrigatório, mas proibido revender o arquivo como está ou usar pessoas e "
            "marcas identificáveis de forma ofensiva — verifique a página da fonte: "
            "https://www.pexels.com/license/"
        ),
    },
    "pixabay": {
        "url": "https://pixabay.com/service/license-summary/",
        "text": (
            "Condições gerais do Pixabay: uso gratuito, inclusive comercial, sem crédito "
            "obrigatório, mas proibido redistribuir o arquivo como está ou usar pessoas e "
            "marcas identificáveis de forma ofensiva — verifique a página da fonte: "
            "https://pixabay.com/service/license-summary/"
        ),
    },
}


def register_builtins(registry):
    """Registra cada preset embutido de permit no registro do SDK."""
    for name, row in PERMIT_PRESETS.items():
        registry.add_preset(name, row["url"], row["text"])


def names():
    """Nomes aceitos por `permit --preset`. Lê os manifestos dos plugins habilitados,
    sem executar código deles: a CLI monta o parser antes de qualquer comando."""
    from .sdk import loader

    try:
        extra = loader.declared("presets")
    except (ValueError, OSError):
        extra = []
    return sorted(set(PERMIT_PRESETS) | set(extra))


def get(name):
    """`{"url", "text"}` do preset. Preset de plugin: o texto sai saneado e marcado como
    informado pelo plugin — nunca uma declaração ou outra evidência forjada."""
    from .sdk.contracts import CORE
    from .sdk.registry import get_registry

    registry = get_registry()
    preset = registry.preset(name)
    if preset is None:
        raise ValueError(f"Preset desconhecido: {name}. Use um de: {', '.join(names())}.")
    owner = registry.owner("preset", name)
    if owner in (None, CORE):
        return {"url": preset.url, "text": preset.text}
    from .http import public_url
    from .sdk.guard import PRESET_EVIDENCE_LABEL, plugin_evidence

    return {
        "url": public_url(preset.url, strict=True),
        "text": plugin_evidence(PRESET_EVIDENCE_LABEL, owner, preset.text),
    }

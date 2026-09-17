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

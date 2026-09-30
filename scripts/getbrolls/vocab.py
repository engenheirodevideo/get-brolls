"""Vocabulário compartilhado do get-brolls, num módulo folha.

Formatos, tipos de casamento e de mídia, estados do candidato, da aprovação e dos
direitos, métodos de entrega, estados de análise e cores de marcador moram aqui uma
vez só. Brief, RULES, CLI, SDK e os schemas publicados repetem estes valores, e
`tests/test_vocab.py` confere cada repetição contra as tuplas abaixo. O módulo não
importa nada do get-brolls, para que qualquer outro (inclusive os módulos folha do
roteiro e do SDK) o leia sem abrir ciclo.
"""

# Formato-alvo da entrega (`video.delivery.format` do brief, `video_format` do RULES).
FORMATS = ("native", "reels", "horizontal")
# Como o candidato casa com o beat: `intent` do brief e `match.kind` do candidato.
MATCH_KINDS = ("literal", "illustrative")
MEDIA_KINDS = ("video", "image")
CANDIDATE_STATES = (
    "candidate",
    "preview_ready",
    "awaiting_approval",
    "approved",
    "acquired",
    "verified",
    "rejected",
    "reference_only",
    "blocked",
    "failed",
)
APPROVAL_STATUSES = ("pending", "approved", "rejected")
RIGHTS_STATUSES = ("unknown", "permitted", "restricted")
# `delivery.method` do candidato; o schema também aceita `null` (ainda não entregue).
DELIVERY_METHODS = ("hardlink", "symlink", "copy", "planned")
# Estado de cada componente de análise de mídia (`analysis/media/<id>/<nome>.json`).
ANALYSIS_STATUSES = (
    "done",
    "done_partial",
    "unavailable",
    "failed",
    "blocked",
    "not_run_by_this_script",
    "no_speech",
)
# Estados de análise que exigem `reason` preenchido.
ANALYSIS_STATUSES_WITH_REASON = ("unavailable", "failed", "blocked", "not_run_by_this_script")
# Cores de marcador: o enum `MarkerColor` do OpenTimelineIO, mesmos nomes.
MARKER_COLORS = (
    "PINK",
    "RED",
    "ORANGE",
    "YELLOW",
    "GREEN",
    "CYAN",
    "BLUE",
    "PURPLE",
    "MAGENTA",
    "BLACK",
    "WHITE",
)

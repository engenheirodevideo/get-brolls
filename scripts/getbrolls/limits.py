"""Limites de duração compartilhados entre `brief` e `roteiro`.

Módulo folha: não importa nada do pacote. Existe para que `roteiro` deixe de
importar `brief` (e assim feche o ciclo `brief <-> roteiro`, já que `brief`
importa `roteiro` tardiamente para `is_roteiro`). `brief.py` reexporta as
duas constantes para manter o caminho de import de sempre.
"""

MIN_HINT_S = 0.5
MAX_HINT_S = 120

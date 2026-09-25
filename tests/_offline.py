"""Ambiente para `resolve --url` de YouTube sem rede nos testes.

`resolve` pede metadados ao yt-dlp; aqui o "yt-dlp" é o próprio Python (existe e é
executável em qualquer SO), que recusa as flags e sai com erro na hora: os
metadados viram `{}` — o mesmo caminho de uma página sem metadados — e nada sai
da máquina. `GB_YTDLP_SLEEP=0,0,0` tira a pausa entre pedidos.
"""

import sys

OFFLINE_YTDLP = {"GB_YTDLP_PATH": sys.executable, "GB_YTDLP_SLEEP": "0,0,0"}

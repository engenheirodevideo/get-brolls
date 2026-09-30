"""Exceções do core que a CLI traduz em código de saída."""


class UsageError(ValueError):
    """Invocação ou configuração errada (exit 2)."""


class PrerequisiteError(ValueError):
    """Falta algo da instalação: dados, ffmpeg, yt-dlp... (exit 4)."""


class DataRootError(PrerequisiteError):
    """Arquivos de dados do pacote ausentes."""


class LockedError(ValueError):
    """Outro processo segura a trava (projeto ou runtime); repita quando ele terminar (exit 1)."""

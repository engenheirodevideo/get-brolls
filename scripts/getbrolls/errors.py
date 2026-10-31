"""Exceções do core que a CLI traduz em código de saída."""


class UsageError(ValueError):
    """Invocação ou configuração errada (exit 2).

    `reason`: motivo estável e curto (`env_missing`, `untrusted`…) para o log, quando quem
    levanta o dá; o `command_refused` do `.env` e do perfil o registra.
    """

    reason: str | None = None

    def __init__(self, *args: object, reason: str | None = None):
        super().__init__(*args)
        if reason is not None:
            self.reason = reason


class PrerequisiteError(ValueError):
    """Falta algo da instalação: dados, ffmpeg, yt-dlp... (exit 4)."""


class DataRootError(PrerequisiteError):
    """Arquivos de dados do pacote ausentes."""


class LockedError(ValueError):
    """Outro processo segura a trava (projeto ou runtime); repita quando ele terminar (exit 1)."""


class EnvReadError(OSError):
    """O `.env` escolhido existe mas não abre (sem permissão de leitura): E/S da pessoa (exit 1).

    `shown` é o arquivo como a mensagem o nomeia: o `--env-file`/`GB_ENV_FILE` como digitado, ou a marca.

    Separado do `OSError` comum para que a recusa antes do comando diga que o problema é o
    `.env` (`reason=env_unreadable`) só quando é ele, e não uma pasta no caminho.
    """

    shown: str | None = None


def _rebuild_refusal(cls, message, reason_code):
    """Refaz um `Refusal` (ou subclasse) para `pickle`, `copy` e `deepcopy`: o `reason_code` é só de palavra-chave."""
    return cls(message, reason_code=reason_code)


class Refusal(ValueError):  # noqa: N818 - o nome vem do contrato (`Refusal`), não de `Error`
    """Recusa de dados com um motivo estável para quem automatiza (exit 1, `INVALID_DATA`).

    `reason_code` é um nome em MAIÚSCULAS da tabela "Motivos de recusa" do docs/CLI_CONTRACT.md, passado
    sempre como argumento nomeado e literal. É enum aberto: quem lê trata um motivo novo como uma recusa
    qualquer. O JSON do erro de operação o leva ao lado do `error_code`; a mensagem segue sendo a frase.
    """

    reason_code: str

    def __init__(self, message: str, *, reason_code: str):
        super().__init__(message)
        self.reason_code = reason_code

    def __reduce__(self):
        return (_rebuild_refusal, (type(self), self.args[0], self.reason_code))


class ProviderError(ValueError):
    """Falha de um provedor, da rede ou de uma fonte (`http`, `providers`, plugins): o comando diz o que houve."""

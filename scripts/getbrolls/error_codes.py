"""The `error_code` of an exception and the exit code the CLI gives it: the one place that decides both."""

from .errors import LockedError, PrerequisiteError, UsageError

# `error_code` → código de saída da CLI; qualquer outro código (INVALID_DATA,
# IO_ERROR, ...) é erro de operação ou de dados: 1. A tabela completa, com o 0, fica
# em `cli.EXIT_CODES`; este é o único lugar que decide a saída de um erro. `LOCKED`
# (outro processo segura a trava do projeto ou do runtime) sai 1, como erro de operação,
# mas com código próprio para quem automatiza saber que basta repetir depois.
ERROR_EXIT = {"USAGE_ERROR": 2, "INTERNAL_ERROR": 3, "PREREQUISITE_MISSING": 4, "LOCKED": 1, "INTERRUPTED": 130}
EXIT_FOR_OTHER_ERRORS = 1


def exit_code_for(error_code):
    """Código de saída de um `error_code` do JSON de erro."""
    return ERROR_EXIT.get(error_code, EXIT_FOR_OTHER_ERRORS)


def error_code_for(exc):
    """`error_code` de uma exceção que a pessoa pode corrigir (não um bug).

    `ValueError` genérico depois do parse continua erro de dados (1), mesmo quando é,
    na prática, uso errado: só `UsageError` explícito vira `USAGE_ERROR`.
    """
    if isinstance(exc, UsageError):
        return "USAGE_ERROR"
    if isinstance(exc, PrerequisiteError):
        return "PREREQUISITE_MISSING"
    if isinstance(exc, LockedError):
        return "LOCKED"
    return "IO_ERROR" if isinstance(exc, OSError) else "INVALID_DATA"

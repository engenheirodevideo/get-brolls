"""`doctor`: o relatório completo (executáveis, engine social, plugins, venv e instalação) e o despacho de
`providers`/`doctor`. A sondagem e o veredito vêm de `readiness`."""

import sys

from . import __version__, effects, install_report, labels, logs, venv_check
from .readiness import (
    INSTALLER,
    doctor_contact_sheet,
    doctor_plugin_problems,
    doctor_resolved,
    doctor_summary,
    readiness_probe,
)


def _doctor_plugin_inventory(result, summary):
    """Sobrepõe status/reason reais do registro no inventário de plugins do `doctor`."""
    from getbrolls.sdk import loader as sdk_loader
    from getbrolls.sdk import requirements
    from getbrolls.sdk.marketplace_install import plugin_origins
    from getbrolls.sdk.registry import get_registry

    try:
        origins = plugin_origins()
        installed = [
            {**row, "requires": requirements.report(manifest)} if manifest is not None else row
            for row, _folder, manifest in sdk_loader.entries()
        ]
    except (ValueError, OSError) as exc:
        # Conteúdo inválido traz a dica de corrigir; arquivo que não dá para ler (pasta, sem permissão)
        # sai como E/S, sem mandar apagar. O relatório continua nos dois casos.
        from getbrolls.sdk.marketplace import portable_text

        result["plugins_error"] = portable_text(labels.os_message(exc) if isinstance(exc, OSError) else exc)
        return
    installed = [{**row, "origin": origins.get(row["id"])} for row in installed]
    if not installed:
        return
    # `loader.inventory()` só lê manifesto e pin (pré-carga: nunca roda
    # código). `get_registry().plugins` reflete o carregamento de verdade
    # (já rodou, porque `providers.capabilities()` acima monta o registro
    # antes) — sobrepomos status/reason por id, sem perder nenhuma pasta
    # que o inventário viu.
    loaded = get_registry().plugins
    result["plugins"] = [
        {**row, "status": loaded[row["id"]]["status"], "reason": loaded[row["id"]]["reason"]}
        if row["id"] in loaded
        else row
        for row in installed
    ]
    problems = doctor_plugin_problems(result["plugins"])
    if problems:
        summary["plugins"] = problems
    # Só informa: requisito faltando não muda `ready` (o plugin carrega mesmo assim).
    lacking = requirements.missing_summary(result["plugins"])
    if lacking:
        summary["plugins_requires"] = lacking


def _doctor_marketplaces(result):
    """Marketplaces fixados (nome, commit, plugins, permitido), só quando há algum; sem rede."""
    from getbrolls.sdk import marketplace

    try:
        rows = marketplace.summary()
    except (ValueError, OSError) as exc:
        # `marketplaces.json` que não dá para ler (pasta, FIFO, sem permissão) sai como E/S, sem mandar apagar.
        result["marketplaces_error"] = marketplace.portable_text(
            labels.os_message(exc) if isinstance(exc, OSError) else exc
        )
        return
    if rows:
        result["marketplaces"] = rows


def _warn_old_env_names() -> None:
    """Um `OLD_SHAPE` com os `GETBROLLS_X` do ambiente que a 2.6 não lê mais (só o `doctor` avisa)."""
    old_names = install_report.old_env_names()
    if old_names:
        from getbrolls import renamed

        renamed.old_shape(
            "o conjunto de variáveis " + ", ".join(old_names) + " do ambiente",
            "Troque cada GETBROLLS_X por GB_X (só GETBROLLS_CACHE_DIR, da 2.5, continua valendo).",
        )


def _doctor_proxy(install):
    """`install.proxy` só quando há variável de proxy no ambiente (o `doctor` sem proxy é o de antes)."""
    from getbrolls import http

    install.update(http.proxy_install_entry())


def _doctor_venv(result):
    """A venv em uso contra os pinos, no `summary` e no `ready` do `doctor`: código novo sobre venv antiga não
    passa por "ok". O bloco `venv` só entra com uma venv em uso (não com o yt-dlp fixado por GB_YTDLP_PATH)."""
    summary = result["summary"]
    venv = venv_check.venv_report()
    missing = venv_check.missing_entries(venv, INSTALLER)
    summary["missing"] += missing
    result["ready"] = not summary["missing"]
    if venv["status"] == "ok":
        summary["ok"] = sorted([*summary["ok"], "venv"])
    if missing:
        summary["do"] = missing[0]["fix"]
    if venv["path"] is not None:
        result["venv"] = venv


def _doctor_report(config, providers_result, live, env_flag=None):
    """Monta o relatório completo de `doctor`: executáveis, engine social, plugins e instalação."""
    from getbrolls import profile

    overrides, pin_problems, social, executables = readiness_probe()
    install = install_report.install_report(env_flag)
    _warn_old_env_names()
    # Onde o log do app da instalação grava (o arquivo só nasce no primeiro comando que muda a instalação).
    install["app_log"] = str(logs.home_log_path())
    # A chave dos hashes do `event=op` (`$GB_HOME/.log-salt`): `missing` até a primeira linha que a usa.
    install["log_salt"] = effects.salt_state()
    # Só com variável de proxy no ambiente: o transporte interno a ignora, `curl`/`yt-dlp`/navegador não.
    _doctor_proxy(install)
    install["profile"] = profile.report()
    # A pasta pessoal que valeu e de onde veio (`env`, `env_file`, `profile` ou `default`).
    install["gb_home_source"] = install["profile"]["values"]["home"]["source"]
    summary = doctor_summary(
        executables, pin_problems, install.get("data_missing") or (), install.get("data_unreadable") or ()
    )
    if social.get("problem"):
        for entry in summary["missing"]:
            if entry["item"] == "yt-dlp":
                entry["note"] = social["problem"]
    summary["missing"] += profile.doctor_problems(profile.current())
    sheet = doctor_contact_sheet(executables.get("ffmpeg"))
    summary["optional"] += sheet["optional"]
    result = {
        # Veredito primeiro: o JSON continua completo logo abaixo dele. `ready` decide a
        # saída (4 quando falta algo obrigatório; ver `cli.result_exit`).
        "summary": summary,
        "ready": not summary["missing"],
        "contact_sheet": sheet["status"],
        "get_brolls": __version__,
        "preview": config,
        "python": sys.version.split()[0],
        "tool_paths": overrides,
        "executables": executables,
        "resolved": doctor_resolved(overrides),
        "providers": providers_result,
        "social": social,
        "install": install,
    }
    _doctor_venv(result)
    _doctor_plugin_inventory(result, summary)
    _doctor_marketplaces(result)
    if live:
        from getbrolls.health import live_checks

        result["live"] = live_checks()
    return result


def _execute_providers_or_doctor(args, config):
    """`providers`/`doctor`: capacidades das fontes e, para `doctor`, o diagnóstico completo."""
    from getbrolls import providers

    result = providers.capabilities()
    if args.command != "doctor":
        return result
    return _doctor_report(config, result, args.live, getattr(args, "env_file", None))

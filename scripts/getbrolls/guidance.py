"""Próximo passo humano: um degrau da escada vira comando pronto, sem adivinhação.

`status`, `brief` (e, adiante, `deliver`) leem a mesma escada, então a pessoa ouve a
mesma frase em qualquer comando. Nada aqui grava: a função recebe o estado já lido e
devolve texto e comando. Quando só o humano tem o valor (nome de quem aprova, frase
dita, evidência real), o comando traz o lugar em MAIÚSCULAS para ele preencher.
"""

import shlex
from pathlib import Path

from .serve import DEFAULT_PORT

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "gb.py"

# Endereço do Storyboard quando servido por `serve`; só entra na resposta se a
# página existir no projeto — senão a pessoa abriria um endereço morto.
BOARD_URL = f"http://127.0.0.1:{DEFAULT_PORT}/review.html"

# Degraus com comando próprio, do topo da escada para a base.
STEPS = (
    "init-brief",
    "brief-invalid",
    "format",
    "search",
    "preview",
    "approve",
    "permit",
    "fetch",
    "verify",
)

# Um comando por degrau; `{project}` e `{candidate}` entram já citados.
TEMPLATES = {
    "init-brief": "init-brief --project {project}",
    "brief-invalid": "brief --validate --project {project}",
    "format": "review --project {project}",
    "search": "search --project {project} --query TERMOS_DA_BUSCA --intent literal",
    "preview": (
        "preview --project {project} --candidate {candidate} --start 0 --end 5"
    ),
    "approve": (
        "approve --project {project} --all --by NOME --channel chat "
        '--statement "FRASE EXATA DITA POR ELE"'
    ),
    "permit": "permit --project {project} --candidate {candidate} --evidence EVIDENCIA_REAL",
    "fetch": "fetch --project {project} --candidate {candidate}",
    "verify": "verify --project {project}",
}


def command_for(step, project, candidate=None):
    """Comando absoluto deste degrau: CLI da skill e o mesmo `--project` que o chamador usa.

    O caminho vai como veio (já absoluto em `status`), sem `resolve()`: assim
    `do.command` e `payload["project"]` combinam mesmo com link simbólico no meio.
    """
    template = TEMPLATES.get(step)
    if template is None:
        return None
    return f'python3 "{CLI}" ' + template.format(
        project=shlex.quote(str(project)),
        candidate=shlex.quote(candidate) if candidate else "ID",
    )


def _counts(state):
    keys = ("candidates", "previews", "approved", "permitted", "delivered", "verified")
    return {key: (state.get("counts") or {}).get(key, 0) for key in keys}


def _action(step, why, for_human, state, url=None, blocking_human=False, command=None):
    return {
        "step": step,
        "why": why,
        "command": command if command is not None else command_for(
            step, state["project"], state.get("candidate")
        ),
        "url": url,
        "for_human": for_human,
        "blocking_human": blocking_human,
    }


def next_action(state):
    """Único passo que faz sentido agora, com a frase para repassar sem parafrasear.

    `state` = {project, counts, format_pending, brief, review_page, rights_mode,
    candidate}. `brief` é None quando o arquivo nem existe, `{"error": "..."}` quando
    existe mas não passa na validação, e {beats, covered, missing[{id, search}],
    conflicts[]} quando está válido.
    """
    counts = _counts(state)
    brief = state.get("brief")
    conflicts = list((brief or {}).get("conflicts") or [])
    if brief is None:
        return _action(
            "init-brief",
            "O projeto ainda não tem BRIEF.md, então nada define o que buscar.",
            "Antes de buscar qualquer coisa, precisamos do plano do vídeo: rode "
            "`/get-brolls-brief` para eu te fazer as perguntas, ou `init-brief` para "
            "criar o modelo e preencher à mão.",
            state,
        )
    if brief.get("error"):
        # Arquivo existe e está errado: corrigir é diferente de começar do zero.
        return _action(
            "brief-invalid",
            f'O BRIEF.md existe mas não passou na validação: {brief["error"]}',
            "O BRIEF.md do projeto tem um problema que preciso que você resolva antes "
            "de eu buscar: vou rodar a validação e te dizer exatamente qual linha "
            "corrigir.",
            state,
        )
    # `review` só faz sentido quando existe algo decidido para revisar de novo; num
    # projeto vazio o conflito de formato vira um aviso colado no degrau de busca.
    if state.get("format_pending") or (conflicts and counts["approved"]):
        detail = (
            f" Além disso: {conflicts[0]}" if conflicts else ""
        )
        return _action(
            "format",
            "As regras editoriais (ou o brief) mudaram o formato-alvo dos itens já decididos.",
            "O formato-alvo mudou, então as aprovações antigas não valem mais para o "
            "corte novo: gere a prévia e o Storyboard de novo e me diga a decisão antes "
            "de coletar." + detail,
            state,
            blocking_human=True,
        )
    missing = list(brief.get("missing") or [])
    if missing:
        first = missing[0]
        return _action(
            "brief-search",
            f'O beat "{first["id"]}" do brief ainda não tem candidato registrado.',
            f'O beat "{first["id"]}" ainda está sem material: vou buscar por ele agora '
            "e te mostrar as opções.",
            state,
            command=first.get("search") or command_for("search", state["project"]),
        )
    if not counts["candidates"]:
        warning = f" Antes disso, resolva: {conflicts[0]}" if conflicts else ""
        return _action(
            "search",
            "Nenhum candidato registrado no projeto ainda."
            + (f" Conflito pendente: {conflicts[0]}" if conflicts else ""),
            "Ainda não há nenhum candidato no projeto: vou buscar as fontes e te "
            "mostrar o que apareceu." + warning,
            state,
        )
    if counts["previews"] < counts["candidates"]:
        return _action(
            "preview",
            "Há candidatos sem prévia gerada; sem prévia ninguém decide. O intervalo "
            "do comando é só um ponto de partida: o real sai do que se vê na fonte "
            "(contact sheet) e não de um palpite.",
            "Vou gerar a prévia dos candidatos que ainda não têm quadro, para você ver "
            "antes de decidir. O `--start`/`--end` do comando é um chute inicial: "
            "confirme o trecho certo pelo contact sheet da fonte antes de aprovar.",
            state,
        )
    if not counts["approved"]:
        return _action(
            "approve",
            "Nenhum item aprovado: falta a decisão explícita de uma pessoa.",
            "Agora é com você: abra o Storyboard e salve as decisões, ou me diga aqui "
            "no chat quem aprova e a frase exata da aprovação. Sem isso eu não coleto "
            "nada.",
            state,
            url=BOARD_URL if state.get("review_page") else None,
            blocking_human=True,
        )
    if counts["permitted"] < counts["approved"]:
        per_item = state.get("rights_mode", "per_item_evidence") == "per_item_evidence"
        return _action(
            "permit",
            "Itens aprovados ainda sem as condições reais de uso registradas.",
            (
                "Falta registrar de onde vem o direito de usar cada trecho aprovado: me "
                "diga a condição real da fonte (licença, autorização, contato) que eu "
                "gravo."
                if per_item
                else "Vou registrar as condições de uso dos aprovados com a declaração "
                "que já está no RULES.md."
            ),
            state,
            blocking_human=per_item,
        )
    if counts["delivered"] < counts["permitted"]:
        return _action(
            "fetch",
            "Itens aprovados e permitidos ainda sem o corte final em clips/.",
            "Está tudo decidido e permitido: vou coletar os cortes finais agora.",
            state,
        )
    if counts["verified"] < counts["delivered"]:
        return _action(
            "verify",
            "Arquivos coletados ainda sem conferência de integridade.",
            "Coletei os cortes; vou conferir se todos os arquivos abrem e estão "
            "íntegros.",
            state,
        )
    return _action(
        "done",
        "Todo item aprovado está coletado e verificado.",
        "Terminamos: todos os trechos aprovados estão coletados, permitidos e "
        "conferidos.",
        state,
        command=None,
    )

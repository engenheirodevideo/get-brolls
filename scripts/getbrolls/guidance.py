"""Próximo passo humano: um degrau da escada vira comando pronto, sem adivinhação.

`status`, `brief` e `deliver` leem a mesma escada, então a pessoa ouve a
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
    "inspect",
    "preview",
    "approve",
    "serve-board",
    "import-review",
    "permit",
    "fetch",
    "verify",
    "deliver",
)

# Um comando por degrau; `{project}` e `{candidate}` entram já citados.
TEMPLATES = {
    "init-brief": "init-brief --project {project}",
    "brief-invalid": "brief --validate --project {project}",
    "format": "review --project {project}",
    "search": "search --project {project} --query TERMOS_DA_BUSCA --intent literal",
    "inspect": ("inspect --project {project} --candidate {candidate} --query NARRACAO_OU_ALVO"),
    "preview": ("preview --project {project} --candidate {candidate} --start 0 --end 5"),
    "approve": ('approve --project {project} --all --by NOME --channel chat --statement "FRASE EXATA DITA POR ELE"'),
    # Rota do board: sobe o servidor sozinho e devolve a URL; a importação depois
    # não precisa de caminho de arquivo, porque a página grava dentro do projeto.
    "serve-board": "serve --project {project} --background",
    "import-review": "import-review --project {project} --by NOME",
    "permit": "permit --project {project} --candidate {candidate} --evidence EVIDENCIA_REAL",
    "fetch": "fetch --project {project} --candidate {candidate}",
    "verify": "verify --project {project}",
    "deliver": "deliver --project {project}",
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
    keys = (
        "candidates",
        "previews",
        "pending",
        "rejected",
        "approved",
        "permitted",
        "delivered",
        "verified",
    )
    return {key: (state.get("counts") or {}).get(key, 0) for key in keys}


def flow_complete(state, counts):
    """Todo item aprovado virou arquivo conferido e já está em `entrega/`?

    É o estado que a pessoa chama de "acabou". Candidato que ninguém decidiu não
    conta: ele é rascunho do agente, não pendência do humano.
    """
    approved = counts["approved"]
    if not approved:
        return False
    return (
        counts["permitted"] >= approved
        and counts["delivered"] >= approved
        and counts["verified"] >= counts["delivered"]
        and not state.get("undelivered")
    )


def leftovers(state, counts):
    """Candidatos que ninguém aprovou nem rejeitou: viram aparte, nunca próximo passo."""
    value = state.get("undecided")
    if value is None:
        value = counts["candidates"] - counts["approved"] - counts["rejected"]
    return max(0, int(value))


def leftover_aside(count):
    """Frase do aparte: quem sobrou não vira tarefa, só nota de rodapé."""
    if not count:
        return ""
    noun = "candidato" if count == 1 else "candidatos"
    verb = "Sobrou" if count == 1 else "Sobraram"
    return (
        f" {verb} {count} {noun} sem decisão; se não vai usá-los, rejeite com "
        "`reject` — ou ignore, que eles não travam nada."
    )


def _pending_preview(state, counts):
    """Quantos itens ainda em jogo estão sem quadro; o rejeitado não conta."""
    value = state.get("pending_preview")
    if value is None:
        return counts["candidates"] - counts["previews"]
    return int(value)


def _action(step, why, for_human, state, url=None, blocking_human=False, command=None):
    return {
        "step": step,
        "why": why,
        "command": command if command is not None else command_for(step, state["project"], state.get("candidate")),
        "url": url,
        "for_human": for_human,
        "blocking_human": blocking_human,
    }


def _library_hint():
    """Lembrete da biblioteca só quando ela existe de fato; nunca altera o comando."""
    from . import library

    try:
        if library.enabled() and library.index_path().exists():
            return " Vou conferir na biblioteca (`library --search`) o que já funcionou em outros vídeos."
    except OSError:
        pass
    return ""


def _missing_beat_phrase(beat):
    """O que dizer sobre um beat sem candidato — sem prometer o que a guarda proíbe.

    Beat literal tem alvo nomeado: buscar é o passo certo. Beat ilustrativo não tem
    fonte literal nenhuma, e oferecer "vou buscar material" ali empurra para o
    preenchimento — a skill inteira existe para não fazer isso. Nesse caso o passo
    real é humano: falta o fato (entidade, data, link) ou o material da própria pessoa.
    """
    name = beat["id"]
    if (beat.get("intent") or "literal") == "literal":
        return f'O beat "{name}" ainda está sem material: vou buscar por ele agora e te mostrar as opções.'
    return (
        f'O beat "{name}" não nomeia nada que eu possa procurar numa fonte real, e eu '
        "não vou pegar imagem aproximada para tapar buraco. Me diga o que falta — a "
        "empresa, a data, o link, a pessoa — ou me mande seu próprio material para "
        "esse trecho. Se não existir nada disso, este beat fica registrado como sem fonte."
    )


def _approve_action(state, pending=0):
    """Degrau da decisão humana: board quando a página existe, chat sempre."""
    board = bool(state.get("review_page"))
    return _action(
        "approve",
        (
            f"Há {pending} item(ns) com prévia esperando a decisão de uma pessoa."
            if pending
            else "Nenhum item aprovado: falta a decisão explícita de uma pessoa."
        ),
        (
            "Agora é com você: vou subir o Storyboard e te mandar o endereço "
            f"({BOARD_URL}). Decida lá e clique em “Salvar decisões” — elas ficam "
            "dentro do projeto, é só voltar aqui e dizer “salvei” que eu rodo "
            "`import-review --by SEU NOME`. Se preferir resolver pelo chat, me diga "
            "quem aprova e a frase exata. Sem isso eu não coleto nada."
            if board
            else "Agora é com você: abra o Storyboard e salve as decisões, ou me "
            "diga aqui no chat quem aprova e a frase exata da aprovação. Sem isso "
            "eu não coleto nada."
        ),
        state,
        url=BOARD_URL if board else None,
        blocking_human=True,
        command=command_for("serve-board", state["project"]) if board else None,
    )


def _done_action(state, counts):
    """Fim de fluxo: o que sobrou sem decisão entra como aparte, nunca como tarefa."""
    aside = leftover_aside(leftovers(state, counts))
    return _action(
        "done",
        "Todo item aprovado está coletado, permitido e verificado." + aside,
        "Fluxo completo: todos os trechos aprovados estão coletados, permitidos, "
        "conferidos e organizados em `entrega/`." + aside,
        state,
        command=None,
    )


def next_action(state):
    """Único passo que faz sentido agora, com a frase para repassar sem parafrasear.

    `state` = {project, counts, format_pending, brief, review_page, rights_mode,
    candidate, duration_unknown, inspect_candidate}. `brief` é None quando o arquivo nem existe, `{"error": "..."}` quando
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
            f"O BRIEF.md existe mas não passou na validação: {brief['error']}",
            "O BRIEF.md do projeto tem um problema que preciso que você resolva antes "
            "de eu buscar: vou rodar a validação e te dizer exatamente qual linha "
            "corrigir.",
            state,
        )
    # `review` só faz sentido quando existe algo decidido para revisar de novo; num
    # projeto vazio o conflito de formato vira um aviso colado no degrau de busca.
    if state.get("format_pending") or (conflicts and counts["approved"]):
        detail = f" Além disso: {conflicts[0]}" if conflicts else ""
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
            _missing_beat_phrase(first),
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
            "mostrar o que apareceu." + _library_hint() + warning,
            state,
        )
    if flow_complete(state, counts):
        # O estado humano vem antes do rascunho do agente: com a entrega pronta e
        # conferida, mandar inspecionar um candidato descartado diria à pessoa que o
        # vídeo dela não acabou quando acabou.
        return _done_action(state, counts)
    # Decisão humana pendente ganha de qualquer degrau do agente: há prévia na mesa
    # esperando alguém decidir, e gerar mais prévia empurra a pessoa para longe disso.
    if counts["pending"] > 0:
        return _approve_action(state, counts["pending"])
    if _pending_preview(state, counts) > 0:
        if state.get("duration_unknown"):
            # Sem saber a duração, qualquer intervalo é chute — e baixar trecho errado
            # custa pedido à fonte. `inspect` responde isso de graça, antes da prévia.
            return _action(
                "inspect",
                "Há candidato sem duração conhecida: analisar a fonte (capítulos, "
                "legendas, tempos da descrição) diz onde olhar antes de pedir mídia.",
                "Antes de gerar prévia, vou analisar a fonte para saber a duração e em "
                "que minuto está o que você pediu — assim o trecho não sai de palpite.",
                state,
                command=command_for(
                    "inspect",
                    state["project"],
                    state.get("inspect_candidate") or state.get("candidate"),
                ),
            )
        return _action(
            "preview",
            "Há candidatos sem prévia gerada; sem prévia ninguém decide. O intervalo "
            "do comando só vale depois de `inspect`: o real sai do que a fonte diz "
            "(capítulos/legendas) e do que se vê no contact sheet, nunca de um palpite.",
            "Vou gerar a prévia dos candidatos que ainda não têm quadro, para você ver "
            "antes de decidir. Rode `inspect` antes se ainda não souber onde está o "
            "trecho: o `--start`/`--end` daqui é só um ponto de partida, confirmado no "
            "contact sheet da fonte antes de aprovar.",
            state,
        )
    if not counts["approved"]:
        return _approve_action(state)
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
                else "Vou registrar as condições de uso dos aprovados com a declaração que já está no RULES.md."
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
            "Coletei os cortes; vou conferir se todos os arquivos abrem e estão íntegros.",
            state,
        )
    if state.get("undelivered"):
        # `brolls/` guarda por hash; quem abre a pasta precisa de nome de gente.
        return _action(
            "deliver",
            "Há arquivo conferido que ainda não aparece em entrega/, a pasta que a pessoa abre.",
            "Vou organizar os trechos conferidos em `entrega/`, uma pasta por beat, com "
            "o contact sheet e a origem de cada um do lado — é essa pasta que você "
            "arrasta para o editor.",
            state,
        )
    return _done_action(state, counts)

"""Ouro da 2.5: a matriz de comandos da 2.5 em modo máquina, gravada e conferida byte a byte.

Cada caso roda a CLI num subprocesso (stdout e stderr em pipe, nunca num terminal) com um
ambiente fechado: `GB_HOME`, cache, runtime, `HOME` e `TMPDIR` dentro de uma pasta temporária,
`PATH` sem ferramentas, ffmpeg e ffprobe só pelos pins `GB_FFMPEG_PATH`/`GB_FFPROBE_PATH`, o
próprio Python no lugar do yt-dlp (recusa as flags: nada sai da máquina) e nenhuma variável de
quem roda. O registro de um caso guarda argv, código de saída, stdout, stderr, os arquivos que
ele criou, mudou ou apagou e as linhas que ele acrescentou aos logs e aos diários `.jsonl`.

A normalização troca só o que muda entre rodadas ou máquinas: pastas, instantes, a data do dia
da rodada, uuids, a época da revisão, milissegundos, o sha de mídia que o ffmpeg grava, versões
do Python e do getbrolls, o que o build do ffmpeg sabe fazer e a cauda do stderr de ferramenta.
O resto vale byte a byte.
Nada aqui importa `getbrolls`: o mesmo módulo grava o checkout em teste e a árvore da v2.5.0.

    python tests/_golden.py record --cli <checkout>/scripts/gb.py --out <pasta>
    python tests/_golden.py freeze-layout0 --cli <árvore da v2.5.0>/scripts/gb.py
"""

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "golden_v25"
EXPECTED = FIXTURES / "expected"
V250 = FIXTURES / "v250"
LAYOUT0 = FIXTURES / "layout0"
MEDIA = FIXTURES / "media" / "original.mp4"
ACCEPTED = FIXTURES / "accepted.json"
REWRITES = FIXTURES / "REGRAVACOES.md"

TEXT_SUFFIXES = {".json", ".md", ".html", ".txt", ".csv", ".lock"}
APPEND_ONLY = {"diagnostics.jsonl", "events.jsonl", "getbrolls.log"}
# O diário `events.jsonl` é estado do projeto e vai junto; log, diagnóstico e a página do
# Storyboard (refeita pelo `review`, e com o logo em base64) não.
NOT_FROZEN = {"getbrolls.log", "diagnostics.jsonl", ".command.lock", "review.html"}
# A trava vazia da rotação do log de um projeto (`work/**/brolls/.getbrolls.log.lock`) só nasce onde
# não há `flock` (Windows): o ouro é um só para os três sistemas, então ela não entra na foto. Qualquer
# outro arquivo com esse nome (o do `$GB_HOME/logs/`, por exemplo) entra como sempre.
PLATFORM_ONLY_NAME = ".getbrolls.log.lock"
_PROJECT_LOCK_DEPTH = 4  # work/<projeto>/brolls/.getbrolls.log.lock


def platform_only(rel):
    """`rel` (relativo à pasta da rodada) é a trava de rotação de um log de projeto?"""
    parts = Path(rel).parts
    return len(parts) >= _PROJECT_LOCK_DEPTH and parts[0] == "work" and parts[-2:] == ("brolls", PLATFORM_ONLY_NAME)


INLINE_TEXT_MAX = 64 * 1024
# HTML entra só por sha: a página do Storyboard traz o logo em base64, que não é texto de ninguém.
NEVER_INLINE = {".html"}
SNAPSHOT_ROOTS = ("work", "gbhome", "userhome")
CASE_TIMEOUT_S = 60
# Fuso do ambiente fechado: a data que a CLI grava no frontmatter (`date.today()`) é a deste fuso.
HERMETIC_TZ = "UTC"
APPROVER = "Fixture humano"
STATEMENT = "Aprovo este trecho para o vídeo."

_TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:\+00:00|Z)?")
_STAMP = re.compile(r"\d{8}T\d{12}Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SESSION = re.compile(r"gb-([0-9a-f]{8})\b")
_LOG_STAMP = re.compile(r"^\S+ (?=level=)", re.MULTILINE)
_MS = re.compile(r"\b((?:waited_|duration_)?ms=)\d+")
_DURATION_MS = re.compile(r'("duration_ms":\s*)\d+')
# O interpretador que faz as vezes do yt-dlp no `event=op` (`python3.11`, `python3.14`, `python`) e a
# chave de cache que sai do caminho e da data do ffmpeg de cada máquina.
_TARGET_PYTHON = re.compile(r"(\btarget=)python(?:3(?:\.\d+)?)?(?=\s|$)")
# No Windows o pino do ffmpeg é `ffmpeg.cmd` (`ffmpeg_pin`); o log guarda o nome do arquivo, e só `.exe` sai dele.
_TARGET_PIN_CMD = re.compile(r"(\btarget=)ffmpeg\.cmd(?=\s|$)")
_CACHE_KEY = re.compile(r"(\btarget=\$GB_CACHE_DIR/)#[0-9a-f]{8}")
# O `browser-plan` fixa o pacote do npx na versão do `package.json` (`@playwright/cli@0.1.21`); a v2.5.0 emitia o
# nome sem versão. A versão muda a cada bump do pacote e não é comportamento do plano: o ouro guarda o nome.
_PLAYWRIGHT_PIN = re.compile(r'("@playwright/cli)@\d+\.\d+\.\d+(?=")')
_PY_VERSION = re.compile(r"(Python |\"python\":\s*\")\d+\.\d+\.\d+")
_STDERR_TAIL = re.compile(r"(; stderr: )(?:[^\"\\]|\\.)*")
# A época da revisão é o sha de quem aprovou e quando: muda a cada rodada, como o instante.
_EPOCH = re.compile(r'(reviewEpoch\\?"\s*:\s*\\?")([0-9a-f]{64})')
_TRACEBACK = re.compile(r'("traceback":\s*)"(?:[^"\\]|\\.)*"')
# Separador (`/`, `\` ou `\\` escapado em JSON) + pedaço sem barra: o `\"` que fecha a string fica.
_TAIL = re.compile(r"(<(?:T|CODE)>)((?:(?:\\\\|\\|/)[^\"\s'\\/]+)+)")
# O que o build do ffmpeg sabe fazer e o tamanho do que ele grava mudam entre máquinas.
_MACHINE_KEYS = re.compile(r'("(?:gif_bytes|sheet_labels|drawtext|labels|font)":\s*)(?:"(?:[^"\\]|\\.)*"|[^,\s}\]]+)')
# O tamanho e o sha do clipe que o ffmpeg cortou dependem do build do ffmpeg (metadados do contêiner), não do
# sistema: só na linha `event=fetch`, e só na comparação (os arquivos do ouro seguem como foram gravados).
# O formato exige um clipe de verdade: `bytes=0` e o sha do arquivo vazio (`e3b0c44298fc`) não contam como campo.
_FETCH_LINE = re.compile(r'event=fetch [^"\n]*')
_CLIP_FIELDS = re.compile(r"\bbytes=[1-9]\d* sha256_prefix=(?!e3b0c44298fc\b)[0-9a-f]{12}\b")
# O nome do Python no comando sugerido: `python3` no POSIX, `python` no Windows (`_paths._interpreter`).
_INTERPRETER = re.compile(r'\bpython3?(?=(?: |",\s*")\\?"?<CODE>/scripts/gb\.py)')
# O comando sugerido cita o valor de uma opção: o Windows sempre com aspas duplas (`_paths._quote_nt`, um caminho
# leva `\\` e `:`); o POSIX deixa o caminho sem aspas e cita o texto com aspas simples (`shlex.quote`). O ouro guarda
# a grafia do POSIX. Só vale depois de os caminhos virarem marcadores e só logo depois de uma opção `--nome `: o
# caminho perde as aspas; o texto das duas opções que o ouro mostra citadas (`QUOTED_TEXT_OPTIONS`) ganha as simples.
# Outras aspas duplas ficam como estão: `--statement "frase"` é texto fixo das mensagens, igual nos dois sistemas.
_NT_QUOTED_VALUE = re.compile(r'(--[\w-]+ )(\\?)"([^"\\\n]*)\2"')
_ROOT_TAG = re.compile(r"<(?:T|CODE|PY|FFMPEG|FFPROBE)>")
QUOTED_TEXT_OPTIONS = ("--query ", "--narration ")
_VERSION_LINE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
# A data do dia no frontmatter que a CLI grava (`created:`/`updated:` de credits, README e ORIGEM).
TODAY = "<TODAY>"
# A data do projeto de layout 0 congelado: um dia fixo no passado, como o de um projeto da 2.5
# criado antes da rodada. `<TODAY>` mascara só o que a rodada grava.
FROZEN_DAY = "2000-01-01"
_FRONTMATTER_DATE = re.compile(r"^((?:created|updated): )(\d{4}-\d\d-\d\d)$", re.MULTILINE)


def version_of(code_root):
    """`__version__` da árvore `code_root`, lido do texto (sem importar a árvore)."""
    text = (Path(code_root) / "scripts" / "getbrolls" / "__init__.py").read_text(encoding="utf-8")
    found = _VERSION_LINE.search(text)
    if found is None:
        raise ValueError(f"sem __version__ em {code_root}")
    return found.group(1)


def run_days(today):
    """A data da rodada e a do dia seguinte: uma rodada dura menos de um minuto e pode cruzar a meia-noite."""
    if not today:
        return ()
    first = dt.date.fromisoformat(today)
    return (first.isoformat(), (first + dt.timedelta(days=1)).isoformat())


def _posix_quoting(match):
    flag, value = match.group(1), match.group(3)
    if _ROOT_TAG.match(value):
        return flag + value
    if flag in QUOTED_TEXT_OPTIONS and "'" not in value:
        return f"{flag}'{value}'"
    return match.group(0)


def _posix_tail(match):
    return match.group(1) + re.sub(r"\\\\|\\", "/", match.group(2))


class Roots:
    """Pastas e executáveis da rodada, do texto mais longo para o mais curto, cada um com o seu marcador."""

    def __init__(self, tmp, code, executables):
        pairs = [(tmp, "<T>"), (code, "<CODE>")]
        pairs += [(path, tag) for tag, path in executables.items() if path]
        self.pairs = []
        for path, tag in pairs:
            for form in dict.fromkeys((str(path), os.path.realpath(path))):
                # Cru, escapado em JSON com acento cru e escapado com `\uXXXX` (Windows, usuário acentuado).
                for variant in dict.fromkeys(
                    (form, json.dumps(form, ensure_ascii=False)[1:-1], json.dumps(form)[1:-1])
                ):
                    self.pairs.append((variant, tag))
        self.pairs.sort(key=lambda pair: len(pair[0]), reverse=True)

    def apply(self, text):
        for raw, tag in self.pairs:
            text = text.replace(raw, tag)
        return _TAIL.sub(_posix_tail, text)


class Normalizer:
    """Troca o que varia por rodada ou máquina; uuid, época e sha de mídia gerada viram marcadores numerados."""

    def __init__(self, roots, version, today=None, windows=None):
        self.roots = roots
        # As normalizações que só o Windows pede (aspas do comando sugerido, nome do pino `.cmd`) valem só nele:
        # no POSIX a comparação continua exata.
        self.windows = _windows() if windows is None else windows
        self.today = re.compile("|".join(rf"(?<![\d-]){re.escape(day)}(?![\d-])" for day in run_days(today)) or r"(?!)")
        self.version = re.compile(
            r'(get-brolls |getbrolls |"get_brolls":\s*"|"version":\s*")' + re.escape(version) + r'(?=[\s"(]|$)',
            re.MULTILINE,
        )
        self.uuids = {}
        self.epochs = {}
        self.binary_shas = {}

    def uuid_tag(self, match):
        return self.uuids.setdefault(match.group(0), f"<UUID:{len(self.uuids) + 1}>")

    def epoch_tag(self, match):
        return match.group(1) + self.epochs.setdefault(match.group(2), f"<EPOCH:{len(self.epochs) + 1}>")

    def session_tag(self, match):
        for value, tag in self.uuids.items():
            if value.startswith(match.group(1)):
                return f"gb-{tag}"
        return match.group(0)

    def text(self, value):
        value = _TRACEBACK.sub(r'\1"<TRACEBACK>"', value)
        value = self.roots.apply(value)
        if self.windows:
            value = _NT_QUOTED_VALUE.sub(_posix_quoting, value)
        value = _INTERPRETER.sub("<PYNAME>", value)
        for sha, tag in self.binary_shas.items():
            value = value.replace(sha, tag)
        value = _UUID.sub(self.uuid_tag, value)
        value = _SESSION.sub(self.session_tag, value)
        value = _EPOCH.sub(self.epoch_tag, value)
        value = _TS.sub("<TS>", value)
        value = _STAMP.sub("<STAMP>", value)
        value = self.today.sub(TODAY, value)
        value = _LOG_STAMP.sub("", value)
        value = _MS.sub(r"\1<MS>", value)
        value = _DURATION_MS.sub(r'\1"<MS>"', value)
        value = _TARGET_PYTHON.sub(r"\1<PYNAME>", value)
        if self.windows:
            value = _TARGET_PIN_CMD.sub(r"\1ffmpeg", value)
        value = _CACHE_KEY.sub(r"\1#<CACHE>", value)
        value = _PY_VERSION.sub(r"\1<PYV>", value)
        value = _PLAYWRIGHT_PIN.sub(r"\1", value)
        value = self.version.sub(r"\1<VERSION>", value)
        value = _STDERR_TAIL.sub(r"\1<STDERR_TAIL>", value)
        return _MACHINE_KEYS.sub(r'\1"<MACHINE>"', value)

    def stream(self, raw):
        """stdout/stderr: JSON quando é JSON (um documento ou uma linha por objeto), senão linhas de texto."""
        if not raw.strip():
            return None
        text = self.text(raw)
        with contextlib.suppress(ValueError):
            return {"json": json.loads(text)}
        lines = text.splitlines()
        with contextlib.suppress(ValueError):
            return {"jsonl": [json.loads(line) for line in lines if line.strip()]}
        return {"text": lines}


# A chave da venv do runtime é um resumo derivado (o sha dos arquivos de requisitos, `_paths.part_sha`): muda a cada
# bump de dependência e a cada regeração dos hashes, sem que o comando mude. Só esse campo, nesse caminho do
# `doctor`; a chave das ferramentas (`tools`) e qualquer outro campo valem byte a byte. O arquivo do ouro guarda a
# chave do dia em que foi gravado (qualquer 16 hex vira marcador); o registro da rodada só vira marcador quando traz
# a chave que o checkout calcula (`venv_key`), então uma chave errada ou velha no `doctor` continua reprovando.
_VENV_KEY_PATH = ("stdout", "json", "install", "runtime", "shas")
_VENV_KEY = re.compile(r"[0-9a-f]{16}")
VENV_KEY_MARK = "<VENV_KEY>"


def _mask_venv_key(text, venv_key=None):
    """`text` com a chave da venv do `doctor` trocada por `<VENV_KEY>`; sem esse campo, o próprio `text`.

    `venv_key=None`: qualquer chave de 16 hex (o arquivo do ouro); com valor: só essa chave (o registro da rodada)."""
    try:
        record = json.loads(text)
    except ValueError:
        return text
    node = record
    for key in _VENV_KEY_PATH:
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict) or not isinstance(node.get("venv"), str) or not _VENV_KEY.fullmatch(node["venv"]):
        return text
    if venv_key is not None and node["venv"] != venv_key:
        return text
    node["venv"] = VENV_KEY_MARK
    return json.dumps(record, ensure_ascii=False, indent=1) + "\n"


# No Windows, `private_files.make_private` registra cada ACL privada como uma linha `proc.run` do `icacls` (fica no
# log por rastreabilidade); o ouro foi gravado no POSIX, onde ela não existe. Só essa forma, inteira e
# bem-sucedida, sai da comparação, e só das linhas acrescentadas a um `getbrolls.log`: qualquer outra linha
# `proc.run` (outra ferramenta, outro desfecho, um campo a mais) continua contando.
ICACLS_OP_LINE = re.compile(
    r"level=INFO logger=getbrolls\.effects event=op operation=proc\.run target=icacls outcome=ok "
    r"duration_ms=<MS> error_code=- error=- command=[a-z_]+ exit=0"
)


def _drop_icacls_lines(text):
    """`text` sem as linhas `ICACLS_OP_LINE` em `appends` de um `getbrolls.log`; sem nenhuma, o próprio `text`."""
    try:
        record = json.loads(text)
    except ValueError:
        return text
    appends = record.get("appends") if isinstance(record, dict) else None
    if not isinstance(appends, dict):
        return text
    changed = False
    for rel, lines in appends.items():
        if not rel.endswith("getbrolls.log") or not isinstance(lines, list):
            continue
        kept = [line for line in lines if not (isinstance(line, str) and ICACLS_OP_LINE.fullmatch(line))]
        if len(kept) != len(lines):
            appends[rel] = kept
            changed = True
    return json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n" if changed else text


def comparable(text, venv_key=None):
    """O texto do ouro como a comparação o vê: `bytes=` e `sha256_prefix=` do `event=fetch` viram marcadores, a
    chave da venv do `doctor` vira `<VENV_KEY>` (ver `_mask_venv_key`) e a linha do `icacls` do Windows sai (ver
    `ICACLS_OP_LINE`). Vale para os dois lados (o arquivo do ouro e o registro serializado); nada mais é mascarado."""
    text = _FETCH_LINE.sub(lambda line: _CLIP_FIELDS.sub("bytes=<N> sha256_prefix=<SHA>", line.group(0)), text)
    return _mask_venv_key(_drop_icacls_lines(text), venv_key)


def fetch_lines_without_clip_fields(text):
    """As linhas `event=fetch` de `text` sem `bytes=<número>` e `sha256_prefix=<12 hex>`: a máscara de `comparable`
    não pode esconder um campo que sumiu."""
    return [line.group(0) for line in _FETCH_LINE.finditer(text) if not _CLIP_FIELDS.search(line.group(0))]


# O ouro foi gravado com um ffmpeg sem o filtro `drawtext` (o do brew); o do apt e o do chocolatey têm. A rodada
# fixa um ffmpeg sem `drawtext`: este script repassa tudo ao ffmpeg real, e de `-filters` tira só a linha do
# `drawtext`, em bytes (a listagem não passa pela codificação do sistema) e com o código de saída do ffmpeg real.
# No POSIX o script é o próprio `ffmpeg` (shebang) e repassa com `execv`. No Windows `execv` devolve o controle antes
# de o filho acabar e perde o código de saída: o script `ffmpeg.py` espera o filho (`subprocess.run`) e sai com o
# código dele, e o `ffmpeg.cmd` ao lado é o que o `GB_FFMPEG_PATH` aponta (`.cmd` roda por CreateProcess, que o
# abre pelo cmd.exe).
_SHIM_BODY = """import os, subprocess, sys
real, args = {real!r}, sys.argv[1:]
if "-filters" in args:
    done = subprocess.run([real, *args], capture_output=True, check=False)
    rows = done.stdout.split(b"\\n")
    sys.stdout.buffer.write(b"\\n".join(row for row in rows if b" drawtext " not in row))
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(done.stderr)
    sys.stderr.buffer.flush()
    sys.exit(done.returncode)
{passthrough}
"""
_EXEC_PASSTHROUGH = "os.execv(real, [real, *args])"
_SPAWN_PASSTHROUGH = "sys.exit(subprocess.run([real, *args], check=False).returncode)"


def shim_source(python, real, spawn=False):
    """O texto do shim que esconde o `drawtext` de `real`; `spawn` espera o filho em vez de trocar o processo
    (Windows). Sem `spawn` o texto leva o shebang do `python`."""
    body = _SHIM_BODY.format(real=real, passthrough=_SPAWN_PASSTHROUGH if spawn else _EXEC_PASSTHROUGH)
    return body if spawn else f"#!{python}\n{body}"


def _windows():
    return os.name == "nt"


def pin_skipped_reason(tmp=None):
    """O motivo de a rodada não poder fixar o ffmpeg, ou `None`.

    POSIX: o shim usa o Python como shebang e não roda com espaço no caminho dele. Windows: o `.cmd` cita os
    caminhos, mas o cmd.exe ainda expandiria um `%` dentro deles."""
    if _windows():
        if "%" in sys.executable or (tmp is not None and "%" in str(tmp)):
            return "% no caminho do Python ou da pasta da rodada (o cmd.exe o expandiria)"
        return None
    if any(char.isspace() for char in sys.executable):
        return "espaço no caminho do Python"
    return None


def _write_if_changed(path, text, newline):
    # Escrito uma vez por rodada: o cache da sonda do `drawtext` tem o mtime do ffmpeg na chave.
    data = text.replace("\n", newline).encode("utf-8")
    if not path.is_file() or path.read_bytes() != data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def ffmpeg_pin(tmp):
    """O ffmpeg da rodada: o do PATH com `drawtext` fora de `-filters`, ou o do PATH como está quando não há como
    fixar (`pin_skipped_reason`)."""
    real = shutil.which("ffmpeg") or ""
    if not real or pin_skipped_reason(tmp):
        return real
    folder = tmp / "pin"  # fora de `tmp/bin`, o PATH da rodada: o caso `doctor` (bare) segue sem ffmpeg
    if _windows():
        script = folder / "ffmpeg.py"
        _write_if_changed(script, shim_source(sys.executable, real, spawn=True), "\n")
        launcher = folder / "ffmpeg.cmd"
        _write_if_changed(launcher, f'@"{sys.executable}" "{script}" %*\n', "\r\n")
        return str(launcher)
    shim = folder / "ffmpeg"
    _write_if_changed(shim, shim_source(sys.executable, real), "\n")
    shim.chmod(0o755)
    return str(shim)


LOCAL_APP_DATA = "userhome/AppData/Local"


def hermetic_env(tmp, bare=False):
    """Ambiente fechado da rodada; `bare` tira os pins do ffmpeg (doctor e providers sem ferramenta)."""
    for name in ("bin", "userhome", "gbhome", "cache", "runtime", "tmp", LOCAL_APP_DATA):
        (tmp / name).mkdir(parents=True, exist_ok=True)
    (tmp / LOCAL_APP_DATA / "Temp").mkdir(exist_ok=True)
    # `.env` vazio por GB_ENV_FILE: o `.env` do checkout (com chaves de API) nunca entra no ouro.
    (tmp / "vazio.env").touch()
    env = {
        "PATH": str(tmp / "bin"),
        "HOME": str(tmp / "userhome"),
        "USERPROFILE": str(tmp / "userhome"),
        # O perfil fechado também no Windows: o `/restore` do `make_private` grava seu texto em
        # `%LOCALAPPDATA%\\Temp` (nunca no `TEMP`); sem ele, a ACL da rodada fica pela metade e a linha sai `error`.
        "LOCALAPPDATA": str(tmp / LOCAL_APP_DATA),
        "GB_HOME": str(tmp / "gbhome"),
        "GB_CACHE_DIR": str(tmp / "cache"),
        "GB_RUNTIME_DIR": str(tmp / "runtime"),
        "GB_PROFILE": "off",
        "GB_ENV_FILE": str(tmp / "vazio.env"),
        "GB_YTDLP_PATH": sys.executable,
        "GB_YTDLP_SLEEP": "0,0,0",
        "GB_PACE_MIN_S": "0",
        "GB_PACE_MAX_S": "0",
        "TMPDIR": str(tmp / "tmp"),
        "TEMP": str(tmp / "tmp"),
        "TMP": str(tmp / "tmp"),
        "TZ": HERMETIC_TZ,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "COLUMNS": "100",
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONUTF8": "1",
    }
    if not bare:
        env["GB_FFMPEG_PATH"] = ffmpeg_pin(tmp)
        env["GB_FFPROBE_PATH"] = shutil.which("ffprobe") or ""
    for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        if key in os.environ:
            env[key] = os.environ[key]
    # Cobertura (scripts/check_coverage.py): o filho continua medido. Só o coverage lê essa
    # variável, e o rcfile dele grava fora de `tmp` sem nada em stdout/stderr: o ouro não muda.
    if "COVERAGE_PROCESS_START" in os.environ:
        env["COVERAGE_PROCESS_START"] = os.environ["COVERAGE_PROCESS_START"]
    return env


def run_date(tmp):
    """A data de hoje que a CLI vê no ambiente fechado (o mesmo Python, o mesmo fuso), em ISO."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", "import datetime; print(datetime.date.today().isoformat())"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=hermetic_env(tmp),
        check=True,
        timeout=CASE_TIMEOUT_S,
    )
    return done.stdout.strip()


COMMAND_LOCK = ".command.lock"


def file_bytes(path, windows=None):
    """Os bytes de `path` como o ouro os guarda. A trava do projeto no Windows (`msvcrt`) tem 1 byte `\\0`
    (`file_locks._acquire_lock`); a do POSIX (`flock`) fica vazia. Só no Windows as duas valem o mesmo: no POSIX um
    byte na trava é uma diferença."""
    data = path.read_bytes()
    if (_windows() if windows is None else windows) and path.name == COMMAND_LOCK and data == b"\0":
        return b""
    return data


def snapshot(tmp):
    """{caminho relativo a `tmp`: bytes} das pastas que a CLI pode tocar."""
    files = {}
    for root in SNAPSHOT_ROOTS:
        base = tmp / root
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                rel = path.relative_to(tmp).as_posix()
                if path.is_file() and not path.is_symlink() and not platform_only(rel):
                    files[rel] = file_bytes(path)
    return files


def _is_text(rel, data):
    if Path(rel).suffix not in TEXT_SUFFIXES:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _appended(old, new):
    return (new or b"").removeprefix(old or b"")


def file_delta(before, after, norm):
    """Arquivos criados, mudados ou apagados por um caso; logs e diários entram como linhas acrescentadas."""
    for rel, data in after.items():
        if not _is_text(rel, data) and Path(rel).name not in APPEND_ONLY:
            norm.binary_shas.setdefault(hashlib.sha256(data).hexdigest(), f"<SHA:{rel}>")
    delta, appends = {}, {}
    for rel in sorted(set(before) | set(after)):
        old, new = before.get(rel), after.get(rel)
        if old == new:
            continue
        key = norm.text(rel)
        if Path(rel).name in APPEND_ONLY:
            appends[key] = [norm.text(line) for line in _appended(old, new).decode("utf-8").splitlines()]
        elif new is None:
            delta[key] = {"removed": True}
        elif _is_text(rel, new):
            text = norm.text(new.decode("utf-8"))
            entry = {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "bytes": len(text.encode("utf-8"))}
            if len(text) <= INLINE_TEXT_MAX and Path(rel).suffix not in NEVER_INLINE:
                entry["lines"] = text.splitlines()
            delta[key] = entry
        else:
            delta[key] = {"binary": True}
    return delta, appends


@contextlib.contextmanager
def project_lock_held(project):
    """Segura `brolls/.command.lock` como outro getbrolls seguraria (flock; no Windows, msvcrt)."""
    lock = project / "brolls" / ".command.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as stream:
        if os.name == "nt":
            import msvcrt  # pylint: disable=import-outside-toplevel,import-error

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write("\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)  # pyright: ignore[reportAttributeAccessIssue]
        else:
            import fcntl  # pylint: disable=import-outside-toplevel

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


@contextlib.contextmanager
def library_lock_held(gbhome):
    """`library/index.lock` recém-criada: o `learn` espera o teto da trava (10 s) e desiste."""
    lock = gbhome / "library" / "index.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def review_decision(project):
    """O JSON que o Storyboard exportaria aprovando todo trecho da página atual."""
    page = (project / "brolls" / "review.html").read_text(encoding="utf-8")
    found = re.search(r"window.GETBROLLS_REVIEW=(.*?);</script>", page)
    if found is None:
        raise ValueError("review.html sem o payload embutido")
    payload = json.loads(found.group(1))
    payload["items"] = [{**item, "state": "approved"} for item in payload["items"]]
    target = project.parent / "decisao.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(target)


def materialize_layout0(tmp):
    """Copia o projeto de layout 0 que a v2.5.0 gravou para `tmp/work/layout0`, trocando `<T>` pela pasta.

    O frontmatter fica com a data fixa `FROZEN_DAY`: o projeto é de um dia anterior à rodada,
    como o de quem vem da 2.5. O `review` reescreve o `credits.md` com a data da rodada, e o
    `verify` mantém o `created:` antigo do `ORIGEM.md` e do `README.md`.
    """
    target = tmp / "work" / "layout0"
    shutil.copytree(LAYOUT0, target)
    for path in target.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md", ".html"}:
            text = path.read_text(encoding="utf-8")
            folder = json.dumps(str(tmp))[1:-1] if path.suffix != ".md" else str(tmp)
            path.write_text(text.replace("<T>", folder), encoding="utf-8", newline="\n")
    return target


def fluxo_cases(tmp, state):
    """Do projeto vazio à entrega: cada comando da 2.5, com os erros que a 2.5 já dava."""
    yield from _collection_cases(tmp, state)
    yield from _side_cases(tmp, state)
    yield from _error_cases(tmp, state)


def _collection_cases(tmp, state):
    """Instalação, brief e regras, e o trecho local do registro à entrega."""
    p = str(tmp / "work" / "proj")
    media = str(tmp / "work" / "media" / "original.mp4")
    yield "version", ["--version"], {}
    yield "providers", ["providers"], {"bare": True}
    yield "doctor", ["doctor"], {"bare": True}
    yield "status_missing", ["status", "--project", p], {}
    yield "resolve_file", ["resolve", "--file", media, "--shot", "abertura", "--project", p], {"id": "candidate"}
    cand = ["--candidate", state.get("candidate", "local:sem-id"), "--project", p]
    yield "status_no_brief", ["status", "--project", p], {}
    yield "init_rules", ["init-rules", "--project", p], {}
    yield "rules", ["rules", "--project", p], {}
    yield "init_brief", ["init-brief", "--project", p], {}
    yield "brief_validate", ["brief", "--validate", "--project", p], {}
    yield "brief", ["brief", "--project", p], {}
    yield "inspect_local", ["inspect", *cand], {}
    yield "fetch_unapproved", ["fetch", *cand], {}
    yield "preview", ["preview", *cand, "--start", "0.5", "--end", "1.5", "--narration", "Linha um"], {}
    approval = ["--start", "0.5", "--end", "1.5", "--by", APPROVER, "--channel", "chat", "--statement", STATEMENT]
    yield "status_previewed", ["status", "--project", p], {}
    yield "approve", ["approve", *cand, *approval], {}
    yield "status_approved", ["status", "--project", p], {}
    yield "fetch_no_permit", ["fetch", *cand], {}
    yield "permit", ["permit", *cand, "--evidence", "Vídeo sintético de teste gerado localmente"], {}
    yield "fetch", ["fetch", *cand], {}
    yield "verify", ["verify", "--project", p], {}
    yield "review", ["review", "--project", p], {}
    yield "import_review", ["import-review", "--file", "{decision}", "--by", APPROVER, "--project", p], {}
    yield "deliver_dry", ["deliver", "--dry-run", "--project", p], {}
    yield "deliver", ["deliver", "--project", p], {}
    yield "status_done", ["status", "--project", p], {}


def _side_cases(tmp, state):
    """Memória, biblioteca, navegador, fila, URL pública e busca, sem rede."""
    p = str(tmp / "work" / "proj")
    cand = ["--candidate", state.get("candidate", "local:sem-id"), "--project", p]
    memory = ["--decision", "approved", "--reason", "Serve na abertura", "--by", APPROVER]
    yield "remember", ["remember", *cand, *memory], {}
    yield "references", ["references", "--project", p], {}
    search = ["--query", "foguete decolando", "--provider", "youtube", "--outcome", "hit"]
    yield "learn", ["learn", *search, "--project", p], {}
    yield "library", ["library", "--search", "foguete", "--project", p], {}
    yield "browser_plan", ["browser-plan", "--url", "https://example.org/noticia", "--project", p], {}
    reel = ["--provider", "instagram", "--url", "https://www.instagram.com/reel/ABC123/"]
    yield "queue_add", ["queue", "--action", "add", *reel, "--project", p], {}
    yield "queue_next", ["queue", "--action", "next", "--project", p], {"id": "queue_item"}
    item = state.get("queue_item", "sem-id")
    yield "queue_mark", ["queue", "--action", "mark", "--id", item, "--done", "--project", p], {}
    yield "queue_status", ["queue", "--action", "status", "--project", p], {}
    yield "resolve_url", ["resolve", "--url", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "--project", p], {}
    yield "status_url", ["status", "--project", p], {}
    yield "search_dry", ["search", "--provider", "youtube", "--query", "foguete", "--dry-run", "--project", p], {}
    yield "reject", ["reject", *cand, "--reason", "Troca de plano"], {}
    yield "serve_stop", ["serve", "--stop", "--project", p], {}


def _error_cases(tmp, state):
    """Erros de uso, de dados e de trava que a 2.5 já devolvia."""
    p = str(tmp / "work" / "proj")
    cand = ["--candidate", state.get("candidate", "local:sem-id"), "--project", p]
    yield "err_unknown_flag", ["status", "--projct", p], {}
    yield "err_missing_project", ["status"], {}
    # Valor inválido de int: a mensagem do argparse não depende de como a versão do Python cita as opções.
    yield "err_bad_int", ["library", "--search", "x", "--limit", "abc", "--project", p], {}
    yield "err_missing_candidate", ["fetch", "--candidate", "local:nao-existe", "--project", p], {}
    yield "err_project_locked", ["reject", *cand], {"hold": "project"}
    miss = ["--query", "trava", "--provider", "youtube", "--outcome", "miss"]
    yield "err_library_locked", ["learn", *miss, "--project", p], {"hold": "library"}


def layout0_cases(tmp, state):
    """O projeto de layout 0 que a v2.5.0 deixou em disco, lido e continuado pela CLI em teste."""
    p = str(tmp / "work" / "layout0" / "proj")
    yield "l0_status", ["status", "--project", p], {}
    yield "l0_brief", ["brief", "--project", p], {}
    yield "l0_verify", ["verify", "--project", p], {}
    yield "l0_review", ["review", "--project", p], {}
    yield "l0_deliver_dry", ["deliver", "--dry-run", "--project", p], {}
    yield "l0_deliver", ["deliver", "--project", p], {}
    yield "l0_status_done", ["status", "--project", p], {}


SCENARIOS = {"fluxo": fluxo_cases, "layout0": layout0_cases}


def _remember(record, options, state):
    payload = (record["stdout"] or {}).get("json") or {}
    if options.get("id") == "candidate":
        state["candidate"] = payload.get("id", "local:sem-id")
    if options.get("id") == "queue_item":
        state["queue_item"] = (payload.get("item") or {}).get("id", "sem-id")


def _run_one(cli_argv, tmp, argv, options):
    env = hermetic_env(tmp, bare=options.get("bare", False))
    with contextlib.ExitStack() as holds:
        if options.get("hold") == "project":
            holds.enter_context(project_lock_held(tmp / "work" / "proj"))
        if options.get("hold") == "library":
            holds.enter_context(library_lock_held(tmp / "gbhome"))
        return subprocess.run(
            [*cli_argv, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=tmp / "work",
            check=False,
            timeout=CASE_TIMEOUT_S,
        )


@dataclasses.dataclass(frozen=True)
class Run:
    """Uma rodada: a CLI que roda, a pasta temporária e o normalizador que acumula os marcadores."""

    cli_argv: list
    tmp: Path
    norm: Normalizer


def _record_case(run, case, argv, options):
    """Roda um caso e devolve o registro normalizado (com o delta de arquivos que ele causou)."""
    norm = run.norm
    before = snapshot(run.tmp)
    done = _run_one(run.cli_argv, run.tmp, argv, options)
    files, appends = file_delta(before, snapshot(run.tmp), norm)
    return {
        "case": case,
        "argv": [norm.text(arg) for arg in argv],
        "exit": done.returncode,
        "stdout": norm.stream(done.stdout),
        "stderr": norm.stream(done.stderr),
        "files": files,
        "appends": appends,
    }


# O sal fixo do `$GB_HOME` da rodada: os nomes que o `event=op` mostra como `#<hash>` saem iguais em
# toda rodada e em toda máquina. Só a rodada do ouro o planta; a instalação de verdade sorteia o dela.
LOG_SALT_FILE = ".log-salt"
LOG_SALT = "0" * 64


def plant_log_salt(gbhome):
    """Grava o sal fixo em `gbhome` (0600) antes do primeiro caso, para ele não contar como arquivo novo."""
    gbhome.mkdir(parents=True, exist_ok=True)
    salt = gbhome / LOG_SALT_FILE
    salt.write_text(LOG_SALT + "\n", encoding="utf-8")
    salt.chmod(0o600)


def record_scenario(cli_argv, code_root, scenario, tmp, stop_after=None):
    """Roda os casos de `scenario` com a CLI `cli_argv` em `tmp`; devolve os registros normalizados.

    Os casos saem de um gerador: o argv de um caso só é montado depois que o anterior rodou,
    então o id do candidato e o do item da fila chegam pelo `state`.
    """
    (tmp / "work" / "proj").mkdir(parents=True, exist_ok=True)
    (tmp / "work" / "media").mkdir(parents=True, exist_ok=True)
    shutil.copy2(MEDIA, tmp / "work" / "media" / "original.mp4")
    plant_log_salt(tmp / "gbhome")
    today = run_date(tmp)
    if scenario == "layout0":
        materialize_layout0(tmp)
    executables = {"<PY>": sys.executable, "<FFMPEG>": ffmpeg_pin(tmp), "<FFPROBE>": shutil.which("ffprobe")}
    run = Run(cli_argv, tmp, Normalizer(Roots(tmp, code_root, executables), version_of(code_root), today=today))
    state, records = {}, []
    for case, planned, options in SCENARIOS[scenario](tmp, state):
        argv = [review_decision(tmp / "work" / "proj") if arg == "{decision}" else arg for arg in planned]
        record = _record_case(run, case, argv, options)
        _remember(record, options, state)
        records.append(record)
        if case == stop_after:
            break
    return records


def serialize(record):
    """O texto exato de um registro em disco: o que o teste compara byte a byte."""
    return json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def dump(records, folder):
    """Grava um arquivo por caso em `folder`, apagando o caso que saiu da matriz."""
    folder.mkdir(parents=True, exist_ok=True)
    names = {f"{record['case']}.json" for record in records}
    for stale in folder.glob("*.json"):
        if stale.name not in names:
            stale.unlink()
    for record in records:
        (folder / f"{record['case']}.json").write_text(serialize(record), encoding="utf-8", newline="\n")


def update_reason(env):
    """O motivo de regravar o ouro, ou ValueError: `GB_UPDATE_GOLDEN=1` sem `GB_GOLDEN_REASON` não grava."""
    reason = (env.get("GB_GOLDEN_REASON") or "").strip()
    if not reason:
        raise ValueError("GB_UPDATE_GOLDEN=1 exige GB_GOLDEN_REASON com o motivo da regravação.")
    if "\n" in reason or len(reason) > 300:  # noqa: PLR2004 - uma linha curta no registro
        raise ValueError("GB_GOLDEN_REASON é uma linha só, com até 300 caracteres.")
    return reason


def log_rewrite(reason, today):
    """Acrescenta `- <data>: <motivo>` ao REGRAVACOES.md (o histórico do ouro nunca é reescrito)."""
    with REWRITES.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"- {today}: {reason}\n")


def freeze_text(text, roots, today):
    """O texto de um arquivo do projeto congelado: pastas viram `<T>` e a data do dia no frontmatter, `FROZEN_DAY`."""
    text = roots.apply(text)
    if "<CODE>" in text:
        raise ValueError("o arquivo cita a instalação; o projeto congelado não pode")
    days = run_days(today)
    return _FRONTMATTER_DATE.sub(
        lambda found: found.group(1) + (FROZEN_DAY if found.group(2) in days else found.group(2)), text
    )


def freeze_layout0(cli_argv, code_root, target=LAYOUT0):
    """Grava em `target` o projeto que a CLI `cli_argv` deixa depois do `verify` do fluxo (a v2.5.0)."""
    with tempfile.TemporaryDirectory(prefix="gb-ouro-") as raw:
        tmp = Path(raw)
        today = run_date(tmp)
        record_scenario(cli_argv, code_root, "fluxo", tmp, stop_after="verify")
        roots = Roots(tmp, code_root, {})
        if target.exists():
            shutil.rmtree(target)
        source = tmp / "work" / "proj"
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.name in NOT_FROZEN:
                continue
            frozen = target / "proj" / path.relative_to(source)
            frozen.parent.mkdir(parents=True, exist_ok=True)
            data = path.read_bytes()
            if _is_text(path.name, data):
                try:
                    text = freeze_text(data.decode("utf-8"), roots, today)
                except ValueError as exc:
                    raise ValueError(f"{path.name}: {exc}") from exc
                frozen.write_text(text, encoding="utf-8", newline="\n")
            else:
                frozen.write_bytes(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grava o ouro da 2.5 com a CLI indicada.")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record", help="Grava as duas rodadas (fluxo e layout0) em --out")
    record.add_argument("--cli", required=True, help="scripts/gb.py da árvore que roda")
    record.add_argument("--out", required=True, help="Pasta que recebe <rodada>/<caso>.json")
    freeze = sub.add_parser("freeze-layout0", help="Congela em fixtures o projeto de layout 0 da CLI indicada")
    freeze.add_argument("--cli", required=True, help="scripts/gb.py da árvore da v2.5.0")
    args = parser.parse_args(argv)
    # O `gb.py` de uma árvore qualquer (a v2.5.0, por exemplo), não a CLI em teste: por isso
    # não passa por `_paths.CLI_ARGV`, que no modo wheel trocaria a árvore pelo pacote instalado.
    script = Path(args.cli).resolve()
    tree_argv = [sys.executable, str(script)]
    if args.command == "freeze-layout0":
        freeze_layout0(tree_argv, script.parents[1])
        sys.stdout.write(json.dumps({"layout0": str(LAYOUT0.relative_to(ROOT))}) + "\n")
        return 0
    out = Path(args.out).resolve()
    for scenario in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix="gb-ouro-") as raw:
            records = record_scenario(tree_argv, script.parents[1], scenario, Path(raw))
        dump(records, out / scenario)
        sys.stdout.write(json.dumps({"scenario": scenario, "cases": len(records)}) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

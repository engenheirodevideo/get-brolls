#!/usr/bin/env bash
# Portão local antes de `git tag`, também chamável pelo CI: roda a mesma
# bateria de verificação de release contra a árvore de trabalho ou um commit
# específico (--ref), para que o que é publicado seja exatamente o que foi
# checado.
# Uso: bash scripts/preflight.sh [--ref <commit-ish>] [--version X.Y.Z]
#
# Sem --ref, roda contra a árvore de trabalho. Com --ref, resolve o ref a um
# commit completo, faz um clone local (`git clone --no-hardlinks`) e faz
# checkout desse commit num diretório temporário, rodando os passos ali, para
# que a verificação seja sobre o que a tag aponta (com histórico e `.git`
# completos), não sobre o HEAD local. Não instala nada e não toca rede.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

REF=""
VERSION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --ref)
      REF="${2:-}"
      if [ -z "$REF" ]; then
        echo "preflight: --ref precisa de um valor" >&2
        exit 1
      fi
      shift 2
      ;;
    --version)
      VERSION="${2:-}"
      if [ -z "$VERSION" ]; then
        echo "preflight: --version precisa de um valor" >&2
        exit 1
      fi
      shift 2
      ;;
    *)
      echo "uso: bash scripts/preflight.sh [--ref <commit-ish>] [--version X.Y.Z]" >&2
      exit 1
      ;;
  esac
done

WORKDIR="$REPO_ROOT"
CLEANUP_DIR=""

if [ -n "$REF" ]; then
  RESOLVED_REF="$(git -C "$REPO_ROOT" rev-parse --verify "${REF}^{commit}" 2>/dev/null)" || {
    echo "preflight: --ref \"$REF\" não resolve a um commit" >&2
    exit 1
  }
  TMP_DIR="$(mktemp -d)"
  CLEANUP_DIR="$TMP_DIR"
  trap 'rm -rf "$CLEANUP_DIR"' EXIT
  git clone -q --no-hardlinks "$REPO_ROOT" "$TMP_DIR/repo"
  git -C "$TMP_DIR/repo" checkout -q --detach "$RESOLVED_REF"
  WORKDIR="$TMP_DIR/repo"
fi

cd "$WORKDIR"
export PYTHONPATH="tests"

read_init_version() {
  python3 - <<'PY'
import re
import sys
from pathlib import Path

path = Path("scripts/getbrolls/__init__.py")
if not path.is_file():
  sys.exit("preflight: scripts/getbrolls/__init__.py não existe")
source = path.read_text(encoding="utf-8")
found = re.search(r'__version__\s*=\s*"([^"]+)"', source)
if not found:
  sys.exit("preflight: __version__ não encontrado em scripts/getbrolls/__init__.py")
print(found.group(1))
PY
}

INIT_VERSION="$(read_init_version)"

if [ -z "$VERSION" ]; then
  VERSION="$INIT_VERSION"
fi

REF_LABEL="${REF:-worktree}"

fail() {
  echo "PREFLIGHT FALHOU: $1" >&2
  exit 1
}

echo "==> 1/7 versão coerente"
if [ ! -f "tests/test_version_coherence.py" ]; then
  fail "versão coerente (tests/test_version_coherence.py ainda não existe)"
fi
if [ ! -f "scripts/bump_version.py" ]; then
  fail "versão coerente (scripts/bump_version.py ainda não existe)"
fi
python3 -m unittest tests.test_version_coherence || fail "versão coerente (tests.test_version_coherence)"
if [ "$INIT_VERSION" != "$VERSION" ]; then
  fail "versão coerente (--version $VERSION != __version__ $INIT_VERSION)"
fi

echo "==> 2/7 frontmatter"
python3 - <<'PY' || fail "frontmatter"
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(".").resolve()
tracked = (
    subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
    .stdout.decode("utf-8")
    .split("\0")
)

try:
    import yaml

    mode = "PyYAML"
except ImportError:
    yaml = None
    mode = "sintático (sem PyYAML)"

print(f"    modo: {mode}")

FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
problems = []

for path in tracked:
    if not path.endswith(".md"):
        continue
    file_path = ROOT / path
    if not file_path.is_file():
        continue
    text = file_path.read_text(encoding="utf-8", errors="replace")
    match = FRONT_RE.match(text)
    if not match:
        continue
    block = match.group(1)
    if yaml is not None:
        try:
            yaml.safe_load(block)
        except yaml.YAMLError as exc:
            problems.append(f"{path}: {exc}")
        continue
    # Sem PyYAML: mesma regra sintática de tests/test_skill_mirror.py — todo
    # valor de primeiro nível com `: ` ou ` #` precisa estar entre aspas, e
    # aspas abertas precisam fechar na mesma linha.
    for line in block.splitlines():
        if not line or line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if not value:
            continue
        if value[0] in "'\"":
            if value[-1] != value[0]:
                problems.append(f"{path}: aspas sem fechar em {key}")
            continue
        if ": " in value or " #" in value or value[0] in "[]{}&*!|>%@`":
            problems.append(f"{path}: valor sem aspas em {key}")

if problems:
    for problem in problems:
        print(problem, file=sys.stderr)
    sys.exit(1)
PY

echo "==> 3/7 material interno"
python3 -m unittest tests.test_repository.RepositoryDocumentationTests.test_no_internal_working_material_is_tracked \
  || fail "material interno (test_no_internal_working_material_is_tracked)"

echo "==> 4/7 espelho da skill"
python3 scripts/gen_skill_mirror.py --check || fail "espelho da skill (gen_skill_mirror.py --check)"

echo "==> 5/7 âncoras"
python3 scripts/check_anchors.py || fail "âncoras (check_anchors.py)"

echo "==> 6/7 suíte"
python3 -m unittest discover -s tests || fail "suíte (unittest discover -s tests)"

echo "==> 7/7 seção do CHANGELOG"
SECTION="$(awk -v header="## $VERSION — " '
  index($0, header) == 1 { inside = 1; next }
  inside && /^## / { exit }
  inside { print }
' CHANGELOG.md)"
if [ -z "$SECTION" ]; then
  fail "seção do CHANGELOG (\"## $VERSION — \" não encontrada)"
fi

echo "PREFLIGHT OK $VERSION $REF_LABEL"

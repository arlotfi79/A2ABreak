#!/usr/bin/env bash
#
# A2ABreak - single installation entry point for artifact evaluation.
#
# This is the script referenced by `install_script` in metadata.toml. It wraps
# the existing setup_venv.sh rather than replacing it, then does the three extra
# things an evaluator needs: link the pipeline where PSM_Benchmark expects it,
# report which optional tools are present, and place the API key if one is set.
#
# Nothing here needs an API key. Tiers 0 and 1 in CLAIMS.md run offline and free.
#
# Usage:
#   scripts/install.sh                  venv + symlinks + tool report
#   scripts/install.sh --check-only     report only, write nothing
#   scripts/install.sh --with-psmbench  also fetch the PSMBench clone (tier 2)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK_ONLY=0
WITH_PSMBENCH=0

for arg in "$@"; do
  case "${arg}" in
    --check-only)    CHECK_ONLY=1 ;;
    --with-psmbench) WITH_PSMBENCH=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: ${arg}" >&2; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  [ ok ] %s\n' "$1"; }
warn() { printf '  [warn] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n' "$1"; }

# ---------------------------------------------------------------- 1. Python
bold "1. Python"
if ! command -v python3 >/dev/null 2>&1; then
  bad "python3 not found on PATH. A2ABreak needs Python 3.11 or newer."
  exit 1
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 11) else 0)')"
if [ "${PY_OK}" != "1" ]; then
  bad "Python ${PY_VER} found, but 3.11+ is required."
  echo "       The pipeline uses PEP 604 unions (\`str | None\`) and tomllib." >&2
  exit 1
fi
ok "python3 ${PY_VER}"

# ------------------------------------------------------- 2. Optional tooling
bold "2. Optional tooling"
if command -v java >/dev/null 2>&1; then
  JAVA_LINE="$(java -version 2>&1 | head -1)"
  ok "java  - ${JAVA_LINE}  (needed by formal_verification/run_all.sh)"
else
  warn "java  - not found. formal_verification/ will not run; everything else is fine."
fi
if command -v dot >/dev/null 2>&1; then
  ok "dot   - $(dot -V 2>&1 | head -1)  (needed only to render FSM figures)"
else
  warn "dot   - not found. src/FSM_to_DOT.py cannot render figures; no claim depends on it."
fi
for tool in curl shasum git; do
  if command -v "${tool}" >/dev/null 2>&1; then
    ok "${tool}"
  else
    warn "${tool} - not found (formal_verification/ and PSMBench provenance checks need it)."
  fi
done

if [ "${CHECK_ONLY}" = "1" ]; then
  echo
  bold "--check-only: nothing was written."
  exit 0
fi

# ---------------------------------------------------- 3. Virtual environment
bold "3. Virtual environment"
"${ROOT_DIR}/setup_venv.sh" >/dev/null
ok ".venv created and requirements.txt installed"

# --------------------------------------------------------- 4. Pipeline links
bold "4. Pipeline links for PSM_Benchmark"
"${ROOT_DIR}/scripts/link_pipeline.sh" >/dev/null
ok "nine pipeline files linked into the repository root"
echo "         (PSM_Benchmark/runner.py resolves them from PIPELINE_DIR; see"
echo "          scripts/link_pipeline.sh for why this is a symlink and not an edit)"

# ------------------------------------------------------------- 5. API key
bold "5. Anthropic API key"
if [ -f "${ROOT_DIR}/src/.env" ]; then
  ok "src/.env already present - left untouched"
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  printf 'ANTHROPIC_API_KEY="%s"\n' "${ANTHROPIC_API_KEY}" > "${ROOT_DIR}/src/.env"
  chmod 600 "${ROOT_DIR}/src/.env"
  ok "src/.env written from \$ANTHROPIC_API_KEY"
  "${ROOT_DIR}/scripts/link_pipeline.sh" >/dev/null   # pick up the new .env
else
  warn "no key found. Not needed for tier 0 or tier 1 (all seven claims verify offline)."
  echo "         For live re-runs (tier 2), create it with:"
  echo "           echo 'ANTHROPIC_API_KEY=\"sk-ant-...\"' > src/.env"
fi

# --------------------------------------------------------- 6. PSMBench clone
if [ "${WITH_PSMBENCH}" = "1" ]; then
  bold "6. PSMBench clone"
  "${ROOT_DIR}/scripts/fetch_psmbench.sh"
fi

cat <<'EOF'

------------------------------------------------------------------
Installation complete.

Next, the kick-the-tires check (offline, no API key, under 10 minutes):

    scripts/smoke_test.sh

Then verify every paper claim against the shipped outputs:

    .venv/bin/python scripts/verify_claims.py

CLAIMS.md maps each paper claim to its script and expected output.
REQUIREMENTS.md lists runtime, cost, and infrastructure per tier.
------------------------------------------------------------------
EOF

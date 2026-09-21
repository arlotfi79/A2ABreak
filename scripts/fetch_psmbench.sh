#!/usr/bin/env bash
#
# Fetch the PSMBench clone that PSM_Benchmark/ scores against.
#
# PSM_Benchmark/runner.py:34 expects the clone at <repo root>/RFC_PSM_Benchmark,
# pinned to commit df0bce6, and runner.py:247 refuses to run if the clone is
# dirty -- the harness reads ground truth from it and must never write to it.
#
# Only tier 2 (live PSMBench re-runs) needs this. Tiers 0 and 1 verify every
# claim from the results already shipped in PSM_Benchmark/protocols/ and
# PSM_Benchmark/results/, so an evaluator can skip this script entirely.
#
# Usage:
#   scripts/fetch_psmbench.sh [--url <git-url>] [--no-deps]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLONE_DIR="${ROOT_DIR}/RFC_PSM_Benchmark"
PINNED_COMMIT="df0bce6"

# PSMBench upstream. Commit df0bce6 ("Remove hardcoded API key from
# output_parser_fsm.py", 2025-12-17) is the pin this artifact was evaluated
# against. Apache License 2.0. Override with --url or $PSMBENCH_URL if the
# repository moves.
PSMBENCH_URL="${PSMBENCH_URL:-https://github.com/Zilinlin/RFC_PSM_Benchmark.git}"
INSTALL_DEPS=1

while [ $# -gt 0 ]; do
  case "$1" in
    --url)     PSMBENCH_URL="$2"; shift 2 ;;
    --no-deps) INSTALL_DEPS=0; shift ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

# ------------------------------------------------------------------- clone
if [ -d "${CLONE_DIR}/.git" ]; then
  echo "Clone already present at ${CLONE_DIR}"
  DIRTY="$(git -C "${CLONE_DIR}" status --porcelain)"
  if [ -n "${DIRTY}" ]; then
    echo "error: the clone has local modifications." >&2
    echo "       PSM_Benchmark/runner.py:247 requires a clean clone and will refuse to run." >&2
    echo "${DIRTY}" >&2
    exit 1
  fi
  HEAD_SHA="$(git -C "${CLONE_DIR}" rev-parse --short HEAD)"
  if [ "${HEAD_SHA}" != "${PINNED_COMMIT}" ]; then
    echo "warning: clone is at ${HEAD_SHA}, expected the pinned ${PINNED_COMMIT}." >&2
    echo "         Results may differ from the shipped ones. Check out the pin with:" >&2
    echo "           git -C RFC_PSM_Benchmark checkout ${PINNED_COMMIT}" >&2
  else
    echo "  at pinned commit ${PINNED_COMMIT}, clean."
  fi
else
  if [ -z "${PSMBENCH_URL}" ]; then
    echo "error: no PSMBench repository URL. Pass --url <git-url>." >&2
    exit 1
  fi
  echo "Cloning PSMBench from ${PSMBENCH_URL}"
  echo "  into ${CLONE_DIR} ..."
  git clone "${PSMBENCH_URL}" "${CLONE_DIR}"
  git -C "${CLONE_DIR}" checkout "${PINNED_COMMIT}"
  echo "  checked out ${PINNED_COMMIT}"
fi

# ------------------------------------------------------------- scorer deps
if [ "${INSTALL_DEPS}" = "1" ]; then
  REQ="${CLONE_DIR}/requirements.txt"
  if [ -f "${REQ}" ]; then
    echo
    echo "Installing the benchmark's own scorer dependencies from ${REQ}"
    echo "  (sentence-transformers, numpy, pandas, matplotlib, seaborn)"
    echo "  Note: the first evaluation downloads a MiniLM sentence-embedding model,"
    echo "        roughly 90 MB, and needs network access."
    "${PY}" -m pip install --quiet -r "${REQ}"
    echo "  done."
  else
    echo "warning: ${REQ} not found; install the scorer dependencies manually." >&2
  fi
fi

cat <<EOF

PSMBench is ready at ${CLONE_DIR}.

Re-score the shipped TCP run without spending anything on the API:

    cd PSM_Benchmark && python3 main.py eval --protocol TCP

A full live re-run of one protocol costs roughly \$20 in API calls; all fourteen
cost \$265.28. See REQUIREMENTS.md for the per-tier cost table.
EOF

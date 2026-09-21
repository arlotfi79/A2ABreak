#!/usr/bin/env bash
#
# Makes the PSMBench harness find the pipeline without editing any source file.
#
# PSM_Benchmark/runner.py sets PIPELINE_DIR = THIS_DIR.parent, i.e. the repository
# root, and then expects the nine PARENT_FILES (runner.py:56-66) to live there:
# it imports them through sys.path (runner.py:36-46), reads config.yaml
# (runner.py:293) and .env (runner.py:201) from that directory, and hashes all
# nine for the run digest (runner.py:452). PSM_Benchmark/evaluation.py:31 and
# PSM_Benchmark/tests/mock_parity_test.py make the same assumption.
#
# The pipeline actually lives in src/. Rather than hand-editing PIPELINE_DIR --
# which the ACSAC Functional badge explicitly rules out ("paths, addresses,
# usernames, and identifiers must not be hardcoded") -- this script places
# relative symlinks at the repository root pointing into src/. Every root-relative
# access above then resolves, and no tracked source file changes.
#
# Usage:
#   scripts/link_pipeline.sh            create the symlinks (idempotent)
#   scripts/link_pipeline.sh --unlink   remove them again
#   scripts/link_pipeline.sh --check    report status, exit 1 if incomplete

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="${ROOT_DIR}/src"

# Must stay in sync with PARENT_FILES in PSM_Benchmark/runner.py:56-66.
PARENT_FILES=(
  stage_a1.py
  stage_a2.py
  stage_b1.py
  stage_b2.py
  merge_stage_a12.py
  verify_stage_a.py
  model_interface.py
  fetcher.py
  config.yaml
)

# .env is read from PIPELINE_DIR by runner.py:201; the pipeline itself reads
# src/.env. Linked only when it exists, since it holds the API key.
OPTIONAL_FILES=(.env)

MODE="link"
case "${1:-}" in
  --unlink) MODE="unlink" ;;
  --check)  MODE="check" ;;
  "")       MODE="link" ;;
  *) echo "usage: $(basename "$0") [--unlink|--check]" >&2; exit 2 ;;
esac

if [ ! -d "${SRC_DIR}" ]; then
  echo "error: ${SRC_DIR} not found - run this from inside the A2ABreak repository." >&2
  exit 1
fi

# Refuse to touch a real file that is not one of our symlinks.
guard() {
  local target="$1"
  if [ -e "${target}" ] && [ ! -L "${target}" ]; then
    echo "error: ${target} exists and is not a symlink." >&2
    echo "       Refusing to overwrite it. Move it aside and re-run." >&2
    exit 1
  fi
}

case "${MODE}" in
  link)
    created=0
    for f in "${PARENT_FILES[@]}"; do
      if [ ! -e "${SRC_DIR}/${f}" ]; then
        echo "error: expected ${SRC_DIR}/${f} but it is missing." >&2
        exit 1
      fi
      guard "${ROOT_DIR}/${f}"
      ln -sfn "src/${f}" "${ROOT_DIR}/${f}"
      created=$((created + 1))
    done
    for f in "${OPTIONAL_FILES[@]}"; do
      if [ -e "${SRC_DIR}/${f}" ]; then
        guard "${ROOT_DIR}/${f}"
        ln -sfn "src/${f}" "${ROOT_DIR}/${f}"
        created=$((created + 1))
      fi
    done
    echo "Linked ${created} pipeline file(s) into the repository root."
    echo "PSM_Benchmark can now import the pipeline. Undo with: scripts/link_pipeline.sh --unlink"
    ;;

  unlink)
    removed=0
    for f in "${PARENT_FILES[@]}" "${OPTIONAL_FILES[@]}"; do
      if [ -L "${ROOT_DIR}/${f}" ]; then
        rm -f "${ROOT_DIR}/${f}"
        removed=$((removed + 1))
      fi
    done
    echo "Removed ${removed} symlink(s) from the repository root."
    ;;

  check)
    missing=0
    for f in "${PARENT_FILES[@]}"; do
      if [ -L "${ROOT_DIR}/${f}" ]; then
        printf '  ok      %s -> %s\n' "${f}" "$(readlink "${ROOT_DIR}/${f}")"
      elif [ -e "${ROOT_DIR}/${f}" ]; then
        printf '  plain   %s (real file, not a link)\n' "${f}"
      else
        printf '  MISSING %s\n' "${f}"
        missing=$((missing + 1))
      fi
    done
    if [ "${missing}" -gt 0 ]; then
      echo "${missing} pipeline file(s) not linked. Run: scripts/link_pipeline.sh" >&2
      exit 1
    fi
    echo "All pipeline files are linked."
    ;;
esac

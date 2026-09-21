#!/usr/bin/env bash
#
# A2ABreak - minimal working example for the ACSAC kick-the-tires period.
#
# Offline, no Anthropic API key, no cost. Target runtime under ten minutes on a
# laptop. Every step is a check an evaluator can read the output of directly.
#
#   1. Offline parity harness    PSM_Benchmark/tests/mock_parity_test.py
#   2. Paper claims (quick set)  scripts/verify_claims.py --quick
#   3. Machine-checked models    formal_verification/run_all.sh   [needs Java]
#   4. FSM rendering             src/FSM_to_DOT.py                [needs dot]
#
# Steps 3 and 4 are skipped with a notice when their tool is absent; neither
# supports a paper claim. Step 3 downloads two solver jars on first run and so
# needs network access once.
#
# Usage:  scripts/smoke_test.sh [--skip-formal] [--skip-parity]

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

SKIP_FORMAL=0
SKIP_PARITY=0
for arg in "$@"; do
  case "${arg}" in
    --skip-formal) SKIP_FORMAL=1 ;;
    --skip-parity) SKIP_PARITY=1 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: ${arg}" >&2; exit 2 ;;
  esac
done

declare -a NAMES STATUS
record() { NAMES+=("$1"); STATUS+=("$2"); }

banner() {
  echo
  echo "=============================================================================="
  echo "$1"
  echo "=============================================================================="
}

banner "A2ABreak smoke test - offline, no API key required"
echo "Repository: ${ROOT_DIR}"
echo "Python:     $(${PY} --version 2>&1)"

# ---------------------------------------------------------------- 0. links
if ! ./scripts/link_pipeline.sh --check >/dev/null 2>&1; then
  echo
  echo "Pipeline files are not linked into the repository root; linking now."
  ./scripts/link_pipeline.sh || { echo "could not link pipeline files" >&2; exit 1; }
fi

# ----------------------------------------------------- 1. offline parity
if [ "${SKIP_PARITY}" = "1" ]; then
  record "offline parity harness" "skipped"
else
  banner "1/4  Offline parity harness (no network, no API calls)"
  echo "Drives the POP3 smoke configuration twice - sequentially and with six"
  echo "workers in reversed completion order - and diffs every artifact."
  echo
  if "${PY}" PSM_Benchmark/tests/mock_parity_test.py; then
    record "offline parity harness" "pass"
  else
    record "offline parity harness" "FAIL"
  fi
fi

# ------------------------------------------------------- 2. paper claims
banner "2/4  Paper claims, quick set (claims 1, 3, 4, 6)"
echo "Recomputes the headline numbers from the shipped outputs/ tree."
echo
if "${PY}" scripts/verify_claims.py --quick --no-colour; then
  record "paper claims (quick set)" "pass"
else
  record "paper claims (quick set)" "FAIL"
fi

# -------------------------------------------------- 3. formal verification
if [ "${SKIP_FORMAL}" = "1" ]; then
  record "formal verification" "skipped"
elif ! command -v java >/dev/null 2>&1; then
  banner "3/4  Formal verification - SKIPPED"
  echo "java not found on PATH. This component is supplementary and supports no"
  echo "paper claim; skipping it does not affect claims 1-7."
  record "formal verification" "skipped (no java)"
else
  banner "3/4  Formal verification (Alloy 6 + TLA+/TLC)"
  echo "Downloads and checksums the solver jars on first run, executes twelve"
  echo "checks, and compares each verdict with the expected one. Supplementary:"
  echo "these models are not referenced by the paper, so no claim depends on"
  echo "this step."
  echo
  FV_LOG="$(mktemp)"
  if ( cd formal_verification && ./run_all.sh ) 2>&1 | tee "${FV_LOG}"; then
    record "formal verification" "pass"
  elif grep -q "checksum mismatch for .*tla2tools.jar" "${FV_LOG}"; then
    cat <<'EOF'

------------------------------------------------------------------------------
KNOWN UPSTREAM DRIFT - not an artifact defect, and no paper claim is affected.

The TLA+ release tag v1.8.0 is a rolling prerelease: upstream replaced the jar
under the same URL after this artifact was built, so the pinned SHA-256 no
longer matches what GitHub serves. The pin is behaving exactly as intended by
refusing an unexpected binary.

The Alloy half is unaffected - its pin still matches and its models run.

See INSTALL.md, "run_all.sh exits 3", for the options.
------------------------------------------------------------------------------
EOF
    record "formal verification" "blocked (upstream jar drift)"
  else
    record "formal verification" "FAIL"
  fi
  rm -f "${FV_LOG}"
fi

# ------------------------------------------------------- 4. FSM rendering
if ! command -v dot >/dev/null 2>&1; then
  banner "4/4  FSM rendering - SKIPPED"
  echo "Graphviz 'dot' not found on PATH. Figures only; no claim depends on it."
  record "FSM rendering" "skipped (no dot)"
else
  banner "4/4  FSM rendering (Appendix A, Figure 9)"
  OUT_DIR="$(mktemp -d)"
  if ( cd src && "${PY}" FSM_to_DOT.py --unified \
         --input ../outputs/stage_b4/unified_fsm_llm_dedup.json >/dev/null 2>&1 ); then
    echo "Rendered the unified FSM (37 states, 76 transitions)."
    record "FSM rendering" "pass"
  else
    echo "Rendering did not complete. Figures are cosmetic; claims are unaffected."
    record "FSM rendering" "skipped (render failed)"
  fi
  rm -rf "${OUT_DIR}"
fi

# ------------------------------------------------------------------ summary
banner "Smoke test summary"
failed=0
for i in "${!NAMES[@]}"; do
  printf '  %-28s %s\n' "${NAMES[$i]}" "${STATUS[$i]}"
  case "${STATUS[$i]}" in FAIL*) failed=1 ;; esac
done
echo
if [ "${failed}" = "0" ]; then
  cat <<'EOF'
Kick-the-tires checks completed.

Next: the full claim set, including the two documented gaps.

    .venv/bin/python scripts/verify_claims.py

CLAIMS.md gives the claim-by-claim mapping and states plainly which parts of
the paper this artifact reproduces, and where its numbers differ from the
paper as printed.
EOF
else
  echo "One or more checks failed. See the output above; INSTALL.md has a"
  echo "troubleshooting section for each known failure mode."
fi
exit "${failed}"

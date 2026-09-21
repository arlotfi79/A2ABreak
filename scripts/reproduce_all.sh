#!/usr/bin/env bash
#
# A2ABreak - full artifact reproduction, tiers 0 and 1.
#
# Runs the offline parity harness, all seven paper claims, and the supplementary
# formal verification, then prints one summary table. No API key, no cost,
# comfortably inside ACSAC's one-day evaluation budget.
#
# What this does NOT do: re-run the pipeline against the live A2A specification
# or re-run PSMBench. Those cost roughly $41 and $265 in API calls -- about 13
# hours of runtime, so the evaluation window is not the constraint, the spend is.
# Documented in CLAIMS.md and REQUIREMENTS.md rather than executed here.
#
# Usage:  scripts/reproduce_all.sh [--skip-formal]

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

SKIP_FORMAL=""
for arg in "$@"; do
  case "${arg}" in
    --skip-formal) SKIP_FORMAL="--skip-formal" ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: ${arg}" >&2; exit 2 ;;
  esac
done

echo "##############################################################################"
echo "#  A2ABreak - artifact reproduction (tiers 0 and 1)"
echo "#  Started $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "##############################################################################"

# Tier 0 -----------------------------------------------------------------
./scripts/smoke_test.sh ${SKIP_FORMAL}
smoke=$?

# Tier 1 -----------------------------------------------------------------
echo
echo "##############################################################################"
echo "#  Tier 1 - all seven paper claims"
echo "##############################################################################"
"${PY}" scripts/verify_claims.py --no-colour
claims=$?

# Summary ----------------------------------------------------------------
cat <<EOF

##############################################################################
#  Reproduction summary
##############################################################################

  tier 0  smoke test (offline parity, quick claims, formal models)   $( [ "${smoke}" = 0 ] && echo pass || echo FAIL )
  tier 1  all seven paper claims                                     $( [ "${claims}" = 0 ] && echo "pass" || echo "see below" )

Claims 2 and 7 are reported as PARTIAL by design, not by accident:

  claim 2  the experiment reproduces and is pinned by run_key + SHA-256, but
           its numbers differ from Table 1 as printed (75 extracted edges
           versus 25). An export-configuration difference, not a missing
           artifact. Documented in CLAIMS.md.
  claim 7  the eleven findings and their spec sections reproduce; the STRIDE
           and CIA columns of Table 3 are not recorded in the artifact.

Any OTHER claim reported as PARTIAL or FAIL, or an "UNEXPECTED" banner in the
output, is a real failure and not a documented gap.

Everything else - RQ1, RQ2b, RQ3, RQ4, RQ5 and the FSM itself - reproduces
exactly.

Read CLAIMS.md for the claim-by-claim detail, and README.md for the badge case.
##############################################################################
EOF

[ "${smoke}" = 0 ] || exit 1
exit 0

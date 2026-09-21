#!/usr/bin/env bash
#
# Claim 4: RQ3 / Figure 3 - pipeline refinement
#
# Paper claim
# -----------
#   Of 929 verified statements only 283 carry transition structure, producing a raw
#   graph of 38 states and 245 transitions that refines to a unified FSM of 37
#   states and 76 transitions - a 69% transition reduction and 92% end to end.
#
# Artifact evidence
# -----------------
#   outputs/stage_b1/, outputs/stage_b4/
#
# This script is a thin wrapper around scripts/verify_claims.py. It is read-only,
# needs no API key, and costs nothing. Run it from anywhere.
#
# Usage:  scripts/claim4_fsm_refinement.sh [--json]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

exec "${PY}" "${ROOT_DIR}/scripts/verify_claims.py" --claim 4 "$@"

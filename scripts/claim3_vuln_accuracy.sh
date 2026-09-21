#!/usr/bin/env bash
#
# Claim 3: RQ2b - vulnerability analysis accuracy
#
# Paper claim
# -----------
#   Across five analysis runs Stage C1 produced 17 unique candidates; Stage C2
#   accepted 16 and rejected 1; expert review validated 11 as true positives,
#   giving precision 73.3% and F1 84.6%.
#
# Artifact evidence
# -----------------
#   outputs/stage_c1_*/, outputs/stage_c2_*/, outputs/final_verified.json
#
# This script is a thin wrapper around scripts/verify_claims.py. It is read-only,
# needs no API key, and costs nothing. Run it from anywhere.
#
# Usage:  scripts/claim3_vuln_accuracy.sh [--json]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

exec "${PY}" "${ROOT_DIR}/scripts/verify_claims.py" --claim 3 "$@"

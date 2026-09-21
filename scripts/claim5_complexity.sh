#!/usr/bin/env bash
#
# Claim 5: RQ4 / Figure 5 - complexity concentration
#
# Paper claim
# -----------
#   Textual volume is a misleading proxy for behavioural complexity: Authentication
#   holds 21 statements but 16 transitions (0.76 per statement), three times the
#   ratio of Discovery (0.24).
#
# Artifact evidence
# -----------------
#   outputs/stage_b1/<phase>.json, outputs/stage_b4/unified_fsm_llm_dedup.json
#
# This script is a thin wrapper around scripts/verify_claims.py. It is read-only,
# needs no API key, and costs nothing. Run it from anywhere.
#
# Usage:  scripts/claim5_complexity.sh [--json]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

exec "${PY}" "${ROOT_DIR}/scripts/verify_claims.py" --claim 5 "$@"

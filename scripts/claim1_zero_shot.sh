#!/usr/bin/env bash
#
# Claim 1: RQ1 - zero-shot comparison
#
# Paper claim
# -----------
#   A zero-shot LLM baseline operating over the same specification produces zero
#   confirmed findings: two runs yielded nine candidate attacks, and Stage C2
#   adversarial verification rejected all nine.
#
# Artifact evidence
# -----------------
#   outputs/zero_shot_run1/, outputs/zero_shot_run2/
#
# This script is a thin wrapper around scripts/verify_claims.py. It is read-only,
# needs no API key, and costs nothing. Run it from anywhere.
#
# Usage:  scripts/claim1_zero_shot.sh [--json]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

exec "${PY}" "${ROOT_DIR}/scripts/verify_claims.py" --claim 1 "$@"

#!/usr/bin/env bash
#
# Claim 6: RQ5 / Table 2 - execution cost
#
# Paper claim
# -----------
#   A single end-to-end run costs $40.97 in API calls, of which $34.67 is consumed
#   by Stages A and B, with Stage B2 the most expensive single step at $11.64.
#
# Artifact evidence
# -----------------
#   outputs/*/*_cost.json
#
# This script is a thin wrapper around scripts/verify_claims.py. It is read-only,
# needs no API key, and costs nothing. Run it from anywhere.
#
# Usage:  scripts/claim6_cost.sh [--json]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

exec "${PY}" "${ROOT_DIR}/scripts/verify_claims.py" --claim 6 "$@"

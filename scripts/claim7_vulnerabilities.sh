#!/usr/bin/env bash
#
# Claim 7: Table 3 - validated vulnerabilities
#
# Paper claim
# -----------
#   A2ABreak identifies eleven protocol-level vulnerabilities spanning discovery,
#   initiation, task execution and interruption, each exploitable under full
#   specification compliance without any implementation flaw.
#
# Artifact evidence
# -----------------
#   outputs/final_verified.json
#
# This script is a thin wrapper around scripts/verify_claims.py. It is read-only,
# needs no API key, and costs nothing. Run it from anywhere.
#
# Usage:  scripts/claim7_vulnerabilities.sh [--json]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

exec "${PY}" "${ROOT_DIR}/scripts/verify_claims.py" --claim 7 "$@"

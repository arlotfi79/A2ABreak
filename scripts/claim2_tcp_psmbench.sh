#!/usr/bin/env bash
#
# Claim 2: RQ2a / Table 1 - TCP FSM validation
#
# Paper claim
# -----------
#   Benchmarked against the TCP ground-truth state machine from PSMBench, the
#   pipeline recovers all 11 protocol states and 19 of 20 ground-truth transitions,
#   achieving precision 0.760, recall 0.950 and F1 0.844.
#
# Artifact evidence
# -----------------
#   PSM_Benchmark/protocols/TCP/output/*/eval.json
#
# This script is a thin wrapper around scripts/verify_claims.py. It is read-only,
# needs no API key, and costs nothing. Run it from anywhere.
#
# Usage:  scripts/claim2_tcp_psmbench.sh [--json]

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT_DIR}/.venv/bin/python"
[ -x "${PY}" ] || PY="python3"

exec "${PY}" "${ROOT_DIR}/scripts/verify_claims.py" --claim 2 "$@"

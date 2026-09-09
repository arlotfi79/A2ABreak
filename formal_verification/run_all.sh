#!/usr/bin/env bash
# ===========================================================================
# A2ABreak — formal verification of the three detailed findings (#3, #1, #9).
#
# Robust runner:
#   * pins + checksum-verifies the solver jars,
#   * builds every run in a fresh temp dir (no stale receipts),
#   * requires each expected output to exist, parse, AND match its expected
#     verdict, distinguishing EXPECTED invariant violations from tool failures,
#   * copies results into results/ only after ALL checks validate,
#   * exits non-zero on ANY mismatch or tool error.
# Requires Java 11+ on PATH.
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

ALLOY="$ROOT/tools/alloy.jar"
TLA="$ROOT/tools/tla2tools.jar"
ALLOY_URL="https://github.com/AlloyTools/org.alloytools.alloy/releases/download/v6.2.0/org.alloytools.alloy.dist.jar"
# "v1.8.0" is the GitHub release TAG; the bundled TLC self-identifies as build
# 2026.08.11.125311 (rev 0894c34). The SHA-256 pin below makes the exact jar unambiguous.
TLA_URL="https://github.com/tlaplus/tlaplus/releases/download/v1.8.0/tla2tools.jar"
ALLOY_SHA="6b8c1cb5bc93bedfc7c61435c4e1ab6e688a242dc702a394628d9a9801edb78d"
TLA_SHA="ab323b79802aedc3203b3f9af37c6aca3ed43f4e0225b36f2aa77b26de46c05f"

sha256() { shasum -a 256 "$1" | awk '{print $1}'; }
fetch() { # url out expected_sha
  local url="$1" out="$2" want="$3"
  if [ ! -f "$out" ]; then echo "downloading $(basename "$out") …"; curl -fsSL -o "$out" "$url"; fi
  local got; got="$(sha256 "$out")"
  if [ "$got" != "$want" ]; then
    echo "FATAL: checksum mismatch for $out" >&2
    echo "  expected $want" >&2; echo "  got      $got" >&2; exit 3
  fi
}
mkdir -p tools results
fetch "$ALLOY_URL" "$ALLOY" "$ALLOY_SHA"
fetch "$TLA_URL"   "$TLA"   "$TLA_SHA"

BUILD="$(mktemp -d)"; STAGE="$BUILD/results"; mkdir -p "$STAGE"
trap 'rm -rf "$BUILD" states tla/states tla/*_TTrace_* 2>/dev/null || true' EXIT
FAIL=0
declare -a SUMMARY=()

# ---- Alloy: run every command, validate each verdict against an expectation map.
run_alloy() { # name  model  "Cmd=verdict Cmd=verdict ..."
  local name="$1" model="$2" expect="$3"
  local log="$BUILD/$name.log" rc=0
  ( cd "$BUILD" && java -jar "$ALLOY" exec -c '*' "$ROOT/$model" ) >"$log" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  [$name] ALLOY TOOL ERROR (exit $rc)"; sed 's/^/      /' "$log" | head -4; FAIL=1; return
  fi
  local stem rec; stem="$(basename "$model" .als)"; rec="$BUILD/$stem/receipt.json"
  if [ ! -s "$rec" ]; then echo "  [$name] MISSING/EMPTY receipt"; FAIL=1; return; fi
  local out
  if ! out="$(python3 "$ROOT/tools/validate_alloy.py" "$rec" "$expect" 2>&1)"; then
    echo "  [$name] VALIDATION FAILED"; echo "$out" | sed 's/^/      /'; FAIL=1; return
  fi
  local sub="${name%%_*}"; mkdir -p "$STAGE/$sub"
  cp "$rec" "$STAGE/$sub/${name#*_}_receipt.json"
  while IFS= read -r l; do SUMMARY+=("    $l"); done <<<"$out"
}

# ---- TLC: classify exit code, require the expected verdict.
run_tlc() { # name  cfg  expected   ("HOLD" or "VIOLATED:<Inv>")
  local name="$1" cfg="$2" expected="$3"
  local log="$BUILD/$name.log" rc=0
  ( cd "$BUILD" && java -cp "$TLA" tlc2.TLC -config "$ROOT/tla/$cfg" "$ROOT/tla/IdentityLoss.tla" ) >"$log" 2>&1 || rc=$?
  local got
  if [ "$rc" -eq 0 ] && grep -q "No error has been found" "$log"; then
    got="HOLD"
  elif [ "$rc" -eq 12 ] && grep -qi "is violated" "$log"; then
    got="VIOLATED:$(grep -oE 'Invariant [A-Za-z]+ is violated' "$log" | head -1 | awk '{print $2}')"
  else
    echo "  [$name] TLC TOOL ERROR (exit $rc)"; grep -iE 'error|exception|fatal|parse' "$log" | head -3 | sed 's/^/      /'; FAIL=1; mkdir -p "$STAGE/${name%%_*}"; cp "$log" "$STAGE/${name%%_*}/${name#*_}.txt"; return
  fi
  if [ "$got" != "$expected" ]; then
    echo "  [$name] VERDICT MISMATCH: expected '$expected', got '$got'"; FAIL=1
  fi
  local sub="${name%%_*}"; mkdir -p "$STAGE/$sub"; cp "$log" "$STAGE/$sub/${name#*_}.txt"
  SUMMARY+=("    ${name}  ->  ${got}")
}

echo "==> #3 Cross-Client Context Injection (Alloy)"
SUMMARY+=("#3 context (Alloy):")
run_alloy finding3_context alloy/context_ownership.als \
  "TaskRuleIsTaskConfidentiality=holds GuardIsAdmissionIntegrity=holds GapSurvivesSection131=counterexample ProtectedTaskInForeignContext=instance Mitigated=holds"

echo "==> #1 Unattested Skill Claims (Alloy)"
SUMMARY+=("#1 skills (Alloy):")
run_alloy finding1_skill alloy/skill_attestation.als \
  "AttestationIsSignedCapability=holds UnsignedForgery=instance SignedButLying=instance SignedCapabilityHonored=counterexample SignedAttestationRestores=holds"

echo "==> #9 Multi-Hop Identity Loss (TLA+/TLC)"
SUMMARY+=("#9 identity (TLA+):")
run_tlc finding9_hopauth        IdentityLoss_hopauth.cfg        "VIOLATED:HopAuthentic"
run_tlc finding9_spec_prov      IdentityLoss_spec_prov.cfg      "VIOLATED:ProvenanceHolds"
run_tlc finding9_spec_harvest   IdentityLoss_spec_harvest.cfg   "VIOLATED:CredentialUnusableByB"
run_tlc finding9_origin_prov    IdentityLoss_origin_prov.cfg    "HOLD"
run_tlc finding9_origin_harvest IdentityLoss_origin_harvest.cfg "VIOLATED:CredentialUnusableByB"
run_tlc finding9_bind_harvest   IdentityLoss_bind_harvest.cfg   "VIOLATED:CredentialUnusableByB"
run_tlc finding9_fixed          IdentityLoss_fixed.cfg          "HOLD"

echo
echo "================ SUMMARY ================"
printf '%s\n' "${SUMMARY[@]}"
if [ "$FAIL" -ne 0 ]; then
  echo >&2
  echo "RESULT: FAILED — a check errored or did not match its expected verdict." >&2
  echo "results/ left UNCHANGED — nothing published on failure." >&2
  exit 1
fi
# Atomic publish: replace results/ as one staged set, only now that all checks passed.
rm -rf results && mv "$STAGE" results
echo "Full logs in results/."
echo "RESULT: all checks matched their expected verdicts."

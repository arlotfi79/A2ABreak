# Formal Verification of the Detailed Findings

These are machine-checked models of the three findings that Section 6 of the paper details. Each
model encodes the protection A2A already enforces, exhibits a counterexample that survives that
protection, and shows that the primitive we propose restores the property.

| # | Finding | Disclosure | Tool | Model |
|---|---|---|---|---|
| **#3** | Cross-Client Context Injection | A2A-01 | Alloy 6 | [`alloy/context_ownership.als`](alloy/context_ownership.als) |
| **#1** | Unattested Skill Claims | none | Alloy 6 | [`alloy/skill_attestation.als`](alloy/skill_attestation.als) |
| **#9** | Multi-Hop Identity Loss | A2A-02 | TLA⁺ / TLC | [`tla/IdentityLoss.tla`](tla/IdentityLoss.tla) |

In `outputs/final_verified.json` these are the entries `run2:C-001:corrected`, `retry4_v2:C-001`,
and `run1:C-002`.

## Running

You need Java 11+, `curl`, `shasum`, and Python 3 (standard library only, for the verdict
validator). `run_all.sh` downloads the Alloy jar into `tools/`, verifies both jars' checksums,
runs each check, and compares every verdict with what we expect. It writes `results/` only when all of them
agree.

The TLA⁺ jar is committed at `tools/tla2tools.jar` rather than downloaded, so only Alloy needs
network access on a first run. See "Tool versions" below for why.

Note that a successful run **replaces** `results/` with its own logs. The copies committed here are
our recorded run; a fresh run produces the same verdicts but different timestamps, seeds, process
IDs and paths, so `git status` will show a diff afterwards. Restore with
`git checkout -- formal_verification/results/`.

```bash
cd formal_verification
./run_all.sh
```

To run a single check, use the commands below. In Alloy, a `check` that returns a counterexample
means the property fails within the scope, and a `run` that returns an instance means the scenario
is satisfiable. TLC prints "No error found" or a violating trace.

```bash
java -jar tools/alloy.jar exec -c '*' -t text -o - alloy/context_ownership.als
java -jar tools/alloy.jar exec -c '*' -t text -o - alloy/skill_attestation.als
for c in hopauth spec_prov spec_harvest origin_prov origin_harvest bind_harvest fixed; do
  java -cp tools/tla2tools.jar tlc2.TLC -config tla/IdentityLoss_$c.cfg tla/IdentityLoss.tla
done
```

## What each model proves

**#3, Cross-Client Context Injection.** Section 13.1 scopes task access to the creator, but a
context has no owner, and sections 3.4.1 and 3.4.3 let any authenticated client start a task in
an existing `contextId`. `GapSurvivesSection131` returns a counterexample in which section 13.1
holds in full and a non-owner still admits a task into a foreign context. `Mitigated` shows that
context ownership closes the gap.

**#1, Unattested Skill Claims.** `AgentSkill` fields are self-asserted (section 4.4.5), and card
signing (sections 8.3, 8.6, 8.7) authenticates the publisher rather than the claim.
`SignedCapabilityHonored` has a counterexample in which a signed agent with an authentic identity
still lies about a capability. `SignedAttestationRestores` shows that signed capability
attestation restores soundness.

**#9, Multi-Hop Identity Loss.** Identity is per hop (section 7), and section 7.6.2 permits
auth-required chains. The final hop therefore cannot see the originator, and an intermediary can
harvest forwarded credentials. The seven TLC configurations show that provenance loss and
harvesting are independent failures. Origin propagation (`PropagateOrigin`, RFC 8693) restores
provenance. Credential binding (`BindCredential`, via RFC 8707, DPoP per RFC 9449, or mTLS)
prevents harvesting, but only once origin is propagated. This matches the layered mitigation in
A2A-02.

Each result line in `results/` is labeled empirical or analytic. An empirical line means a search
found the instance or counterexample. An analytic line holds by the logic of the model, as the
fix-sufficiency checks do. Every gap counterexample has to satisfy A2A's actual rule, so each
vulnerability survives the protection instead of appearing only when the protection is switched off.

## Why two tools

Findings #3 and #1 are structural authorization gaps, where no relation binds one entity to
another, and a bounded relational model finder such as Alloy fits them. Finding #9 is a property
of executions over a delegation chain and needs a temporal model checker, which is why we use
TLA⁺. A Dolev-Yao verifier such as ProVerif would let the attacker break cryptography, which lies
outside our compliant-adversary threat model.

## Tool versions

| Tool | Release | SHA-256 | Source |
|---|---|---|---|
| Alloy | `org.alloytools.alloy.dist.jar` v6.2.0 | `6b8c1cb5bc93bedfc7c61435c4e1ab6e688a242dc702a394628d9a9801edb78d` | downloaded |
| TLA⁺ | `tla2tools.jar` build 2026.09.17.032053 (rev `142d0ba`) | `9d36716ffb5e49d1ba8fae4651eba59f3189887e12eb90e204a42d2e6e993fef` | **vendored** |

`run_all.sh` verifies both checksums and aborts on a mismatch.

**Why the TLA⁺ jar is committed.** The tlaplus `v1.8.0` GitHub tag is a rolling *prerelease*:
upstream replaces the asset in place, so a URL plus a checksum is not a durable pin. The build this
work was originally developed against (2026.08.11.125311, rev `0894c34`) was replaced on
2026-09-17 and is no longer retrievable, which broke the download for anyone running the script
afterwards. The jar is 4.5 MB, so committing it costs little and makes the component reproducible
offline and immune to further drift.

We re-ran all seven TLC configurations against the 2026-09-17 build on 2026-09-20: every verdict is
unchanged, so the findings do not depend on the TLC version. The logs committed in `results/` are
from the original 2026-08-11 run and still carry that version string.

Alloy stays a download: its `v6.2.0` tag is a normal immutable release, its pin still verifies, and
at 21 MB it is not worth committing.

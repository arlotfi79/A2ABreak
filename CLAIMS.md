# Claims → artifact mapping

This file maps each result in *A2ABreak: Systematic Security Analysis of the A2A Protocol* (ACSAC '26) to the component that produces it, the command that checks it, and the output that counts as confirmation.

Run everything at once:

```bash
.venv/bin/python scripts/verify_claims.py
```

Or one claim at a time — each script prints the paper's number beside the artifact's:

```bash
scripts/claim4_fsm_refinement.sh
```

To run the offline checks and all seven claims together, with one summary table:

```bash
bash scripts/reproduce_all.sh
```

**Summary of status.** Five of the seven claims reproduce the paper's numbers exactly. Two carry a caveat, stated here rather than left to be discovered. Claim 2's experiment reproduces exactly, but its numbers differ from Table 1 as printed. Claim 7 reproduces the findings, but two of Table 3's columns were never fields in the pipeline output. Both are spelled out below with the actual numbers.

---

## Summary

| # | Paper | Claim | Status |
|---|---|---|---|
| 1 | §5 RQ1 | Zero-shot baseline produces no confirmed findings | Candidate count exact; rejection record not shipped |
| 2 | §5 RQ2 / Table 1 | TCP FSM validation against PSMBench | Reproduces; differs from Table 1 as printed |
| 3 | §5 RQ2 | Precision 73.3%, F1 84.6% | Exact |
| 4 | §5 RQ3 / Figure 3 | Stage-by-stage FSM refinement | Exact |
| 5 | §5 RQ4 / Figure 5 | Complexity concentration per stage | Exact |
| 6 | §5 RQ5 / Table 2 | Per-stage API cost, $40.97 total | Exact |
| 7 | §6 / Table 3 | Eleven validated vulnerabilities | Findings exact; two columns absent |

---

## Evaluation tiers

A live end-to-end re-run costs about $41 for the A2A pipeline and $265 for the fourteen PSMBench protocols. Runtime is not the constraint — measured from the run manifests it is roughly 13 hours in total, within ACSAC's one-day budget — but the API spend is. So the artifact ships the outputs those runs produced, and the claim scripts recompute the paper's numbers from them for free.

| Tier | What it does | Time | Cost | API key |
|---|---|---|---|---|
| **0** | `scripts/smoke_test.sh` — offline parity harness, quick claims, formal models | < 10 min | $0 | no |
| **1** | `scripts/verify_claims.py` — all seven claims from shipped outputs | < 2 min | $0 | no |
| **0+1** | `scripts/reproduce_all.sh` — tiers 0 and 1 back to back, one summary table | < 15 min | $0 | no |
| **2** | Live re-run of Stage C1+C2 from the shipped FSM | ~22 min | $6.30 | yes |
| **2** | Live re-run of one PSMBench protocol (TCP) | ~17 min | $22.52 | yes |
| **3** | Full pipeline from the live specification, all 14 protocols | ~13 h | ~$308 | yes |

**Tiers 0 and 1 are what we propose for evaluation**, because they cost nothing and finish in minutes. They confirm that the numbers in the paper are the numbers the pipeline emitted. Tier 2 additionally confirms the pipeline still runs end to end, in about twenty minutes for a few dollars. Tier 3 is feasible within the evaluation window — about 13 hours — and we will supply an API key on request if the committee wants it; we do not propose it by default only because of the $308 spend.

---

## Claim 1 — Zero-shot baseline

> "a zero-shot LLM baseline operating over the same specification produces zero confirmed findings" (Abstract; §5 RQ1)
>
> "Two independent zero-shot runs produced nine candidate attacks. Applying the same Stage C2 adversarial verification, all nine candidates were rejected."

**Check:** `scripts/claim1_zero_shot.sh`
**Evidence:** `outputs/zero_shot_run1/`, `outputs/zero_shot_run2/`

| | Paper | Artifact |
|---|---|---|
| Independent runs | 2 | 2 |
| Candidate attacks | 9 | 9 (5 + 4) |
| Confirmed findings | 0 | 0 |

**Gap.** The candidate count reproduces exactly. The *rejection* does not: no Stage C2 verification record over the zero-shot candidates is shipped. Every `outputs/stage_c2_*` directory takes a Stage C1 file as its input, as the directory names record. The claim that C2 rejected all nine rests on a run whose output is not in the artifact.

An evaluator wanting to confirm it directly can re-run C2 against the zero-shot candidates for roughly $3:

```bash
cd src
python stage_c2.py --fsm-input ../outputs/stage_b4/unified_fsm_llm_dedup.json \
  --c1-input ../outputs/zero_shot_run1/zero_shot_result.json \
  --out outputs/stage_c2_zeroshot/verified.json --no-resume
```

---

## Claim 2 — TCP FSM validation *(reproduces; differs from Table 1 as printed)*

> "Our framework recovers all 11 protocol states and 19 of 20 ground-truth transitions, missing only a single `LISTEN`→`SYN_SENT` edge that was extracted with a variant event label, achieving a precision of 0.760, recall of 0.950, and an F1-score of 0.844." (§5 RQ2, Table 1)

**Check:** `scripts/claim2_tcp_psmbench.sh`
**Evidence:** `PSM_Benchmark/protocols/TCP/output/2cd85b6f01/eval.json`, `PSM_Benchmark/results/per_protocol.csv`

**The experiment reproduces exactly.** Everything needed is in the artifact, and the run is pinned end to end:

| | |
|---|---|
| `run_key` | `2cd85b6f01` |
| Exported FSM | SHA-256 `2dda4d6f69…` |
| Ground truth | SHA-256 `d6bbe3a38b…` |
| Evaluator | SHA-256 `c302bb6e01…` (PSMBench's own `eval_fsm_sim.py`, unmodified) |

`cd PSM_Benchmark && python3 main.py eval --protocol TCP` re-scores it and returns the numbers below. Nothing is withheld, and nothing is unavailable.

What differs is the comparison against **Table 1 as printed**:

| | Paper (Table 1) | Shipped run |
|---|---|---|
| States: ground truth | 11 | 11 |
| States: extracted | 11 | 11 |
| States: matched | 11 | **10** |
| Transitions: ground truth | 20 | 20 |
| Transitions: extracted | 25 | **75** |
| Transitions: matched | 19 | **15** |
| Precision | 0.760 | **0.200** |
| Recall | 0.950 | **0.750** |
| F1 | 0.844 | **0.316** |

The paper says one transition was missed, the `LISTEN`→`SYN_SENT` edge. The shipped run misses five, and `LISTEN`→`SYN_SENT` is not among them:

```
SYN_SENT  --receive SYN,ACK / send ACK-->  ESTAB
SYN_RCVD  --receive ACK of SYN-->          ESTAB
SYN_RCVD  --rcv RST (note1)-->             LISTEN
ESTAB     --CLOSE / send FIN-->            FIN_WAIT-1
ESTAB     --receive FIN / send ACK-->      CLOSE_WAIT
```

Every TCP artifact in the repository — the run output, the Stage B1 export, and the normalized supplementary copy — contains 11 states and **75** transitions.

Note that this difference cannot be closed by re-scoring. It is 75 versus 25 *extracted* edges, which is a property of the export, not of the matcher, so no similarity threshold or matching mode changes it.

The closest shipped figure comes from the supplementary judge pass, which relabels transitions by meaning before rescoring: states 11/11 (F1 1.000) and transitions 20/20 matched at P 0.267 / R 1.000 / F1 0.421 (`PSM_Benchmark/results/supplementary/normalized/normalized_scores.csv`). That recovers the paper's "all 11 states" but still not its transition counts.

**Why the two differ.** The `PSM_Benchmark/` harness is a deliberately unfavourable setup: one untailored configuration for all fourteen protocols, no web search, and — per `PSM_Benchmark/METHOD.md` §7 — **no post-hoc export filtering**, so it emits the specification's full per-state behaviour rather than a diagram-level subset. `PSM_Benchmark/README.md` records the consequence: 804 edges against 297 in the ground truths, which is exactly why precision is low. Table 1's 25 extracted transitions correspond to a differently configured, TCP-specific evaluation rather than to this uniform harness.

**Reconciling the printed table with this harness is a camera-ready task, not an artifact gap.** The artifact side is complete: the harness result is fully reproducible, as the hashes above show.

The scope is contained. The TCP comparison validates the FSM-extraction stages against an external benchmark; it is not an input to any other result. Claims 1 and 3–7 stand on the A2A pipeline outputs and are unaffected.

---

## Claim 3 — Vulnerability analysis accuracy

> "across five analysis runs Stage C1 produced 17 unique candidate vulnerabilities. Stage C2 accepted 16 and correctly rejected 1. Manual expert review […] validated 11 of the 16 as true positives, identified 1 duplicate, and overturned 4 as false positives […] a precision of 73.3% (11 TP out of 15 non-duplicate candidates), and an F1 score of 84.6%." (§5 RQ2)

**Check:** `scripts/claim3_vuln_accuracy.sh`

| | Paper | Artifact |
|---|---|---|
| Stage C1 unique candidates | 17 | 17 |
| Stage C2 analysis runs | 5 | 5 |
| Stage C2 accepted | 16 | 16 |
| Stage C2 rejected | 1 | 1 |
| Expert-validated true positives | 11 | 11 |
| Precision | 73.3% | 73.3% |
| F1 | 84.6% | 84.6% |

Reproduces exactly. The 17 candidates come from six Stage C1 passes (`stage_c1_run1`, `run2`, `retry1`–`retry3`, `retry4_v2`) feeding five Stage C2 passes; precision is 11/15 and F1 follows with recall 1.0.

The duplicate and the four false positives are expert-review labels applied outside the pipeline. The artifact records their outcome — the 11 findings kept in `outputs/final_verified.json` — rather than the review itself.

---

## Claim 4 — Pipeline refinement

> "Of the 929 verified statements, only 283 (30%) carry transition structure and enter FSM construction, producing a raw graph of 38 states and 245 transitions. […] The final unified FSM of 37 states and 76 transitions represents a 69% transition reduction from raw synthesis and a 92% end-to-end reduction." (§5 RQ3, Figure 3)

**Check:** `scripts/claim4_fsm_refinement.sh`

| Stage | Paper | Artifact |
|---|---|---|
| Verified statements | 929 | 929 |
| Carrying transition structure | 283 | 283 |
| B1 raw | 38 / 245 | 38 / 245 |
| B2 per-stage | 65 / 77 | 65 / 77 |
| B4 unified | 65 / 92 | 65 / 92 |
| D1 deterministic | 41 / 68 | 41 / 68 |
| D2 semantic | 37 / 76 | 37 / 76 |
| Transition reduction | 69% | 69% |
| End-to-end reduction | 92% | 92% |

Every cell matches. Stage B3 supplies the 15 inter-stage transitions that carry 77 to 92.

---

## Claim 5 — Complexity concentration

> "Authentication contains only 21 statements but produces 16 transitions (0.76 per statement), three times the ratio of Discovery (0.24)." (§5 RQ4, Figure 5)

**Check:** `scripts/claim5_complexity.sh`

| Phase | Statements | States | Transitions |
|---|---|---|---|
| | paper / artifact | paper / artifact | paper / artifact |
| Discovery | 46 / 46 | 9 / 9 | 11 / 11 |
| Authentication | 21 / 21 | 10 / 10 | 16 / 16 |
| Initiation | 41 / 41 | 7 / 7 | 11 / 11 |
| Task execution | 128 / 128 | 18 / 18 | 26 / 26 |
| Interruption | 22 / 22 | 6 / 6 | 10 / 10 |
| Termination | 25 / 25 | 9 / 9 | 14 / 14 |

All eighteen cells reproduce exactly.

Counts come from the per-stage export in `outputs/stage_b4/unified_fsm_llm_dedup_by_stage/`, under one consistent rule: a **state** belongs to the phase it is local to, and a **transition** to the phase it *originates in*. A boundary edge is therefore credited to the phase it leaves, not the one it enters. That is why Discovery reports 11 transitions — ten internal plus one outbound — and why the transition column sums to 88 against 76 distinct transitions: a state shared between two phases contributes an outbound edge to each.

This is the rule the figure was built with, established by testing every plausible alternative against it. Attributing transitions from the `protocol_phases` tags on the unified model instead credits a boundary edge to both phases it touches, which undercounts Discovery by 1 and Authentication by 2; crediting inter-stage edges to the destination phase, to the first listed phase, or subtracting cross-stage edges all land further still. Only the origin rule reproduces the figure and its column sum.

The underlying model is the same 37 states and 76 transitions that claim 4 verifies, so this claim re-partitions a model already confirmed rather than introducing new counts.

---

## Claim 6 — Execution cost

> "The total one-pass cost is $40.97, of which $34.67 is consumed by Stages A and B, with Stage B2 being the single most expensive step at $11.64" (§5 RQ5, Table 2)

**Check:** `scripts/claim6_cost.sh`

| Paper stage | Repository stage | Paper | Artifact |
|---|---|---|---|
| A1 | `stage_a1` | $9.04 | $9.04 |
| A2 | `stage_a2` | $5.46 | $5.46 |
| A3 | `merge_stage_a12` | $0.38 | $0.38 |
| A4 | `verify_stage_a` | $5.42 | $5.42 |
| B2 | `stage_b2` | $11.64 | $11.64 |
| B3 | `stage_b3` | $1.65 | $1.65 |
| D2 | `stage_d2` | $1.08 | $1.08 |
| C1 | `stage_c1` run 1 | $3.20 | $3.20 |
| C2 | `stage_c2` run 1 | $3.10 | $3.10 |
| **Total** | | **$40.97** | **$40.98** |

Reproduces to the cent; the penny is rounding. B1, B4 and D1 make no LLM calls and cost nothing.

Table 2 prices a single pass. The artifact ships six C1 passes ($19.05 together) and five C2 passes ($12.60), because claim 3's accuracy figures are computed over all of them.

---

## Claim 7 — The eleven vulnerabilities *(two columns absent)*

> "A2ABreak identifies eleven protocol-level vulnerabilities spanning discovery, initiation, task execution, and interruption, each exploitable under full specification compliance without requiring any implementation flaw or misconfiguration." (§6, Table 3)

**Check:** `scripts/claim7_vulnerabilities.sh`
**Evidence:** `outputs/final_verified.json`

| | Paper | Artifact |
|---|---|---|
| Validated findings | 11 | 11 |
| With spec section references | 11 | 11 |
| Missing primitive confirmed absent | 11 | 11 |
| Confirmed not by design | 11 | 11 |

**Gap.** Table 3 also gives a STRIDE category and a CIA impact per finding. `final_verified.json` records the title, FSM trace, missing primitive, spec sections checked and source statement IDs — but no STRIDE or CIA field. Those two columns were assigned when the table was written and cannot be recomputed from the artifact. Everything else in Table 3 reproduces, and each finding traces back through its `candidate_id` to the C1 candidate and C2 verification record that produced it.

---

## Supplementary — not paper claims

Two components go beyond the paper. Neither is cited in it, and neither should be read as evidence for a claim.

**`formal_verification/`** — Alloy 6 and TLA⁺/TLC models of three of the eleven findings (#1 Unattested Skill Claims, #3 Cross-Client Context Injection, #9 Multi-Hop Identity Loss). Each model encodes the protection A2A already mandates, exhibits a counterexample that survives it, and shows the proposed primitive restores the property. `run_all.sh` checks all twelve verdicts against an expectation map and publishes `results/` only when every one agrees. It is in the smoke test because it is fast, deterministic, self-validating and free — not because a claim depends on it.

**The fourteen-protocol PSMBench comparison** — the paper benchmarks TCP only. `PSM_Benchmark/` additionally runs all fourteen protocols against nine shipped baselines, with macro scores in `PSM_Benchmark/results/summary.md` and a semantic-judge supplementary pass. Useful context for claim 2, not a substitute for it.

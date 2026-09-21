# A2ABreak — ACSAC 2026 Artifact

Artifact for **"A2ABreak: Systematic Security Analysis of the A2A Protocol"**, accepted at the 42nd Annual Computer Security Applications Conference (ACSAC '26).

A2ABreak is a security-analysis framework for the [Agent-to-Agent (A2A) Protocol](https://a2a-protocol.org). It reads the natural-language specification — version **v1.0.0**, retrieved 2026-05-13 — extracts traceable formal statements, and builds a unified finite-state machine from them. It then searches that FSM for protocol-level vulnerabilities and verifies each candidate adversarially. The findings hold even when every party follows the specification.

From 929 formalized statements it derives a model of 37 states and 76 transitions, and from that model eleven vulnerabilities exploitable by a specification-compliant adversary.

**Authors**

| | |
|---|---|
| Alireza Lotfi *(contact)* | Purdue University — `lotfia@purdue.edu` |
| Mirza Masfiqur Rahman | Purdue University — `rahman75@purdue.edu` |
| Imtiaz Karim | The University of Texas at Dallas — `imtiaz.karim@utdallas.edu` |
| Elisa Bertino | Purdue University — `bertino@purdue.edu` |

---

## Start here

```bash
bash scripts/install.sh      # venv, pipeline links, dependency report
bash scripts/smoke_test.sh   # kick-the-tires: offline, no API key, < 10 min
```

Then check the paper's numbers against the shipped outputs:

```bash
.venv/bin/python scripts/verify_claims.py
```

Or do both in one go, with a summary table at the end:

```bash
bash scripts/reproduce_all.sh
```

No API key is needed for any of this, and none of it costs anything.

| Document | What it covers |
|---|---|
| [`INSTALL.md`](INSTALL.md) | Build, install, per-component setup, troubleshooting |
| [`REQUIREMENTS.md`](REQUIREMENTS.md) | Hardware, software, network, runtime, cost, public infrastructure |
| [`CLAIMS.md`](CLAIMS.md) | **Every paper claim → the script that checks it → the expected output** |
| [`PROVENANCE.md`](PROVENANCE.md) | Where the data came from and how it was produced |
| [`ETHICS.md`](ETHICS.md) | Responsible disclosure, data collection, intended use |

---

## Badges requested

**Available**, **Functional**, and **Reproduced**.

Five of the seven paper claims reproduce exactly from the shipped outputs. Two do not reproduce in full:

- **Claim 2 (Table 1, TCP validation)** reproduces as an experiment — the run is pinned by `run_key` and by SHA-256 over the export, the ground truth and PSMBench's own evaluator, so anyone can re-score it. Its numbers differ from Table 1 as printed: 75 extracted transitions and 15 of 20 matched, against the paper's 25 and 19. That is an export-configuration difference to reconcile at camera-ready, not a missing artifact. `CLAIMS.md` lays out the evidence.
- **Claim 7** reproduces the eleven findings and their spec sections, but Table 3's STRIDE and CIA columns were never fields in the pipeline output.

RQ1, RQ2b, RQ3, RQ4, RQ5 and the FSM itself reproduce exactly, to the cent in the case of the cost table.

**Functional.** Every component the paper describes is present: Stage A formalization (`src/stage_a1.py`, `stage_a2.py`, `merge_stage_a12.py`, `verify_stage_a.py`), Stage B FSM construction (`stage_b1`–`b4`, `stage_d1`, `stage_d2`), Stage C analysis and adversarial verification, and the zero-shot baseline. `scripts/install.sh` installs, `scripts/smoke_test.sh` runs a minimal working example offline in under ten minutes with no API key, and the offline parity harness (`PSM_Benchmark/tests/mock_parity_test.py`, 55 checks) exercises the pipeline modules end to end with monkeypatched LLM calls — no network, no spend.

Nothing requires a hardcoded path. `PSM_Benchmark/runner.py` resolves `PIPELINE_DIR` to the repository root while the pipeline lives in `src/`; earlier revisions told the reader to hand-edit that constant, and `scripts/link_pipeline.sh` now places symlinks instead so the harness works unmodified on any machine.

Two components go beyond the paper and support no claim: the Alloy/TLA⁺ models in `formal_verification/`, and the fourteen-protocol PSMBench comparison (the paper benchmarks TCP only). Both are labelled supplementary in [`CLAIMS.md`](CLAIMS.md).

### Notes for evaluators

- **Contact.** Alireza Lotfi, `lotfia@purdue.edu`, reachable throughout the evaluation period via HotCRP.
- **API key.** Tiers 0 and 1 need none. For tier 2, or a full live reproduction, we will supply a scoped key through HotCRP on request rather than embed one here.
- **No tracking.** The artifact contains no analytics, telemetry or tracking of any kind.
- **Verified environment.** macOS 26.5.1 (arm64) only. The artifact is pure Python with an optional JVM and no platform-specific code, so it should run anywhere Python 3.11+ runs, but we have not tested other platforms.
- **Known caveats** — author paths recorded in some shipped output files, and `formal_verification/run_all.sh` republishing `results/` on a successful run — are listed under "Known environmental caveats" in [`REQUIREMENTS.md`](REQUIREMENTS.md) and in [`INSTALL.md`](INSTALL.md)'s troubleshooting section.

---

## Public release

**The entire artifact as evaluated will be released publicly.** Nothing is withheld — no proprietary models, no restricted datasets, no embargoed code. It is MIT licensed ([`LICENSE`](LICENSE)) and will be deposited in a permanent public repository with a DOI before the camera-ready deadline.

---

## Repository layout

| Path | Contents |
|---|---|
| [`src/`](src/) | The pipeline: stage scripts, shared modules, and `config.yaml` with the models and all twelve prompts. [`src/README.md`](src/README.md) documents each stage. |
| [`outputs/`](outputs/) | What the pipeline produced for A2A, one directory per stage, plus the curated `final_verified.json` with the eleven findings. |
| [`PSM_Benchmark/`](PSM_Benchmark/) | The FSM-extraction stages measured against PSMBench: 14 RFC protocols, 9 shipped baselines, result tables, and an offline parity harness. |
| [`formal_verification/`](formal_verification/) | *Supplementary.* Alloy 6 and TLA⁺ models of three findings, with logs and a runner that checks its own verdicts. |
| [`scripts/`](scripts/) | Artifact-evaluation entry points: install, smoke test, per-claim verification. |
| `metadata.toml` | ACSAC packaging metadata (`artmeta` schema). |

Git ignores `.env`, `.venv/`, `PSM_Benchmark/eval_workspace/`, and `RFC_PSM_Benchmark/` — the PSMBench clone the harness reads.

---

## Claim-to-component map

| # | Paper | Component | Check |
|---|---|---|---|
| 1 | §5 RQ1 | `outputs/zero_shot_run*/` | `scripts/claim1_zero_shot.sh` |
| 2 | §5 RQ2 / Table 1 | `PSM_Benchmark/protocols/TCP/` | `scripts/claim2_tcp_psmbench.sh` |
| 3 | §5 RQ2 | `outputs/stage_c1_*/`, `stage_c2_*/` | `scripts/claim3_vuln_accuracy.sh` |
| 4 | §5 RQ3 / Figure 3 | `outputs/stage_b1/`, `stage_b4/` | `scripts/claim4_fsm_refinement.sh` |
| 5 | §5 RQ4 / Figure 5 | `outputs/stage_b4/` | `scripts/claim5_complexity.sh` |
| 6 | §5 RQ5 / Table 2 | `outputs/*/*_cost.json` | `scripts/claim6_cost.sh` |
| 7 | §6 / Table 3 | `outputs/final_verified.json` | `scripts/claim7_vulnerabilities.sh` |

Full detail, including the expected output for each and the two partial claims, is in [`CLAIMS.md`](CLAIMS.md).

Two components go beyond the paper and support no claim: the Alloy/TLA⁺ models in `formal_verification/`, and the fourteen-protocol PSMBench comparison (the paper benchmarks TCP only).

---

## Cost and runtime

Reproducing the paper live costs about **$308 in API calls**. Runtime is not the obstacle — measured from the run manifests, a full reproduction is roughly **13 hours**, inside ACSAC's one-day limit. The artifact therefore ships the outputs those runs produced and verifies the paper's numbers against them, so an evaluator can confirm every figure for free; a live re-run remains available to anyone with a key.

| Tier | What | Time | Cost | API key |
|---|---|---|---|---|
| **0** | `scripts/smoke_test.sh` | < 10 min | $0 | no |
| **1** | `scripts/verify_claims.py` — all 7 claims | < 2 min | $0 | no |
| **0+1** | `scripts/reproduce_all.sh` — both of the above, one summary | < 15 min | $0 | no |
| 2 | Live Stage C1+C2, or one PSMBench protocol | 17–22 min | $6.30–22.52 | yes |
| 3 | Full pipeline, all 14 protocols | ~13 h | ~$308 | yes |

**Tiers 0 and 1 are the proposed evaluation path**, and `scripts/reproduce_all.sh` runs them both. See [`REQUIREMENTS.md`](REQUIREMENTS.md).

---

## Running the pipeline live

Needs Python 3.11+, an Anthropic API key, and network access. Graphviz is needed only for figures, Java only for formal verification.

```bash
bash scripts/install.sh
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > src/.env
```

Stages read `config.yaml` and `.env` from `src/` and write to `src/outputs/`. A fresh run rebuilds the whole chain from the live specification; to resume from the published artifacts, copy them in first with `cp -r ../outputs ./outputs`.

> **Pin the specification first if you want to match our corpus.** `config.yaml` fetches from `a2a-protocol.org/latest/`, and on that site `latest` aliases the **`dev` branch** rather than a release. Our analysis used **v1.0.0**, so set `fetcher.url` to `https://a2a-protocol.org/v1.0.0/specification/` before running Stage A. Against `/latest/` you will be analysing a much newer document and should not expect 929 statements. See [`PROVENANCE.md`](PROVENANCE.md).

```bash
cd src
python stage_a1.py && python stage_a2.py           # extract statements from the spec
python merge_stage_a12.py                          # merge (review outputs/stage_a12/needs_review.json)
python verify_stage_a.py                           # verify against spec text
python stage_b1.py && python stage_b2.py           # filter, then per-phase FSMs
python stage_b3.py && python stage_b4.py           # cross-phase edges, unified FSM
python stage_d1.py outputs/stage_b4/unified_fsm.json outputs/stage_b4/unified_fsm_deterministic.json
python stage_d2.py --force                         # semantic dedup
python stage_c1.py --input outputs/stage_b4/unified_fsm_llm_dedup.json --out outputs/stage_c1_run1/candidates.json
python stage_c2.py --fsm-input outputs/stage_b4/unified_fsm_llm_dedup.json --out outputs/stage_c2_run1/verified.json
```

[`src/README.md`](src/README.md) covers retry passes, the zero-shot baseline, resume behaviour, and each stage in detail.

**Rendering the FSM** (Appendix A, Figure 9):

```bash
cd src
python FSM_to_DOT.py --unified --input ../outputs/stage_b4/unified_fsm_llm_dedup.json --pdf
python FSM_to_DOT.py --stage discovery             # one phase, from outputs/stage_b2/
```

**Formal verification** — downloads Alloy and TLC, verifies their checksums, runs each check, and writes `results/` only when every verdict matches expectations:

```bash
cd formal_verification && ./run_all.sh
```

**PSMBench comparison** — needs the clone at `RFC_PSM_Benchmark/`, pinned to `df0bce6`:

```bash
scripts/fetch_psmbench.sh --url <psmbench-git-url>
cd PSM_Benchmark
python3 main.py prepare-all                        # inputs, configs, prompt checks
python3 main.py run --protocol TCP --workers 6     # one protocol
python3 main.py eval --protocol TCP                # re-score with the benchmark evaluator
python3 main.py summarize                          # results/*.md|csv
```

`scripts/install.sh` links the pipeline into the repository root, which `PSM_Benchmark/runner.py` requires. Earlier revisions of this README told you to hand-edit `PIPELINE_DIR` in `runner.py`; that is no longer necessary.

---

## Where the paper's results are

- The eleven findings are in `outputs/final_verified.json`. Each traces back through its `candidate_id` to a Stage C1 candidate, its Stage C2 verification record, and the FSM in `outputs/stage_b4/unified_fsm_llm_dedup.json`. The baseline that skips the FSM is in `outputs/zero_shot_run*/`.
- The PSMBench tables are in `PSM_Benchmark/results/`.
- The formal verification logs are in `formal_verification/results/`.

---

## Citation

Accepted at the 42nd Annual Computer Security Applications Conference (ACSAC '26). Preprint: <https://arxiv.org/abs/2609.10871>.

```bibtex
@misc{lotfi2026a2abreaksystematicsecurityanalysis,
      title={A2ABreak: Systematic Security Analysis of the A2A Protocol},
      author={Alireza Lotfi and Mirza Masfiqur Rahman and Imtiaz Karim and Elisa Bertino},
      year={2026},
      eprint={2609.10871},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2609.10871},
}
```

Licensed under the MIT License. All eleven findings were disclosed to the A2A maintainers under the Linux Foundation before publication and have been acknowledged by them; we are engaged with the maintainers and providing further detail as they assess the findings. See [`ETHICS.md`](ETHICS.md).

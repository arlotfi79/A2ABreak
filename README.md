# A2ABreak Artifact Repository

🏆 **Paper accepted at 42nd IEEE Annual Computer Security Applications Conference (ACSAC ’26)**

A2ABreak is a security-analysis framework for the [Agent-to-Agent (A2A) Protocol](https://a2a-protocol.org).
It reads the natural-language specification, extracts traceable formal statements, and builds a
unified finite-state machine (FSM) from them. It then searches that FSM for protocol-level
vulnerabilities and verifies each candidate adversarially. The findings hold even when every party
follows the specification.

---

## 👥 Authors

- **Alireza Lotfi** — Purdue University — lotfia@purdue.edu
- **Mirza Masfiqur Rahman** — Purdue University — rahman75@purdue.edu
- **Imtiaz Karim** — The University of Texas at Dallas — imtiaz.karim@utdallas.edu
- **Elisa Bertino** — Purdue University — bertino@purdue.edu

---

## 📁 Repository layout

| Path                                           | Contents                                                                                                                                           |
|------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| [`src/`](src/)                                 | The pipeline: stage scripts, shared modules, and `config.yaml` with the models and prompts. [`src/README.md`](src/README.md) documents each stage. |
| [`outputs/`](outputs/)                         | What the pipeline produced for A2A, one directory per stage, plus the curated `final_verified.json` with the 11 findings.                          |
| [`formal_verification/`](formal_verification/) | Alloy 6 and TLA⁺ models of the three findings the paper details, with our logs and a runner that checks its own verdicts.                          |
| [`PSM_Benchmark/`](PSM_Benchmark/)             | The FSM-extraction stages measured against PSMBench: 14 RFC protocols, 9 shipped baselines, and our result tables.                                 |
| `requirements.txt`, `setup_venv.sh`            | Python dependencies and environment setup.                                                                                                         |

Git ignores `.env`, `.venv/`, `PSM_Benchmark/eval_workspace/`, and `RFC_PSM_Benchmark/`, the PSMBench clone the harness reads.

---

## 🚀 How to run

### Setup (once)

You need Python 3.11+ and an Anthropic API key. Graphviz (`dot`) matters only for FSM figures, and Java 11+ only for formal verification.

```bash
./setup_venv.sh && source .venv/bin/activate
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > src/.env
```

### 1. Pipeline (`src/`)

Run the scripts from inside `src/`. They read `config.yaml` and `.env` there and write to `src/outputs/`.
A fresh run rebuilds the whole chain from the live specification. To start from the published
artifacts instead, copy them in first with `cp -r ../outputs ./outputs`.

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

[`src/README.md`](src/README.md) covers retry passes, the zero-shot baseline, and each stage in detail.

### 2. Rendering the FSM

```bash
cd src
python FSM_to_DOT.py --unified --input ../outputs/stage_b4/unified_fsm_llm_dedup.json --pdf
python FSM_to_DOT.py --stage discovery             # one phase, from outputs/stage_b2/
```

### 3. Formal verification (`formal_verification/`)

The script downloads Alloy and TLC, verifies their checksums, runs each check, and compares the verdicts with what we expect. It writes `results/` only when all of them agree.

```bash
cd formal_verification
./run_all.sh
```

### 4. PSMBench comparison (`PSM_Benchmark/`)

The harness needs a read-only clone of PSMBench at commit `df0bce6`, placed at `RFC_PSM_Benchmark/` in the repo root.
It also looks for the pipeline in its parent directory. Since the code now lives in `src/`, point `PIPELINE_DIR` in `runner.py` there before running.

```bash
cd PSM_Benchmark
python3 main.py prepare-all                        # inputs, configs, prompt checks
python3 main.py run --protocol TCP --workers 6     # one protocol
python3 main.py run-many --protocols SIP BGP --workers 6
python3 main.py eval --protocol TCP                # re-score with the benchmark evaluator
python3 main.py eval-baselines                     # the 9 shipped baselines
python3 main.py summarize                          # results/*.md|csv
```

---

## 🔬 Where the paper's results are

- The findings are in `outputs/final_verified.json`. Each one traces back to a C1 candidate, its C2 verification record, and the FSM in `outputs/stage_b4/unified_fsm_llm_dedup.json`. The baseline that skips the FSM is in `outputs/zero_shot_run*/`.
- The PSMBench tables are in `PSM_Benchmark/results/`.
- The formal verification logs are in `formal_verification/results/`.

# A2ABreak Pipeline

This directory holds the stage scripts, the shared modules, and `config.yaml` with the models,
prompts, and output paths. Run the scripts from here. They read `config.yaml` and `.env` from
this directory and write to `src/outputs/`. The published artifacts live in
[`../outputs/`](../outputs/). To resume from them, copy them in with `cp -r ../outputs ./outputs`.

## Stages

| Stage | Script | What it does | Output (under `outputs/`) |
|---|---|---|---|
| A1 | `stage_a1.py` | Extracts the spec's structure: states, field constraints, and implicit transitions (D- IDs). Checkpoints per section. | `stage_a1/final.json` |
| A2 | `stage_a2.py` | Extracts behavior: each RFC 2119 sentence becomes a transition (N- IDs). | `stage_a2/final.json` |
| Merge | `merge_stage_a12.py` | Deduplicates A1 and A2 by exact and fuzzy match. An LLM settles the ambiguous cases. Review `needs_review.json`. | `stage_a12/final.json` |
| Verify | `verify_stage_a.py` | Rechecks each statement against the spec text and applies corrections. Review `final_report.json`. | `verify_stage_a12/final.json` |
| B1 | `stage_b1.py` | No LLM. Keeps the FSM-relevant statements and splits them into six protocol phases. | `stage_b1/<phase>.json` |
| B2 | `stage_b2.py` | Builds one FSM per phase with Opus and web search. `--stage <phase>` runs a subset. | `stage_b2/<phase>.json` |
| B3 | `stage_b3.py` | One call that finds the transitions crossing between the six B2 FSMs. | `stage_b3/inter_stage.json` |
| B4 | `stage_b4.py` | No LLM. Merges B2 and B3 into the raw unified FSM. | `stage_b4/unified_fsm.json` |
| D1 | `stage_d1.py` | No LLM. Mechanical cleanup and validation. Flags what needs semantic judgment. | `stage_b4/unified_fsm_deterministic.json` |
| D2 | `stage_d2.py` | LLM cleanup of meta-states, absence states, and orphans, using the `fsm_dedup` prompt. | `stage_b4/unified_fsm_llm_dedup.json` |
| C1 | `stage_c1.py` | Searches the deduplicated FSM for vulnerabilities that survive full compliance. `--retry` excludes what C2 already verified. | `stage_c1*/candidates.json` |
| C2 | `stage_c2.py` | Verifies each C1 candidate adversarially: FSM trace, compliance, normative rules, then severity. | `stage_c2*/verified.json` |
| Final | manual | We curate the findings that survive C2. | `final_verified.json` |

A transition carries `from_state`, `pre_cond`, `post_cond`, `to_state`, and `trigger`. The six
phases are discovery, authentication, initiation, task_execution, interruption, and termination.

Four more files live here. `FSM_to_DOT.py` renders an FSM with Graphviz and needs `dot` on the
path. `zero_shot.py` is the single-call baseline that skips the FSM. `fetcher.py` fetches and
splits the spec, and `model_interface.py` wraps LiteLLM with cost tracking.

## Requirements

Python 3.11+ and the packages in `../requirements.txt`: `litellm`, `pyyaml`, `requests`, and
`python-dotenv`. `setup_venv.sh` installs them. You also need an Anthropic API key in `.env`, and
Graphviz (`dot`) if you want figures from `FSM_to_DOT.py`.

## Running

```bash
# from the repo root, once
./setup_venv.sh && source .venv/bin/activate
cd src && echo 'ANTHROPIC_API_KEY="sk-ant-..."' > .env

python stage_a1.py
python stage_a2.py
python merge_stage_a12.py                          # review needs_review.json
python verify_stage_a.py                           # review final_report.json
python stage_b1.py
python stage_b2.py                                 # or: --stage discovery [--no-resume]
python stage_b3.py
python stage_b4.py
python stage_d1.py outputs/stage_b4/unified_fsm.json outputs/stage_b4/unified_fsm_deterministic.json
python stage_d2.py --force                         # D1 may exit non-zero when D2 work remains

python FSM_to_DOT.py --unified --input outputs/stage_b4/unified_fsm_llm_dedup.json --pdf

python stage_c1.py --input outputs/stage_b4/unified_fsm_llm_dedup.json --out outputs/stage_c1_run1/candidates.json
python stage_c2.py --fsm-input outputs/stage_b4/unified_fsm_llm_dedup.json --out outputs/stage_c2_run1/verified.json

# retry discovery excluding what C2 already verified, then verify the new candidates
python stage_c1.py --retry --prompt-key stage_c1_retry_v2 --input outputs/stage_b4/unified_fsm_llm_dedup.json \
  --out outputs/stage_c1_retry_v2/candidates.json --no-resume
python stage_c2.py --fsm-input outputs/stage_b4/unified_fsm_llm_dedup.json \
  --c1-input outputs/stage_c1_retry_v2/candidates.json --out outputs/stage_c2_run2/verified.json --no-resume

python zero_shot.py --out-dir outputs/zero_shot_run1   # baseline
```

By default C2 merges all of `outputs/stage_c1_run*/candidates.json`. Pass `--c1-input` to choose
specific files, and give each run its own `--out` path to keep them apart.

## Configuration

`config.yaml` holds the default model, the per-stage overrides in `model.stage_models` and
`model.stage_effort`, the web-search context size, the spec URL in `fetcher.url`, the output base
directory in `output.base_dir`, and the system prompts under `system_prompts.<stage>`. The API key
comes from `.env` alone.

## Resuming

A1, A2, and Verify checkpoint after each section and skip finished ones when rerun. B2, B3, C1,
and C2 skip an existing valid output unless you pass `--no-resume`. B4 and D1 overwrite their
output on each run. D2 overwrites only with `--force`. Merge and B1 run in a single pass.

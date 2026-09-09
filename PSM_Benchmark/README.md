# A2ABreak on PSMBench

This harness runs the A2ABreak extraction stages (A1 and A2, merge, verify, then FSM synthesis)
over the 14 RFC protocols in PSMBench, using one configuration for all of them. It scores the
output with the benchmark's own evaluator, `eval_fsm_sim.py` at threshold 0.5 with
`if_partial=False`, and places the result beside the 9 baseline LLM outputs the benchmark ships.

## Requirements

- Python 3.11+ with the pipeline packages from the repo's `requirements.txt`, and an Anthropic API key in the pipeline's `.env`.
- A read-only clone of PSMBench at commit `df0bce6`, placed at `../RFC_PSM_Benchmark/`. The harness never writes to it.
- The scorer's packages from the clone's own `requirements.txt`, mainly `sentence-transformers`, `numpy`, `pandas`, `matplotlib`, and `seaborn`. The evaluator loads a MiniLM sentence-embedding model on first use.

```bash
pip install -r ../RFC_PSM_Benchmark/requirements.txt   # after placing the clone
```

One caveat: `runner.py` looks for the pipeline modules in its parent directory. With the code in
`../src/`, point `PIPELINE_DIR` there before running.

## Results (PSMBench evaluator, macro over 14 protocols)

| System | States F1 | Transitions P | Transitions R | Transitions F1 |
|---|---|---|---|---|
| **A2ABreak** | 0.714 | 0.212 | **0.481** | 0.275 |
| deepseek-chat | 0.757 | 0.356 | 0.415 | 0.367 |
| deepseek-reasoner | 0.699 | 0.342 | 0.409 | 0.354 |
| claude-3-7-sonnet-20250219 | 0.559 | 0.211 | 0.356 | 0.249 |
| qwen3:32b | 0.649 | 0.292 | 0.120 | 0.168 |
| gemini-2.0-flash | 0.513 | 0.116 | 0.376 | 0.167 |
| gpt-4o-mini | 0.436 | 0.139 | 0.226 | 0.155 |
| qwq / gemma3:27b / mistral-small3.1 | 0.532 / 0.481 / 0.378 | 0.21 / 0.10 / 0.10 | 0.07 / 0.12 / 0.10 | 0.104 / 0.103 / 0.089 |

The baseline rows are the FSMs that ship with PSMBench, scored by the same evaluator on the same
ground truth. A2ABreak ranks second on states and has the highest transition recall of the ten
systems. Its precision is low for a structural reason: it emits the specification's full
per-state behaviour, 804 edges in total, while the diagram-level ground truths contain 297.
Per-protocol numbers are in `results/summary.md`, `results/per_protocol.csv`, and
`results/baselines_comparison.csv`. The evaluator's state matcher is many-to-one, so a few
baselines score precision above 1.0. `results/metric_caveat_precision_gt_1.json` lists them, and
we report the evaluator's numbers as they come.

## Supplementary: matching by meaning

PSMBench matches transitions by the embedding similarity of their labels. That misses
abbreviations such as `ESTAB` against `ESTABLISHED`, byte codes, and state names the annotators
invented. To measure recall of meaning, we ask an LLM judge (Claude Opus 4.6 at temperature 0,
with the prompt frozen in `results/supplementary/judge/`) to align our full export with the full
ground truth one-to-one, counting matches at confidence 0.6 or above. We judge our output only.

| | String matcher | By meaning |
|---|---|---|
| Ground-truth states found | 99 / 108 | 98 / 108 |
| Ground-truth transitions found | 147 / 297 (49%) | 211 / 297 (71%) |

As a second check, we rename each judge-matched label to the ground truth's wording, add or remove
nothing, and rescore with the unmodified evaluator. Transitions then reach P/R/F1 of
0.318 / 0.701 / 0.408 (`results/supplementary/normalized/`). The official numbers above stay as
they are.

## Method and fairness constraints

- We use the stage scripts and `config.yaml` unchanged. Adaptation consists of a generated per-protocol config and reversible runtime patches in `patches.py`. We import `eval_fsm_sim.py` as shipped.
- The only protocol-specific input the model sees is the protocol name. No RFC number, scope hint, event vocabulary, or section pointer reaches a prompt, and the 14 rendered prompt sets are identical once the name is masked. `prepare` checks this.
- The benchmark's segment text is the only input. We split it at column-0 numbered RFC subheadings into spans whose concatenation matches the segment byte for byte, 1489 chunks in all.
- Only `evaluation.py` reads the ground truth. No extraction, synthesis, or export step can see it.
- There is no web search, no export filtering, and full Stage A verification with corrections applied. A statement whose verification cannot be parsed is dropped and listed in the manifest, which affects at most 1% of statements.
- The synthesis prompt keeps PSMBench's formatting conventions (short state names, the event prefixes `receive`, `send`, `timeout`, and `cond`, and actions of a verb plus at most four words) but drops the shipped examples, because those coincide with FTP and POP3 ground-truth labels.
- The models match the paper: Sonnet 4.6 for extraction, merge, and verification, and Opus 4.6 for synthesis.
- Prompts are frozen by hash. A run's `run_key` digests its input text, rendered prompts, model settings, and pipeline file hashes, and `manifest.json` records the models, effort, request hashes, and costs.

## Commands

```bash
python3 main.py prepare-all                                 # inputs, configs, invariants, run keys
python3 main.py run      --protocol TCP --workers 6
python3 main.py run-many --protocols SIP BGP --workers 6
python3 main.py eval     --protocol TCP                     # re-score with the benchmark evaluator
python3 main.py eval-baselines                              # 9 baselines x 14 protocols
python3 main.py summarize                                   # results/*.md|csv
```

Runs resume per section. Pass `--rerun` to recompute.

## Layout

```
main.py runner.py chunking.py prompts.py patches.py concurrency.py evaluation.py summarize.py anonymity.py
protocols/<P>/input/            segments.md, chunk_map.json, <P>_config.yaml, prompts/rendered.json
protocols/<P>/output/<run_key>/ manifest.json, eval.json, fsm/, prompts/, stage artifacts
results/                        summary.md, per_protocol.csv, baselines_comparison.csv, costs.csv, supplementary/
tests/                          offline harness (no API calls)
```

The 14 runs cost $265.28 and the semantic judge $2.02, itemized in `results/costs.csv`.

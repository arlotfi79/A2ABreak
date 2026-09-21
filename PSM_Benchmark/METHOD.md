# METHOD — fairness constraints for the PSMBench comparison

Nine source files under `PSM_Benchmark/` cite this document: `runner.py` (lines 7, 1738, 2066), `chunking.py` (9, 171), `evaluation.py` (7), `patches.py` (5), `prompts.py` (8, 651) and `summarize.py` (276). It was absent from the repository and has been restored here from the constraints stated in `PSM_Benchmark/README.md` and in those docstrings, keeping the section numbering the code refers to. The authors have confirmed its contents, including the §5 pre-registration statement that `summarize.py` prints alongside the supplementary results.

---

## §1 Scope and configuration

The harness runs the A2ABreak extraction stages — A1 and A2, merge, verification, then FSM synthesis — over the fourteen RFC protocols in PSMBench using **one untailored configuration for all of them**:

| Stage | Model | Effort | Web search |
|---|---|---|---|
| A1 + A2 | Sonnet | from config | no |
| merge A1/A2 | Sonnet | `low` (fixed in the parent pipeline) | no |
| verify Stage A | Sonnet | `low` (fixed in the parent pipeline) | no |
| B0 filter/slim | none — local | — | — |
| B synthesis | Opus | `high` (fixed in `stage_b2`) | **no** |
| export | none — local | — | — |

The models match the paper: Sonnet 4.6 for extraction, merge and verification, Opus 4.6 for synthesis.

Adaptation of the parent pipeline consists of a generated per-protocol config and reversible runtime patches in `patches.py`. Not one byte of the parent pipeline's stage logic is modified. `eval_fsm_sim.py` is imported from the benchmark clone exactly as shipped.

Web search is disabled throughout, unlike the A2A analysis in the paper. The benchmark's segment text is the only input.

## §2 The only model-visible protocol parameter is the protocol name

No RFC number, scope hint, event vocabulary, state-name list or section pointer reaches a prompt. The fourteen rendered prompt sets are byte-identical once the protocol name is masked, and `main.py prepare-all` verifies this rather than asserting it: `prompts.py` raises if any prompt differs between two protocols beyond the name substitution.

`anonymity.py` additionally scrubs home directories and user identities from logs, prints and child-process tracebacks, and sets `PYTHONDONTWRITEBYTECODE=1` so `.pyc` files cannot embed absolute build paths.

## §3 Chunking

The benchmark's segment text is split at column-0 numbered RFC subheadings into spans whose concatenation reproduces the segment byte for byte — 1489 chunks across the fourteen protocols. The rule is identical for all of them and uses no protocol-specific knowledge.

## §4 Ground truth is isolated

Only `evaluation.py` reads ground truth. No extraction, synthesis or export step can see it, and none receives it as input. The benchmark clone is read-only: the evaluator symlinks into it rather than copying, `sys.dont_write_bytecode` keeps it free of `__pycache__`, and `runner.py:247` aborts the run if the clone is dirty.

Scoring uses the benchmark's own entry points at its own settings — `eval_fsm_sim.py` at threshold 0.5 with `if_partial=False` — and the resulting numbers are reported as they come. The evaluator's state matcher is many-to-one, which lets a few baselines score precision above 1.0; `results/metric_caveat_precision_gt_1.json` lists them rather than correcting them.

## §5 Supplementary analyses are fixed in advance and reported separately

Two supplementary analyses accompany the official numbers. Both were specified before any result was inspected, and **neither alters the official PSMBench numbers**:

1. **Semantic alignment by LLM judge.** PSMBench matches transitions by embedding similarity of their labels, which misses abbreviations (`ESTAB` against `ESTABLISHED`), byte codes and annotator-invented state names. An LLM judge aligns our export against the ground truth one-to-one, counting matches at confidence ≥ 0.6. The prompt is frozen in `results/supplementary/judge/`. **We judge our own output only** — no baseline is rescored this way, so the comparison is never tilted in our favour.
2. **Label normalization.** Each judge-matched label is renamed to the ground truth's wording — nothing added, nothing removed — and rescored with the unmodified evaluator (`results/supplementary/normalized/`).

Each analysis is reported in its own section so a reader can accept or discard it on its own terms. `summarize.py` emits this note verbatim beside them.

## §6 Prompts are frozen; the re-run rule keys on the template hash

`prompts.template_sha256()` hashes the raw prompt builders *before* protocol-name substitution. This protocol-independent number is what the paper freezes and what the re-run rule keys on; the rendered per-protocol hash necessarily differs between protocols and is recorded separately.

A run's `run_key` digests its input text, rendered prompts, model settings and pipeline file hashes. A run is re-executed when and only when its `run_key` changes; `manifest.json` records the models, effort, request hashes and costs of every run. Runs resume per section, and `--rerun` forces recomputation.

## §7 Export carries no post-hoc filtering

The synthesis output is exported to the benchmark's four-field FSM format with **no post-hoc filtering** (`runner.py:1738`). Nothing is dropped to improve a score.

This is the direct cause of the low transition precision in the official results: the pipeline emits the specification's full per-state behaviour, 804 edges across the fourteen protocols, while the diagram-level ground truths contain 297. The synthesis prompt follows PSMBench's formatting conventions — short state names, the event prefixes `receive`, `send`, `timeout`, `cond`, and actions of a verb plus at most four words — but drops the shipped examples, because those coincide with FTP and POP3 ground-truth labels.

## §8 Verification is mandatory

Stage A verification cannot be skipped. `runner.py:2066` refuses `--skip verify` outright. A statement whose verification output cannot be parsed is dropped and listed in the manifest; this affects at most 1% of statements, and `MAX_UNVERIFIABLE_FRACTION` in `runner.py` enforces that ceiling — a run that loses more is not trustworthy enough to report.

---

## Relation to the paper

The paper benchmarks **TCP only** (Table 1). The fourteen-protocol comparison in this directory is supplementary material that goes beyond it, and `../CLAIMS.md` labels it as such.

The two do not currently agree. Table 1 reports 25 extracted TCP transitions with 19 matched; this harness extracts 75 and matches 15. The constraints above explain why this setup is the less favourable of the two — one uniform configuration, no web search, no export filtering, full per-state behaviour — but they do not by themselves account for the discrepancy, which is documented as an open item in `../CLAIMS.md`, claim 2.

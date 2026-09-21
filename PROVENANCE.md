# Data provenance

This is the file referenced by `provenance` in `metadata.toml`. The artifact is code *and* data: alongside the pipeline it ships a derived statement corpus, a set of finite-state machines, vulnerability analysis traces, and fourteen protocol runs against an external benchmark. This describes where each came from.

---

## What the datasets are

| Dataset | Location | Size | Derived from |
|---|---|---|---|
| Statement corpus | `outputs/stage_a1/`, `stage_a2/`, `stage_a12/`, `verify_stage_a12/` | 929 verified statements | A2A specification |
| Per-phase FSMs | `outputs/stage_b1/`, `stage_b2/`, `stage_b3/` | 6 phase FSMs + 15 inter-stage edges | statement corpus |
| Unified FSMs | `outputs/stage_b4/` | 3 successive models, final 37 states / 76 transitions | per-phase FSMs |
| Vulnerability traces | `outputs/stage_c1_*/`, `stage_c2_*/` | 17 candidates, 16 accepted, 1 rejected | unified FSM |
| Validated findings | `outputs/final_verified.json` | 11 findings | expert curation of C2 output |
| Zero-shot baseline | `outputs/zero_shot_run1/`, `run2/` | 9 candidates | A2A specification, no FSM |
| PSMBench runs | `PSM_Benchmark/protocols/<P>/` | 14 protocols | PSMBench RFC segments |
| PSMBench scores | `PSM_Benchmark/results/` | macro + per-protocol tables | the above, scored by the benchmark's evaluator |

---

## Source material

**The A2A specification.** Every statement, state and transition derives from the public Agent2Agent protocol specification, version **v1.0.0** (`https://a2a-protocol.org/v1.0.0/specification/`), an open standard governed by the Linux Foundation. `src/fetcher.py` retrieves it and splits it into a section hierarchy at `###` boundaries, merging `####` subsections below a 200-word threshold. No login, licence or special access is involved.

**PSMBench.** The fourteen protocol runs read cleaned RFC specification segments and manually validated ground-truth state machines from [PSMBench](https://github.com/Zilinlin/RFC_PSM_Benchmark), pinned at commit `df0bce6` ("Remove hardcoded API key from output_parser_fsm.py", 2025-12-17). PSMBench is third-party work by its own authors, licensed Apache 2.0; it is referenced at a pinned commit rather than redistributed here, and this artifact makes no modification to it. The clone is read-only throughout: `PSM_Benchmark/evaluation.py` imports the benchmark's own `eval_fsm_sim.py` and symlinks into the clone rather than copying, and `runner.py:247` aborts if the clone is dirty. Only `evaluation.py` can see ground truth — no extraction, synthesis or export step has access to it.

Neither source involves human subjects, personal data, proprietary material or anything requiring an ethics approval. See `ETHICS.md`.

---

## How the data was produced

**Specification version: A2A v1.0.0**, retrieved 2026-05-13.

For evaluators and future readers, the stable citable source is:

```
https://a2a-protocol.org/v1.0.0/specification/
```

Establishing this took some care, because `src/config.yaml` sets `fetcher.url` to `https://a2a-protocol.org/latest/specification/`, and on that site `latest` is an **alias for the `dev` branch**, not for a tagged release (see `https://a2a-protocol.org/versions.json`). The fetched text is not retained in the artifact and the section records carry no version string, so the version was identified from three independent lines of evidence:

1. **Structure.** The analysed document has 14 top-level sections, an Appendix A "Migration & Legacy Compatibility", an Appendix B "Relationship to MCP", a §13 "Security Considerations" and a §14 "IANA Considerations". The v1.0.x specification matches this exactly (103 headings, 14 top-level). v0.3.0 has 11 top-level sections, no migration appendix and no §13 or §14, so the analysed document is definitively from the v1.0 family.
2. **Date.** Stage A1 fetches the specification at the start of its run, and its first section record is dated 2026-05-13 00:12 local time (America/Indiana/Indianapolis, UTC−4 in May).
3. **Release timeline.** v1.0.0 was published 2026-03-12 and v1.0.1 on 2026-05-28. The fetch therefore falls after v1.0.0 and two weeks *before* v1.0.1 existed.

17 of the 18 specification sections cited in the paper and in `formal_verification/README.md` are present in v1.0.0 under the same numbering.

**One caveat, stated precisely.** Because `/latest/` served `dev` rather than a tag, the analysed text is v1.0.0 **plus whatever had landed on the dev branch by 2026-05-13** — not necessarily byte-identical to the v1.0.0 tag. v1.0.0 and v1.0.1 have identical heading structure, so structure alone cannot separate them; the release dates do. Anyone reproducing Stage A should pin to the versioned URL above rather than `/latest/`, which now serves a much later document.

**Run timeline.** Stage-by-stage, reconstructed from the artifact:

| Stage | Completed |
|---|---|
| A1 structural extraction | 2026-05-13 03:13 |
| A2 behavioural extraction | 2026-05-13 16:51 |
| A4 verification | 2026-05-14 20:07 |
| A3 merge | 2026-05-15 17:09 |
| B2 per-phase FSMs | 2026-05-18 04:04 |
| C1 discovery, run 1 | 2026-05-18 18:05 |
| C2 verification, run 1 | 2026-05-20 10:24 |
| D2 semantic dedup | 2026-05-22 20:58 UTC |
| Zero-shot baseline | 2026-05-22 05:53 and 06:05 UTC |
| Final curated findings | 2026-05-25 18:48 |

Three of these are embedded in the artifact as UTC timestamps inside the cost records (D2 and the two zero-shot runs). The rest are filesystem modification times from the original working copy, which agree with the embedded three and run in pipeline order — strong corroboration, though mtimes are evidence rather than proof.

The repository was first committed on 2026-09-09.

To reproduce Stage A against the same source, set `fetcher.url` in `src/config.yaml` to the versioned
URL `https://a2a-protocol.org/v1.0.0/specification/` rather than the default `/latest/`. `config.yaml`
also documents a `spec/specification.md` local-file alternative for running against a fixed snapshot.

**Models.** Per `src/config.yaml`:

| Stage | Model | Reasoning effort |
|---|---|---|
| A1, A2, merge, verify | `anthropic/claude-sonnet-4-6` | high |
| B2, B3, D2, C1, C2 | `anthropic/claude-opus-4-6` | high |
| zero-shot baseline | `anthropic/claude-opus-4-6` | high |

`max_tokens` 64000, `request_timeout_seconds` 1800, web search enabled for B2, B3, C1, C2 and the baseline at `search_context_size: high`. B1, B4 and D1 make no model calls at all.

**Pipeline.** Stage A extracts structural statements (A1, `D-` IDs) and behavioural RFC 2119 statements (A2, `N-` IDs) in two independent passes, reconciles them, and verifies each against the specification text, yielding 929 verified statements. Stage B filters to the 283 that carry transition structure, builds six per-phase FSMs, resolves 15 inter-stage transitions, and merges them; D1 and D2 then deduplicate mechanically and semantically to 37 states and 76 transitions. Stage C searches that model for vulnerabilities surviving full compliance and verifies each adversarially. `src/README.md` documents every stage; claim 4 in `CLAIMS.md` checks the counts.

**Human checkpoints.** Three points are curated rather than automated, and the artifact records the outcome rather than the deliberation:

1. `outputs/stage_a12/needs_review.json` — ambiguous merges resolved by hand.
2. `outputs/verify_stage_a12/final_report.json` — verification corrections reviewed before use.
3. `outputs/final_verified.json` — the 11 findings kept from the 16 Stage C2 accepted, after expert review identified 1 duplicate and 4 false positives.

**Cost.** One end-to-end A2A run cost $40.97, itemized per stage in `outputs/*/*_cost.json` and checked by claim 6. The fourteen PSMBench runs cost $265.28 and the semantic judge pass $2.02, itemized in `PSM_Benchmark/results/costs.csv`.

---

## Integrity and reproducibility aids

- **Prompts are frozen by hash.** A PSMBench run's `run_key` digests its input text, rendered prompts, model settings and pipeline file hashes; `manifest.json` records models, effort, request hashes and costs.
- **Fairness is enforced, not asserted.** The only protocol-specific input reaching a prompt is the protocol name — no RFC number, scope hint, event vocabulary or section pointer. `main.py prepare-all` checks that the fourteen rendered prompt sets are identical once the name is masked. `PSM_Benchmark/METHOD.md` states the full rule set.
- **Determinism.** LLM stages are not bit-reproducible; re-running produces similar but not identical output. The mechanical stages (B1, B4, D1) and the whole evaluation path are fully deterministic, which is why `scripts/verify_claims.py` recomputes from shipped outputs rather than re-running.
- **Known artefact.** Twenty-one files under `outputs/` record absolute paths from the authoring machine in provenance fields such as `c1_sources`. They document which input file a run consumed; nothing reads them back.

---

## Reuse

Everything is MIT licensed and reusable. The statement corpus and unified FSM are the pieces most likely to be useful on their own — they are the first formal model of the complete A2A interaction lifecycle, and `outputs/stage_b4/unified_fsm_llm_dedup.json` is self-contained JSON with `source_ids` on every state and transition tracing back to the specification statements that produced it.

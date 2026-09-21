# Requirements and infrastructure

This is the file referenced by `infrastructure_resources` in `metadata.toml`. It states what the artifact needs, what it costs, and how long it takes.

**Short version:** tiers 0 and 1 — which verify all seven paper claims — need Python 3.11+, about 400 MB of disk, no API key, no GPU, no special hardware, and under fifteen minutes. Everything beyond that is optional.

The artifact itself is 39 MB unpacked (25 MB as a zip). The 400 MB figure is almost entirely the Python virtual environment that `scripts/install.sh` builds.

---

## Hardware

No special hardware. No GPU. No accelerator, FPGA, SGX or other trusted-execution requirement.

| | Tiers 0–1 | Tier 2 | Tier 3 |
|---|---|---|---|
| CPU | any x86-64 or arm64 | 4+ cores useful for `--workers` | 8+ cores |
| RAM | 4 GB | 8 GB | 16 GB |
| Disk | 400 MB | 2.5 GB | 3 GB |
| Network | only for the Alloy jar | yes | yes |

Tier 2's disk requirement is dominated by the PSMBench scorer stack: `sentence-transformers` pulls in PyTorch (the shipped `eval.json` fingerprint records torch 2.12.0), plus a ~90 MB MiniLM model. The PSMBench clone itself is only ~30 MB.

The published artifact is **39 MB unpacked across 19,539 files** (25 MB as a zip); the bulk is the PSMBench per-protocol runs under `PSM_Benchmark/protocols/`. No single file exceeds 5 MB, so archiving to Zenodo is unconstrained. A working checkout grows to roughly 450 MB once `install.sh` has built the virtual environment (255 MB) and `run_all.sh` has downloaded the Alloy jar (20 MB); neither is part of the artifact.

**Verified on macOS 26.5.1 (arm64).** That is the only environment we have run it in. The artifact is pure Python plus an optional JVM for the supplementary formal verification, with no platform-specific code, compiled extensions or OS-specific calls, so it should run on any machine with Python 3.11+ — but we have not tested Linux or Windows and make no claim about them.

---

## Software

### Required

- **Python 3.11 or newer.** The pipeline uses PEP 604 unions (`str | None`) and `tomllib`. `scripts/install.sh` checks the version and refuses to continue below 3.11.
- The four packages in `requirements.txt`: `requests`, `pyyaml`, `litellm`, `python-dotenv`. `setup_venv.sh` installs them into `.venv/`.

### Optional, by component

| Tool | Needed for | Without it |
|---|---|---|
| **Java 11+** | `formal_verification/run_all.sh` | Supplementary component skipped. No claim affected. |
| `curl`, `shasum` | downloading and checksumming the Alloy jar | as above |
| **Graphviz** (`dot`) | `src/FSM_to_DOT.py` figures | Figures unavailable. No claim affected. |
| `git` | PSMBench clone-cleanliness check (`runner.py:247`) | Tier 2 PSMBench runs refuse to start |
| PSMBench scorer stack | live PSMBench re-scoring | Tier 2 PSMBench only; shipped `eval.json` files are already scored |

The scorer stack (`sentence-transformers`, `numpy`, `pandas`, `matplotlib`, `seaborn`) comes from the PSMBench clone's own `requirements.txt`, not this repository's. `scripts/fetch_psmbench.sh` installs it.

`scripts/install.sh --check-only` probes all of the above and writes nothing.

---

## Credentials

One credential, and only for live re-runs: **`ANTHROPIC_API_KEY`**.

It is read from `src/.env`, or from the environment, or from `api_keys.anthropic` in `src/config.yaml` (left empty on purpose). Place it with:

```bash
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > src/.env
```

**Tiers 0 and 1 need no key.** All seven claims verify from the shipped outputs. The key matters only when re-running pipeline stages against the live API.

`src/model_interface.py` also maps `OPENAI_API_KEY`, `GEMINI_API_KEY` and `MISTRAL_API_KEY` for anyone swapping models through LiteLLM. None is needed to reproduce the paper.

---

## Network endpoints

| Endpoint | Used by | When |
|---|---|---|
| `a2a-protocol.org` (spec; analysed version is **v1.0.0**) | `src/fetcher.py` | tier 3 only — fresh spec fetch |
| Anthropic API | `src/model_interface.py` | tiers 2–3 |
| `github.com/AlloyTools/...` (Alloy 6.2.0 jar) | `formal_verification/run_all.sh` | first run only |
| PSMBench repository | `scripts/fetch_psmbench.sh` | tier 2 PSMBench |
| HuggingFace (MiniLM, ~90 MB) | PSMBench scorer | tier 2 PSMBench, first eval |

Both solver jars are pinned by SHA-256 and `run_all.sh` aborts on a mismatch:

```
Alloy 6.2.0            6b8c1cb5bc93bedfc7c61435c4e1ab6e688a242dc702a394628d9a9801edb78d   downloaded
TLA+ 2026.09.17.032053 9d36716ffb5e49d1ba8fae4651eba59f3189887e12eb90e204a42d2e6e993fef   vendored
```

The TLA⁺ jar is committed with the artifact rather than downloaded, because the upstream `v1.8.0`
tag is a rolling prerelease that replaces its asset in place — a URL plus a checksum is not a
durable pin there. Only Alloy needs network access, and only on a first run.

The artifact embeds no analytics and no tracking of any kind, in keeping with ACSAC's single-blind requirement.

---

## Runtime and cost

| Tier | What | Wall clock | API cost |
|---|---|---|---|
| 0 | `scripts/smoke_test.sh` | 5–10 min | $0 |
| 1 | `scripts/verify_claims.py` (all 7 claims) | < 5 s | $0 |
| 2a | Live Stage C1 + C2 from the shipped FSM | ~22 min | $6.30 |
| 2b | Live Stage D2 semantic dedup | ~7 min | $1.08 |
| 2c | Live PSMBench, one protocol (TCP) | ~17 min | $22.52 |
| 3a | Full A2A pipeline from the live spec | ~4.5 h | $40.97 |
| 3b | Full PSMBench, 14 protocols | ~8.5 h | $265.28 |

**Tiers 0 and 1 are the proposed evaluation path.** Together they take under fifteen minutes and cost nothing.

The tier-3 figures are measured, not estimated: `wall_clock_seconds` in each `PSM_Benchmark/protocols/<P>/output/<run_key>/manifest.json` and `elapsed_seconds` in the `outputs/*/*_cost.json` records. Ten of the fourteen PSMBench protocols carry real timings summing to 6.13 h at a 37-minute mean; the other four manifests were rewritten by a resumed run and record cache hits, so their share is extrapolated from that mean. A full reproduction is therefore about 13 hours in total — within ACSAC's one-day limit. The reason the artifact ships the outputs and verifies against them is the ~$308 of API spend, not the runtime.

Per-stage costs are in `outputs/*/*_cost.json` and `PSM_Benchmark/results/costs.csv`; claim 6 checks them against Table 2.

---

## Public research infrastructure

Tiers 0 and 1 are pure CPU with no credentials, so they run anywhere:

Nothing in the artifact requires private infrastructure, special hardware, a specific operating system, or a testbed-specific resource. Tiers 0 and 1 are CPU-only and need no credentials, so a single general-purpose node on any platform is sufficient — no allocation, reservation or topology.

What that needs in practice: **Python 3.11+**, plus `default-jre` and `graphviz` if you also want the supplementary formal verification and the FSM figures. Then:

```bash
git clone <artifact-url> A2ABreak && cd A2ABreak
bash scripts/install.sh
bash scripts/reproduce_all.sh
```

This has been run on macOS 26.5.1 (arm64). It has not been tested on a public research testbed. The artifact carries no platform-specific dependency that would make one node type preferable to another.

Tier 2 needs an Anthropic API key, which the authors supply to evaluators on request through HotCRP rather than embedding in the artifact.

---

## Known environmental caveats

- **The specification is fetched live from a moving URL.** The analysed document is **A2A v1.0.0**, retrieved 2026-05-13. But `src/config.yaml` sets `fetcher.url` to `https://a2a-protocol.org/latest/specification/`, and on that site `latest` aliases the **`dev` branch**, not a tagged release. A tier-3 run today therefore reads a much later document and will not reproduce the 929-statement corpus. Pin to `https://a2a-protocol.org/v1.0.0/specification/`, or use the `spec/specification.md` local-file fallback that `config.yaml` documents. See `PROVENANCE.md`.
- **`PSM_Benchmark` expects the pipeline at the repository root.** `scripts/link_pipeline.sh` handles this with symlinks; `scripts/install.sh` runs it. Without it, `import runner` fails with `ModuleNotFoundError`.
- **PSMBench is an external dependency, not bundled.** `scripts/fetch_psmbench.sh` clones `https://github.com/Zilinlin/RFC_PSM_Benchmark.git` at the pinned commit `df0bce6` (Apache 2.0). Only tier 2 needs it; claim 2 verifies from the `eval.json` files already shipped under `PSM_Benchmark/protocols/`.
- **Some shipped output files contain absolute paths from the authoring machine** (21 files under `outputs/`, in provenance fields such as `c1_sources`). They are recorded inputs, not executed paths — nothing reads them back — so they do not affect portability.

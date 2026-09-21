# Installation

## The short path

```bash
git clone <artifact-url> A2ABreak
cd A2ABreak
bash scripts/install.sh
bash scripts/smoke_test.sh
```

That builds a virtual environment, links the pipeline where `PSM_Benchmark/` expects it, reports which optional tools are present, and then runs the kick-the-tires checks. No API key, no cost, under fifteen minutes.

To check your machine before installing anything:

```bash
bash scripts/install.sh --check-only
```

---

## What `install.sh` does

1. **Checks Python.** Refuses to continue below 3.11 — the pipeline uses PEP 604 unions and `tomllib`.
2. **Probes optional tooling** — `java`, `dot`, `curl`, `shasum`, `git` — and reports each without failing. Missing tools disable specific components, never a paper claim.
3. **Builds the virtual environment** by calling the existing `setup_venv.sh`, which creates `.venv/` and installs `requirements.txt`.
4. **Links the pipeline** by calling `scripts/link_pipeline.sh` (see below).
5. **Places the API key** from `$ANTHROPIC_API_KEY` into `src/.env` if one is set, and otherwise says so and continues.

Options: `--check-only` probes and exits; `--with-psmbench` also fetches the PSMBench clone.

---

## The pipeline links

`PSM_Benchmark/runner.py` sets `PIPELINE_DIR` to the repository root and expects nine pipeline files to live there — it imports them through `sys.path`, reads `config.yaml` and `.env` from that directory, and hashes all nine into the run digest. The pipeline actually lives in `src/`.

`scripts/link_pipeline.sh` places relative symlinks at the root pointing into `src/`. Every root-relative access then resolves and no tracked source file changes:

```bash
scripts/link_pipeline.sh          # create
scripts/link_pipeline.sh --check  # status
scripts/link_pipeline.sh --unlink # remove
```

Earlier revisions of this repository told the reader to hand-edit `PIPELINE_DIR` in `runner.py`. That is no longer necessary and should not be done.

---

## Per-component setup

### The A2A pipeline (`src/`)

Needs only the virtual environment. An API key is required to run stages, not to inspect their outputs:

```bash
echo 'ANTHROPIC_API_KEY="sk-ant-..."' > src/.env
chmod 600 src/.env
```

Stages read `config.yaml` and `.env` from `src/` and write to `src/outputs/`. To resume from the published artifacts rather than a fresh run, copy them in first:

```bash
cd src && cp -r ../outputs ./outputs
```

`src/README.md` documents each stage, its flags, and its resume behaviour.

### Formal verification (`formal_verification/`)

Needs Java 11+, `curl` and `shasum`. Nothing to install by hand — the runner downloads the Alloy jar (the TLA⁺ jar is committed with the artifact), verifies both SHA-256s, runs all twelve checks, and writes `results/` only when every verdict matches the expected one:

```bash
cd formal_verification && ./run_all.sh
```

First run needs network access for the Alloy jar only. Supplementary: no paper claim depends on this.

### PSMBench comparison (`PSM_Benchmark/`)

Only for tier 2. Needs a read-only PSMBench clone at `RFC_PSM_Benchmark/`, pinned to commit `df0bce6`, plus the scorer stack:

```bash
scripts/fetch_psmbench.sh
```

That clones <https://github.com/Zilinlin/RFC_PSM_Benchmark> (Apache 2.0) at the pinned commit and installs the scorer stack. Override the source with `--url` if the repository moves. The harness never writes to the clone and refuses to run if it is dirty.

---

## Verifying the install

```bash
scripts/link_pipeline.sh --check                       # all nine links present
.venv/bin/python -c "import sys; sys.path.insert(0,'PSM_Benchmark'); import runner; print('ok')"
.venv/bin/python scripts/verify_claims.py --quick      # claims 1, 3, 4, 6
bash scripts/smoke_test.sh                             # everything offline
bash scripts/reproduce_all.sh                          # smoke test + all 7 claims
```

The parity harness alone is a good single check — it exercises the pipeline modules through the symlinks with no network and no API calls:

```bash
.venv/bin/python PSM_Benchmark/tests/mock_parity_test.py
```

It ends with `55/55 checks passed`.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'runner'` / `'stage_a1'`**
The pipeline links are missing. Run `scripts/link_pipeline.sh`.

**`No Anthropic API key — set api_keys.anthropic in config.yaml or export ANTHROPIC_API_KEY`**
Only live stages need one. For claim verification use `scripts/verify_claims.py`, which reads shipped outputs. Otherwise write `src/.env` as above. Leave `api_keys.anthropic` in `config.yaml` empty — the key belongs in `.env`.

**`Graphviz 'dot' not found on PATH`**
Figures only. `brew install graphviz` or `apt install graphviz`. No claim depends on it.

**`run_all.sh` exits 3 — `checksum mismatch for alloy.jar`**
Corrupted download. Delete `formal_verification/tools/alloy.jar` and retry. The pinned hash is in `formal_verification/README.md`.

**`run_all.sh` exits 3 — `checksum mismatch for tla2tools.jar`**
Should not happen: the TLA⁺ jar is committed at `formal_verification/tools/tla2tools.jar` rather than downloaded, precisely because the upstream `v1.8.0` tag is a rolling prerelease that upstream replaces in place. If you see this, the committed jar was modified or lost — restore it with `git checkout -- formal_verification/tools/tla2tools.jar`.

**`git status` is dirty after running `run_all.sh`**
Expected. A successful run republishes `formal_verification/results/` with its own logs, which carry fresh timestamps, seeds, process IDs and absolute paths. The verdicts are identical; only the surrounding log text differs. The committed copies are the authors' recorded run. Restore them with `git checkout -- formal_verification/results/`.

**`RFC_PSM_Benchmark is dirty`**
`runner.py:247` requires a clean clone because it reads ground truth from it. Run `git -C RFC_PSM_Benchmark checkout .` and retry.

**`error: <file> exists and is not a symlink`**
`link_pipeline.sh` refuses to overwrite a real file at the repository root. Move it aside and retry.

**`SyntaxWarning: "\." is an invalid escape sequence` from `fetcher.py`**
Cosmetic, pre-existing, in a docstring. Harmless.

**Python 3.10 or older**
Not supported. Install 3.11+ and re-run `scripts/install.sh`.

---

## Packaging for deposit

To build the archive for Zenodo or another permanent repository:

```bash
scripts/package_artifact.sh              # -> dist/A2ABreak-artifact.zip
scripts/package_artifact.sh --flat       # also emit docs+scripts as loose files
```

The file set comes from git, not a directory walk, so `.gitignore` decides what ships. That keeps the 255 MB `.venv/`, the downloaded `alloy.jar`, `.idea/`, `src/.env`, the `link_pipeline.sh` symlinks and the PSMBench clone out of the archive, and keeps the vendored `tla2tools.jar` in. The script refuses to build if any of that is wrong and re-checks the finished zip. Do not use `zip -r` — it would ship the virtual environment.

The virtual environment is not part of the artifact by design: it is created from `requirements.txt` by `scripts/install.sh`.

`--flat` additionally copies the documents and scripts out as loose files, for a Zenodo-only deposit where `metadata.toml`'s per-file URLs need to resolve to `https://zenodo.org/records/<id>/files/<name>` rather than GitHub.

---

## Uninstalling

```bash
scripts/link_pipeline.sh --unlink
rm -rf .venv RFC_PSM_Benchmark formal_verification/tools/alloy.jar   # keep tla2tools.jar: it ships with the artifact
rm -rf PSM_Benchmark/tests/_scratch PSM_Benchmark/eval_workspace
```

Nothing is installed outside the repository directory.

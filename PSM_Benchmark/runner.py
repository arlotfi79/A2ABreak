#!/usr/bin/env python3
"""
runner.py
---------
Protocol-generic driver for the A2ABreak FSM pipeline over PSMBench.

One untailored configuration for all 14 protocols (METHOD.md):
  Stage A1 + A2  (Sonnet, effort from config)      -> statements per chunk
  merge A1/A2    (Sonnet, reasoning_effort="low" hardcoded in the parent)
  verify Stage A (Sonnet, reasoning_effort="low" hardcoded in the parent)
  Stage B0       (local filter/slim, no model)
  Stage B synth  (Opus, effort "high" hardcoded in stage_b2, NO web search)
  export         -> the benchmark's four-field FSM JSON
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import yaml

THIS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = THIS_DIR.parent
BENCHMARK_CLONE = PIPELINE_DIR / "RFC_PSM_Benchmark"

if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import merge_stage_a12                       # noqa: E402
import stage_a1                              # noqa: E402
import stage_a2                              # noqa: E402
import stage_b2                              # noqa: E402
import verify_stage_a                        # noqa: E402
from fetcher import parse_sections           # noqa: E402

import anonymity                             # noqa: E402
import chunking                              # noqa: E402
import concurrency                           # noqa: E402
import patches                               # noqa: E402
import prompts as prompt_lib                 # noqa: E402

LOGGER = logging.getLogger("psmbench")

PARENT_FILES = (
    "stage_a1.py",
    "stage_a2.py",
    "stage_b1.py",
    "stage_b2.py",
    "merge_stage_a12.py",
    "verify_stage_a.py",
    "model_interface.py",
    "fetcher.py",
    "config.yaml",
)

MODEL_NAME = "a2a-pipeline"
FALLBACK_TEMPERATURE = 0.2
# A run may lose at most this fraction of its statements to unverifiability before the
# result stops being trustworthy enough to report.
MAX_UNVERIFIABLE_FRACTION = 0.01
EXPECTED_PROTOCOL_COUNT = 14
B_TOKEN_LIMIT = 180_000

SKIPPABLE_STAGES = {
    "stage_a1", "stage_a2", "stage_a12", "verify_stage_a12",
    "stage_b0", "stage_b1", "export", "eval",
}
SKIP_ALIASES = {
    "a1": ("stage_a1",), "a2": ("stage_a2",),
    "stage_a": ("stage_a1", "stage_a2"),
    "merge": ("stage_a12",), "verify": ("verify_stage_a12",),
    "b0": ("stage_b0",), "b1": ("stage_b1",), "b": ("stage_b1",),
    "evaluation": ("eval",),
}


class RunError(RuntimeError):
    """A hard-fail condition: never downgraded to a warning."""


class FatalApiError(RunError):
    """
    An API failure that retrying cannot fix — account quota exhausted, bad
    credentials, revoked permissions. Aborts the run immediately instead of
    burning pass-loop attempts against a wall.
    """

    def __init__(self, message: str, *, detail: str, regain_access: str | None):
        super().__init__(message)
        self.detail = detail
        self.regain_access = regain_access


_FATAL_API_PATTERNS = (
    "reached your specified api usage limits",
    "credit balance is too low",
    "invalid x-api-key",
    "authentication_error",
    "permission_error",
    "your account has been disabled",
)

_REGAIN_ACCESS_RE = re.compile(
    r"regain access on ([0-9]{4}-[0-9]{2}-[0-9]{2}(?: at [0-9:]+ ?[A-Z]*)?)",
    re.IGNORECASE,
)


def _is_fatal_api_error(text: str | None) -> bool:
    if not text:
        return False
    lowered = str(text).lower()
    return any(pattern in lowered for pattern in _FATAL_API_PATTERNS)


def _raise_if_fatal(errors: Iterable[str | None], *, where: str) -> None:
    for error in errors:
        if _is_fatal_api_error(error):
            detail = str(error).strip()
            match = _REGAIN_ACCESS_RE.search(detail)
            regain = match.group(1) if match else None
            suffix = f" Access returns {regain}." if regain else ""
            raise FatalApiError(
                f"fatal API error during {where}; retrying cannot help.{suffix} "
                f"Upstream message: {detail[:300]}",
                detail=detail,
                regain_access=regain,
            )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Anonymity: nothing written to disk may carry a filesystem path that identifies a
# machine or a person. The paper is double-blind, so every persisted path is stored
# relative to the repository root and resolved to absolute only in memory.
#
# Two different bases are unavoidable, each dictated by how the PARENT resolves it:
#   * output.base_dir is joined onto the CONFIG FILE's directory
#     (stage_a1.py:266-270), so it is written relative to that file.
#   * fetcher.url is passed to fetch_markdown, which resolves against the process
#     working directory (fetcher.py:134-139), so it is written relative to the repo
#     root and the CLI sets the working directory accordingly.
# ---------------------------------------------------------------------------

def repo_relative(path: Any) -> str:
    """Path as a repo-root-relative string; unchanged if it is outside the repo."""
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(PIPELINE_DIR))
    except ValueError:
        return str(candidate)


def repo_absolute(path: Any) -> Path:
    """Inverse of repo_relative, for use in memory only."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (PIPELINE_DIR / candidate)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    """Atomic write: a crash or a concurrent reader never sees a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_dotenv_value(key: str) -> str | None:
    env_path = PIPELINE_DIR / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY") or load_dotenv_value("ANTHROPIC_API_KEY")
    if not key:
        raise RunError(
            "ANTHROPIC_API_KEY not found in the environment or .env — merge and "
            "verify silently skip their LLM work without it (merge_stage_a12.py:1621)"
        )
    os.environ["ANTHROPIC_API_KEY"] = key
    return key


def git_state(repo: Path) -> dict[str, Any]:
    """HEAD and dirtiness only — never the checkout path or any configured identity."""
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
    return {
        "repo": repo.name,
        "head": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain")),
    }


def assert_clone_clean(stage: str) -> None:
    """Never write into the benchmark clone."""
    result = subprocess.run(
        ["git", "-C", str(BENCHMARK_CLONE), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    if result.stdout.strip():
        raise RunError(
            f"RFC_PSM_Benchmark is dirty {stage}:\n{result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Protocol discovery (mechanical, no hand data)
# ---------------------------------------------------------------------------

def discover_protocols(*, require_all: bool = True) -> dict[str, dict[str, Any]]:
    # Discovery is by SEGMENT file only. Nothing outside evaluation.py may locate,
    # require, hash or even name a *_state_machine.json: extraction must be blind to
    # the ground truth, and "the scorer is the only reader" has to be true of paths
    # as well as contents.
    found: dict[str, dict[str, Any]] = {}
    for entry in sorted(BENCHMARK_CLONE.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        segments = sorted(entry.glob("*_segments.json"))
        if len(segments) != 1:
            continue
        rfc_match = re.match(r"rfc(\d+)_segments\.json$", segments[0].name)
        found[entry.name] = {
            "protocol": entry.name,
            "segments_file": segments[0],
            "rfc_number": rfc_match.group(1) if rfc_match else None,
        }
    if require_all and len(found) != EXPECTED_PROTOCOL_COUNT:
        raise RunError(
            f"expected {EXPECTED_PROTOCOL_COUNT} protocols in {BENCHMARK_CLONE}, "
            f"found {len(found)}: {sorted(found)}"
        )
    return found


def protocol_info(protocol: str) -> dict[str, Any]:
    all_protocols = discover_protocols()
    if protocol not in all_protocols:
        raise RunError(f"unknown protocol {protocol!r}; have {sorted(all_protocols)}")
    return all_protocols[protocol]


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

def load_parent_config() -> dict[str, Any]:
    with (PIPELINE_DIR / "config.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Layout
#
#   protocols/<P>/input/             segments.md, chunk_map.json, config, prompts/
#   protocols/<P>/output/<run_key>/  the current run: manifest.json, eval.json,
#                                    RUN_KEY, prompts/, fsm/, stage directories
#   protocols/<P>/output/cost.json   cost of the current run
#   results/                         cross-protocol tables, baselines, costs
#   archive_output/                  runs superseded by a new run_key (moved, not
#                                    deleted; not part of the released results)
# ---------------------------------------------------------------------------

def protocols_dir() -> Path:
    return THIS_DIR / "protocols"


def input_dir(protocol: str) -> Path:
    return protocols_dir() / protocol / "input"


def results_dir() -> Path:
    return THIS_DIR / "results"


def archive_dir() -> Path:
    return THIS_DIR / "archive_output"


def data_dir() -> Path:
    """Chunking writes into protocols/<P>/input/; it appends the protocol itself."""
    return protocols_dir()


def config_path_for(protocol: str) -> Path:
    return input_dir(protocol) / f"{protocol}_config.yaml"


def protocol_output_dir(protocol: str) -> Path:
    return protocols_dir() / protocol / "output"


def build_config(
    *,
    protocol: str,
    info: dict[str, Any],
    chunk_stats: dict[str, Any],
    rendered: dict[str, str],
    output_base: Path,
) -> dict[str, Any]:
    parent = load_parent_config()
    cfg: dict[str, Any] = json.loads(json.dumps(parent))

    model_block = cfg.get("model") or {}
    stage_models = dict(model_block.get("stage_models") or {})
    b_model = stage_models.get("stage_b2")
    if not isinstance(b_model, str) or not b_model.strip():
        raise RunError(
            "parent config.yaml has no model.stage_models.stage_b2 — Stage B "
            "synthesis model is undefined"
        )
    stage_models["stage_b1"] = b_model
    model_block["stage_models"] = stage_models
    cfg["model"] = model_block

    cfg["fetcher"] = {
        # Relative to the repository root; the CLI runs from there.
        "url": repo_relative(input_dir(protocol) / "segments.md"),
        "merge_threshold_words": 0,
    }
    stages_base = stages_dir(output_base)
    # Relative to THIS config file, because that is what the parent joins it onto.
    relative_base = os.path.relpath(stages_base, config_path_for(protocol).parent)
    cfg["output"] = {
        "base_dir": relative_base,
        "sections_dir": os.path.join(relative_base, "sections"),
        "logs_dir": os.path.join(relative_base, "logs"),
    }
    cfg["system_prompts"] = {key: rendered[key] for key in prompt_lib.PROMPT_KEYS}
    cfg["api_keys"] = {key: "" for key in (parent.get("api_keys") or {})}
    cfg["benchmark"] = {
        "protocol": protocol,
        "rfc_number": info["rfc_number"],
        "segments_file": repo_relative(info["segments_file"]),
        "segment_count": chunk_stats["segment_count"],
        "chunk_count": chunk_stats["chunk_count"],
        "stage_label": prompt_lib.stage_label(protocol),
        "subjects": prompt_lib.subjects(protocol),
        "web_search": False,
        "error_sink_filter": False,
        "stage_b1_model_alias_of": "stage_b2",
        "prompt_template_sha256": prompt_lib.template_sha256(),
        "prompt_set_sha256": prompt_lib.prompt_set_sha256(rendered),
        "prompt_sha256": prompt_lib.prompt_hashes(rendered),
        "a1_user_template": rendered["a1_user_template"],
        "a2_user_template": rendered["a2_user_template"],
    }
    return cfg


def write_config(cfg: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=False, width=100000)


# ---------------------------------------------------------------------------
# Run namespace / digest
# ---------------------------------------------------------------------------

def wrapper_file_hashes() -> dict[str, str]:
    return {
        path.name: sha256_file(path)
        for path in sorted(THIS_DIR.glob("*.py"))
    }


def wrapper_set_sha256() -> str:
    return sha256_text(json.dumps(wrapper_file_hashes(), sort_keys=True))


def record_stage_provenance(stage_dir: Path) -> dict[str, Any]:
    """
    Append the current wrapper-code fingerprint to this stage's provenance log and
    return the latest entry. Wrapper hashes are not part of the run_key, so
    provenance is tracked per artifact: each stage records which wrapper produced
    (or last touched) it, and the history survives resumes.
    """
    path = stage_dir / "wrapper_provenance.json"
    history: list[dict[str, Any]] = []
    if path.is_file():
        try:
            loaded = read_json(path)
            if isinstance(loaded, list):
                history = loaded
        except (OSError, json.JSONDecodeError):
            history = []
    entry = {
        "wrapper_set_sha256": wrapper_set_sha256(),
        "wrapper_files": wrapper_file_hashes(),
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not history or history[-1].get("wrapper_set_sha256") != entry["wrapper_set_sha256"]:
        history.append(entry)
        stage_dir.mkdir(parents=True, exist_ok=True)
        write_json(path, history)
    return {
        "wrapper_files_at_completion": history[-1],
        "wrapper_provenance_history": [
            {"wrapper_set_sha256": h["wrapper_set_sha256"], "recorded_utc": h["recorded_utc"]}
            for h in history
        ],
    }


def parent_file_hashes() -> dict[str, str]:
    return {name: sha256_file(PIPELINE_DIR / name) for name in PARENT_FILES}


def compute_run_digest(
    *,
    protocol: str,
    markdown_sha: str,
    rendered: dict[str, str],
    model_block: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    # The run identity is what the run means: the input text, the rendered prompts,
    # the model/settings block and the parent pipeline files. Wrapper code hashes
    # are recorded in the manifest but are not part of the key, so a wrapper-only
    # change does not discard completed model work. Staleness is still caught: the
    # per-stage input sha256 checks invalidate B0 -> B -> export -> eval, and any
    # chunking or prompt change shows up in the two hashes below.
    components = {
        "protocol": protocol,
        "segments_markdown_sha256": markdown_sha,
        "prompt_sha256": prompt_lib.prompt_hashes(rendered),
        "prompt_template_sha256": prompt_lib.template_sha256(),
        "model_block": model_block,
        "parent_files": parent_file_hashes(),
    }
    digest = sha256_text(json.dumps(components, sort_keys=True, ensure_ascii=False))
    return digest, components


def run_base_dir(protocol: str, run_key: str, *, namespace: str | None) -> Path:
    """
    Where a run lives. The default namespace is the protocol's single output/
    directory; named namespaces (smoke) keep the run_key in the path because several
    may coexist and none of them is the protocol's current result.
    """
    if namespace in (None, "generic"):
        # The run_key stays in the path: it is the run's identity, resume keys on it,
        # and keeping it visible makes "which run is this?" answerable from the tree.
        return protocol_output_dir(protocol) / run_key
    return THIS_DIR / namespace / protocol / run_key


def stages_dir(run_dir: Path) -> Path:
    """Stage trees sit directly in the run directory, as the parent pipeline expects."""
    return run_dir


ARCHIVE_INDEX_ENTRIES = {
    "superseded_runs": (
        "Completed runs whose run_key is no longer the current configuration's. Not "
        "comparable to current results: a different run_key means different prompts, "
        "models, inputs or parent code. Each keeps its own manifest, eval and prompts."),
    "smoke": (
        "Truncated smoke runs (--max-sections). Their scores are meaningless by "
        "construction; they only exercise the plumbing."),
    "run_logs": (
        "Per-child logs and per-batch summaries from `run-many`: which batch ran what, "
        "when, and with what exit code. results/ holds only reportable artifacts."),
}


def write_archive_index() -> Path:
    """archive_output/INDEX.md — what each archived item is and why it is kept."""
    root = archive_dir()
    root.mkdir(parents=True, exist_ok=True)
    lines = [
        "# archive_output",
        "",
        "Nothing here is in current use, and nothing here is ever deleted. Runs are",
        "moved (never copied away) when their run_key stops matching the current",
        "configuration, so the evidence for an earlier claim always survives.",
        "",
    ]
    for entry in sorted(root.iterdir()):
        if entry.name == "INDEX.md" or not entry.is_dir():
            continue
        lines.append(f"## `{entry.name}/`")
        lines.append("")
        lines.append(ARCHIVE_INDEX_ENTRIES.get(
            entry.name, "Archived material; see the directory's own manifest files."))
        lines.append("")
        for child in sorted(entry.rglob("manifest.json")):
            try:
                data = read_json(child)
            except (OSError, json.JSONDecodeError):
                continue
            evaluation = data.get("eval") or {}
            states = (evaluation.get("states") or {}).get("F1-Score")
            transitions = (evaluation.get("transitions") or {}).get("F1-Score")
            score = ("" if states is None else
                     f" — states F1 {states}, transitions F1 {transitions}")
            lines.append(
                f"- `{child.parent.relative_to(root)}` — run_key "
                f"`{data.get('run_key')}`, prompt_template "
                f"`{str((data.get('prompts') or {}).get('prompt_template_sha256'))[:8]}`"
                f"{score}")
        lines.append("")
    path = root / "INDEX.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def superseded_dir() -> Path:
    return archive_dir() / "superseded_runs"


def archive_superseded_runs(protocol: str, *, current_run_key: str | None = None
                            ) -> list[dict[str, Any]]:
    """
    Keep exactly ONE run in protocols/<P>/output/: the newest.

    Any other namespace is moved to archive_output/superseded_runs/<P>/<run_key>/ as
    soon as a newer run_key exists — not after the successor finishes — so the tree
    never shows two candidates for "the current result". Rename, never delete: the
    superseded run keeps its manifest, eval, prompts and cost records, and a run that
    inherited stages from it still names it in stages_reused_from_an_earlier_run.
    """
    output_dir = protocol_output_dir(protocol)
    if not output_dir.is_dir():
        return []
    current = current_run_key or prepare_protocol(protocol)["run_key"]
    moved: list[dict[str, Any]] = []
    for candidate in sorted(output_dir.iterdir()):
        if not candidate.is_dir() or candidate.name == current:
            continue
        target = superseded_dir() / protocol / candidate.name
        n = 1
        while target.exists():
            target = superseded_dir() / protocol / f"{candidate.name}_{n}"
            n += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(candidate, target)
        LOGGER.info("Archived superseded run %s/%s -> %s", protocol, candidate.name,
                    target)
        moved.append({"protocol": protocol, "run_key": candidate.name,
                      "moved_to": repo_relative(target)})
    return moved

def archive_namespace(base: Path) -> Path | None:
    """--no-resume: move the namespace aside, never delete."""
    if not base.exists() or not any(base.iterdir()):
        return None
    n = 1
    while (base / f"attempt_{n}").exists():
        n += 1
    target = base / f"attempt_{n}"
    target.mkdir(parents=True)
    for item in list(base.iterdir()):
        if item.name.startswith("attempt_"):
            continue
        shutil.move(str(item), str(target / item.name))
    LOGGER.info("Archived previous attempt to %s", target)
    return target


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def prepare_protocol(protocol: str) -> dict[str, Any]:
    info = protocol_info(protocol)
    assert_clone_clean("before prepare")

    chunk_stats = chunking.prepare_protocol_text(
        protocol=protocol,
        segments_path=info["segments_file"],
        out_dir=input_dir(protocol),
    )

    rendered = prompt_lib.build_prompts(protocol)
    prompt_lib.lint_prompts(rendered, protocol=protocol)

    # Lint EVERY discovered protocol, then assert their rendered sets are identical
    # once the protocol name is masked. The negative check (no forbidden content) and
    # the positive one (no protocol got special treatment) are both hard failures.
    all_protocols = sorted(discover_protocols())
    for other in all_protocols:
        prompt_lib.lint_prompts(prompt_lib.build_prompts(other), protocol=other)
    prompt_lib.assert_cross_protocol_identical(all_protocols)

    digest, components = compute_run_digest(
        protocol=protocol,
        markdown_sha=chunk_stats["markdown_sha256"],
        rendered=rendered,
        model_block=(load_parent_config().get("model") or {}),
    )
    run_key = digest[:10]
    cfg = build_config(
        protocol=protocol,
        info=info,
        chunk_stats=chunk_stats,
        rendered=rendered,
        output_base=run_base_dir(protocol, run_key, namespace=None),
    )
    write_config(cfg, config_path_for(protocol))
    write_json(input_dir(protocol) / "prompts" / "rendered.json", {
        "protocol": protocol,
        "prompt_template_sha256": cfg["benchmark"]["prompt_template_sha256"],
        "prompt_set_sha256": cfg["benchmark"]["prompt_set_sha256"],
        "prompt_sha256": cfg["benchmark"]["prompt_sha256"],
        "prompts": rendered,
    })
    # Scoped to what THIS protocol just wrote. A whole-tree scan here would let a
    # concurrent sibling's leak abort an unrelated batch — which is exactly what
    # happened when three parents ran at once. The whole-tree check still gates
    # reporting, via `anonymity-check` and the end of run-many.
    anonymity.assert_clean(input_dir(protocol))
    assert_clone_clean("after prepare")

    stats = dict(chunk_stats)
    stats.update({
        "rfc_number": info["rfc_number"],
        "prompt_set_sha256": cfg["benchmark"]["prompt_set_sha256"],
        "prompt_template_sha256": cfg["benchmark"]["prompt_template_sha256"],
        "run_digest": digest,
        "run_key": run_key,
        "config_path": repo_relative(config_path_for(protocol)),
        "stage_label": cfg["benchmark"]["stage_label"],
    })
    return stats


# ---------------------------------------------------------------------------
# Cost aggregation (recomputed from per-call records, never trusted aggregates)
# ---------------------------------------------------------------------------

# Stage B writes the same money into one directory three ways: a run total
# (<phase>.json), one record PER TURN (<phase>_turn_NN.json) and a turns summary
# (<phase>_turns.json, which carries no top-level cost). Summing the directory blindly
# counts a single-turn call twice.
#
# The per-turn records are the authoritative unit: one file per actual request, so a
# three-turn synthesis reports three calls rather than one, and their costs sum to the
# aggregate. Where per-turn records exist we count only those; the aggregate and the
# turns summary are excluded.
_TURN_COST_FILE_RE = re.compile(r"_turn_\d+\.json$")
_TURNS_SUMMARY_RE = re.compile(r"_turns\.json$")


def _cost_files(cost_dir: Path, skip: Iterable[str] = ()) -> list[Path]:
    """The per-call cost records in a stage directory, without double counting."""
    if not cost_dir.is_dir():
        return []
    skip_names = set(skip)
    files = [p for p in sorted(cost_dir.glob("*.json"))
             if p.name not in skip_names and not _TURNS_SUMMARY_RE.search(p.name)]
    per_turn = [p for p in files if _TURN_COST_FILE_RE.search(p.name)]
    if per_turn:
        # Stage B: the turn records ARE the calls; drop the run aggregate.
        return per_turn
    return files


def aggregate_cost_dir(cost_dir: Path, *, skip: Iterable[str] = ()) -> dict[str, Any]:
    skip_names = set(skip)
    calls = 0
    tokens = 0
    cost = 0.0
    if cost_dir.is_dir():
        for path in _cost_files(cost_dir, skip_names):
            try:
                record = read_json(path)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            calls += 1
            tokens += int(record.get("total_tokens") or 0)
            cost += float(record.get("cost_usd") or 0.0)
    return {"llm_calls": calls, "total_tokens": tokens, "cost_usd": round(cost, 6)}


# ---------------------------------------------------------------------------
# Stage integrity checks
# ---------------------------------------------------------------------------

def expected_sections(cfg: dict[str, Any], max_sections: int | None) -> list[Any]:
    markdown = Path(cfg["fetcher"]["url"]).read_text(encoding="utf-8")
    sections = parse_sections(markdown, merge_threshold_words=0)
    return sections[:max_sections] if max_sections else sections


def missing_stage_a_sections(stage_dir: Path, sections: list[Any]) -> list[str]:
    """Sections with no per-section artifact — the only terminal Stage A failure."""
    sections_dir = stage_dir / "sections"
    return [
        s.section_id for s in sections
        if not stage_a1.section_output_path(sections_dir, s).is_file()
    ]


def stage_a_section_errors(stage_dir: Path) -> list[dict[str, Any]]:
    """Every section_error line the stage ever logged, across all passes."""
    errors: list[dict[str, Any]] = []
    for log_path in sorted((stage_dir / "logs").glob("*.jsonl")):
        if not log_path.is_file():
            continue
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("event") == "section_error":
                errors.append({
                    "section_id": entry.get("section_id"),
                    "error": entry.get("error"),
                    "timestamp": entry.get("timestamp"),
                })
    return errors


def check_stage_a_complete(stage_dir: Path, sections: list[Any], label: str) -> None:
    missing = missing_stage_a_sections(stage_dir, sections)
    if missing:
        raise RunError(
            f"{label}: {len(missing)} section artifact(s) missing "
            f"(first: {missing[:5]}) — Stage A swallows per-section exceptions, "
            f"so this is a terminal failure, not an empty extraction"
        )


def verify_quality(verify_dir: Path) -> dict[str, Any]:
    """
    Correction bookkeeping. `ready_for_stage_b` is deliberately NOT a gate — it only
    means automated_issues == 0 and hallucinated < 10, and the pipeline's original
    run proceeded with it false. What IS a gate: the LLM proposing corrections that the
    parent then applies to nothing, which is what happens when the prompt's
    correction schema drifts from apply_corrections' {"was","should_be","reason"}
    contract (verify_stage_a.py:646-660).
    """
    report = read_json(verify_dir / "final_report.json")
    summary = report.get("summary", {})
    stats = {
        "corrected": int(summary.get("corrected", 0)),
        "corrections_applied": int(summary.get("corrections_applied", 0)),
        "hallucinated_removed": int(summary.get("hallucinated_removed", 0)),
        "vague": int(summary.get("vague", 0)),
        "automated_issues": int(summary.get("automated_issues", 0)),
        "ready_for_stage_b": summary.get("ready_for_stage_b"),
    }
    if stats["corrected"] > 0 and stats["corrections_applied"] == 0:
        raise RunError(
            f"verify proposed corrections for {stats['corrected']} statement(s) but "
            f"applied 0 — the correction schema no longer matches the parent's "
            f"{{was, should_be, reason}} contract (verify_stage_a.py:646-660)"
        )
    corrections_log = report.get("corrections_log", []) or []
    fsm_fields = {"from_state", "to_state", "trigger", "actions"}
    stats["corrections_by_field"] = {}
    for entry in corrections_log:
        field = entry.get("field", "?")
        stats["corrections_by_field"][field] = (
            stats["corrections_by_field"].get(field, 0) + 1
        )
    stats["corrections_touching_fsm_fields"] = sum(
        1 for e in corrections_log if e.get("field") in fsm_fields
    )
    return stats


def check_verify_coverage(
    merged_path: Path, verify_dir: Path, *, unverifiable: set[str] | None = None
) -> dict[str, Any]:
    report_path = verify_dir / "final_report.json"
    if not report_path.is_file():
        raise RunError(f"verify report missing: {report_path}")
    report = read_json(report_path)
    merged_ids = {
        s.get("statement_id")
        for s in read_json(merged_path).get("statements", [])
    }
    covered: set[str] = set()
    bad: list[str] = []
    skipped_sections: list[str] = []
    for section in report.get("sections", []):
        if section.get("skipped"):
            skipped_sections.append(str(section.get("section_id")))
            continue
        for result in section.get("results", []):
            sid = result.get("statement_id")
            covered.add(sid)
            status = result.get("status")
            existence = result.get("existence")
            if status in {"error", "unverified"} or existence == "unchecked":
                if sid in (unverifiable or set()):
                    continue      # accounted for, excluded from the FSM input below
                bad.append(f"{sid}: status={status} existence={existence}")
    uncovered = sorted(x for x in merged_ids - covered if x)
    if skipped_sections:
        raise RunError(
            f"verify skipped {len(skipped_sections)} section(s) "
            f"({skipped_sections[:5]}) — their statements were never checked"
        )
    if uncovered:
        raise RunError(
            f"verify covered {len(covered)}/{len(merged_ids)} statements; "
            f"missing {uncovered[:5]}"
        )
    if bad:
        raise RunError(f"verify left {len(bad)} statement(s) unusable: {bad[:5]}")
    return {
        "statements_covered": len(covered),
        "statements_merged": len(merged_ids),
        "llm_calls": report.get("summary", {}).get("llm_calls", 0),
    }


def check_source_sections(statements: list[dict], valid_ids: set[str]) -> dict[str, Any]:
    """Diagnostic: statements whose source_section is not one of our chunk ids."""
    bad = [
        s.get("statement_id")
        for s in statements
        if str(s.get("source_section", "")).strip() not in valid_ids
    ]
    return {"off_chunk_source_sections": len(bad), "examples": bad[:5]}


# ---------------------------------------------------------------------------
# Stage A / merge / verify
# ---------------------------------------------------------------------------

# Backoff before each Stage A / verify retry pass. ModelInterface.call and
# verify_stage_a.LLMClient.call both retry ONLY rate limits (model_interface.py:177-190,
# verify_stage_a.py:389-409); every other transient failure — connection reset,
# InternalServerError, timeout — is swallowed into an empty extraction or an
# `error` statement. These passes re-call the model for the affected sections only,
# because both stages cache per-section artifacts and resume skips the rest.
RETRY_BACKOFF_SECONDS = (30, 60, 120)


def _backoff(attempt_index: int) -> int:
    idx = min(attempt_index, len(RETRY_BACKOFF_SECONDS) - 1)
    return RETRY_BACKOFF_SECONDS[idx]


def _run_stage_a_one(
    *,
    label: str,
    stage_module: Any,
    runner_fn,
    config_path: Path,
    stage_dir: Path,
    sections: list[Any],
    passes: int,
    workers: int,
    gate: Any,
) -> dict[str, Any]:
    """Run one Stage A stage, retrying only the sections that produced no artifact."""
    attempts: list[dict[str, Any]] = []
    seen_errors: set[tuple[Any, Any]] = {
        (e["section_id"], e["timestamp"]) for e in stage_a_section_errors(stage_dir)
    }
    recovered: list[dict[str, Any]] = []

    pool_stats: list[dict[str, Any]] = []
    for attempt in range(1, passes + 1):
        if workers > 1:
            # Fill the parent's per-section cache concurrently using the parent's
            # own extract_section, then let the parent aggregate on cache hits.
            pool = concurrency.parallel_stage_a_sections(
                stage_module=stage_module,
                config_path=config_path,
                sections=sections,
                workers=workers,
                logger=LOGGER,
                gate=gate,
            )
            pool_stats.append(pool)
            if pool["cache_complete"]:
                # Proven cache-only: the parent will not issue a single request, so
                # suppressing its inter-section pause cannot disable any backoff
                # (Stage A's rate-limit sleep lives in model_interface, not here).
                with concurrency.no_inter_section_sleep(stage_module):
                    runner_fn(config_path=str(config_path), resume=True)
                pool_stats[-1]["aggregated"] = True
            else:
                # Do NOT aggregate: the parent would re-call the missing sections
                # inline and bypass this loop's backoff. Fall through to the retry.
                LOGGER.warning(
                    "%s: cache incomplete after pool (%d section(s): %s) — deferring "
                    "aggregation to the next pass",
                    label, len(pool["unusable_after_pool"]),
                    sorted(pool["unusable_after_pool"])[:5],
                )
        else:
            # resume=True is what makes a retry cheap: cached sections are skipped.
            runner_fn(config_path=str(config_path), resume=True)

        new_errors = [
            {**e, "pass": attempt}
            for e in stage_a_section_errors(stage_dir)
            if (e["section_id"], e["timestamp"]) not in seen_errors
        ]
        seen_errors.update((e["section_id"], e["timestamp"]) for e in new_errors)
        _raise_if_fatal((e.get("error") for e in new_errors), where=f"{label} pass {attempt}")
        # Completion is judged by artifact USABILITY, not mere file presence: a
        # present-but-malformed artifact must keep the loop going so it can be
        # evicted and re-called on the next pass.
        unusable = concurrency.stage_a_artifact_problems(
            stage_module=stage_module, sections=sections,
            sections_dir=stage_dir / "sections", cost_dir=stage_dir / "cost",
        )
        missing = sorted(unusable)
        attempts.append({
            "pass": attempt,
            "section_errors": new_errors,
            "missing_after_pass": list(missing),
        })
        recovered.extend(new_errors)

        if not missing:
            if attempt > 1:
                LOGGER.info("%s: recovered on pass %d", label, attempt)
            break
        if attempt == passes:
            break
        wait = _backoff(attempt - 1)
        LOGGER.warning(
            "%s: %d section(s) still missing after pass %d (%s) — retrying in %ds",
            label, len(missing), attempt, missing[:5], wait,
        )
        time.sleep(wait)

    # Terminal success is judged by artifact USABILITY plus an aggregation that
    # actually ran in this invocation. Falling back to output-file presence would let
    # a stale final.json from an earlier run stand in for work that never completed.
    still_missing = sorted(concurrency.stage_a_artifact_problems(
        stage_module=stage_module, sections=sections,
        sections_dir=stage_dir / "sections", cost_dir=stage_dir / "cost",
    ))
    aggregated = any(a.get("aggregated") for a in pool_stats) if pool_stats else True
    # Two independent conditions, both fatal. A caller must never be handed a
    # final.json that this run did not produce, whether the gap is an unusable
    # section artifact or an aggregation that never ran.
    if still_missing:
        raise RunError(
            f"{label}: {len(still_missing)} section(s) still unusable after "
            f"{len(attempts)} pass(es): {still_missing[:5]} — refusing to continue on a "
            f"final.json that does not reflect them"
        )
    if not aggregated:
        raise RunError(
            f"{label}: every section artifact is usable but no aggregation completed in "
            f"this run — refusing to report success on a final.json this run did not "
            f"produce"
        )
    # Errors whose artifact now exists were recovered by a retry.
    recovered_only = [e for e in recovered if e["section_id"] not in set(still_missing)]
    return {
        "passes_run": len(attempts),
        "attempts": attempts,
        "section_errors_recovered": recovered_only,
        "section_errors_total": len(recovered),
        "missing_sections": still_missing,
        "aggregated_in_this_run": aggregated,
        "workers": workers,
        "pool": pool_stats,
    }


def run_stage_a(
    *,
    cfg: dict[str, Any],
    config_path: Path,
    resume: bool,
    skip: set[str],
    max_sections: int | None,
    sections: list[Any],
    output_base: Path,
    passes: int = 3,
    workers: int = 1,
    gate: Any = None,
) -> dict[str, Any]:
    bench = cfg["benchmark"]
    retry_info: dict[str, Any] = {}
    with patches.stage_a_user_templates(
        bench["a1_user_template"], bench["a2_user_template"]
    ), patches.limited_sections(max_sections):
        for label, stage_module, runner_fn in (
            ("stage_a1", stage_a1, stage_a1.run_stage_a1),
            ("stage_a2", stage_a2, stage_a2.run_stage_a2),
        ):
            if label in skip:
                LOGGER.info("Skipping %s by request", label)
                continue
            if not resume:
                # The namespace was archived; nothing is cached, so pass 1 is a
                # full run and later passes only fill genuine gaps.
                LOGGER.info("%s: fresh namespace", label)
            retry_info[label] = _run_stage_a_one(
                label=label,
                stage_module=stage_module,
                runner_fn=runner_fn,
                config_path=config_path,
                stage_dir=output_base / label,
                sections=sections,
                passes=passes,
                workers=workers,
                gate=gate,
            )
    return retry_info


def run_merge(
    *,
    cfg: dict[str, Any],
    output_base: Path,
    api_key: str,
    max_sections: int | None,
    merge_stats: dict[str, int],
) -> Path:
    bench = cfg["benchmark"]
    out_path = output_base / "stage_a12" / "final.json"
    model = (cfg.get("model") or {}).get("name", "anthropic/claude-sonnet-4-6")
    with patches.merge_adapted(
        protocol=bench["protocol"],
        stage_label=bench["stage_label"],
        subjects=bench["subjects"],
        stats=merge_stats,
    ), patches.limited_sections(max_sections):
        merge_stage_a12.merge(
            a1_path=str(output_base / "stage_a1" / "final.json"),
            a2_path=str(output_base / "stage_a2" / "final.json"),
            out_path=str(out_path),
            api_key=api_key,
            model=model,
            spec_url=cfg["fetcher"]["url"],
            spec_merge_threshold=0,
            skip_resolve_warnings=False,
        )
    return out_path


def merge_input_fingerprint(output_base: Path) -> dict[str, str]:
    return {
        "a1_sha256": sha256_file(output_base / "stage_a1" / "final.json"),
        "a2_sha256": sha256_file(output_base / "stage_a2" / "final.json"),
    }


def merge_is_current(output_base: Path) -> bool:
    """
    True when stage_a12/final.json was produced from exactly the A1/A2 finals now
    on disk. Merge has no per-call cache of its own and its LLMClient SWALLOWS API
    failures (merge_stage_a12.py:337-350) — a re-run against a dead API would
    silently replace a good merge with a degraded one and invalidate every cached
    verify section. So resume must skip a merge that is already current.
    """
    merged = output_base / "stage_a12" / "final.json"
    marker = output_base / "stage_a12" / "merge_inputs.json"
    if not (merged.is_file() and marker.is_file()):
        return False
    try:
        recorded = read_json(marker)
    except (OSError, json.JSONDecodeError):
        return False
    current = merge_input_fingerprint(output_base)
    return all(recorded.get(k) == v for k, v in current.items())


def write_merge_marker(output_base: Path, merged_path: Path) -> None:
    write_json(output_base / "stage_a12" / "merge_inputs.json", {
        **merge_input_fingerprint(output_base),
        "output_sha256": sha256_file(merged_path),
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })


def merge_call_errors(output_base: Path) -> list[str]:
    """Error strings merge recorded for LLM calls it could not complete."""
    errors: list[str] = []
    cost_path = output_base / "stage_a12" / "stage_a12_cost.json"
    if cost_path.is_file():
        try:
            for record in read_json(cost_path).get("per_call", []) or []:
                if isinstance(record, dict) and record.get("error"):
                    errors.append(str(record["error"]))
        except (OSError, json.JSONDecodeError):
            pass
    merged = output_base / "stage_a12" / "final.json"
    if merged.is_file():
        try:
            for entry in read_json(merged).get("merge_log", []) or []:
                if isinstance(entry, dict) and entry.get("error"):
                    errors.append(str(entry["error"]))
        except (OSError, json.JSONDecodeError):
            pass
    return errors


def check_merge_llm(merged_path: Path, output_base: Path) -> dict[str, Any]:
    merged = read_json(merged_path)
    summary = merged.get("summary", {})
    warnings = summary.get("validation", {}).get("warnings", []) or []
    non_fsm = [w for w in warnings if str(w).startswith("NON_FSM_BEHAVIORAL:")]
    needs_review = int(summary.get("pipeline", {}).get("needs_human_review", 0))
    cost_path = output_base / "stage_a12" / "stage_a12_cost.json"
    llm_calls = 0
    if cost_path.is_file():
        llm_calls = int(read_json(cost_path).get("llm_calls_made", 0))
    if (non_fsm or needs_review) and llm_calls == 0:
        raise RunError(
            f"merge reported {len(non_fsm)} NON_FSM_BEHAVIORAL warning(s) and "
            f"{needs_review} needs_review entr(ies) but made 0 LLM calls — the "
            f"API key did not reach merge_stage_a12.merge()"
        )
    return {
        "llm_calls": llm_calls,
        "non_fsm_warnings": len(non_fsm),
        "needs_review_remaining": needs_review,
        "statements": len(merged.get("statements", [])),
    }


def _drop_unusable_verify_sections(verify_dir: Path) -> list[dict[str, Any]]:
    """
    verify_section caches a section file even when some statements ended `error`
    (verify_stage_a.py:536-545,575-581), so a plain resume would never retry them.
    Delete those section artifacts and report what was dropped.
    """
    dropped: list[dict[str, Any]] = []
    sections_dir = verify_dir / "sections"
    if not sections_dir.is_dir():
        return dropped
    for path in sorted(sections_dir.glob("*.json")):
        try:
            data = read_json(path)
        except (OSError, json.JSONDecodeError):
            path.unlink()
            dropped.append({"section_file": path.name, "reason": "unreadable"})
            continue
        bad = [
            {"statement_id": r.get("statement_id"), "error": r.get("error")}
            for r in (data.get("results") or [])
            if r.get("status") in {"error", "unverified"}
            or r.get("existence") == "unchecked"
        ]
        if data.get("skipped") or bad:
            path.unlink()
            dropped.append({
                "section_id": data.get("section_id", path.stem),
                "skipped": bool(data.get("skipped")),
                "unusable_statements": bad[:5],
                "unusable_count": len(bad),
            })
    return dropped


def run_verify(
    *,
    cfg: dict[str, Any],
    config_path: Path,
    output_base: Path,
    merged_path: Path,
    api_key: str,
    resume: bool,
    max_sections: int | None,
    passes: int = 3,
    json_stats: dict[str, Any] | None = None,
    workers: int = 1,
    gate: Any = None,
) -> tuple[Path, dict[str, Any]]:
    verify_dir = output_base / "verify_stage_a12"
    json_stats = json_stats if json_stats is not None else {}
    attempts: list[dict[str, Any]] = []
    pool_stats: list[dict[str, Any]] = []
    out_path = verify_dir / "final.json"
    for attempt in range(1, passes + 1):
        if attempt > 1:
            wait = _backoff(attempt - 2)
            LOGGER.warning(
                "verify: retrying unusable sections (pass %d) in %ds", attempt, wait
            )
            time.sleep(wait)
        # Drop unusable cached sections before EVERY pass, including the first.
        # On a resume, pass 1 would otherwise reuse section artifacts left behind by
        # a previous attempt — statements that ended `error` would never be retried,
        # and their stale error strings would be re-read as if they were fresh.
        dropped = _drop_unusable_verify_sections(verify_dir)
        # Always resume=True: it is what makes verify write per-section artifacts
        # at all (verify_stage_a.py:778-783), and --no-resume already archived the
        # namespace, so there is nothing stale left to reuse.
        with patches.tolerant_verify_json(json_stats):
            if workers > 1:
                with patches.limited_sections(max_sections):
                    pool = concurrency.parallel_verify_sections(
                        config_path=config_path,
                        merged_path=merged_path,
                        spec_url=cfg["fetcher"]["url"],
                        merge_threshold=0,
                        api_key=api_key,
                        output_path=verify_dir / "final.json",
                        workers=workers,
                        logger=LOGGER,
                        gate=gate,
                    )
                pool_stats.append(pool)
                if pool["cache_complete"]:
                    # section_delay=0 rather than a time proxy: verify_stage_a.time is
                    # also what LLMClient.call sleeps on for rate-limit backoff
                    # (verify_stage_a.py:387-407), so patching the module would disable
                    # that. Only the outer per-section pause is suppressed, and only
                    # once the cache is proven complete.
                    with concurrency.verify_cost_replay(verify_dir / "cost"):
                        out_path = _verify_once(
                            cfg=cfg, config_path=config_path, output_base=output_base,
                            merged_path=merged_path, api_key=api_key, resume=True,
                            max_sections=max_sections, section_delay=0.0,
                        )
                else:
                    LOGGER.warning(
                        "verify: cache incomplete after pool (%d section(s): %s) — "
                        "deferring aggregation to the next pass",
                        len(pool["unusable_after_pool"]),
                        sorted(pool["unusable_after_pool"])[:5],
                    )
            else:
                # Replay applies here too: a workers=1 RESUME is just as much a
                # cache-hit aggregation, and without replay the parent reports zero
                # calls while per-statement cost files exist — which the three-way
                # accounting assertion then (correctly) rejects.
                with concurrency.verify_cost_replay(verify_dir / "cost"):
                    out_path = _verify_once(
                        cfg=cfg, config_path=config_path, output_base=output_base,
                        merged_path=merged_path, api_key=api_key, resume=True,
                        max_sections=max_sections,
                    )
        sanitise_verify_paths(verify_dir)
        problems = verify_problem_summary(merged_path, verify_dir)
        _raise_if_fatal(problems["error_messages"], where=f"verify pass {attempt}")
        attempts.append({
            "pass": attempt,
            "dropped_cached_sections": dropped,
            "unusable_statements": problems["unusable"],
            "skipped_sections": problems["skipped_sections"],
            "uncovered_statements": problems["uncovered"],
        })
        if not (problems["unusable"] or problems["skipped_sections"]
                or problems["uncovered"]):
            break
    # Last-ditch pass for statements that survive every retry.
    #
    # Read the failures from the section artifacts, not from final_report.json: when
    # the last pass leaves the cache incomplete no report exists, and a report-based
    # check would skip the fallback it is meant to guard.
    groups = verify_stage_a.group_by_section(
        read_json(merged_path).get("statements", []))
    sections_dir = verify_dir / "sections"
    pending = concurrency.unusable_statements_from_sections(
        groups=groups, sections_dir=sections_dir)

    if pending:
        LOGGER.warning(
            "verify: %d statement(s) still unusable after %d pass(es); one final "
            "attempt at temperature %.1f for those sections only: %s",
            len(pending), len(attempts), FALLBACK_TEMPERATURE,
            sorted({p["section_id"] for p in pending})[:5],
        )
        for section_id in sorted({p["section_id"] for p in pending}):
            safe_id = section_id.replace(".", "_").replace("/", "_")
            _evict_paths = [sections_dir / f"{safe_id}.json"]
            for stmt in groups.get(section_id, []):
                sid = str(stmt.get("statement_id")).replace("-", "_")
                _evict_paths.append(verify_dir / "cost" / f"{sid}.json")
            for path in _evict_paths:
                if path.is_file():
                    path.unlink()
        with patches.tolerant_verify_json(json_stats), \
                concurrency.sampling_temperature(FALLBACK_TEMPERATURE):
            if workers > 1:
                with patches.limited_sections(max_sections):
                    concurrency.parallel_verify_sections(
                        config_path=config_path, merged_path=merged_path,
                        spec_url=cfg["fetcher"]["url"], merge_threshold=0,
                        api_key=api_key, output_path=verify_dir / "final.json",
                        workers=workers, logger=LOGGER, gate=gate)
            else:
                _verify_once(
                    cfg=cfg, config_path=config_path, output_base=output_base,
                    merged_path=merged_path, api_key=api_key, resume=True,
                    max_sections=max_sections, section_delay=0.0,
                )
        pending = concurrency.unusable_statements_from_sections(
            groups=groups, sections_dir=sections_dir)
        attempts.append({"pass": "fallback_temperature",
                         "temperature": FALLBACK_TEMPERATURE,
                         "still_unusable": [p["statement_id"] for p in pending]})

    unverifiable: list[dict[str, Any]] = list(pending)
    if unverifiable:
        LOGGER.warning(
            "verify: %d statement(s) unverifiable; excluded from the FSM input: %s",
            len(unverifiable), [u["statement_id"] for u in unverifiable][:5],
        )

    # ALWAYS aggregate last, with the unverifiable statements accepted, so the parent
    # writes final.json and final_report.json. Without this the run could end with no
    # report at all and fail on its absence rather than on anything substantive.
    with patches.tolerant_verify_json(json_stats), \
            concurrency.verify_cost_replay(verify_dir / "cost"):
        out_path = _verify_once(
            cfg=cfg, config_path=config_path, output_base=output_base,
            merged_path=merged_path, api_key=api_key, resume=True,
            max_sections=max_sections, section_delay=0.0,
        )
    sanitise_verify_paths(verify_dir)

    return out_path, {
        "passes_run": len(attempts),
        "attempts": attempts,
        "unverifiable_statements": unverifiable,
        "workers": workers,
        "pool": pool_stats,
        "verify_json_recovered": {
            "count": json_stats.get("count", 0),
            "statement_ids": json_stats.get("statement_ids", []),
        },
    }


def sanitise_verify_paths(verify_dir: Path) -> list[str]:
    """
    The parent records the absolute input_file/spec_url it was handed
    (verify_stage_a.py summary block). We must hand it real paths, so the identifying
    strings are removed from its output afterwards rather than never written — the
    double-blind rule applies to what ends up on disk.
    """
    touched: list[str] = []
    for name in ("final_report.json", "final.json"):
        path = verify_dir / name
        if not path.is_file():
            continue
        try:
            data = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        changed = False
        for holder in (data.get("summary"), data.get("verification_report")):
            if not isinstance(holder, dict):
                continue
            for field in ("input_file", "spec_url"):
                value = holder.get(field)
                if isinstance(value, str) and value:
                    relative = repo_relative(value)
                    if relative != value:
                        holder[field] = relative
                        changed = True
        if changed:
            write_json(path, data)
            touched.append(name)
    return touched


def verify_problem_summary(merged_path: Path, verify_dir: Path) -> dict[str, Any]:
    """Non-raising view of what check_verify_coverage would reject."""
    report_path = verify_dir / "final_report.json"
    if not report_path.is_file():
        return {"unusable": ["<no report>"], "unusable_detail": [],
                "error_messages": [], "skipped_sections": [], "uncovered": []}
    report = read_json(report_path)
    merged_ids = {
        s.get("statement_id") for s in read_json(merged_path).get("statements", [])
    }
    covered: set[str] = set()
    unusable: list[str] = []
    unusable_detail: list[dict[str, Any]] = []
    error_messages: list[str] = []
    skipped: list[str] = []
    for section in report.get("sections", []):
        if section.get("skipped"):
            skipped.append(str(section.get("section_id")))
            continue
        for result in section.get("results", []):
            covered.add(result.get("statement_id"))
            if (result.get("status") in {"error", "unverified"}
                    or result.get("existence") == "unchecked"):
                unusable.append(
                    f"{result.get('statement_id')}: status={result.get('status')} "
                    f"existence={result.get('existence')}"
                )
                unusable_detail.append({
                    "statement_id": result.get("statement_id"),
                    "section_id": section.get("section_id"),
                    "status": result.get("status"),
                    "existence": result.get("existence"),
                    "error": str(result.get("error") or ""),
                })
                if result.get("error"):
                    error_messages.append(str(result["error"]))
    return {
        "unusable": unusable,
        "unusable_detail": unusable_detail,
        "error_messages": error_messages,
        "skipped_sections": skipped,
        "uncovered": sorted(x for x in merged_ids - covered if x),
    }


def _verify_once(
    *,
    cfg: dict[str, Any],
    config_path: Path,
    output_base: Path,
    merged_path: Path,
    api_key: str,
    resume: bool,
    max_sections: int | None,
    section_delay: float | None = None,
) -> Path:
    model_cfg = cfg.get("model") or {}
    out_path = output_base / "verify_stage_a12" / "final.json"
    with patches.limited_sections(max_sections):
        verify_stage_a.verify(
            input_path=str(merged_path),
            output_path=str(out_path),
            api_key=api_key,
            model=model_cfg.get("name", "anthropic/claude-sonnet-4-6"),
            max_tokens=int(model_cfg.get("max_tokens", 16000)),
            max_retries=int(model_cfg.get("max_retries", 5)),
            retry_wait=int(model_cfg.get("rate_limit_retry_wait_seconds", 60)),
            section_delay=(
                float(model_cfg.get("section_delay_seconds", 5))
                if section_delay is None else section_delay
            ),
            resume=resume,
            dry_run=False,
            spec_url=cfg["fetcher"]["url"],
            merge_threshold=0,
            config_path=config_path,
        )
    return out_path


# ---------------------------------------------------------------------------
# Stage B0 (local) and Stage B synthesis
# ---------------------------------------------------------------------------

def _statement_id(s: dict[str, Any]) -> str | None:
    value = s.get("statement_id") or s.get("id")
    return str(value) if value else None


def _keep_for_fsm(s: dict[str, Any]) -> bool:
    category = s.get("category")
    if category in {"FSM_STATE", "IMPLICIT_TRANSITION"}:
        return True
    if category == "BEHAVIORAL":
        return any(s.get(k) for k in ("from_state", "to_state", "trigger"))
    return False


def _slim_state(s: dict[str, Any], stage_label: str) -> dict[str, Any]:
    return {
        "id": _statement_id(s),
        "state_name": s.get("state_name"),
        "semantic_type": s.get("semantic_type"),
        "is_initial": s.get("is_initial", False),
        "is_final": s.get("is_final", False),
        "invariant_cond": s.get("invariant_cond"),
        "stage": stage_label,
        "source_section": s.get("source_section"),
    }


def _slim_transition(s: dict[str, Any]) -> dict[str, Any]:
    # `actions` is deliberately preserved (the parent stage_b1 filter drops it);
    # the benchmark evaluator scores event+action together.
    return {
        "id": _statement_id(s),
        "category": s.get("category"),
        "from_state": s.get("from_state"),
        "to_state": s.get("to_state"),
        "trigger": s.get("trigger"),
        "pre_cond": s.get("pre_cond"),
        "post_cond": s.get("post_cond"),
        "modality": s.get("modality"),
        "actors": s.get("actors"),
        "actions": s.get("actions"),
        "source_section": s.get("source_section"),
    }


def run_stage_b0(
    *, input_path: Path, output_base: Path, stage_label: str,
    exclude_ids: set[str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    out_dir = output_base / "stage_b0"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = read_json(input_path)
    statements = [s for s in data.get("statements", []) if isinstance(s, dict)]
    excluded = set(exclude_ids or ())
    dropped_unverifiable = [
        s.get("statement_id") for s in statements if s.get("statement_id") in excluded
    ]
    # C6 intact: nothing that failed verification may reach the FSM.
    statements = [s for s in statements if s.get("statement_id") not in excluded]
    kept = [s for s in statements if _keep_for_fsm(s)]
    for s in kept:
        s["stage"] = stage_label

    states = [_slim_state(s, stage_label) for s in kept if s.get("category") == "FSM_STATE"]
    transitions = [_slim_transition(s) for s in kept if s.get("category") != "FSM_STATE"]
    fsm_input = {"states": states, "transitions": transitions}

    out_path = out_dir / f"{stage_label}.json"
    write_json(out_path, {
        "stage": stage_label,
        "input_path": repo_relative(input_path),
        "input_sha256": sha256_file(input_path),
        "fsm_input": fsm_input,
        "statements": kept,
    })
    write_json(out_dir / "all.json", {"fsm_input": fsm_input, "statements": kept})
    stats = {
        "input_statements": len(statements),
        "excluded_unverifiable": dropped_unverifiable,
        "kept": len(kept),
        "states_in": len(states),
        "transitions_in": len(transitions),
    }
    LOGGER.info("Stage B0: kept %d/%d statements", len(kept), len(statements))
    return out_path, stats


def _resolve_b_model(cfg: dict[str, Any]) -> str:
    model = ((cfg.get("model") or {}).get("stage_models") or {}).get("stage_b1")
    if not isinstance(model, str) or not model.strip():
        raise RunError("generated config has no model.stage_models.stage_b1")
    return model.strip()


def _resolve_b_prompt(cfg: dict[str, Any]) -> str:
    prompt = (cfg.get("system_prompts") or {}).get("stage_b1")
    if not prompt or not prompt.strip():
        raise RunError("generated config has no system_prompts.stage_b1")
    return prompt.strip()


def _resolve_b_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    model_cfg = cfg.get("model") or {}
    return {
        "max_tokens": int(model_cfg.get("max_tokens", 64000)),
        "max_retries": int(model_cfg.get("max_retries", 5)),
        "retry_wait": int(model_cfg.get("rate_limit_retry_wait_seconds", 60)),
        "timeout": int(model_cfg.get("request_timeout_seconds", 1800)),
    }


def _assert_no_web_search(cost_record: dict[str, Any]) -> dict[str, Any]:
    turns = cost_record.get("per_turn") or []
    offenders = []
    requests_total = 0
    for turn in turns:
        executed = turn.get("web_search_executed")
        requests = turn.get("web_search_requests")
        queries = turn.get("web_search_queries") or []
        requests_total += int(requests or 0)
        if executed or (requests not in (0, None)) or queries:
            offenders.append({
                "turn": turn.get("turn"),
                "executed": executed,
                "requests": requests,
                "queries": queries,
            })
    if offenders:
        raise RunError(f"Stage B performed web search although it is disabled: {offenders}")
    return {
        "turns_checked": len(turns),
        "web_search_requests_total": requests_total,
        "web_search_executed": False,
    }


def _b_complete(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool((read_json(path) or {}).get("states"))
    except (OSError, json.JSONDecodeError):
        return False


def run_stage_b(
    *,
    cfg: dict[str, Any],
    output_base: Path,
    stage_label: str,
    protocol: str,
    resume: bool,
) -> tuple[Path, dict[str, Any]]:
    in_path = output_base / "stage_b0" / f"{stage_label}.json"
    out_dir = output_base / "stage_b1"
    cost_dir = out_dir / "cost"
    out_path = out_dir / f"{stage_label}.json"
    cost_path = cost_dir / f"{stage_label}.json"

    model = _resolve_b_model(cfg)
    prompt = _resolve_b_prompt(cfg)
    settings = _resolve_b_settings(cfg)

    stage_data = read_json(in_path)
    fsm_input = stage_data.get("fsm_input")
    if not isinstance(fsm_input, dict):
        raise RunError(f"{in_path} has no object field 'fsm_input'")

    user_message = (
        f"Build the complete {protocol} protocol state machine from the extracted "
        f"statements below. The pipeline phase label is {stage_label!r}; treat it as a "
        "single bucket for the whole state machine, not as a real protocol phase.\n\n"
        "INPUT:\n"
        f"{json.dumps({'stage': stage_label, 'fsm_input': fsm_input}, indent=2)}"
    )

    token_count = None
    try:
        token_count = stage_b2.litellm.token_counter(
            model=model, messages=[{"role": "user", "content": user_message}]
        )
    except Exception as exc:                              # pragma: no cover
        LOGGER.warning("token preflight unavailable: %s", exc)
    if token_count is not None and token_count > B_TOKEN_LIMIT:
        raise RunError(
            f"Stage B user message is {token_count} tokens (> {B_TOKEN_LIMIT}); "
            f"stopping instead of auto-splitting"
        )

    b0_sha = sha256_file(in_path)
    summary_path = out_dir / "stage_b_summary.json"
    if resume and _b_complete(out_path) and cost_path.is_file():
        # Never reuse a Stage B output that was built from a different Stage B0.
        previous = read_json(summary_path) if summary_path.is_file() else {}
        if previous.get("b0_input_sha256") not in (None, b0_sha):
            raise RunError(
                f"{out_path} was synthesised from a different stage_b0 input "
                f"({previous.get('b0_input_sha256')} != {b0_sha}); re-run with "
                f"--no-resume so the namespace is archived and rebuilt"
            )
        LOGGER.info("Stage B: reusing existing output %s", repo_relative(out_path))
        result = read_json(out_path)
        cost_record = read_json(cost_path)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        cost_dir.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Stage B synthesis with %s (no web search)", model)
        with patches.no_search_continuation():
            result, cost_record = stage_b2.call_with_search(
                protocol_phase=stage_label,
                model=model,
                prompt=prompt,
                user_msg=user_message,
                max_tokens=settings["max_tokens"],
                max_retries=settings["max_retries"],
                retry_wait=settings["retry_wait"],
                timeout=settings["timeout"],
                web_search_kwargs={},
                out_dir=out_dir,
                out_path=out_path,
                cost_dir=cost_dir,
            )
        write_json(out_path, result)
        write_json(cost_path, cost_record)

    search_check = _assert_no_web_search(cost_record)
    stats = {
        "model": model,
        "effort": "high (hardcoded in stage_b2.call_with_search)",
        "b0_input_sha256": b0_sha,
        "verify_input_sha256": stage_data.get("input_sha256"),
        "prompt_tokens_estimate": token_count,
        "turns": cost_record.get("turns"),
        "states": len(result.get("states", []) or []),
        "transitions": len(result.get("transitions", []) or []),
        "gaps": len(result.get("gaps", []) or []),
        "unresolved": len(result.get("unresolved", []) or []),
        "discarded_states": len(result.get("discarded_states", []) or []),
        "cost_usd": cost_record.get("cost_usd"),
        "total_tokens": cost_record.get("total_tokens"),
        "elapsed_seconds": cost_record.get("elapsed_seconds"),
        "web_search": search_check,
    }
    write_json(summary_path, stats)
    return out_path, stats


# ---------------------------------------------------------------------------
# Export to the benchmark's FSM format (no post-hoc filtering — METHOD.md)
# ---------------------------------------------------------------------------

def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value if v is not None)
    return re.sub(r"\s+", " ", str(value)).strip()


def _event_of(transition: dict[str, Any]) -> str:
    event = _norm(transition.get("event") or transition.get("trigger"))
    if event:
        return event.replace("_", " ")
    guard = _norm(transition.get("guard") or transition.get("pre_cond"))
    return guard.replace("_", " ")


def _action_of(transition: dict[str, Any]) -> str:
    actions = transition.get("actions")
    if isinstance(actions, list) and actions:
        return "; ".join(_norm(a) for a in actions if _norm(a))
    return _norm(transition.get("action") or transition.get("post_cond")).replace("_", " ")


def export_fsm(*, b_path: Path, out_path: Path) -> dict[str, Any]:
    data = read_json(b_path)

    states: list[str] = []
    seen: set[str] = set()
    declared_initial: list[str] = []
    final_states: list[str] = []
    for state in data.get("states", []) or []:
        name = _norm(state.get("state_name") if isinstance(state, dict) else state)
        if not name or name in seen:
            continue
        seen.add(name)
        states.append(name)
        if isinstance(state, dict) and state.get("is_initial"):
            declared_initial.append(name)
        if isinstance(state, dict) and state.get("is_final") and name not in final_states:
            final_states.append(name)

    if len(declared_initial) > 1:
        raise RunError(
            f"Stage B declared {len(declared_initial)} initial states "
            f"({declared_initial}); the FSM format allows exactly one"
        )
    if declared_initial:
        initial_state = declared_initial[0]
        provenance = "declared"
    elif states:
        initial_state = states[0]
        provenance = "fallback_first_state"
    else:
        raise RunError("Stage B produced no states")

    transitions: list[dict[str, str]] = []
    keys: set[tuple[str, str, str, str]] = set()
    dropped_incomplete = 0
    for transition in data.get("transitions", []) or []:
        if not isinstance(transition, dict):
            dropped_incomplete += 1
            continue
        row = {
            "from": _norm(transition.get("from_state") or transition.get("from")),
            "event": _event_of(transition),
            "action": _action_of(transition),
            "to": _norm(transition.get("to_state") or transition.get("to")),
        }
        if not row["from"] or not row["to"]:
            dropped_incomplete += 1
            continue
        key = (row["from"], row["event"], row["action"], row["to"])
        if key in keys:
            continue
        keys.add(key)
        transitions.append(row)

    fsm = {
        "states": states,
        "initial_state": initial_state,
        "final_states": final_states,
        "transitions": transitions,
    }

    # Integrity (hard fail)
    problems: list[str] = []
    if not states:
        problems.append("no states")
    known = set(states)
    for row in transitions:
        for field_name in ("from", "event", "action", "to"):
            if not isinstance(row[field_name], str):
                problems.append(f"non-string field {field_name} in {row}")
        if row["from"] not in known:
            problems.append(f"transition from unknown state {row['from']!r}")
        if row["to"] not in known:
            problems.append(f"transition to unknown state {row['to']!r}")
    if len(keys) != len(transitions):
        problems.append("duplicate (from,event,action,to) rows survived")
    if not isinstance(initial_state, str) or not initial_state:
        problems.append("missing initial_state")
    if problems:
        raise RunError("export integrity check failed: " + "; ".join(problems[:8]))

    write_json(out_path, fsm)
    LOGGER.info(
        "Exported %s (%d states, %d transitions)",
        repo_relative(out_path), len(states), len(transitions),
    )
    return {
        "path": repo_relative(out_path),
        "states": len(states),
        "transitions": len(transitions),
        "final_states": len(final_states),
        "initial_state": initial_state,
        "initial_state_provenance": provenance,
        "dropped_incomplete_transitions": dropped_incomplete,
        "sha256": sha256_file(out_path),
    }


# ---------------------------------------------------------------------------
# Environment capture
# ---------------------------------------------------------------------------

def package_versions() -> dict[str, str | None]:
    from importlib.metadata import PackageNotFoundError, version
    out: dict[str, str | None] = {"python": sys.version.split()[0]}
    for package in ("numpy", "torch", "sentence-transformers", "pandas",
                    "scikit-learn", "litellm"):
        try:
            out[package] = version(package)
        except PackageNotFoundError:
            out[package] = None
    return out


def minilm_snapshot() -> dict[str, Any]:
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots"
    if not model_dir.is_dir():
        return {"found": False}
    snapshots = sorted(p.name for p in model_dir.iterdir() if p.is_dir())
    return {"found": True, "snapshots": snapshots}


# ---------------------------------------------------------------------------
# Full run
# ---------------------------------------------------------------------------

def normalize_skip(values: Iterable[str] | None) -> set[str]:
    out: set[str] = set()
    for raw in values or []:
        for token in re.split(r"[,\s]+", raw):
            name = token.strip().lower().replace("-", "_")
            if not name:
                continue
            if name in SKIP_ALIASES:
                out.update(SKIP_ALIASES[name])
            elif name in SKIPPABLE_STAGES:
                out.add(name)
            else:
                raise RunError(
                    f"unknown stage for --skip: {token!r}; valid: "
                    + ", ".join(sorted(SKIPPABLE_STAGES | set(SKIP_ALIASES)))
                )
    return out


# Namespace of the run currently in progress, so a fatal abort can be recorded in
# the right place from the CLI's exception handler.
_ACTIVE_OUTPUT_BASE: Path | None = None


def _write_abort_record(
    output_base: Path, exc: "FatalApiError", *, rate_limit_retries: int | None = None
) -> None:
    aborts_dir = output_base / "aborts"
    aborts_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    record = {
        "fatal_error": {
            "message": str(exc),
            "detail": exc.detail,
            "regain_access": exc.regain_access,
        },
        "aborted_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wrapper_set_sha256": wrapper_set_sha256(),
        "rate_limit_retries_before_abort": rate_limit_retries,
        "note": (
            "Retrying cannot clear this class of error. Every completed stage is "
            "cached; re-run the same command once access is restored."
        ),
    }
    write_json(aborts_dir / f"abort_{stamp}.json", record)
    write_json(output_base / "abort.json", record)   # latest, for convenience


def _run_protocol_inner(
    *,
    rate_limits: Any,
    gate: Any,
    request_audit: dict[str, dict[str, Any]],
    protocol: str,
    resume: bool = True,
    skip: Iterable[str] | None = None,
    max_sections: int | None = None,
    namespace: str | None = None,
    run_eval: bool = True,
    stage_a_passes: int = 3,
    workers: int = 1,
) -> dict[str, Any]:
    started = time.time()
    if workers < 1:
        raise RunError(f"--workers must be >= 1, got {workers}")
    skipped = normalize_skip(skip)
    api_key = require_api_key()
    assert_clone_clean("before run")

    prep = prepare_protocol(protocol)
    run_key = prep["run_key"]
    # run_dir holds the run's own artifacts; the parent pipeline's stage_* trees live
    # one level down under stages/, because output.base_dir is what the parent joins
    # "stage_a1" and friends onto.
    run_dir = run_base_dir(protocol, run_key, namespace=namespace)
    if namespace in (None, "generic"):
        # output/ holds exactly one run: archive any older namespace before this one
        # starts, so the tree never shows two candidates for "the current result".
        archive_superseded_runs(protocol, current_run_key=run_key)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "RUN_KEY").write_text(run_key + "\n", encoding="utf-8")
    output_base = stages_dir(run_dir)
    global _ACTIVE_OUTPUT_BASE
    _ACTIVE_OUTPUT_BASE = run_dir

    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        previous = read_json(manifest_path)
        if previous.get("run_digest") != prep["run_digest"]:
            raise RunError(
                f"namespace {output_base} was produced by a different digest "
                f"({previous.get('run_digest')}); refusing to reuse it"
            )

    if not resume:
        archive_namespace(run_dir)

    # The generated config must point at THIS namespace.
    info = protocol_info(protocol)
    rendered = prompt_lib.build_prompts(protocol)
    cfg = build_config(
        protocol=protocol,
        info=info,
        chunk_stats=prep,
        rendered=rendered,
        output_base=run_dir,          # build_config derives stages/ from the run dir
    )
    config_path = config_path_for(protocol)
    write_config(cfg, config_path)

    # Keep the exact prompts this run used inside the run, so any later "what
    # changed between run N and run N+1" question is answerable from artifacts
    # rather than from hashes plus memory.
    write_json(run_dir / "prompts" / "rendered.json", {
        "protocol": protocol,
        "prompt_template_sha256": cfg["benchmark"]["prompt_template_sha256"],
        "prompt_set_sha256": cfg["benchmark"]["prompt_set_sha256"],
        "prompt_sha256": cfg["benchmark"]["prompt_sha256"],
        "prompts": rendered,
    })

    stage_label = cfg["benchmark"]["stage_label"]
    sections = expected_sections(cfg, max_sections)
    if not sections:
        raise RunError(f"{protocol}: no sections parsed from {cfg['fetcher']['url']}")
    LOGGER.info("%s: %d section(s) to process", protocol, len(sections))

    stages: dict[str, Any] = {}
    merge_patch_stats: dict[str, int] = {}


    retry_info = run_stage_a(
        cfg=cfg, config_path=config_path, resume=resume,
        skip=skipped, max_sections=max_sections,
        sections=sections, output_base=output_base, passes=stage_a_passes,
        workers=workers, gate=gate,
    )
    for label, stage_dir in (("stage_a1", output_base / "stage_a1"),
                             ("stage_a2", output_base / "stage_a2")):
        check_stage_a_complete(stage_dir, sections, label)
        final = read_json(stage_dir / "final.json")
        stage_retry = retry_info.get(label, {})
        stages[label] = {
            "sections": len(sections),
            "statements": len(final.get("statements", [])),
            "passes_run": stage_retry.get("passes_run", 1),
            "section_errors_recovered": stage_retry.get("section_errors_recovered", []),
            "section_errors_total": stage_retry.get("section_errors_total", 0),
            **aggregate_cost_dir(stage_dir / "cost", skip=(f"{label}_cost.json",)),
            **record_stage_provenance(stage_dir),
        }

    merged_path = output_base / "stage_a12" / "final.json"
    if "stage_a12" in skipped:
        LOGGER.info("Skipping merge by request; using %s", merged_path)
    elif resume and merge_is_current(output_base):
        LOGGER.info(
            "Merge is current for these A1/A2 outputs — reusing %s (no LLM calls)",
            repo_relative(merged_path),
        )
    else:
        merged_path = run_merge(
            cfg=cfg, output_base=output_base, api_key=api_key,
            max_sections=max_sections, merge_stats=merge_patch_stats,
        )
        _raise_if_fatal(merge_call_errors(output_base), where="merge")
        write_merge_marker(output_base, merged_path)
    stages["stage_a12"] = {
        **check_merge_llm(merged_path, output_base),
        **aggregate_cost_dir(output_base / "stage_a12" / "cost"),
        "prompt_rewrites": merge_patch_stats,
        **record_stage_provenance(output_base / "stage_a12"),
    }

    if "verify_stage_a12" in skipped:
        raise RunError("verification is mandatory (METHOD.md); --skip verify is refused")
    verified_path, verify_retry = run_verify(
        cfg=cfg, config_path=config_path, output_base=output_base,
        merged_path=merged_path, api_key=api_key, resume=resume,
        max_sections=max_sections, passes=stage_a_passes, workers=workers, gate=gate,
    )
    verify_dir = output_base / "verify_stage_a12"
    unverifiable = verify_retry.get("unverifiable_statements") or []
    unverifiable_ids = {u["statement_id"] for u in unverifiable if u.get("statement_id")}
    merged_count = len(read_json(merged_path).get("statements", []))
    if unverifiable_ids and merged_count:
        fraction = len(unverifiable_ids) / merged_count
        if fraction > MAX_UNVERIFIABLE_FRACTION:
            raise RunError(
                f"{len(unverifiable_ids)} of {merged_count} statements "
                f"({fraction:.1%}) could not be verified even at temperature "
                f"{FALLBACK_TEMPERATURE} — above the {MAX_UNVERIFIABLE_FRACTION:.0%} "
                f"ceiling, so the result is not trustworthy: "
                f"{sorted(unverifiable_ids)[:5]}"
            )
        LOGGER.warning(
            "verify: %d/%d statements (%.2f%%) unverifiable; excluded from the FSM input",
            len(unverifiable_ids), merged_count, 100 * fraction,
        )
    coverage = check_verify_coverage(merged_path, verify_dir,
                                     unverifiable=unverifiable_ids)
    verified = read_json(verified_path)
    stages["verify_stage_a12"] = {
        **assert_verify_totals_agree(verify_dir),
        **coverage,
        "passes_run": verify_retry["passes_run"],
        "retry_attempts": verify_retry["attempts"],
        "verify_json_recovered": verify_retry["verify_json_recovered"],
        "unverifiable_statements": unverifiable,
        "statements_out": len(verified.get("statements", [])),
        **aggregate_cost_dir(verify_dir / "cost"),
        **verify_quality(verify_dir),
        **record_stage_provenance(verify_dir),
    }

    chunk_ids = {s.section_id for s in sections}
    stages["source_section_check"] = check_source_sections(
        verified.get("statements", []), chunk_ids
    )

    if "stage_b0" in skipped:
        b0_path = output_base / "stage_b0" / f"{stage_label}.json"
        stages["stage_b0"] = {"skipped": True}
    else:
        b0_path, b0_stats = run_stage_b0(
            input_path=verified_path, output_base=output_base, stage_label=stage_label,
            exclude_ids=unverifiable_ids,
        )
        stages["stage_b0"] = {
            **b0_stats,
            **record_stage_provenance(output_base / "stage_b0"),
        }

    b_path = output_base / "stage_b1" / f"{stage_label}.json"
    if "stage_b1" in skipped:
        LOGGER.info("Skipping Stage B synthesis by request; using %s", b_path)
        stages["stage_b"] = {"skipped": True}
    else:
        b_path, b_stats = run_stage_b(
            cfg=cfg, output_base=output_base, stage_label=stage_label,
            protocol=protocol, resume=resume,
        )
        stages["stage_b"] = {
            **b_stats,
            **record_stage_provenance(output_base / "stage_b1"),
        }

    export_path = run_dir / "fsm" / f"{protocol}_{MODEL_NAME}_final_fsm.json"
    if "export" in skipped:
        LOGGER.info("Skipping export by request; using %s", export_path)
        stages["export"] = {"skipped": True}
    else:
        stages["export"] = {
            **export_fsm(b_path=b_path, out_path=export_path),
            **record_stage_provenance(export_path.parent),
        }

    manifest = {
        "protocol": protocol,
        "run_key": run_key,
        "run_digest": prep["run_digest"],
        "namespace": namespace or "generic",
        "run_dir": repo_relative(run_dir),
        "stages_dir": repo_relative(output_base),
        "config_path": repo_relative(config_path),
        "smoke_max_sections": max_sections,
        "stage_a_passes_allowed": stage_a_passes,
        "concurrency": {
            "stage_a_workers": workers,
            "verify_workers": workers,
            "note": ("Sections are processed concurrently by the wrapper using the "
                     "parent's own per-section functions; all aggregation is still "
                     "done by the parent on cache hits."),
        },
        "python_version": sys.version.split()[0],
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "wall_clock_seconds": round(time.time() - started, 2),
        "inputs": {
            "segments_file": repo_relative(info["segments_file"]),
            "rfc_number": info["rfc_number"],
            "segment_count": prep["segment_count"],
            "chunk_count": prep["chunk_count"],
            "sections_processed": len(sections),
            "markdown_sha256": prep["markdown_sha256"],
            "escaped_heading_lines": prep["escaped_heading_lines"],
            "blank_chunks": prep["blank_chunks"],
        },
        "models": {
            "stage_a1_a2": {
                "model": (cfg["model"] or {}).get("name"),
                "effort": (cfg["model"].get("stage_effort") or {}).get("stage_a1"),
                "source": "config.yaml via ModelInterface",
            },
            "merge": {
                "model": (cfg["model"] or {}).get("name"),
                "effort": "low",
                "source": "hardcoded in merge_stage_a12.py:298-303,431-436,583-588",
            },
            "verify": {
                "model": (cfg["model"] or {}).get("name"),
                "effort": "low",
                "source": "hardcoded in verify_stage_a.py:387-396",
            },
            "stage_b": {
                "model": _resolve_b_model(cfg),
                "effort": "high",
                "source": "hardcoded in stage_b2.py:546-555",
                "stage_b1_model_alias_of": "stage_b2",
                "max_tokens": _resolve_b_settings(cfg)["max_tokens"],
                "timeout_seconds": _resolve_b_settings(cfg)["timeout"],
                "web_search": False,
            },
        },
        "prompts": {
            "prompt_template_sha256": cfg["benchmark"]["prompt_template_sha256"],
            "prompt_set_sha256": cfg["benchmark"]["prompt_set_sha256"],
            "prompt_sha256": cfg["benchmark"]["prompt_sha256"],
            "verify_a2a_defaults_reached": False,
        },
        "stages": stages,
        "cost_usd_total": round(sum(
            float(v.get("cost_usd") or 0.0)
            for v in stages.values() if isinstance(v, dict)
        ), 6),
        "hashes": {
            "parent_files": parent_file_hashes(),
            "wrapper_files": wrapper_file_hashes(),
        },
        "git": {
            "pipeline": git_state(PIPELINE_DIR),
            "benchmark_clone": git_state(BENCHMARK_CLONE),
        },
        "environment": {
            "packages": package_versions(),
            "minilm": minilm_snapshot(),
        },
    }
    manifest["cost_report"] = write_cost_report(
        protocol=protocol, run_dir=run_dir, manifest=manifest,
        migrated_stages=migrated_stage_names(run_dir),
    )
    manifest["rate_limit_retries"] = rate_limits.count
    manifest["request_audit"] = {
        "calls_recorded": len(request_audit or {}),
        "note": ("sha256 of the system prompt and of the full outbound payload per "
                 "distinct request; the rendered prompt set is in prompts/rendered.json"),
        "requests": request_audit or {},
    }
    write_json(manifest_path, manifest)
    try:
        anonymity.assert_clean(run_dir)
    except anonymity.AnonymityError as exc:
        # Record before raising: a namespace that fails the double-blind gate must
        # still be diagnosable without re-running the whole protocol.
        aborts_dir = run_dir / "aborts"
        aborts_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        record = {
            "anonymity_violation": {
                "message": str(exc),
                "findings": anonymity.scan(),
            },
            "aborted_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_key": run_key,
            "stages_completed": sorted(stages),
            "note": ("Every stage above completed and its artifacts are on disk; the run "
                     "stopped at the anonymity gate. Fix the leak and re-run — the cache "
                     "makes it free."),
        }
        write_json(aborts_dir / f"anonymity_{stamp}.json", record)
        write_json(run_dir / "abort.json", record)
        raise
    assert_clone_clean("after run")

    if run_eval and "eval" not in skipped and not stages["export"].get("skipped"):
        import evaluation
        eval_result = evaluation.evaluate_protocol(
            protocol=protocol,
            exported_fsm=export_path,
            output_base=run_dir,
            model_name=MODEL_NAME,
        )
        manifest["eval"] = eval_result.get("official")
        write_json(manifest_path, manifest)

    LOGGER.info("Manifest: %s", manifest_path)
    return manifest


COST_STAGE_DIRS = {
    "stage_a1": ("stage_a1/cost", "stage_a1_cost.json"),
    "stage_a2": ("stage_a2/cost", "stage_a2_cost.json"),
    "stage_a12": ("stage_a12/cost", None),
    "verify_stage_a12": ("verify_stage_a12/cost", None),
    "stage_b1": ("stage_b1/cost", None),
}


def _stage_cost(cost_dir: Path, skip_name: str | None) -> dict[str, Any]:
    totals = aggregate_cost_dir(cost_dir, skip=(skip_name,) if skip_name else ())
    tokens_in = tokens_out = 0
    if cost_dir.is_dir():
        for path in _cost_files(cost_dir, (skip_name,) if skip_name else ()):
            try:
                record = read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                tokens_in += int(record.get("input_tokens") or 0)
                tokens_out += int(record.get("output_tokens") or 0)
    return {"llm_calls": totals["llm_calls"], "input_tokens": tokens_in,
            "output_tokens": tokens_out, "total_tokens": totals["total_tokens"],
            "cost_usd": totals["cost_usd"]}


def _run_cost_total(run_dir: Path) -> float:
    """What this run's artifacts cost, recomputed from its per-call records."""
    return round(sum(
        _stage_cost(run_dir / relative, skip_name)["cost_usd"]
        for relative, skip_name in COST_STAGE_DIRS.values()
    ), 6)


def write_cost_report(
    *,
    protocol: str,
    run_dir: Path,
    manifest: dict[str, Any],
    migrated_stages: list[str],
) -> dict[str, Any]:
    """
    protocols/<P>/output/cost.json — the cost of THIS run, and nothing else.

    Always recomputed from per-call cost records rather than from a stage's own
    summary, so a resumed run reports the true cost of the artifacts it holds.
    Stage B's per-turn records are excluded by aggregate_cost_dir, since it writes
    the same figure as both a run total and a per-turn record.
    """
    stages_report = {
        stage: {**_stage_cost(run_dir / relative, skip_name),
                "wall_clock_seconds": (manifest.get("stages", {}).get(
                    "stage_b" if stage == "stage_b1" else stage, {}) or {}
                ).get("elapsed_seconds"),
                "model": (manifest.get("models", {}).get(
                    {"stage_a1": "stage_a1_a2", "stage_a2": "stage_a1_a2",
                     "stage_a12": "merge", "verify_stage_a12": "verify",
                     "stage_b1": "stage_b"}[stage], {}) or {}).get("model")}
        for stage, (relative, skip_name) in COST_STAGE_DIRS.items()
    }
    report = {
        "protocol": protocol,
        "run_key": manifest.get("run_key"),
        "prompt_template_sha256": (manifest.get("prompts") or {}).get(
            "prompt_template_sha256"),
        "stages": stages_report,
        "total_llm_calls": sum(v["llm_calls"] for v in stages_report.values()),
        "total_tokens": sum(v["total_tokens"] for v in stages_report.values()),
        "total_cost_usd": round(sum(v["cost_usd"] for v in stages_report.values()), 6),
        "total_wall_clock_s": manifest.get("wall_clock_seconds"),
        "workers": (manifest.get("concurrency") or {}).get("stage_a_workers"),
        "stages_reused_from_an_earlier_run": migrated_stages,
        "note": ("Cost of this run's artifacts, recomputed from per-call records. "
                 "Stages listed in stages_reused_from_an_earlier_run were computed "
                 "under an earlier run_key and copied in unchanged; their cost is "
                 "included here because they are part of this result."),
    }
    write_json(protocol_output_dir(protocol) / "cost.json", report)
    return report


def refresh_cost_report(protocol: str) -> dict[str, Any] | None:
    """
    Rebuild protocols/<P>/output/cost.json for the CURRENT run, without running
    anything. output/ holds exactly one namespace, so that namespace is the subject
    even if it has not finished — an unfinished run reports what it has spent so far
    and says so, rather than silently reporting its predecessor's numbers.
    """
    output_dir = protocol_output_dir(protocol)
    if not output_dir.is_dir():
        return None
    candidates = [d for d in sorted(output_dir.iterdir()) if d.is_dir()]
    if not candidates:
        return None
    current_key = prepare_protocol(protocol)["run_key"]
    run_dir = next((d for d in candidates if d.name == current_key), candidates[-1])
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {
        "run_key": run_dir.name,
        "prompts": {"prompt_template_sha256": prompt_lib.template_sha256()},
    }
    report = write_cost_report(protocol=protocol, run_dir=run_dir, manifest=manifest,
                               migrated_stages=migrated_stage_names(run_dir))
    if not manifest_path.is_file():
        report["status"] = "incomplete: this run has not finished; no manifest yet"
        report["eval"] = "pending"
        write_json(output_dir / "cost.json", report)
    return report

def migrated_stage_names(run_dir: Path) -> list[str]:
    migration = run_dir / "migration.json"
    if not migration.is_file():
        return []
    try:
        return list(read_json(migration).get("migrated_stages") or [])
    except (OSError, json.JSONDecodeError):
        return []


def assert_verify_totals_agree(verify_dir: Path) -> dict[str, Any]:
    """
    The verification report, the stage cost summary and the per-call cost files must
    tell the same story. They can diverge when sections come from cache — the parent
    returns early without repopulating cost_records (verify_stage_a.py:466-469) — so
    this is checked, not assumed.
    """
    report = read_json(verify_dir / "final_report.json").get("summary", {})
    summary_path = verify_dir / "verify_stage_a12_cost.json"
    cost_summary = read_json(summary_path) if summary_path.is_file() else {}
    per_call = aggregate_cost_dir(verify_dir / "cost")

    report_calls = int(report.get("llm_calls", 0))
    summary_calls = int(cost_summary.get("llm_calls", 0))
    if not (report_calls == summary_calls == per_call["llm_calls"]):
        raise RunError(
            "verify accounting disagrees: report says "
            f"{report_calls} calls, {summary_path.name} says {summary_calls}, "
            f"per-call files say {per_call['llm_calls']}"
        )
    report_tokens = int(report.get("total_tokens", 0))
    if report_tokens != per_call["total_tokens"]:
        raise RunError(
            f"verify token totals disagree: report {report_tokens} vs per-call "
            f"files {per_call['total_tokens']}"
        )
    return {
        "accounting_checked": True,
        "report_llm_calls": report_calls,
        "report_total_tokens": report_tokens,
    }


def run_protocol(**kwargs: Any) -> dict[str, Any]:
    """
    Public entry point. Owns the rate-limit counter and the fatal gate so that both
    survive an abort: the counter is detached in a finally, and a PoolFatalError
    raised inside a worker is converted into the FatalApiError the CLI knows how to
    turn into exit 3 plus an abort record.
    """
    rate_limits = concurrency.RateLimitCounter()
    gate = concurrency.FatalGate(_is_fatal_api_error)
    request_audit: dict[str, dict[str, Any]] = {}
    try:
        # One boundary for the whole run: it covers Stage A, merge, verify and Stage B,
        # every retry included, and records what was actually sent.
        with rate_limits.attached(), concurrency.model_call_boundary(
            gate=gate, audit=request_audit
        ):
            return _run_protocol_inner(rate_limits=rate_limits, gate=gate,
                                       request_audit=request_audit, **kwargs)
    except concurrency.PoolFatalError as exc:
        detail = exc.detail
        match = _REGAIN_ACCESS_RE.search(detail)
        regain = match.group(1) if match else None
        suffix = f" Access returns {regain}." if regain else ""
        raise FatalApiError(
            f"fatal API error inside a worker pool; queued sections were cancelled."
            f"{suffix} Upstream message: {detail[:300]}",
            detail=detail,
            regain_access=regain,
        ) from exc
    finally:
        LAST_RATE_LIMIT_COUNT["value"] = rate_limits.count


LAST_RATE_LIMIT_COUNT: dict[str, int] = {"value": 0}

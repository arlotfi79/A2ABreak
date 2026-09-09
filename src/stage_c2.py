#!/usr/bin/env python3
"""
stage_c2.py
-----------
Stage C2: adversarial verification of C1 candidates against the unified FSM.

Reads `system_prompts.stage_c2` and `model.stage_models.stage_c2` from config.yaml.
Loads:
  - all `outputs/stage_c1_run*/candidates.json` files (merged; repeatable --c1-input)
  - `outputs/stage_b4/unified_fsm_llm_dedup.json` (states/transitions only — no structural_issues)

Output:
  outputs/stage_c2/verified.json
  outputs/stage_c2/iterations/c2/c2_turn_NN.json
  outputs/stage_c2/cost/c2.json
  outputs/stage_c2/stage_c2_cost.json

Usage:
    python stage_c2.py
    python stage_c2.py --no-resume
    python stage_c2.py --c1-input outputs/stage_c1_run1/candidates.json \\
                       --c1-input outputs/stage_c1_run2/candidates.json
    python stage_c2.py --fsm-input outputs/stage_b4/unified_fsm_llm_dedup.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import litellm
import yaml
from dotenv import load_dotenv

from stage_b2 import (
    call_with_search,
    _resolve_settings,
    _web_search_completion_kwargs,
)

load_dotenv(Path(__file__).parent / ".env")

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
STAGE_TAG = "stage_c2"
C1_STAGE_TAG = "stage_c1"
C1_RUN_GLOB = "stage_c1_run*"
B4_STAGE_TAG = "stage_b4"
DEFAULT_FSM_NAME = "unified_fsm_llm_dedup.json"
RUN_ID = "c2"
DEFAULT_MODEL = "anthropic/claude-opus-4-6"

_SEVERITIES = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW"})
_STEP_FAILED = frozenset({"STEP_0", "STEP_1", "STEP_2", "STEP_3"})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_model(cfg: dict) -> str:
    stage_models = (cfg.get("model") or {}).get("stage_models") or {}
    raw = stage_models.get(STAGE_TAG)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return DEFAULT_MODEL


def _resolve_prompt(cfg: dict) -> str:
    prompt = (cfg.get("system_prompts") or {}).get(STAGE_TAG)
    if not prompt:
        raise ValueError(f"Missing system_prompts.{STAGE_TAG} in config.yaml")
    return prompt.strip()


def _resolve_c2_settings(cfg: dict) -> dict:
    m = cfg.get("model") or {}
    c2 = m.get("stage_c2") or {}
    raw = c2.get("max_tokens", m.get("max_tokens"))
    max_tokens = int(raw) if raw is not None else None
    base = _resolve_settings(cfg)
    return {**base, "max_tokens": max_tokens}


def _reasoning_effort(cfg: dict) -> str:
    eff = (cfg.get("model") or {}).get("stage_effort") or {}
    v = eff.get(STAGE_TAG)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return "high"


def _default_paths(cfg: dict) -> tuple[Path, Path, Path, Path, Path]:
    base = SCRIPT_DIR / cfg["output"]["base_dir"]
    fsm_path = base / B4_STAGE_TAG / DEFAULT_FSM_NAME
    out_dir = base / STAGE_TAG
    cost_dir = out_dir / "cost"
    out_path = out_dir / "verified.json"
    return base, fsm_path, out_path, out_dir, cost_dir


def _discover_c1_candidate_files(base_dir: Path) -> list[Path]:
    """All stage_c1_run*/candidates.json under outputs, sorted by run folder name."""
    paths = sorted(base_dir.glob(f"{C1_RUN_GLOB}/candidates.json"))
    if not paths:
        legacy = base_dir / C1_STAGE_TAG / "candidates.json"
        if legacy.is_file():
            return [legacy]
    return paths


def _run_label(run_dir_name: str) -> str:
    prefix = f"{C1_STAGE_TAG}_"
    if run_dir_name.startswith(prefix):
        return run_dir_name[len(prefix) :]
    return run_dir_name


def _unique_candidate_id(run_label: str, original_id: str) -> str:
    return f"{run_label}:{original_id}"


def _load_unified_fsm(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(
            f"Deduped unified FSM not found: {path} — run dedup_fsm.py and fsm_dedup_llm.py first."
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    if not data.get("states"):
        raise ValueError(f"{path}: missing or empty 'states'")
    if not data.get("transitions"):
        raise ValueError(f"{path}: missing or empty 'transitions'")
    return data


def _load_c1_candidates_file(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"C1 candidates not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    if "candidates" not in data:
        raise ValueError(f"{path}: missing 'candidates'")
    if not isinstance(data["candidates"], list):
        raise ValueError(f"{path}: 'candidates' must be a list")
    for i, c in enumerate(data["candidates"]):
        if not isinstance(c, dict):
            raise ValueError(f"{path}: candidates[{i}] must be an object")
        if not c.get("candidate_id"):
            raise ValueError(f"{path}: candidates[{i}] missing candidate_id")
    observations = data.get("structural_observations", [])
    if not isinstance(observations, list):
        raise ValueError(f"{path}: 'structural_observations' must be a list")
    return {
        "candidates": data["candidates"],
        "structural_observations": observations,
    }


def _load_merged_c1_candidates(paths: list[Path]) -> dict:
    """Merge candidates from every C1 run; assign globally unique candidate_id values."""
    if not paths:
        raise FileNotFoundError(
            f"No C1 candidate files found — expected {C1_RUN_GLOB}/candidates.json "
            f"under outputs/ or pass --c1-input."
        )

    merged_candidates: list[dict] = []
    merged_observations: list[dict] = []
    c1_sources: list[str] = []

    for path in paths:
        run_dir = path.parent.name
        run_label = _run_label(run_dir)
        c1_sources.append(str(path))
        data = _load_c1_candidates_file(path)

        if not data["candidates"]:
            logger.warning("Skipping %s — empty candidates list", path)
            continue

        for c in data["candidates"]:
            original_id = c["candidate_id"]
            entry = dict(c)
            entry["c1_source_run"] = run_dir
            entry["c1_original_candidate_id"] = original_id
            entry["candidate_id"] = _unique_candidate_id(run_label, original_id)
            merged_candidates.append(entry)

        for obs in data["structural_observations"]:
            text = obs if isinstance(obs, str) else json.dumps(obs, ensure_ascii=False)
            merged_observations.append(
                {"c1_source_run": run_dir, "observation": text}
            )

    if not merged_candidates:
        raise ValueError(
            "No candidates loaded from C1 runs — all inputs were empty or missing."
        )

    logger.info(
        "Merged %d candidate(s) from %d C1 run(s): %s",
        len(merged_candidates),
        len(paths),
        ", ".join(p.parent.name for p in paths if p.is_file()),
    )

    return {
        "candidates": merged_candidates,
        "structural_observations": merged_observations,
        "c1_sources": c1_sources,
    }


def _build_unified_fsm_for_llm(unified: dict) -> dict:
    """B3 graph for C2: only states and transitions."""
    return {
        "states": unified.get("states") or [],
        "transitions": unified.get("transitions") or [],
    }


def _build_llm_payload(unified: dict, c1: dict) -> dict:
    return {
        "unified_fsm": _build_unified_fsm_for_llm(unified),
        "candidates": c1.get("candidates") or [],
        "structural_observations": c1.get("structural_observations") or [],
    }


def _validate_c2_result(data: dict) -> None:
    for key in ("findings", "discarded", "verified_properties", "inconclusive"):
        if key not in data:
            raise ValueError(f"C2 JSON missing required key: {key!r}")
        if not isinstance(data[key], list):
            raise ValueError(f"C2 JSON key {key!r} must be a list")

    for i, f in enumerate(data["findings"]):
        if not isinstance(f, dict):
            raise ValueError(f"findings[{i}] must be an object")
        if not f.get("finding_id"):
            raise ValueError(f"findings[{i}] missing finding_id")
        if not f.get("candidate_id"):
            raise ValueError(f"findings[{i}] missing candidate_id")
        sev = f.get("severity")
        if sev not in _SEVERITIES:
            raise ValueError(
                f"findings[{i}] severity must be one of {_SEVERITIES}, got {sev!r}"
            )

    for i, d in enumerate(data["discarded"]):
        if not isinstance(d, dict):
            raise ValueError(f"discarded[{i}] must be an object")
        if not d.get("candidate_id"):
            raise ValueError(f"discarded[{i}] missing candidate_id")
        step = d.get("step_failed")
        if step is not None and step not in _STEP_FAILED:
            raise ValueError(
                f"discarded[{i}] step_failed must be one of {_STEP_FAILED}, got {step!r}"
            )

    for i, inc in enumerate(data["inconclusive"]):
        if not isinstance(inc, dict):
            raise ValueError(f"inconclusive[{i}] must be an object")
        if not inc.get("candidate_id"):
            raise ValueError(f"inconclusive[{i}] missing candidate_id")


def _c2_complete(out_path: Path) -> bool:
    if not out_path.is_file():
        return False
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _validate_c2_result(data)
        return True
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _print_summary(data: dict) -> None:
    print(f"\n{'=' * 60}")
    print("=== STAGE C2 — VERIFIED FINDINGS ===")
    for f in data.get("findings", []):
        fid = f.get("finding_id", "?")
        cid = f.get("candidate_id", "?")
        sev = f.get("severity", "?")
        title = f.get("title", "?")
        print(f"  {fid} ({cid})  [{sev}]  {title}")

    discarded = data.get("discarded") or []
    if discarded:
        print("\n=== DISCARDED ===")
        for d in discarded:
            print(
                f"  {d.get('candidate_id', '?')}  "
                f"{d.get('step_failed', '?')}: "
                f"{str(d.get('reason', ''))[:80]}"
            )

    inconclusive = data.get("inconclusive") or []
    if inconclusive:
        print("\n=== INCONCLUSIVE ===")
        for inc in inconclusive:
            print(
                f"  {inc.get('candidate_id', '?')}: "
                f"{str(inc.get('reason', ''))[:80]}"
            )

    props = data.get("verified_properties") or []
    if props:
        print("\n=== VERIFIED PROPERTIES ===")
        for p in props:
            print(f"  {p.get('property', '?')}")


def run_stage_c2(
    *,
    cfg: dict,
    c1_paths: list[Path],
    fsm_path: Path,
    out_path: Path,
    out_dir: Path,
    cost_dir: Path,
    resume: bool,
) -> dict:
    model = _resolve_model(cfg)
    prompt = _resolve_prompt(cfg)
    settings = _resolve_c2_settings(cfg)
    effort = _reasoning_effort(cfg)

    logger.info("=" * 60)
    logger.info("STAGE C2 — candidate verification (web search enabled)")
    logger.info("=" * 60)
    logger.info("C1 inputs (%d):", len(c1_paths))
    for p in c1_paths:
        logger.info("  %s", p)
    logger.info("FSM input: %s", fsm_path)
    logger.info("Output:    %s", out_path)
    logger.info("Model:     %s", model)

    if resume and _c2_complete(out_path):
        logger.info("Skipping — valid output exists: %s", out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.load(f)
        cost_file = cost_dir / f"{RUN_ID}.json"
        cost: dict = {"skipped": True}
        if cost_file.is_file():
            with open(cost_file, "r", encoding="utf-8") as f:
                cost = json.load(f)
        return {"result": result, "cost": cost, "skipped": True}

    c1 = _load_merged_c1_candidates(c1_paths)
    unified = _load_unified_fsm(fsm_path)
    llm_input = _build_llm_payload(unified, c1)

    n_candidates = len(llm_input["candidates"])
    c1_sources = c1.get("c1_sources") or [str(p) for p in c1_paths]
    n_states = len(llm_input["unified_fsm"]["states"])
    n_trans = len(llm_input["unified_fsm"]["transitions"])
    n_obs = len(llm_input["structural_observations"])

    user_message = (
        "Perform Stage C2 verification on the A2A protocol using the inputs below.\n\n"
        "INPUT keys:\n"
        "  - unified_fsm: deduped Stage B4 graph (states and transitions only)\n"
        "  - candidates: merged from all Stage C1 runs (unique candidate_id per run)\n"
        "  - structural_observations: merged Stage C1 background context\n\n"
        "Execute the five-step verification process for each candidate. "
        "Use web search wherever spec confirmation is required.\n\n"
        "Respond with ONLY the JSON object described in your instructions "
        "(findings, discarded, verified_properties, inconclusive).\n\n"
        f"{json.dumps(llm_input, indent=2)}"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    cost_dir.mkdir(parents=True, exist_ok=True)

    web_kwargs = _web_search_completion_kwargs(cfg)
    cap = (
        f"max_tokens={settings['max_tokens']}"
        if settings["max_tokens"] is not None
        else "max_tokens=unlimited (model max)"
    )
    logger.info(
        "Calling model (web search, context=%s, %s, reasoning_effort=%s, "
        "timeout=%ss, %d candidates, unified: %d states / %d transitions, "
        "%d structural_observations)...",
        web_kwargs["web_search_options"]["search_context_size"],
        cap,
        effort,
        settings["timeout"],
        n_candidates,
        n_states,
        n_trans,
        n_obs,
    )

    result, cost_record = call_with_search(
        protocol_phase=RUN_ID,
        model=model,
        prompt=prompt,
        user_msg=user_message,
        max_tokens=settings["max_tokens"],
        max_retries=settings["max_retries"],
        retry_wait=settings["retry_wait"],
        timeout=settings["timeout"],
        web_search_kwargs=web_kwargs,
        out_dir=out_dir,
        out_path=out_path,
        cost_dir=cost_dir,
    )

    _validate_c2_result(result)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    cost_file = cost_dir / f"{RUN_ID}.json"
    with open(cost_file, "w", encoding="utf-8") as f:
        json.dump(cost_record, f, indent=2, ensure_ascii=False)

    c1_ids = {c.get("candidate_id") for c in llm_input["candidates"]}
    accounted = set()
    for f in result.get("findings", []):
        accounted.add(f.get("candidate_id"))
    for d in result.get("discarded", []):
        accounted.add(d.get("candidate_id"))
    for inc in result.get("inconclusive", []):
        accounted.add(inc.get("candidate_id"))
    missing = c1_ids - accounted
    if missing:
        logger.warning(
            "C2 output does not account for C1 candidate(s): %s",
            ", ".join(sorted(missing)),
        )

    summary_path = out_dir / f"{STAGE_TAG}_cost.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "stage": STAGE_TAG,
                "model": model,
                "c1_sources": c1_sources,
                "unified_fsm_source": str(fsm_path),
                "input_candidate_count": n_candidates,
                "input_states": n_states,
                "input_transitions": n_trans,
                "finding_count": len(result.get("findings", [])),
                "discarded_count": len(result.get("discarded", [])),
                "inconclusive_count": len(result.get("inconclusive", [])),
                "verified_property_count": len(
                    result.get("verified_properties", [])
                ),
                "input_tokens": cost_record.get("input_tokens", 0),
                "output_tokens": cost_record.get("output_tokens", 0),
                "total_tokens": cost_record.get("total_tokens", 0),
                "total_cost_usd": cost_record.get("cost_usd"),
                "elapsed_seconds": cost_record.get("elapsed_seconds"),
                "per_turn": cost_record.get("per_turn", []),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(
        "C2 done: findings=%d  discarded=%d  inconclusive=%d  "
        "verified_properties=%d",
        len(result.get("findings", [])),
        len(result.get("discarded", [])),
        len(result.get("inconclusive", [])),
        len(result.get("verified_properties", [])),
    )
    logger.info("Cost summary: %s", summary_path)

    _print_summary(result)

    return {
        "result": result,
        "cost": cost_record,
        "out_path": str(out_path),
        "skipped": False,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage C2: verify C1 candidates against unified FSM and spec.",
    )
    p.add_argument(
        "--c1-input",
        action="append",
        type=Path,
        default=None,
        dest="c1_inputs",
        metavar="PATH",
        help=(
            "C1 candidates.json (repeatable). Default: merge all "
            "outputs/stage_c1_run*/candidates.json."
        ),
    )
    p.add_argument(
        "--fsm-input",
        type=Path,
        default=None,
        help=(
            "Path to deduped stage_b4 unified FSM JSON "
            f"(default: outputs/stage_b4/{DEFAULT_FSM_NAME})."
        ),
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: outputs/stage_c2/verified.json).",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-run even if outputs/stage_c2/verified.json already exists.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    resume = not args.no_resume
    cfg = _load_config()

    api_key = (cfg.get("api_keys") or {}).get("anthropic", "")
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        logger.error(
            "No Anthropic API key — set api_keys.anthropic in config.yaml "
            "or export ANTHROPIC_API_KEY."
        )
        sys.exit(1)

    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    model = _resolve_model(cfg)
    try:
        if not litellm.supports_web_search(model=model):
            logger.warning(
                "LiteLLM reports %s may not support web search — searches might be skipped.",
                model,
            )
    except Exception:
        pass

    base_dir, fsm_path, default_out, out_dir, cost_dir = _default_paths(cfg)
    if args.c1_inputs:
        c1_paths = list(args.c1_inputs)
    else:
        c1_paths = _discover_c1_candidate_files(base_dir)
    fsm_path = args.fsm_input if args.fsm_input is not None else fsm_path
    out_path = args.out if args.out is not None else default_out

    t0 = time.time()
    try:
        run_stage_c2(
            cfg=cfg,
            c1_paths=c1_paths,
            fsm_path=fsm_path,
            out_path=out_path,
            out_dir=out_dir,
            cost_dir=cost_dir,
            resume=resume,
        )
    except Exception:
        logger.exception("Stage C2 failed")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("STAGE C2 COMPLETE (%.1fs)", time.time() - t0)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
stage_c1.py
-----------
Stage C1: protocol-level security vulnerability discovery from the unified FSM.

Reads `system_prompts.stage_c1` (or a retry prompt selected with
`--prompt-key`) and `model.stage_models.stage_c1` from config.yaml.

Initial mode: `outputs/stage_b4/unified_fsm_llm_dedup.json` → `unified_fsm` states/transitions only.
Retry mode (`--retry`): FSM (states/transitions only) + merged C2 findings
from all `outputs/stage_c2_run*/verified.json` (or repeatable `--c2-input`).

Output:
  outputs/stage_c1/candidates.json
  outputs/stage_c1_retry/candidates.json   (--retry)
  outputs/stage_c1/iterations/c1/c1_turn_NN.json
  outputs/stage_c1/cost/c1.json
  outputs/stage_c1/cost/c1_turn_NN.json
  outputs/stage_c1/stage_c1_cost.json

Usage:
    python stage_c1.py
    python stage_c1.py --no-resume
    python stage_c1.py --input outputs/stage_b4/unified_fsm_llm_dedup.json

    # Second pass: new candidates only (after C2)
    python stage_c1.py --retry
    python stage_c1.py --retry --prompt-key stage_c1_retry_v2
    python stage_c1.py --retry --c2-input outputs/stage_c2_run1/verified.json \\
        --c2-input outputs/stage_c2_run2/verified.json --no-resume
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

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
STAGE_TAG        = "stage_c1"
STAGE_C1_RETRY   = "stage_c1_retry"
STAGE_C1_RETRY_V2 = "stage_c1_retry_v2"
B4_STAGE_TAG     = "stage_b4"
DEFAULT_FSM_NAME = "unified_fsm_llm_dedup.json"
C2_STAGE_TAG     = "stage_c2"
C2_RUN_GLOB      = "stage_c2_run*"
C1_RETRY_OUT_TAG = "stage_c1_retry"
C1_RETRY_V2_OUT_TAG = "stage_c1_retry_v2"
RUN_ID           = "c1"
RUN_ID_RETRY     = "c1_retry"
RUN_ID_RETRY_V2  = "c1_retry_v2"
DEFAULT_MODEL = "anthropic/claude-opus-4-6"

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


def _resolve_prompt_key(*, retry: bool, requested: str | None) -> str:
    key = requested.strip() if isinstance(requested, str) and requested.strip() else None
    if key is None:
        return STAGE_C1_RETRY if retry else STAGE_TAG
    if not retry and key != STAGE_TAG:
        raise ValueError("--prompt-key for non-retry C1 must be stage_c1")
    if retry and key == STAGE_TAG:
        raise ValueError("--retry requires a retry prompt, not stage_c1")
    return key


def _resolve_prompt(cfg: dict, *, prompt_key: str) -> str:
    prompt = (cfg.get("system_prompts") or {}).get(prompt_key)
    if not prompt:
        raise ValueError(f"Missing system_prompts.{prompt_key} in config.yaml")
    return prompt.strip()


def _resolve_c1_settings(cfg: dict) -> dict:
    m   = cfg.get("model") or {}
    c1  = m.get("stage_c1") or {}
    raw = c1.get("max_tokens", m.get("max_tokens"))
    max_tokens = int(raw) if raw is not None else None
    base = _resolve_settings(cfg)
    return {**base, "max_tokens": max_tokens}


def _reasoning_effort(cfg: dict) -> str:
    eff = (cfg.get("model") or {}).get("stage_effort") or {}
    v = eff.get(STAGE_TAG)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return "high"


def _default_paths(
    cfg: dict, *, retry: bool, prompt_key: str
) -> tuple[Path, Path, Path, Path]:
    base = SCRIPT_DIR / cfg["output"]["base_dir"]
    b4 = base / B4_STAGE_TAG
    if retry:
        out_dir = (
            base / C1_RETRY_V2_OUT_TAG
            if prompt_key == STAGE_C1_RETRY_V2
            else base / C1_RETRY_OUT_TAG
        )
    else:
        out_dir = base / STAGE_TAG
    cost_dir = out_dir / "cost"
    in_path = b4 / DEFAULT_FSM_NAME
    out_path = out_dir / "candidates.json"
    return base, in_path, out_path, out_dir, cost_dir


def _discover_c2_verified_files(base_dir: Path) -> list[Path]:
    """All stage_c2_run*/verified.json under outputs, sorted by folder name."""
    paths = sorted(base_dir.glob(f"{C2_RUN_GLOB}/verified.json"))
    if not paths:
        legacy = base_dir / C2_STAGE_TAG / "verified.json"
        if legacy.is_file():
            return [legacy]
    return paths


def _c2_run_label(run_dir_name: str) -> str:
    prefix = f"{C2_STAGE_TAG}_"
    if run_dir_name.startswith(prefix):
        return run_dir_name[len(prefix) :]
    return run_dir_name


def _unique_finding_id(run_label: str, original_id: str) -> str:
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


def _build_unified_fsm_for_llm(unified: dict, *, graph_only: bool = False) -> dict:
    states = unified.get("states") or []
    transitions = unified.get("transitions") or []
    _ = graph_only
    return {"states": states, "transitions": transitions}


def _build_llm_payload(unified: dict) -> dict:
    """Shape input for system_prompts.stage_c1."""
    return {"unified_fsm": _build_unified_fsm_for_llm(unified)}


def _load_c2_verified_file(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"C2 verified output not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # final_verified.json is sometimes hand-edited with //-commented blocks.
        stripped = "\n".join(
            line for line in raw.splitlines() if not line.lstrip().startswith("//")
        )
        stripped = re.sub(r",(\s*[\]}])", r"\1", stripped)
        data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    if "findings" in data:
        findings = data["findings"]
    elif "final_findings" in data:
        findings = data["final_findings"]
    else:
        raise ValueError(f"{path}: missing 'findings' or 'final_findings'")
    if not isinstance(findings, list):
        raise ValueError(f"{path}: findings must be a list")
    return findings


def _load_merged_c2_findings(paths: list[Path]) -> tuple[list[dict], list[str]]:
    """Merge findings from every C2 run; assign globally unique finding_id values."""
    if not paths:
        raise FileNotFoundError(
            f"No C2 verified files found — expected {C2_RUN_GLOB}/verified.json "
            f"under outputs/ or pass --c2-input."
        )

    merged: list[dict] = []
    sources: list[str] = []

    for path in paths:
        run_dir = path.parent.name
        run_label = _c2_run_label(run_dir)
        sources.append(str(path))
        findings = _load_c2_verified_file(path)
        if not findings:
            logger.warning("Skipping %s — empty findings list", path)
            continue
        for f in findings:
            if not isinstance(f, dict):
                raise ValueError(f"{path}: each finding must be an object")
            original_id = f.get("finding_id") or f"F-{len(merged) + 1:03d}"
            entry = dict(f)
            entry["c2_source_run"] = run_dir
            entry["c2_original_finding_id"] = original_id
            entry["finding_id"] = _unique_finding_id(run_label, original_id)
            merged.append(entry)

    if not merged:
        raise ValueError(
            "No verified findings loaded from C2 runs — all inputs were empty."
        )

    logger.info(
        "Merged %d verified finding(s) from %d C2 run(s): %s",
        len(merged),
        len(paths),
        ", ".join(p.parent.name for p in paths),
    )
    return merged, sources


def _build_protocol_operations(unified: dict) -> dict:
    """Compact operation inventory for stage_c1_retry_v2."""
    transitions = unified.get("transitions") or []
    states = unified.get("states") or []

    trigger_counts: dict[str, dict] = {}
    for t in transitions:
        if not isinstance(t, dict):
            continue
        trigger = str(t.get("trigger") or "").strip()
        if not trigger:
            continue
        item = trigger_counts.setdefault(
            trigger,
            {
                "trigger": trigger,
                "count": 0,
                "stages": set(),
                "actors": set(),
            },
        )
        item["count"] += 1
        for key in ("protocol_phase", "from_stage", "to_stage"):
            value = t.get(key)
            if value:
                item["stages"].add(str(value))
        actor = t.get("actor")
        if actor:
            item["actors"].add(str(actor))

    operations = []
    for item in trigger_counts.values():
        operations.append(
            {
                "trigger": item["trigger"],
                "count": item["count"],
                "stages": sorted(item["stages"]),
                "actors": sorted(item["actors"]),
            }
        )

    data_types = sorted(
        {
            str(s.get("state_name"))
            for s in states
            if isinstance(s, dict) and s.get("state_name")
        }
    )

    return {
        "operations": sorted(operations, key=lambda x: x["trigger"]),
        "data_types_or_state_names": data_types,
    }


def _build_retry_llm_payload(
    unified: dict, verified_findings: list[dict], *, prompt_key: str
) -> dict:
    """
    Shape input for retry prompts.

    No structural_issues from B1/B3 — only the graph (states + transitions)
    plus merged C2 findings.
    """
    graph = _build_unified_fsm_for_llm(unified, graph_only=True)
    if prompt_key == STAGE_C1_RETRY_V2:
        return {
            "unified_fsm": graph,
            "excluded_areas": verified_findings,
            "protocol_operations": _build_protocol_operations(unified),
        }
    return {
        "unified_fsm": graph,
        "verified_findings": verified_findings,
    }


def _validate_c1_result(data: dict) -> None:
    if "candidates" not in data:
        raise ValueError("C1 JSON missing required key: 'candidates'")
    if "structural_observations" not in data:
        raise ValueError("C1 JSON missing required key: 'structural_observations'")
    if not isinstance(data["candidates"], list):
        raise ValueError("'candidates' must be a list")
    if not isinstance(data["structural_observations"], list):
        raise ValueError("'structural_observations' must be a list")
    for i, c in enumerate(data["candidates"]):
        if not isinstance(c, dict):
            raise ValueError(f"candidates[{i}] must be an object")
        if not c.get("candidate_id"):
            raise ValueError(f"candidates[{i}] missing candidate_id")


def _c1_complete(out_path: Path) -> bool:
    if not out_path.is_file():
        return False
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _validate_c1_result(data)
        return True
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _print_summary(data: dict) -> None:
    print(f"\n{'=' * 60}")
    print("=== STAGE C1 — SECURITY CANDIDATES ===")
    for c in data.get("candidates", []):
        cid = c.get("candidate_id", "?")
        title = c.get("title", "?")
        conf = c.get("preliminary_confidence", "?")
        print(f"  {cid}  [{conf}]  {title}")

    obs = data.get("structural_observations") or []
    if obs:
        print("\n=== STRUCTURAL OBSERVATIONS ===")
        for line in obs:
            text = str(line)
            print(f"  {text[:100]}{'...' if len(text) > 100 else ''}")


def run_stage_c1(
    *,
    cfg: dict,
    prompt_key: str,
    unified_path: Path,
    c2_paths: list[Path] | None,
    out_path: Path,
    out_dir: Path,
    cost_dir: Path,
    resume: bool,
    retry: bool,
) -> dict:
    model = _resolve_model(cfg)
    prompt = _resolve_prompt(cfg, prompt_key=prompt_key)
    settings = _resolve_c1_settings(cfg)
    effort = _reasoning_effort(cfg)
    run_id = (
        RUN_ID_RETRY_V2
        if retry and prompt_key == STAGE_C1_RETRY_V2
        else RUN_ID_RETRY if retry else RUN_ID
    )

    logger.info("=" * 60)
    if retry:
        logger.info("STAGE C1 RETRY — second-pass discovery (web search enabled)")
    else:
        logger.info("STAGE C1 — security vulnerability discovery (web search enabled)")
    logger.info("=" * 60)
    logger.info("FSM input:  %s", unified_path)
    logger.info("Prompt:     system_prompts.%s", prompt_key)
    if retry and c2_paths:
        logger.info("C2 inputs (%d):", len(c2_paths))
        for p in c2_paths:
            logger.info("  %s", p)
    logger.info("Output:    %s", out_path)
    logger.info("Model:     %s", model)

    if resume and _c1_complete(out_path):
        logger.info("Skipping — valid output exists: %s", out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.load(f)
        cost_file = cost_dir / f"{run_id}.json"
        cost: dict = {"skipped": True}
        if cost_file.is_file():
            with open(cost_file, "r", encoding="utf-8") as f:
                cost = json.load(f)
        return {"result": result, "cost": cost, "skipped": True}

    unified = _load_unified_fsm(unified_path)
    n_gaps = 0
    n_unr = 0
    n_verified = 0

    c2_sources: list[str] = []
    if retry:
        if not c2_paths:
            raise ValueError("retry mode requires c2_paths")
        verified_findings, c2_sources = _load_merged_c2_findings(c2_paths)
        llm_input = _build_retry_llm_payload(
            unified, verified_findings, prompt_key=prompt_key
        )
        if prompt_key == STAGE_C1_RETRY_V2:
            n_verified = len(llm_input["excluded_areas"])
            user_message = (
                "Perform Stage C1 retry-v2 security analysis using the inputs below.\n\n"
                "INPUT keys:\n"
                "  - unified_fsm: states and transitions only (no structural_issues from B1/B3)\n"
                "  - excluded_areas: merged C2 findings only from every stage_c2_run* "
                "(no verified_properties included; unique finding_id per run)\n"
                "  - protocol_operations: operation/state inventory derived from the FSM\n\n"
                "Find NEW protocol-level candidates only. "
                "Respond with ONLY the JSON object described in your instructions "
                "(candidates, structural_observations).\n\n"
                f"{json.dumps(llm_input, indent=2)}"
            )
        else:
            n_verified = len(llm_input["verified_findings"])
            user_message = (
                "Perform Stage C1 retry (second-pass) security analysis using the inputs below.\n\n"
                "INPUT keys:\n"
                "  - unified_fsm: states and transitions only (no structural_issues from B1/B3)\n"
                "  - verified_findings: merged confirmed findings from all listed C2 runs "
                "(unique finding_id per run) — do not rediscover any of these\n\n"
                "Find NEW protocol-level candidates only. "
                "Respond with ONLY the JSON object described in your instructions "
                "(candidates, structural_observations).\n\n"
                f"{json.dumps(llm_input, indent=2)}"
            )
    else:
        llm_input = _build_llm_payload(unified)
        user_message = (
            "Perform Stage C1 security analysis on the A2A protocol using the inputs below.\n\n"
            "INPUT keys:\n"
            "  - unified_fsm: deduped Stage B4 graph, states and transitions only\n\n"
            "Respond with ONLY the JSON object described in your instructions "
            "(candidates, structural_observations).\n\n"
            f"{json.dumps(llm_input, indent=2)}"
        )

    n_states = len(llm_input["unified_fsm"]["states"])
    n_trans = len(llm_input["unified_fsm"]["transitions"])

    out_dir.mkdir(parents=True, exist_ok=True)
    cost_dir.mkdir(parents=True, exist_ok=True)

    web_kwargs = _web_search_completion_kwargs(cfg)
    cap = (
        f"max_tokens={settings['max_tokens']}"
        if settings["max_tokens"] is not None
        else "max_tokens=unlimited (model max)"
    )
    if retry:
        logger.info(
            "Calling model (web search, context=%s, %s, reasoning_effort=%s, "
            "timeout=%ss, unified: %d states / %d transitions, "
            "%s: %d)...",
            web_kwargs["web_search_options"]["search_context_size"],
            cap,
            effort,
            settings["timeout"],
            n_states,
            n_trans,
            (
                "excluded_areas"
                if prompt_key == STAGE_C1_RETRY_V2
                else "verified_findings"
            ),
            n_verified,
        )
    else:
        logger.info(
            "Calling model (web search, context=%s, %s, reasoning_effort=%s, "
            "timeout=%ss, unified: %d states / %d transitions)...",
            web_kwargs["web_search_options"]["search_context_size"],
            cap,
            effort,
            settings["timeout"],
            n_states,
            n_trans,
        )

    result, cost_record = call_with_search(
        protocol_phase=run_id,
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

    _validate_c1_result(result)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    cost_file = cost_dir / f"{run_id}.json"
    with open(cost_file, "w", encoding="utf-8") as f:
        json.dump(cost_record, f, indent=2, ensure_ascii=False)

    summary_path = out_dir / f"{prompt_key}_cost.json"
    summary = {
        "stage": prompt_key,
        "mode": "retry" if retry else "initial",
        "prompt_key": prompt_key,
        "model": model,
        "unified_fsm_source": str(unified_path),
        "input_states": n_states,
        "input_transitions": n_trans,
        "candidate_count": len(result.get("candidates", [])),
        "structural_observation_count": len(
            result.get("structural_observations", [])
        ),
        "input_tokens": cost_record.get("input_tokens", 0),
        "output_tokens": cost_record.get("output_tokens", 0),
        "total_tokens": cost_record.get("total_tokens", 0),
        "total_cost_usd": cost_record.get("cost_usd"),
        "elapsed_seconds": cost_record.get("elapsed_seconds"),
        "per_turn": cost_record.get("per_turn", []),
    }
    if retry:
        summary["c2_verified_sources"] = c2_sources
        if prompt_key == STAGE_C1_RETRY_V2:
            summary["input_excluded_areas"] = n_verified
        else:
            summary["input_verified_findings"] = n_verified
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info(
        "C1 done: candidates=%d  structural_observations=%d",
        len(result.get("candidates", [])),
        len(result.get("structural_observations", [])),
    )
    logger.info("Cost summary: %s", summary_path)

    _print_summary(result)

    return {
        "result":   result,
        "cost":     cost_record,
        "out_path": str(out_path),
        "skipped":  False,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage C1: protocol-level security candidates from unified FSM.",
    )
    p.add_argument(
        "--input",
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
        help="Output path (default: outputs/stage_c1/candidates.json).",
    )
    p.add_argument(
        "--retry",
        action="store_true",
        help=(
            "Second-pass C1 using a retry prompt. Merges C2 findings from "
            "outputs/stage_c2_run*/verified.json by default."
        ),
    )
    p.add_argument(
        "--prompt-key",
        default=None,
        help=(
            "system_prompts key to use. Defaults to stage_c1, or "
            "stage_c1_retry with --retry. Use stage_c1_retry_v2 for the "
            "excluded_areas payload."
        ),
    )
    p.add_argument(
        "--c2-input",
        action="append",
        type=Path,
        default=None,
        dest="c2_inputs",
        metavar="PATH",
        help=(
            "C2 verified.json (repeatable). Default with --retry: merge all "
            "outputs/stage_c2_run*/verified.json."
        ),
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-run even if the output candidates.json already exists.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    resume = not args.no_resume
    cfg = _load_config()
    prompt_key = _resolve_prompt_key(retry=args.retry, requested=args.prompt_key)

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

    base_dir, in_path, default_out, out_dir, cost_dir = _default_paths(
        cfg, retry=args.retry, prompt_key=prompt_key
    )
    unified_path = args.input if args.input is not None else in_path
    out_path = args.out if args.out is not None else default_out

    if args.retry:
        if args.c2_inputs:
            c2_paths = list(args.c2_inputs)
        else:
            c2_paths = _discover_c2_verified_files(base_dir)
    else:
        c2_paths = None
        if args.c2_inputs:
            logger.warning("--c2-input is ignored without --retry")

    t0 = time.time()
    try:
        run_stage_c1(
            cfg=cfg,
            prompt_key=prompt_key,
            unified_path=unified_path,
            c2_paths=c2_paths,
            out_path=out_path,
            out_dir=out_dir,
            cost_dir=cost_dir,
            resume=resume,
            retry=args.retry,
        )
    except Exception:
        logger.exception("Stage C1 failed")
        sys.exit(1)

    logger.info("=" * 60)
    label = prompt_key.upper()
    logger.info("%s COMPLETE (%.1fs)", label, time.time() - t0)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

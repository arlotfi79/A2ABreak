#!/usr/bin/env python3
"""
fsm_dedup_llm.py
----------------
Semantic FSM deduplication pass.

Input:
  outputs/stage_b4/unified_fsm_deterministic.json

Prompt:
  system_prompts.fsm_dedup from config.yaml

Output:
  outputs/stage_b4/unified_fsm_llm_dedup.json
  outputs/stage_b4/fsm_dedup_llm_raw_response.txt
  outputs/stage_b4/fsm_dedup_llm_cost.json

Usage:
  python3 fsm_dedup_llm.py
  python3 fsm_dedup_llm.py --input outputs/stage_b4/unified_fsm_deterministic.json \
      --output outputs/stage_b4/unified_fsm_llm_dedup.json --force
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from model_interface import ModelInterface

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
STAGE_TAG = "fsm_dedup"
DEFAULT_INPUT = SCRIPT_DIR / "outputs" / "stage_b4" / "unified_fsm_deterministic.json"
DEFAULT_OUTPUT = SCRIPT_DIR / "outputs" / "stage_b4" / "unified_fsm_llm_dedup.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_config(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_fsm(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"FSM input not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be a JSON object")
    if not isinstance(data.get("states"), list) or not data["states"]:
        raise ValueError(f"{path}: missing non-empty 'states' list")
    if not isinstance(data.get("transitions"), list):
        raise ValueError(f"{path}: missing 'transitions' list")
    if "llm_analysis_needed" not in data:
        logger.warning("%s has no llm_analysis_needed field", path)
    return data


def _resolve_model(cfg: dict[str, Any], requested: str | None) -> str | None:
    if requested and requested.strip():
        return requested.strip()

    model_cfg = cfg.get("model") or {}
    stage_models = model_cfg.get("stage_models") or {}
    for key in (STAGE_TAG, "stage_b3", "stage_c1"):
        raw = stage_models.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    raw_default = model_cfg.get("name")
    if isinstance(raw_default, str) and raw_default.strip():
        return raw_default.strip()
    return None


def _resolve_prompt(cfg: dict[str, Any]) -> str:
    prompt = (cfg.get("system_prompts") or {}).get(STAGE_TAG)
    if not prompt:
        raise ValueError(f"Missing system_prompts.{STAGE_TAG} in config.yaml")
    return str(prompt).strip()


def _build_user_prompt(fsm: dict[str, Any]) -> str:
    return (
        "Apply the semantic FSM deduplication pass to the input JSON below.\n"
        "Use the validation and llm_analysis_needed fields as the worklist.\n"
        "Return ONLY one complete valid JSON object for the corrected FSM.\n\n"
        "Input JSON:\n"
        f"{json.dumps(fsm, indent=2, ensure_ascii=False)}"
    )


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating surrounding text or markdown fences."""
    raw = _strip_json_fences(raw)
    if not raw:
        raise ValueError("empty model response")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object found in model response")

    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                parsed = json.loads(raw[start : idx + 1])
                if isinstance(parsed, dict):
                    return parsed
                break

    raise ValueError("could not extract a complete JSON object")


def _structural_validation(fsm: dict[str, Any]) -> dict[str, Any]:
    states = fsm.get("states") or []
    transitions = fsm.get("transitions") or []

    state_names: set[str] = set()
    duplicate_state_names: dict[str, int] = {}
    counts: dict[str, int] = {}
    for state in states:
        if not isinstance(state, dict):
            continue
        name = state.get("state_name")
        if not isinstance(name, str) or not name:
            continue
        counts[name] = counts.get(name, 0) + 1
        state_names.add(name)
    duplicate_state_names = {name: count for name, count in counts.items() if count > 1}

    initial_states = {
        s.get("state_name")
        for s in states
        if isinstance(s, dict) and s.get("is_initial") and isinstance(s.get("state_name"), str)
    }
    final_states = {
        s.get("state_name")
        for s in states
        if isinstance(s, dict) and s.get("is_final") and isinstance(s.get("state_name"), str)
    }

    incoming_real: set[str] = set()
    outgoing: set[str] = set()
    reference_errors: list[str] = []
    identity_interstage: list[dict[str, Any]] = []

    for idx, transition in enumerate(transitions):
        if not isinstance(transition, dict):
            reference_errors.append(f"transitions[{idx}] is not an object")
            continue
        from_state = transition.get("from_state")
        to_state = transition.get("to_state")
        if not isinstance(from_state, str) or not from_state:
            reference_errors.append(f"transitions[{idx}] missing from_state")
            continue
        if not isinstance(to_state, str) or not to_state:
            reference_errors.append(f"transitions[{idx}] missing to_state")
            continue
        if from_state not in state_names:
            reference_errors.append(f"Unknown from_state: {from_state}")
        if to_state not in state_names:
            reference_errors.append(f"Unknown to_state: {to_state}")
        outgoing.add(from_state)
        if from_state != to_state:
            incoming_real.add(to_state)
        if transition.get("kind") == "inter_stage" and from_state == to_state:
            identity_interstage.append(transition)

    orphaned = sorted(state_names - incoming_real - initial_states)
    dead_ends = sorted((state_names - outgoing) - final_states)
    all_passed = (
        not orphaned
        and not dead_ends
        and not reference_errors
        and not duplicate_state_names
        and not identity_interstage
    )

    return {
        "orphaned_states": orphaned,
        "dead_end_non_terminals": dead_ends,
        "reference_errors": reference_errors,
        "duplicate_state_names": duplicate_state_names,
        "identity_interstage_remaining": len(identity_interstage),
        "all_passed": all_passed,
    }


def _normalize_output(fsm: dict[str, Any], *, input_path: Path, cost: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fsm.get("states"), list):
        raise ValueError("model output missing 'states' list")
    if not isinstance(fsm.get("transitions"), list):
        raise ValueError("model output missing 'transitions' list")

    fsm["state_count"] = len(fsm["states"])
    fsm["transition_count"] = len(fsm["transitions"])
    fsm.setdefault("schema_version", "semantic_pass_v1")
    fsm["post_llm_validation"] = _structural_validation(fsm)
    fsm["fsm_dedup_llm_run"] = {
        "stage": STAGE_TAG,
        "input_path": str(input_path),
        "model": cost.get("model"),
        "input_tokens": cost.get("input_tokens"),
        "output_tokens": cost.get("output_tokens"),
        "total_tokens": cost.get("total_tokens"),
        "cost_usd": cost.get("cost_usd"),
        "elapsed_seconds": cost.get("elapsed_seconds"),
        "timestamp": cost.get("timestamp"),
    }
    return fsm


def run(
    *,
    input_path: Path,
    output_path: Path,
    config_path: Path,
    model_override: str | None,
    force: bool,
) -> dict[str, Any]:
    cfg = _load_config(config_path)
    system_prompt = _resolve_prompt(cfg)
    fsm = _load_fsm(input_path)

    if output_path.exists() and not force:
        raise FileExistsError(f"Output exists: {output_path} (pass --force to overwrite)")

    model = _resolve_model(cfg, model_override)
    logger.info("Input: %s", input_path)
    logger.info("Output: %s", output_path)
    logger.info("Model: %s", model or "(config default)")
    logger.info("Input FSM: %d states, %d transitions", len(fsm["states"]), len(fsm["transitions"]))

    mi = ModelInterface(str(config_path))
    result = mi.call(
        _build_user_prompt(fsm),
        stage=STAGE_TAG,
        system=system_prompt,
        json_mode=True,
        model_override=model,
    )

    raw_path = output_path.with_name(f"{output_path.stem}_raw_response.txt")
    cost_path = output_path.with_name(f"{output_path.stem}_cost.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(result.content)

    cost = result.to_dict()
    with open(cost_path, "w", encoding="utf-8") as f:
        json.dump(cost, f, indent=2, ensure_ascii=False)

    parsed = _extract_json_object(result.content)
    parsed = _normalize_output(parsed, input_path=input_path, cost=cost)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, indent=2, ensure_ascii=False)

    validation = parsed["post_llm_validation"]
    logger.info(
        "Wrote: %s (%d states, %d transitions)",
        output_path,
        parsed["state_count"],
        parsed["transition_count"],
    )
    logger.info("Structural validation all_passed=%s", validation["all_passed"])
    if not validation["all_passed"]:
        logger.warning("Orphans: %s", validation["orphaned_states"] or "none")
        logger.warning("Dead ends: %s", validation["dead_end_non_terminals"] or "none")
        logger.warning("Reference errors: %d", len(validation["reference_errors"]))

    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the fsm_dedup semantic LLM pass on a deterministic FSM JSON.",
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--model", default=None, help="Optional LiteLLM model override.")
    parser.add_argument("--force", action="store_true", help="Overwrite output if it exists.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        run(
            input_path=args.input,
            output_path=args.output,
            config_path=args.config,
            model_override=args.model,
            force=args.force,
        )
    except Exception as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
stage_b3.py
-----------
Stage B3: merge the six per-phase B2 FSMs into one cross-boundary model.

Reads `system_prompts.stage_b3` and `model.stage_models.stage_b3` from config.yaml.
Loads all six `outputs/stage_b2/<phase>.json` files and asks the model (with web
search) to emit only inter-stage transitions plus unresolved/gaps — see the
prompt in config for the JSON schema.

Output:
  outputs/stage_b3/inter_stage.json
  outputs/stage_b3/iterations/unified/unified_turn_NN.json
  outputs/stage_b3/cost/unified_turn_NN.json
  outputs/stage_b3/cost/unified_turns.json
  outputs/stage_b3/stage_b3_cost.json

Note: B3 reuses stage_b2.call_with_search for the HTTP loop only; per-turn
checkpoint JSON may still show pipeline_stage "stage_b2" inside those files.
The top-level stage_b3_cost.json and inter_stage.json are B3 artifacts.

Usage:
    python stage_b3.py
    python stage_b3.py --no-resume
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

from stage_b1 import STAGES as PROTOCOL_STAGES
from stage_b2 import (
    STAGE_TAG as B2_STAGE_TAG,
    call_with_search,
    _resolve_settings,
    _web_search_completion_kwargs,
)

load_dotenv(Path(__file__).parent / ".env")

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
STAGE_TAG   = "stage_b3"
RUN_ID      = "unified"
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


def _resolve_prompt(cfg: dict) -> str:
    prompt = (cfg.get("system_prompts") or {}).get(STAGE_TAG)
    if not prompt:
        raise ValueError(
            f"Missing system_prompts.{STAGE_TAG} in config.yaml"
        )
    return prompt.strip()


def _resolve_b3_settings(cfg: dict) -> dict:
    """Same knobs as B2; max_tokens from model.stage_b3 or global model."""
    m   = cfg.get("model") or {}
    b3  = m.get("stage_b3") or {}
    raw = b3.get("max_tokens", m.get("max_tokens"))
    max_tokens = int(raw) if raw is not None else None
    base = _resolve_settings(cfg)
    return {
        **base,
        "max_tokens": max_tokens,
    }


def _paths(cfg: dict) -> tuple[Path, Path, Path, Path]:
    base_dir = SCRIPT_DIR / cfg["output"]["base_dir"]
    b2_dir   = base_dir / B2_STAGE_TAG
    out_dir  = base_dir / STAGE_TAG
    cost_dir = out_dir / "cost"
    out_path = out_dir / "inter_stage.json"
    return b2_dir, out_path, out_dir, cost_dir


def _load_all_b2(b2_dir: Path) -> dict[str, dict]:
    missing: list[str] = []
    payload: dict[str, dict] = {}
    for phase in PROTOCOL_STAGES:
        p = b2_dir / f"{phase}.json"
        if not p.is_file():
            missing.append(str(p))
            continue
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{p} root must be a JSON object")
        if not (data.get("states") or []):
            missing.append(f"{p} (empty states)")
        payload[phase] = data
    if missing:
        raise FileNotFoundError(
            "Missing or incomplete B2 inputs — run stage_b2.py first:\n  "
            + "\n  ".join(missing)
        )
    if len(payload) != len(PROTOCOL_STAGES):
        raise RuntimeError(
            f"Expected {len(PROTOCOL_STAGES)} B2 files, got {len(payload)}"
        )
    return payload


def _validate_b3_result(data: dict) -> None:
    for key in ("inter_stage_transitions", "unresolved", "gaps"):
        if key not in data:
            raise ValueError(f"B3 JSON missing required key: {key!r}")
        if not isinstance(data[key], list):
            raise ValueError(f"B3 JSON key {key!r} must be a list")


def _b3_complete(out_path: Path) -> bool:
    if not out_path.is_file():
        return False
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _validate_b3_result(data)
        return True
    except (json.JSONDecodeError, OSError, ValueError):
        return False


def _print_summary(data: dict) -> None:
    print(f"\n{'=' * 60}")
    print("=== STAGE B3 — INTER-STAGE TRANSITIONS ===")
    for t in data.get("inter_stage_transitions", []):
        fs = t.get("from_stage", "?")
        fn = t.get("from_state", "?")
        ts = t.get("to_stage", "?")
        tn = t.get("to_state", "?")
        tr = t.get("trigger", "?")
        print(f"  [{fs}] {fn}  --{tr}-->  [{ts}] {tn}")

    if data.get("unresolved"):
        print("\n=== UNRESOLVED ===")
        for u in data["unresolved"]:
            print(
                f"  {u.get('from_stage')} / {u.get('from_state')} "
                f"→ {u.get('to_stage')}: {u.get('issue', '')[:80]}"
            )

    if data.get("gaps"):
        print("\n=== GAPS ===")
        for g in data["gaps"]:
            print(f"  {g.get('boundary', '?')}: {g.get('issue', '')[:80]}")


def run_stage_b3(*, cfg: dict, resume: bool) -> dict:
    model    = _resolve_model(cfg)
    prompt   = _resolve_prompt(cfg)
    settings = _resolve_b3_settings(cfg)

    b2_dir, out_path, out_dir, cost_dir = _paths(cfg)

    logger.info("=" * 60)
    logger.info("STAGE B3 — unified inter-stage FSM (web search enabled)")
    logger.info("=" * 60)
    logger.info(f"B2 dir:  {b2_dir}")
    logger.info(f"Output:  {out_path}")
    logger.info(f"Model:   {model}")

    if resume and _b3_complete(out_path):
        logger.info(f"Skipping — valid output exists: {out_path}")
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.load(f)
        cost_file = cost_dir / f"{RUN_ID}.json"
        cost: dict = {"skipped": True}
        if cost_file.is_file():
            with open(cost_file, "r", encoding="utf-8") as f:
                cost = json.load(f)
        return {"result": result, "cost": cost, "skipped": True}

    b2_fsms = _load_all_b2(b2_dir)

    user_message = (
        "Below is the complete Stage B2 FSM output for all six protocol phases. "
        "Follow your instructions: output ONLY the JSON object with "
        "`inter_stage_transitions`, `unresolved`, and `gaps`.\n\n"
        "If the conversation continues after tool use, any reminder to emit "
        "\"FSM\" JSON still means this same B2 object (those three keys only), "
        "not a per-phase states/transitions FSM.\n\n"
        f"{json.dumps({'stage_order': list(PROTOCOL_STAGES), 'b2_fsms': b2_fsms}, indent=2)}"
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
        f"Calling model (web search, context="
        f"{web_kwargs['web_search_options']['search_context_size']}, "
        f"{cap}, timeout={settings['timeout']}s)..."
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

    _validate_b3_result(result)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    cost_dir.mkdir(parents=True, exist_ok=True)
    cost_file = cost_dir / f"{RUN_ID}.json"
    with open(cost_file, "w", encoding="utf-8") as f:
        json.dump(cost_record, f, indent=2, ensure_ascii=False)

    summary_path = out_dir / f"{STAGE_TAG}_cost.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "stage":               STAGE_TAG,
                "model":               model,
                "input_tokens":        cost_record.get("input_tokens", 0),
                "output_tokens":       cost_record.get("output_tokens", 0),
                "total_tokens":        cost_record.get("total_tokens", 0),
                "total_cost_usd":      cost_record.get("cost_usd"),
                "elapsed_seconds":     cost_record.get("elapsed_seconds"),
                "inter_stage_count":   len(result.get("inter_stage_transitions", [])),
                "unresolved_count":    len(result.get("unresolved", [])),
                "gaps_count":          len(result.get("gaps", [])),
                "per_turn":            cost_record.get("per_turn", []),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(
        f"B3 done: transitions={len(result.get('inter_stage_transitions', []))}  "
        f"unresolved={len(result.get('unresolved', []))}  "
        f"gaps={len(result.get('gaps', []))}"
    )
    logger.info(f"Cost summary: {summary_path}")

    _print_summary(result)

    return {
        "result":   result,
        "cost":     cost_record,
        "out_path": str(out_path),
        "skipped":  False,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage B3: inter-stage transitions from all six B2 FSMs.",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-run even if outputs/stage_b3/inter_stage.json already exists.",
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
    litellm.set_verbose         = False
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    model = _resolve_model(cfg)
    try:
        if not litellm.supports_web_search(model=model):
            logger.warning(
                f"LiteLLM reports {model} may not support web search — "
                "searches might be skipped."
            )
    except Exception:
        pass

    t0 = time.time()
    try:
        run_stage_b3(cfg=cfg, resume=resume)
    except Exception:
        logger.exception("Stage B3 failed")
        raise

    logger.info("=" * 60)
    logger.info(f"STAGE B3 COMPLETE ({round(time.time() - t0, 1)}s)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

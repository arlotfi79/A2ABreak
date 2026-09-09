#!/usr/bin/env python3
"""
stage_b2.py
---------------
Stage B2: build a formal FSM for each A2A protocol phase.

Reads API key, retries, tokens, and system_prompts.stage_b2 from config.yaml.
Uses model.stage_models.stage_b2 (Opus by default).
Web search enabled — the model can search the A2A spec for missing transitions.

Input:  outputs/stage_b1/<phase>.json  (expects `fsm_input`)
Output: outputs/stage_b2/<phase>.json
        outputs/stage_b2/iterations/<phase>_turn_NN.json
Cost:   outputs/stage_b2/cost/<phase>.json
        outputs/stage_b2/cost/<phase>_turn_NN.json
        outputs/stage_b2/cost/<phase>_turns.json
        outputs/stage_b2/stage_b2_cost.json

Usage:
    python stage_b2.py
    python stage_b2.py --stage discovery
    python stage_b2.py --stage discovery authentication --no-resume
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import litellm
import yaml
from dotenv import load_dotenv

from stage_b1 import STAGES as PROTOCOL_STAGES

load_dotenv(Path(__file__).parent / ".env")

SCRIPT_DIR    = Path(__file__).parent
CONFIG_PATH   = SCRIPT_DIR / "config.yaml"
STAGE_TAG     = "stage_b2"
MAX_TURNS     = 10
DEFAULT_MODEL = "anthropic/claude-opus-4-6"

_FINISHED_REASONS = frozenset({"stop", "end_turn", "length", "max_tokens"})

_CONTINUATION_USER_MSG = (
    "Continue. If you still need spec lookups, use web search. "
    "Otherwise respond with ONLY a single valid JSON object for the FSM "
    "(no markdown, no commentary)."
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

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


def _resolve_settings(cfg: dict) -> dict:
    m = cfg.get("model") or {}
    ws  = cfg.get("web_search") or {}
    # Stage B2: no output cap — omit max_tokens so Anthropic uses model maximum.
    b2  = m.get("stage_b2") or {}
    raw = b2.get("max_tokens", m.get("max_tokens"))
    max_tokens = int(raw) if raw is not None else None
    return {
        "max_tokens":  max_tokens,
        "max_retries": int(m.get("max_retries", 5)),
        "retry_wait":  int(m.get("rate_limit_retry_wait_seconds", 60)),
        "phase_delay": int(m.get("section_delay_seconds", 5)),
        "timeout":     int(m.get("request_timeout_seconds", 1800)),
        "web_search_context_size": str(
            ws.get("search_context_size", "medium")
        ),
    }


def _is_retryable_completion_error(exc: Exception) -> tuple[bool, str]:
    """Return (should_retry, kind) for transient model/API failures."""
    err_lower = str(exc).lower()
    if "rate_limit" in err_lower or "rate limit" in err_lower:
        return True, "rate_limit"
    if "timeout" in err_lower or "timed out" in err_lower:
        return True, "timeout"
    transient_markers = (
        "serviceunavailable",
        "service unavailable",
        "503",
        "upstream connect error",
        "disconnect/reset before headers",
        "connection termination",
        "connection reset",
        "apiconnectionerror",
        "api connection",
        "bad gateway",
        "502",
        "gateway timeout",
        "504",
    )
    if any(marker in err_lower for marker in transient_markers):
        return True, "transient_api_error"
    return False, ""


def _web_search_completion_kwargs(cfg: dict) -> dict:
    """
    LiteLLM maps web_search_options → Anthropic web_search_20250305 server tool.
    Searches run server-side inside a single API call (not separate client turns).
    """
    size = _resolve_settings(cfg)["web_search_context_size"]
    if size not in ("low", "medium", "high"):
        size = "medium"
    return {"web_search_options": {"search_context_size": size}}


def _paths_for_phase(cfg: dict, protocol_phase: str) -> tuple[Path, Path, Path, Path]:
    base_dir = SCRIPT_DIR / cfg["output"]["base_dir"]
    in_path  = base_dir / "stage_b1" / f"{protocol_phase}.json"
    out_dir  = base_dir / STAGE_TAG
    cost_dir = out_dir / "cost"
    out_path = out_dir / f"{protocol_phase}.json"
    return in_path, out_path, out_dir, cost_dir


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_text_from_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text") or "")
            else:
                text = getattr(block, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(p for p in parts if p)
    return str(content)


def parse_fsm_json(raw: str) -> dict:
    raw = _strip_json_fences(raw)
    if not raw:
        raise ValueError("empty model text")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    if start < 0:
        raise ValueError("no JSON object found in model text")

    depth = 0
    for i in range(start, len(raw)):
        ch = raw[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = raw[start : i + 1]
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
                break

    raise ValueError("could not extract a complete JSON object from model text")


def _assistant_turn_for_history(message) -> dict:
    content = getattr(message, "content", None)
    if isinstance(content, list):
        return {"role": "assistant", "content": content}
    if content:
        return {"role": "assistant", "content": content}
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        return {
            "role":       "assistant",
            "content":    content or "",
            "tool_calls": [
                {
                    "id":   tc.id,
                    "type": "function",
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        }
    return {"role": "assistant", "content": content or ""}


def _needs_continuation(finish_reason: str, message) -> bool:
    if finish_reason in _FINISHED_REASONS:
        return False
    if finish_reason in ("tool_calls", "tool_use", "pause_turn"):
        return True
    content = getattr(message, "content", None)
    if isinstance(content, list):
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
            if btype in ("tool_use", "server_tool_use", "web_search_tool_use"):
                return True
    if getattr(message, "tool_calls", None):
        return True
    return False


def _append_continuation_turn(messages: list, message) -> None:
    messages.append(_assistant_turn_for_history(message))
    messages.append({"role": "user", "content": _CONTINUATION_USER_MSG})


def _iter_content_blocks(message) -> list[dict]:
    """Normalize assistant message.content to a list of block dicts."""
    content = getattr(message, "content", None)
    if isinstance(content, list):
        blocks: list[dict] = []
        for block in content:
            if isinstance(block, dict):
                blocks.append(block)
            else:
                btype = getattr(block, "type", None)
                if btype:
                    blocks.append({
                        "type": getattr(block, "type", ""),
                        "name": getattr(block, "name", None),
                        "input": getattr(block, "input", None),
                    })
        return blocks
    return []


def _usage_web_search_requests(response) -> int | None:
    """Billed search count from Anthropic usage (authoritative when present)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    server_tool_use = getattr(usage, "server_tool_use", None)
    if server_tool_use is not None:
        n = getattr(server_tool_use, "web_search_requests", None)
        if n is not None:
            return int(n)

    # LiteLLM also surfaces this on prompt_tokens_details
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        n = getattr(details, "web_search_requests", None)
        if n is not None:
            return int(n)

    return None


def _extract_web_search_activity(message, response) -> dict:
    """
  Analyze whether Anthropic server-side web search actually ran.

  With web_search_20250305, searches execute inside one HTTP request.
  finish_reason=stop can still include server_tool_use + result blocks.
    """
    blocks = _iter_content_blocks(message)

    queries: list[str] = []
    tool_use_blocks = 0
    result_blocks   = 0

    for block in blocks:
        btype = block.get("type", "")
        name  = block.get("name") or ""

        if btype == "server_tool_use" and name == "web_search":
            tool_use_blocks += 1
            inp = block.get("input") or {}
            q = inp.get("query", "") if isinstance(inp, dict) else ""
            if q:
                queries.append(str(q))
        elif btype in ("web_search_tool_result", "web_search_result"):
            result_blocks += 1

    billed = _usage_web_search_requests(response)
    executed = (billed or 0) > 0 or tool_use_blocks > 0 or result_blocks > 0

    return {
        "executed":              executed,
        "mode":                  "server_side_single_request",
        "queries":               queries,
        "tool_use_blocks":       tool_use_blocks,
        "result_blocks":         result_blocks,
        "requests_billed":       billed,
    }


def _log_web_search_activity(turn: int, activity: dict) -> None:
    billed   = activity.get("requests_billed")
    queries  = activity.get("queries") or []
    n_query  = len(queries)
    n_billed = billed if billed is not None else n_query

    if activity.get("executed"):
        logger.info(
            f"  Web search (inside turn {turn}, server-side): "
            f"{n_billed} billed request(s), "
            f"{activity.get('result_blocks', 0)} result block(s)"
        )
        for i, q in enumerate(queries, start=1):
            logger.info(f"    [{i}] {q[:140]}")
    else:
        logger.warning(
            f"  Web search: no server_tool_use / usage evidence on turn {turn} "
            f"(tool may be disabled or model answered without searching)"
        )


def _turn_cost_record(
    *,
    protocol_phase: str,
    model: str,
    turn: int,
    usage,
    cumulative_input: int,
    cumulative_output: int,
    elapsed: float,
    response,
    web_search_activity: dict | None = None,
) -> dict:
    turn_in  = getattr(usage, "prompt_tokens",     0) if usage else 0
    turn_out = getattr(usage, "completion_tokens", 0) if usage else 0
    rec = {
        "pipeline_stage":  STAGE_TAG,
        "protocol_phase":  protocol_phase,
        "model":           model,
        "turn":            turn,
        "input_tokens":    turn_in,
        "output_tokens":   turn_out,
        "total_tokens":    turn_in + turn_out,
        "cumulative_input_tokens":  cumulative_input,
        "cumulative_output_tokens": cumulative_output,
        "cumulative_total_tokens":  cumulative_input + cumulative_output,
        "elapsed_seconds": round(elapsed, 2),
        "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if response is not None:
        try:
            rec["cost_usd"] = round(
                litellm.completion_cost(completion_response=response), 8
            )
        except Exception:
            rec["cost_usd"] = None
    else:
        rec["cost_usd"] = None

    if web_search_activity:
        rec["web_search_requests"] = web_search_activity.get("requests_billed")
        rec["web_search_executed"] = web_search_activity.get("executed", False)
        rec["web_search_queries"]  = web_search_activity.get("queries", [])

    return rec


def _write_turn_cost_summary(
    cost_dir: Path,
    protocol_phase: str,
    per_turn: list[dict],
) -> None:
    total_cost = sum((t.get("cost_usd") or 0) for t in per_turn)
    summary = {
        "pipeline_stage":  STAGE_TAG,
        "protocol_phase":  protocol_phase,
        "turns":           len(per_turn),
        "total_cost_usd":  round(total_cost, 8),
        "per_turn":        per_turn,
    }
    path = cost_dir / f"{protocol_phase}_turns.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


def _save_iteration_checkpoint(
    *,
    protocol_phase: str,
    out_dir: Path,
    out_path: Path,
    cost_dir: Path,
    turn: int,
    status: str,
    finish_reason: str,
    raw_text: str,
    parsed_fsm: dict | None,
    web_search: dict,
    cost_turn: dict,
    per_turn_costs: list[dict],
) -> None:
    iter_dir = out_dir / "iterations" / protocol_phase
    iter_dir.mkdir(parents=True, exist_ok=True)
    cost_dir.mkdir(parents=True, exist_ok=True)

    turn_name = f"{protocol_phase}_turn_{turn:02d}"
    checkpoint = {
        "pipeline_stage": STAGE_TAG,
        "protocol_phase": protocol_phase,
        "turn":           turn,
        "status":         status,
        "finish_reason":  finish_reason,
        "web_search":     web_search,
        "web_searches":   web_search.get("queries", []),
        "raw_text_len":   len(raw_text),
        "raw_text":       raw_text,
        "fsm":            parsed_fsm,
        "cost":           cost_turn,
        "timestamp":      cost_turn.get("timestamp"),
    }

    iter_path = iter_dir / f"{turn_name}.json"
    with open(iter_path, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2, ensure_ascii=False)
    logger.info(f"  Checkpoint: {iter_path}")

    cost_turn_path = cost_dir / f"{turn_name}.json"
    with open(cost_turn_path, "w", encoding="utf-8") as f:
        json.dump(cost_turn, f, indent=2, ensure_ascii=False)

    turn_usd = cost_turn.get("cost_usd")
    cum_usd  = sum((t.get("cost_usd") or 0) for t in per_turn_costs)
    if turn_usd is not None:
        logger.info(
            f"  Turn cost: ${turn_usd:.6f}  "
            f"(turn tokens: {cost_turn['total_tokens']:,}; "
            f"cumulative: ${cum_usd:.6f}, "
            f"{cost_turn['cumulative_total_tokens']:,} tokens)"
        )
    else:
        logger.info(
            f"  Turn tokens: {cost_turn['total_tokens']:,}  "
            f"(cumulative: {cost_turn['cumulative_total_tokens']:,})"
        )

    _write_turn_cost_summary(cost_dir, protocol_phase, per_turn_costs)

    if parsed_fsm is not None:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(parsed_fsm, f, indent=2, ensure_ascii=False)
        logger.info(f"  Latest FSM: {out_path}")


def call_with_search(
    *,
    protocol_phase: str,
    model: str,
    prompt: str,
    user_msg: str,
    max_tokens: int | None,
    max_retries: int,
    retry_wait: int,
    timeout: int,
    web_search_kwargs: dict,
    out_dir: Path | None = None,
    out_path: Path | None = None,
    cost_dir: Path | None = None,
) -> tuple[dict, dict]:
    messages = [{"role": "user", "content": user_msg}]

    total_input      = 0
    total_output     = 0
    turns            = 0
    t0               = time.time()
    response         = None
    result           = None
    per_turn_costs: list[dict] = []
    save_checkpoints = (
        out_dir is not None and out_path is not None and cost_dir is not None
    )

    for turn in range(MAX_TURNS):
        turns = turn + 1

        last_err = None
        if turns == 1:
            cap_msg = (
                f"max_tokens={max_tokens}"
                if max_tokens is not None
                else "max_tokens unset (model maximum output)"
            )
            logger.info(
                f"  {cap_msg}; HTTP timeout: {timeout}s "
                f"(web search may run several minutes server-side)"
            )

        for attempt in range(max_retries):
            try:
                completion_kwargs: dict = {
                    "model":            model,
                    "messages":         messages,
                    "system":           prompt,
                    "reasoning_effort": "high",
                    "timeout":          timeout,
                    **web_search_kwargs,
                }
                if max_tokens is not None:
                    completion_kwargs["max_tokens"] = max_tokens
                response = litellm.completion(**completion_kwargs)
                break
            except Exception as e:
                last_err = e
                retryable, kind = _is_retryable_completion_error(e)
                if retryable:
                    wait = retry_wait * (attempt + 1)
                    logger.warning(
                        f"  {kind} (attempt {attempt+1}/{max_retries}), "
                        f"waiting {wait}s before retry..."
                    )
                    time.sleep(wait)
                else:
                    raise
        else:
            raise RuntimeError(
                f"Exceeded {max_retries} retries: {last_err}"
            )

        usage = getattr(response, "usage", None)
        total_input  += getattr(usage, "prompt_tokens",     0) if usage else 0
        total_output += getattr(usage, "completion_tokens", 0) if usage else 0

        finish_reason = response.choices[0].finish_reason
        message       = response.choices[0].message

        logger.info(
            f"  Turn {turns}: finish_reason={finish_reason}  "
            f"tokens={getattr(usage, 'total_tokens', 0):,}"
        )

        raw_text          = extract_text_from_content(
            getattr(message, "content", None)
        )
        web_search_activity = _extract_web_search_activity(message, response)
        _log_web_search_activity(turns, web_search_activity)

        elapsed   = time.time() - t0
        cost_turn = _turn_cost_record(
            protocol_phase=protocol_phase,
            model=model,
            turn=turns,
            usage=usage,
            cumulative_input=total_input,
            cumulative_output=total_output,
            elapsed=elapsed,
            response=response,
            web_search_activity=web_search_activity,
        )
        per_turn_costs.append(cost_turn)

        def _checkpoint(status: str, parsed: dict | None = None) -> None:
            if not save_checkpoints:
                return
            _save_iteration_checkpoint(
                protocol_phase=protocol_phase,
                out_dir=out_dir,
                out_path=out_path,
                cost_dir=cost_dir,
                turn=turns,
                status=status,
                finish_reason=finish_reason,
                raw_text=raw_text,
                parsed_fsm=parsed,
                web_search=web_search_activity,
                cost_turn=cost_turn,
                per_turn_costs=per_turn_costs,
            )

        if _needs_continuation(finish_reason, message):
            _append_continuation_turn(messages, message)
            _checkpoint("continuing")
            continue

        if finish_reason in _FINISHED_REASONS:
            if not raw_text.strip():
                logger.warning(
                    "  finish_reason=%s but no parseable text — "
                    "nudging model to emit FSM JSON",
                    finish_reason,
                )
                _append_continuation_turn(messages, message)
                _checkpoint("nudge_no_text")
                continue
            try:
                result = parse_fsm_json(raw_text)
                _checkpoint("complete", result)
                break
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "  JSON parse failed on final turn (%s) — nudging model",
                    e,
                )
                _append_continuation_turn(messages, message)
                _checkpoint("nudge_parse_failed")
                continue

        logger.warning(
            "  Unexpected finish_reason=%r — attempting to parse content",
            finish_reason,
        )
        if raw_text.strip():
            try:
                result = parse_fsm_json(raw_text)
                _checkpoint("complete", result)
                break
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(
                    "  JSON parse failed (%s) — nudging model", e
                )
                _append_continuation_turn(messages, message)
                _checkpoint("nudge_parse_failed")
                continue
        _append_continuation_turn(messages, message)
        _checkpoint("continuing")
        continue

    else:
        raise RuntimeError(
            f"[{protocol_phase}] agentic loop exceeded {MAX_TURNS} turns"
        )

    if result is None:
        raise RuntimeError(f"[{protocol_phase}] no FSM JSON produced")

    elapsed      = round(time.time() - t0, 2)
    total_tokens = total_input + total_output
    total_cost   = sum((t.get("cost_usd") or 0) for t in per_turn_costs)

    cost_record = {
        "pipeline_stage":  STAGE_TAG,
        "protocol_phase":  protocol_phase,
        "model":           model,
        "turns":           turns,
        "input_tokens":    total_input,
        "output_tokens":   total_output,
        "total_tokens":    total_tokens,
        "cost_usd":        round(total_cost, 8),
        "elapsed_seconds": elapsed,
        "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "per_turn":        per_turn_costs,
    }

    if save_checkpoints:
        _write_turn_cost_summary(cost_dir, protocol_phase, per_turn_costs)

    return result, cost_record


# ---------------------------------------------------------------------------
# Per-phase runner
# ---------------------------------------------------------------------------

def _phase_complete(out_path: Path) -> bool:
    if not out_path.is_file():
        return False
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("states"))
    except (json.JSONDecodeError, OSError):
        return False


def _print_fsm_summary(protocol_phase: str, result: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"=== {protocol_phase.upper()} — STATES ===")
    for s in result.get("states", []):
        print(
            f"  {str(s.get('state_name', '?')):45s}"
            f"  semantic={str(s.get('semantic_type', '?')):12s}"
            f"  initial={s.get('is_initial')}  final={s.get('is_final')}"
        )

    print(f"\n=== {protocol_phase.upper()} — TRANSITIONS ===")
    for t in result.get("transitions", []):
        fs = str(t.get("from_state") or "null")
        ts = str(t.get("to_state")   or "null")
        tr = str(t.get("trigger")    or "null")
        print(f"  {fs:35s} --{tr:35s}--> {ts}")

    if result.get("discarded_states"):
        print(f"\n=== {protocol_phase.upper()} — DISCARDED ===")
        for d in result["discarded_states"]:
            print(f"  {str(d.get('state_name', '?')):35s}  {d.get('reason', '')}")

    if result.get("unresolved"):
        print(f"\n=== {protocol_phase.upper()} — UNRESOLVED ===")
        for u in result["unresolved"]:
            print(f"  {str(u.get('source_id', '?')):20s}  {u.get('issue', '')}")

    if result.get("gaps"):
        print(f"\n=== {protocol_phase.upper()} — GAPS ===")
        for g in result["gaps"]:
            print(f"  {str(g.get('state_name', '?')):35s}  {g.get('issue', '')}")

    if result.get("inter_stage"):
        ist = result["inter_stage"]
        print(f"\n=== {protocol_phase.upper()} — INTER-STAGE ===")
        print(f"  entry_from_previous: {ist.get('entry_from_previous', [])}")
        print(f"  exit_to_next:        {ist.get('exit_to_next', [])}")


def run_protocol_phase(
    *,
    protocol_phase: str,
    cfg: dict,
    model: str,
    prompt: str,
    settings: dict,
    resume: bool,
) -> dict:
    in_path, out_path, out_dir, cost_dir = _paths_for_phase(cfg, protocol_phase)

    logger.info("=" * 60)
    logger.info(f"STAGE B2 — {protocol_phase} (web search enabled)")
    logger.info("=" * 60)
    logger.info(f"Input:  {in_path}")
    logger.info(f"Output: {out_path}")

    if resume and _phase_complete(out_path):
        logger.info(f"Skipping {protocol_phase} — output already exists: {out_path}")
        with open(out_path, "r", encoding="utf-8") as f:
            result = json.load(f)
        cost: dict = {"protocol_phase": protocol_phase, "skipped": True}
        cost_file = cost_dir / f"{protocol_phase}.json"
        if cost_file.is_file():
            with open(cost_file, "r", encoding="utf-8") as f:
                cost = json.load(f)
        return {
            "protocol_phase": protocol_phase,
            "result":         result,
            "cost":           cost,
            "out_path":       str(out_path),
            "skipped":        True,
        }

    if not in_path.is_file():
        raise FileNotFoundError(
            f"Input not found: {in_path} — re-run stage_b1.py first."
        )

    with open(in_path, "r", encoding="utf-8") as f:
        stage_data = json.load(f)

    fsm_input = stage_data.get("fsm_input")
    if not fsm_input or not isinstance(fsm_input, dict):
        raise ValueError(f"{in_path} missing 'fsm_input'")

    states_n      = len(fsm_input.get("states", []) or [])
    transitions_n = len(fsm_input.get("transitions", []) or [])
    logger.info(f"FSM input: {states_n} states, {transitions_n} transitions")

    user_message = (
        f"Build the FSM for the '{protocol_phase}' phase "
        f"of the A2A protocol.\n\n"
        f"INPUT:\n"
        f"{json.dumps({'stage': protocol_phase, 'fsm_input': fsm_input}, indent=2)}"
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    cost_dir.mkdir(parents=True, exist_ok=True)

    web_search_kwargs = _web_search_completion_kwargs(cfg)
    cap = (
        f"max_tokens={settings['max_tokens']}"
        if settings["max_tokens"] is not None
        else "max_tokens=unlimited (model max)"
    )
    logger.info(
        "Calling model (web search enabled, "
        f"context={web_search_kwargs['web_search_options']['search_context_size']}, "
        f"{cap}, timeout={settings['timeout']}s)..."
    )
    result, cost_record = call_with_search(
        protocol_phase=protocol_phase,
        model=model,
        prompt=prompt,
        user_msg=user_message,
        max_tokens=settings["max_tokens"],
        max_retries=settings["max_retries"],
        retry_wait=settings["retry_wait"],
        timeout=settings["timeout"],
        web_search_kwargs=web_search_kwargs,
        out_dir=out_dir,
        out_path=out_path,
        cost_dir=cost_dir,
    )

    logger.info(
        f"[{protocol_phase}] done in {cost_record['elapsed_seconds']}s  "
        f"({cost_record['turns']} turn(s))"
    )
    logger.info(
        f"[{protocol_phase}] tokens: {cost_record['total_tokens']:,}  "
        f"cost: ${cost_record['cost_usd']:.6f}"
    )
    logger.info(
        f"[{protocol_phase}] states={len(result.get('states', []))}  "
        f"transitions={len(result.get('transitions', []))}  "
        f"gaps={len(result.get('gaps', []))}"
    )

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    cost_file = cost_dir / f"{protocol_phase}.json"
    with open(cost_file, "w", encoding="utf-8") as f:
        json.dump(cost_record, f, indent=2, ensure_ascii=False)

    _print_fsm_summary(protocol_phase, result)

    return {
        "protocol_phase": protocol_phase,
        "result":         result,
        "cost":           cost_record,
        "out_path":       str(out_path),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage B2: build FSMs for all A2A protocol phases.",
    )
    parser.add_argument(
        "--stage",
        action="append",
        dest="stages",
        choices=PROTOCOL_STAGES,
        help="Run only these phase(s). Repeatable. Default: all six phases.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-run phases even if outputs/stage_b2/<phase>.json already exists.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    phases = args.stages or list(PROTOCOL_STAGES)
    resume = not args.no_resume

    cfg      = _load_config()
    model    = _resolve_model(cfg)
    prompt   = _resolve_prompt(cfg)
    settings = _resolve_settings(cfg)

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

    try:
        if not litellm.supports_web_search(model=model):
            logger.warning(
                f"LiteLLM reports {model} may not support web search — "
                "searches might be skipped."
            )
    except Exception:
        pass

    base_dir = SCRIPT_DIR / cfg["output"]["base_dir"]
    out_dir  = base_dir / STAGE_TAG
    cost_dir = out_dir / "cost"
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("STAGE B2 — all protocol phases")
    logger.info(f"Model:   {model}")
    logger.info(f"Phases:  {', '.join(phases)}")
    logger.info(f"Resume:  {resume}")

    phase_results: list[dict] = []
    all_per_turn: list[dict]  = []
    run_t0 = time.time()

    for i, protocol_phase in enumerate(phases):
        if i > 0 and settings["phase_delay"] > 0:
            logger.info(
                f"Waiting {settings['phase_delay']}s before next phase..."
            )
            time.sleep(settings["phase_delay"])

        try:
            phase_out = run_protocol_phase(
                protocol_phase=protocol_phase,
                cfg=cfg,
                model=model,
                prompt=prompt,
                settings=settings,
                resume=resume,
            )
        except Exception:
            logger.exception(f"Failed on phase: {protocol_phase}")
            raise

        cost = phase_out["cost"]
        phase_results.append({
            "protocol_phase":  protocol_phase,
            "skipped":         phase_out.get("skipped", False),
            "turns":           cost.get("turns", 0),
            "input_tokens":    cost.get("input_tokens", 0),
            "output_tokens":   cost.get("output_tokens", 0),
            "total_tokens":    cost.get("total_tokens", 0),
            "cost_usd":        cost.get("cost_usd", 0) or 0,
            "elapsed_seconds": cost.get("elapsed_seconds", 0),
            "states":          len(phase_out["result"].get("states", [])),
            "transitions":     len(phase_out["result"].get("transitions", [])),
            "gaps":            len(phase_out["result"].get("gaps", [])),
        })
        all_per_turn.extend(cost.get("per_turn", []))

    total_cost   = sum(p["cost_usd"] for p in phase_results)
    total_tokens = sum(p["total_tokens"] for p in phase_results)
    elapsed      = round(time.time() - run_t0, 2)

    summary_path = out_dir / f"{STAGE_TAG}_cost.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "stage":               STAGE_TAG,
                "model":               model,
                "phases":              phases,
                "phase_count":         len(phases),
                "total_input_tokens":  sum(p["input_tokens"] for p in phase_results),
                "total_output_tokens": sum(p["output_tokens"] for p in phase_results),
                "total_tokens":        total_tokens,
                "total_cost_usd":      round(total_cost, 8),
                "elapsed_seconds":     elapsed,
                "per_phase":           phase_results,
                "per_turn":            all_per_turn,
            },
            f, indent=2, ensure_ascii=False,
        )

    logger.info("=" * 60)
    logger.info("STAGE B2 COMPLETE")
    logger.info(f"Phases run:    {len(phases)}")
    logger.info(f"Total tokens:  {total_tokens:,}")
    logger.info(f"Total cost:    ${total_cost:.6f}")
    logger.info(f"Elapsed:       {elapsed}s")
    logger.info(f"Cost summary:  {summary_path}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

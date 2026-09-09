#!/usr/bin/env python3
"""
zero_shot.py
------------
Run a direct A2A security analysis without using the extraction/FSM pipeline.

The script loads system_prompts.zero_shot from config.yaml, asks Opus to read
the public A2A specification with web search enabled, and writes the JSON result
plus cost/usage metadata under outputs/zero_shot/.
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
from typing import Any

import litellm
import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
STAGE_TAG = "zero_shot"
DEFAULT_MODEL = "anthropic/claude-opus-4-6"
DEFAULT_USER_MESSAGE = """Analyze the A2A protocol specification for security vulnerabilities.

Use web search to inspect the official public A2A specification broadly,
including the security-relevant protocol behavior and interoperability details.
Distinguish protocol-design gaps from implementation mistakes.

Identify only concrete attacks that remain possible when every party follows
the specification exactly. For each candidate, make the attacker, victim,
attacker gain, missing protocol primitive, and compliance gap explicit. If a
candidate depends on a server policy choice, generic web vulnerability,
misconfiguration, or implementation bug, discard it.

Return ONLY a single valid JSON object."""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_config(config_path: Path) -> dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_prompt(cfg: dict[str, Any]) -> str:
    prompt = (cfg.get("system_prompts") or {}).get(STAGE_TAG)
    if not prompt:
        raise ValueError(f"Missing system_prompts.{STAGE_TAG} in config.yaml")
    return str(prompt).strip()


def _resolve_model(cfg: dict[str, Any], requested: str | None) -> str:
    if requested:
        return requested.strip()

    stage_models = (cfg.get("model") or {}).get("stage_models") or {}
    raw = stage_models.get(STAGE_TAG)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    return DEFAULT_MODEL


def _resolve_settings(
    cfg: dict[str, Any],
    *,
    requested_max_tokens: int | None,
    no_search: bool,
) -> dict[str, Any]:
    model_cfg = cfg.get("model") or {}
    zero_cfg = model_cfg.get(STAGE_TAG) or {}
    effort_cfg = model_cfg.get("stage_effort") or {}
    web_cfg = cfg.get("web_search") or {}

    raw_max_tokens = (
        requested_max_tokens
        if requested_max_tokens is not None
        else zero_cfg.get("max_tokens", model_cfg.get("max_tokens"))
    )

    context_size = str(web_cfg.get("search_context_size", "medium"))
    if context_size not in {"low", "medium", "high"}:
        context_size = "medium"

    return {
        "max_tokens": int(raw_max_tokens) if raw_max_tokens is not None else None,
        "max_retries": int(model_cfg.get("max_retries", 5)),
        "retry_wait": int(model_cfg.get("rate_limit_retry_wait_seconds", 60)),
        "timeout": int(model_cfg.get("request_timeout_seconds", 1800)),
        "reasoning_effort": str(
            effort_cfg.get(STAGE_TAG)
            or model_cfg.get("reasoning_effort")
            or "high"
        ),
        "web_search": not no_search,
        "web_search_context_size": context_size,
    }


def _inject_anthropic_key(cfg: dict[str, Any]) -> None:
    key = (cfg.get("api_keys") or {}).get("anthropic", "")
    if key:
        os.environ["ANTHROPIC_API_KEY"] = str(key)
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise RuntimeError(
            "No Anthropic API key. Set api_keys.anthropic in config.yaml "
            "or export ANTHROPIC_API_KEY."
        )


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
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
    for i in range(start, len(raw)):
        ch = raw[i]
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
                parsed = json.loads(raw[start : i + 1])
                if isinstance(parsed, dict):
                    return parsed
                break

    raise ValueError("could not extract a complete JSON object")


def _extract_text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
            else:
                text = getattr(block, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(p for p in parts if p)
    return str(content)


def _iter_content_blocks(message: Any) -> list[dict[str, Any]]:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []

    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        else:
            btype = getattr(block, "type", None)
            if btype:
                blocks.append(
                    {
                        "type": getattr(block, "type", ""),
                        "name": getattr(block, "name", None),
                        "input": getattr(block, "input", None),
                    }
                )
    return blocks


def _usage_web_search_requests(response: Any) -> int | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    server_tool_use = getattr(usage, "server_tool_use", None)
    if server_tool_use is not None:
        n = getattr(server_tool_use, "web_search_requests", None)
        if n is not None:
            return int(n)

    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        n = getattr(details, "web_search_requests", None)
        if n is not None:
            return int(n)

    return None


def _extract_web_search_activity(message: Any, response: Any) -> dict[str, Any]:
    queries: list[str] = []
    tool_use_blocks = 0
    result_blocks = 0

    for block in _iter_content_blocks(message):
        btype = block.get("type", "")
        name = block.get("name") or ""
        if btype == "server_tool_use" and name == "web_search":
            tool_use_blocks += 1
            inp = block.get("input") or {}
            if isinstance(inp, dict) and inp.get("query"):
                queries.append(str(inp["query"]))
        elif btype in {"web_search_tool_result", "web_search_result"}:
            result_blocks += 1

    billed = _usage_web_search_requests(response)
    executed = (billed or 0) > 0 or tool_use_blocks > 0 or result_blocks > 0
    return {
        "executed": executed,
        "mode": "server_side_single_request",
        "queries": queries,
        "tool_use_blocks": tool_use_blocks,
        "result_blocks": result_blocks,
        "requests_billed": billed,
    }


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "rate_limit" in msg or "rate limit" in msg or "timeout" in msg or "timed out" in msg


def _completion_cost(response: Any) -> float | None:
    try:
        return round(float(litellm.completion_cost(completion_response=response)), 8)
    except Exception:
        return None


def _make_completion_kwargs(
    *,
    model: str,
    prompt: str,
    user_message: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": user_message}],
        "system": prompt,
        "reasoning_effort": settings["reasoning_effort"],
        "timeout": settings["timeout"],
    }
    if settings["max_tokens"] is not None:
        kwargs["max_tokens"] = settings["max_tokens"]
    if settings["web_search"]:
        kwargs["web_search_options"] = {
            "search_context_size": settings["web_search_context_size"]
        }
    return kwargs


def _call_model(
    *,
    model: str,
    prompt: str,
    user_message: str,
    settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    kwargs = _make_completion_kwargs(
        model=model,
        prompt=prompt,
        user_message=user_message,
        settings=settings,
    )

    t0 = time.time()
    response = None
    last_err: Exception | None = None

    for attempt in range(settings["max_retries"]):
        try:
            response = litellm.completion(**kwargs)
            break
        except Exception as exc:
            last_err = exc
            if not _is_retryable(exc) or attempt == settings["max_retries"] - 1:
                raise
            wait = settings["retry_wait"] * (attempt + 1)
            logger.warning(
                "Retryable model error (attempt %d/%d); waiting %ss: %s",
                attempt + 1,
                settings["max_retries"],
                wait,
                exc,
            )
            time.sleep(wait)

    if response is None:
        raise RuntimeError(f"Model call failed: {last_err}")

    elapsed = round(time.time() - t0, 2)
    choice = response.choices[0]
    message = choice.message
    raw_text = _extract_text_from_content(getattr(message, "content", None))
    result = _parse_json_object(raw_text)
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
    web_search = _extract_web_search_activity(message, response)
    cost = _completion_cost(response)

    cost_record = {
        "pipeline_stage": STAGE_TAG,
        "mode": "zero_shot_no_pipeline",
        "model": model,
        "finish_reason": getattr(choice, "finish_reason", None),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost,
        "elapsed_seconds": elapsed,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "reasoning_effort": settings["reasoning_effort"],
        "web_search": web_search,
    }
    return result, cost_record, raw_text


def _write_outputs(
    *,
    out_dir: Path,
    result: dict[str, Any],
    cost_record: dict[str, Any],
    raw_text: str,
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "zero_shot_result.json"
    cost_path = out_dir / "zero_shot_cost.json"
    raw_path = out_dir / "raw_response.txt"

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(cost_path, "w", encoding="utf-8") as f:
        json.dump(cost_record, f, indent=2, ensure_ascii=False)
    raw_path.write_text(raw_text, encoding="utf-8")

    return result_path, cost_path, raw_path


def _print_summary(result: dict[str, Any], cost_record: dict[str, Any]) -> None:
    candidates = result.get("candidates") or []
    observations = result.get("structural_observations") or []
    print(f"\n{'=' * 60}")
    print("=== ZERO SHOT SECURITY CANDIDATES ===")
    for candidate in candidates:
        cid = candidate.get("candidate_id", "?")
        title = candidate.get("title", "?")
        conf = candidate.get("preliminary_confidence", "?")
        print(f"  {cid}  [{conf}]  {title}")
    if not candidates:
        print("  (none)")

    if observations:
        print("\n=== STRUCTURAL OBSERVATIONS ===")
        for obs in observations:
            text = str(obs)
            print(f"  {text[:120]}{'...' if len(text) > 120 else ''}")

    web = cost_record.get("web_search") or {}
    print("\n=== RUN ===")
    print(f"  model:       {cost_record.get('model')}")
    print(f"  tokens:      {cost_record.get('total_tokens'):,}")
    print(f"  cost_usd:    {cost_record.get('cost_usd')}")
    print(f"  elapsed_sec: {cost_record.get('elapsed_seconds')}")
    print(f"  web_search:  {web.get('executed')} ({web.get('requests_billed')} billed)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zero-shot A2A security analysis without the pipeline.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to config.yaml.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory. Default: outputs/zero_shot.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"LiteLLM model string. Default: {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Override output token cap.",
    )
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="Disable server-side web search.",
    )
    parser.add_argument(
        "--user-message",
        default=DEFAULT_USER_MESSAGE,
        help="User message sent with system_prompts.zero_shot.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = _load_config(args.config)
    prompt = _resolve_prompt(cfg)
    model = _resolve_model(cfg, args.model)
    settings = _resolve_settings(
        cfg,
        requested_max_tokens=args.max_tokens,
        no_search=args.no_search,
    )
    out_dir = args.out_dir or (SCRIPT_DIR / cfg["output"]["base_dir"] / STAGE_TAG)

    _inject_anthropic_key(cfg)

    litellm.suppress_debug_info = True
    litellm.set_verbose = False
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    if settings["web_search"]:
        try:
            if not litellm.supports_web_search(model=model):
                logger.warning(
                    "LiteLLM reports %s may not support web search; "
                    "the model may answer without searching.",
                    model,
                )
        except Exception:
            pass

    logger.info("ZERO SHOT - A2A security analysis without pipeline")
    logger.info("Model:  %s", model)
    logger.info("Prompt: system_prompts.%s", STAGE_TAG)
    logger.info("Output: %s", out_dir)
    logger.info(
        "Search: %s",
        (
            f"enabled ({settings['web_search_context_size']})"
            if settings["web_search"]
            else "disabled"
        ),
    )

    try:
        result, cost_record, raw_text = _call_model(
            model=model,
            prompt=prompt,
            user_message=args.user_message,
            settings=settings,
        )
    except Exception:
        logger.exception("Zero-shot run failed")
        sys.exit(1)

    result_path, cost_path, raw_path = _write_outputs(
        out_dir=out_dir,
        result=result,
        cost_record=cost_record,
        raw_text=raw_text,
    )
    logger.info("Result: %s", result_path)
    logger.info("Cost:   %s", cost_path)
    logger.info("Raw:    %s", raw_path)
    _print_summary(result, cost_record)


if __name__ == "__main__":
    main()

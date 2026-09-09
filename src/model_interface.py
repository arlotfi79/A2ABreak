"""
model_interface.py
------------------
Single point of contact for all LLM calls in the pipeline.

- Swap model:   change config.yaml model.name
- Swap API key: set ANTHROPIC_API_KEY (etc.) in .env
- Edit prompts: change config.yaml system_prompts.<stage>

Every call returns a CallResult carrying content + token counts + cost.
"""

import json
import logging
import os
import time
from pathlib import Path
import yaml
from dataclasses import dataclass, asdict
from typing import Optional
from dotenv import load_dotenv
import litellm
from litellm import completion_cost

load_dotenv(Path(__file__).parent / ".env")

_DEFAULT_CONFIG = str(Path(__file__).parent / "config.yaml")

litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Map LiteLLM provider prefixes → config key names
_PROVIDER_KEY_MAP = {
    "anthropic":   "anthropic",
    "openai":      "openai",
    "gemini":      "gemini",
    "mistral":     "mistral",
    "ollama":      None,   # local, no key needed
    "huggingface": None,
}

_ENV_VAR_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
}


@dataclass
class CallResult:
    """Returned by every model call — content plus full usage/cost record."""
    content: str
    model: str
    stage: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    elapsed_seconds: float
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(config_path: str = _DEFAULT_CONFIG) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _resolve_provider(model_name: str) -> Optional[str]:
    """Extract provider from model name, e.g. 'anthropic/claude-...' → 'anthropic'."""
    if "/" in model_name:
        return model_name.split("/")[0].lower()
    name = model_name.lower()
    if name.startswith("claude"):    return "anthropic"
    if name.startswith("gpt"):       return "openai"
    if name.startswith("o1"):        return "openai"
    if name.startswith("gemini"):    return "gemini"
    if name.startswith("mistral"):   return "mistral"
    return None


class ModelInterface:
    """
    Wraps LiteLLM with config-driven model, key, effort, and prompt selection.
    All pipeline stages call only this class.
    """

    def __init__(self, config_path: str = _DEFAULT_CONFIG):
        self.config = load_config(config_path)
        self.model_name   = self.config["model"]["name"]
        self.default_effort = self.config["model"]["reasoning_effort"]
        self.stage_effort = self.config["model"].get("stage_effort", {})
        self.max_tokens     = self.config["model"].get("max_tokens", 64000)
        self.system_prompts = self.config.get("system_prompts", {})
        self.api_keys     = self.config.get("api_keys", {})
        self._inject_api_key()

    def _inject_api_key(self) -> None:
        provider = _resolve_provider(self.model_name)
        if not provider:
            logger.warning(f"Could not determine provider for: {self.model_name}")
            return
        config_key = _PROVIDER_KEY_MAP.get(provider)
        if config_key is None:
            return  # local provider, no key needed
        key_value = self.api_keys.get(config_key, "")
        if not key_value:
            logger.warning(
                f"api_keys.{config_key} is empty in config.yaml — "
                f"falling back to environment variable."
            )
            return
        env_var = _ENV_VAR_MAP.get(provider)
        if env_var:
            os.environ[env_var] = key_value
            logger.debug(f"Injected API key for '{provider}' → {env_var}")

    def _get_effort(self, stage: Optional[str]) -> str:
        if stage and stage in self.stage_effort:
            return self.stage_effort[stage]
        return self.default_effort

    def get_system_prompt(self, stage: str) -> str:
        prompt = self.system_prompts.get(stage, "")
        if not prompt:
            logger.warning(f"No system_prompt for '{stage}' in config.yaml")
        return prompt.strip()

    def call(
        self,
        prompt: str,
        stage: str,
        system: Optional[str] = None,
        json_mode: bool = False,
        model_override: Optional[str] = None,
    ) -> CallResult:
        """
        Make a single LLM call. Returns a CallResult with content + usage.

        Args:
            prompt:    The user message
            stage:     Stage name — drives effort routing and system prompt lookup
            system:    Override system prompt (None = load from config by stage)
            json_mode: Strip markdown fences from response if True
            model_override: Use this LiteLLM model id for this call only (e.g. Opus for stage_b2).
        """
        effort         = self._get_effort(stage)
        system_content = system if system is not None else self.get_system_prompt(stage)
        model_use      = (model_override or "").strip() or self.model_name

        messages = []
        if system_content:
            messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": prompt})

        request_timeout = self.config["model"].get("request_timeout_seconds", 1800)
        kwargs = {
            "model":            model_use,
            "messages":         messages,
            "max_tokens":       self.max_tokens,
            "reasoning_effort": effort,
            "timeout":          request_timeout,
        }

        max_retries = self.config["model"].get("max_retries", 5)
        retry_wait  = self.config["model"].get("rate_limit_retry_wait_seconds", 60)

        logger.debug(
            f"Calling {model_use} [stage={stage}, effort={effort}, max_tokens={self.max_tokens}]"
        )

        t0 = time.time()
        for attempt in range(max_retries + 1):
            try:
                response = litellm.completion(**kwargs)
                break
            except litellm.RateLimitError as e:
                if attempt == max_retries:
                    raise
                wait = retry_wait * (attempt + 1)
                logger.warning(
                    f"Rate limit hit (attempt {attempt + 1}/{max_retries}) — "
                    f"waiting {wait}s before retry..."
                )
                time.sleep(wait)
        elapsed = round(time.time() - t0, 2)

        content = response.choices[0].message.content or ""

        if json_mode:
            content = content.strip()
            if content.startswith("```"):
                lines   = content.split("\n")
                content = "\n".join(lines[1:-1]).strip()

        # ── Token counts ──────────────────────────────────────────────────────
        usage         = response.usage
        input_tokens  = getattr(usage, "prompt_tokens",     0)
        output_tokens = getattr(usage, "completion_tokens", 0)
        total_tokens  = getattr(usage, "total_tokens",      0)

        # ── Cost ──────────────────────────────────────────────────────────────
        try:
            cost = completion_cost(completion_response=response)
        except Exception as e:
            logger.warning(f"Cost calculation failed: {e} — defaulting to 0.0")
            cost = 0.0

        return CallResult(
            content         = content,
            model           = model_use,
            stage           = stage,
            input_tokens    = input_tokens,
            output_tokens   = output_tokens,
            total_tokens    = total_tokens,
            cost_usd        = round(cost, 8),
            elapsed_seconds = elapsed,
            timestamp       = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    @staticmethod
    def _recover_truncated_json_array(content: str) -> list:
        """
        Extract every complete {...} object from a truncated JSON array.
        Used when the model response is cut off before the closing ].
        Returns a list of all fully-parseable objects found.
        """
        objects = []
        depth = 0
        start = None
        for i, ch in enumerate(content):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objects.append(json.loads(content[start : i + 1]))
                    except json.JSONDecodeError:
                        pass
                    start = None
        return objects

    def call_json(
        self,
        prompt: str,
        stage: str,
        system: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> tuple[dict | list, CallResult]:
        """
        Like call() but parses the response as JSON.
        Returns (parsed_data, CallResult).
        Falls back to partial recovery if the response is truncated.
        Raises ValueError only if no valid JSON can be recovered at all.
        """
        result = self.call(
            prompt,
            stage=stage,
            system=system,
            json_mode=True,
            model_override=model_override,
        )

        if not result.content.strip():
            logger.info("Model returned empty response — treating as empty array")
            return [], result

        try:
            parsed = json.loads(result.content)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed: {e}\nRaw:\n{result.content[:500]}")
            recovered = self._recover_truncated_json_array(result.content)
            if recovered:
                logger.warning(
                    f"Response was truncated — recovered {len(recovered)} complete "
                    f"objects (last object was cut off and dropped)"
                )
                return recovered, result
            raise ValueError(f"Model returned invalid JSON: {e}") from e
        return parsed, result
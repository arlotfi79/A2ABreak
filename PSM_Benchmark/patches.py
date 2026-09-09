#!/usr/bin/env python3
"""
patches.py
----------
Runtime adaptation of the parent pipeline. METHOD.md: not one byte of
stage_*.py / merge_stage_a12.py / verify_stage_a.py / fetcher.py /
model_interface.py / config.yaml is edited — everything here restores the
original module state on exit.

What is patched and why:
  * stage_a1/stage_a2 EXTRACTION_PROMPT_TEMPLATE  -> protocol-generic user templates.
  * merge_stage_a12 STAGE_ORDER/VALID_STAGES/VALID_SUBJECTS/NL_MARKERS
                                                  -> this protocol's single stage
                                                     label and generic subjects.
  * merge_stage_a12.LLMClient                     -> its three internal prompts are
                                                     A2A-worded (merge_stage_a12.py:253,
                                                     :377-412, :527-532). We intercept the
                                                     prompt text they build, at the litellm
                                                     boundary, and rewrite the A2A-specific
                                                     fragments to protocol-generic wording of
                                                     identical structure. Intercepting the
                                                     text (rather than re-implementing three
                                                     ~120-line methods) keeps the call, cost
                                                     accounting and retry behaviour bit-identical.
  * stage_b2._CONTINUATION_USER_MSG               -> drops the "use web search" sentence.
  * fetcher.parse_sections (+ the three module-level rebindings)
                                                  -> --max-sections smoke truncation.
"""

from __future__ import annotations

import contextlib
import re
import threading
from typing import Any, Iterable

import fetcher
import merge_stage_a12
import stage_a1
import stage_a2
import stage_b2

NL_MARKERS_GENERIC = [
    "the ", "if ", "when ", "after ", "before ",
    "must ", "should ", "which ", "that ", "this ",
    "client ", "server ", "message ", "session ",
]

NO_SEARCH_CONTINUATION = (
    "Continue. Respond with ONLY a single valid JSON object for the FSM "
    "(no markdown, no commentary)."
)


@contextlib.contextmanager
def stage_a_user_templates(a1_template: str, a2_template: str) -> Iterable[None]:
    old_a1 = stage_a1.EXTRACTION_PROMPT_TEMPLATE
    old_a2 = stage_a2.EXTRACTION_PROMPT_TEMPLATE
    stage_a1.EXTRACTION_PROMPT_TEMPLATE = a1_template
    stage_a2.EXTRACTION_PROMPT_TEMPLATE = a2_template
    try:
        yield
    finally:
        stage_a1.EXTRACTION_PROMPT_TEMPLATE = old_a1
        stage_a2.EXTRACTION_PROMPT_TEMPLATE = old_a2


# ---------------------------------------------------------------------------
# merge_stage_a12 constants + internal prompts
# ---------------------------------------------------------------------------

def _generic_merge_prompt(text: str, protocol: str) -> str:
    """
    Rewrite the A2A-specific fragments of merge's three internal prompts.
    Structure, ordering and JSON schema are untouched.
    """
    replacements = [
        ("an A2A protocol", f"a {protocol} protocol"),
        ("the same A2A protocol", f"the same {protocol} protocol"),
        ("A2A protocol", f"{protocol} protocol"),
        ('"task.state == WORKING"', '"protocol.state == <StateA>"'),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(
        r'"IDLE",\s*"TASK_ACTIVE",\s*\n?\s*"AUTHENTICATED"',
        '"<StateA>", "<StateB>", "<StateC>"',
        text,
    )
    return text


class _PromptRewritingLiteLLM:
    """Proxy around the litellm module that genericises outgoing prompt text."""

    def __init__(self, inner: Any, protocol: str, stats: dict[str, int]):
        self._inner = inner
        self._protocol = protocol
        self._stats = stats

    def completion(self, *args: Any, **kwargs: Any) -> Any:
        messages = kwargs.get("messages")
        if isinstance(messages, list):
            rewritten = []
            for message in messages:
                content = message.get("content")
                if isinstance(content, str):
                    new_content = _generic_merge_prompt(content, self._protocol)
                    if new_content != content:
                        self._stats["prompts_rewritten"] = (
                            self._stats.get("prompts_rewritten", 0) + 1
                        )
                    if "A2A" in new_content:
                        raise RuntimeError(
                            "merge prompt still contains 'A2A' after rewriting — "
                            "the parent prompt text changed; update patches.py"
                        )
                    message = {**message, "content": new_content}
                rewritten.append(message)
            kwargs["messages"] = rewritten
        self._stats["calls"] = self._stats.get("calls", 0) + 1
        return self._inner.completion(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@contextlib.contextmanager
def merge_adapted(
    *,
    protocol: str,
    stage_label: str,
    subjects: Iterable[str],
    stats: dict[str, int],
) -> Iterable[None]:
    old = {
        "STAGE_ORDER": merge_stage_a12.STAGE_ORDER,
        "VALID_STAGES": merge_stage_a12.VALID_STAGES,
        "VALID_SUBJECTS": merge_stage_a12.VALID_SUBJECTS,
        "NL_MARKERS": merge_stage_a12.NL_MARKERS,
        "LLMClient": merge_stage_a12.LLMClient,
    }
    base_client = old["LLMClient"]

    class GenericPromptLLMClient(base_client):          # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._litellm = _PromptRewritingLiteLLM(self._litellm, protocol, stats)

    merge_stage_a12.STAGE_ORDER = {stage_label: 0}
    merge_stage_a12.VALID_STAGES = {stage_label}
    merge_stage_a12.VALID_SUBJECTS = set(subjects)
    merge_stage_a12.NL_MARKERS = list(NL_MARKERS_GENERIC)
    merge_stage_a12.LLMClient = GenericPromptLLMClient
    try:
        yield
    finally:
        for name, value in old.items():
            setattr(merge_stage_a12, name, value)


# ---------------------------------------------------------------------------
# Stage B synthesis: no web search anywhere in the loop
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def no_search_continuation() -> Iterable[None]:
    old = stage_b2._CONTINUATION_USER_MSG
    stage_b2._CONTINUATION_USER_MSG = NO_SEARCH_CONTINUATION
    try:
        yield
    finally:
        stage_b2._CONTINUATION_USER_MSG = old


# ---------------------------------------------------------------------------
# Smoke-test truncation
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def limited_sections(max_sections: int | None) -> Iterable[None]:
    """Truncate the parsed section list everywhere the pipeline reads sections."""
    if not max_sections:
        yield
        return

    import verify_stage_a

    original = fetcher.parse_sections

    def limited(markdown: str, merge_threshold_words: int = 200):
        return original(markdown, merge_threshold_words=merge_threshold_words)[:max_sections]

    targets = [fetcher, stage_a1, stage_a2, verify_stage_a]
    olds = [(module, getattr(module, "parse_sections")) for module in targets]
    for module, _ in olds:
        setattr(module, "parse_sections", limited)
    try:
        yield
    finally:
        for module, value in olds:
            setattr(module, "parse_sections", value)


# ---------------------------------------------------------------------------
# verify_stage_a JSON robustness
# ---------------------------------------------------------------------------
#
# verify_stage_a.LLMClient.call strips a leading ```json fence and a TRAILING
# fence anchored to end-of-string, then calls json.loads (verify_stage_a.py:417-420).
# When the model emits a fenced JSON object FOLLOWED BY prose ("**Note:** ..."),
# the trailing-fence regex cannot match, the fence and prose survive, and a
# complete, valid verification object is thrown away as "Extra data".
#
# stage_a1/a2 (model_interface._recover_truncated_json_array) and stage_b2
# (parse_fsm_json) both already recover from this class of response; verify is
# the only stage without a recovery path. This proxy gives it the same one and
# nothing more: strict parsing is tried first and the fallback can only run
# where strict parsing already raised.

def _first_balanced_object(text: str) -> str | None:
    """Return the first balanced {...} span, ignoring braces inside strings."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


class _TolerantJSON:
    """Proxy for the json module: only `loads` gains a recovery fallback."""

    def __init__(self, inner: Any, stats: dict[str, Any]):
        self._inner = inner
        self._stats = stats
        self._lock = threading.Lock()      # verify sections may run concurrently

    def loads(self, s: Any, **kwargs: Any) -> Any:
        try:
            return self._inner.loads(s, **kwargs)
        except self._inner.JSONDecodeError:
            candidate = _first_balanced_object(s) if isinstance(s, str) else None
            if candidate is None:
                raise
            result = self._inner.loads(candidate, **kwargs)
            with self._lock:
                self._stats["count"] = self._stats.get("count", 0) + 1
                if isinstance(result, dict) and result.get("statement_id"):
                    ids = self._stats.setdefault("statement_ids", [])
                    ids.append(result["statement_id"])
                    ids.sort()             # order must not depend on completion order
            return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@contextlib.contextmanager
def tolerant_verify_json(stats: dict[str, Any]) -> Iterable[None]:
    import verify_stage_a

    old = verify_stage_a.json
    verify_stage_a.json = _TolerantJSON(old, stats)
    try:
        yield
    finally:
        verify_stage_a.json = old

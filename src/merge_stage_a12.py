#!/usr/bin/env python3
"""
merge_stage_a12.py

Merges Stage A1 (structural/definitional) and Stage A2 (RFC 2119 behavioral)
outputs into a single verified statement list ready for verify_stage_a.

Strategy:
  - Code handles all structural operations and exact/fuzzy deduplication
  - LLM handles ambiguous dedup cases (similarity in [AMBIGUOUS_LOW, AMBIGUOUS_HIGH))
  - LLM resolves NON_FSM_BEHAVIORAL warnings by fetching the source spec section
    and deciding: enrich FSM fields in-place OR mark is_global_constraint=true
  - Human reviews LLM decisions logged in needs_review.json
  - FSM tuple construction happens AFTER verify_stage_a

Usage:
    python merge_stage_a12.py \
        --a1 outputs/stage_a1/final.json \
        --a2 outputs/stage_a2/final.json \
        --out outputs/stage_a12/final.json \
        [--api-key sk-ant-...] \
        [--model anthropic/claude-sonnet-4-6] \
        [--similarity-threshold 0.92] \
        [--ambiguous-low 0.75] \
        [--spec-url https://a2a-protocol.org/latest/specification/] \
        [--skip-resolve-warnings]
"""

import json
import re
import argparse
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


STAGE          = "stage_a12"
_SCRIPT_DIR    = Path(__file__).resolve().parent
_DEFAULT_CFG   = _SCRIPT_DIR / "config.yaml"


def _load_config(config_path: Path = _DEFAULT_CFG) -> dict:
    """Read config.yaml; return {} on any failure (PyYAML missing, no file, etc.)."""
    if yaml is None or not config_path.exists():
        return {}
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _default_paths(config_path: Path = _DEFAULT_CFG) -> dict:
    """
    Resolve the canonical output paths for this stage from config.yaml.

    Mirrors the pattern used by stage_a1.py / stage_a2.py:
        base_dir   = config_root / config["output"]["base_dir"]
        stage_dir  = base_dir / STAGE
        final.json lives inside stage_dir
    Falls back to "outputs" when PyYAML isn't installed or the config
    file is unreadable, so the script stays runnable on its own.
    """
    config_root = config_path.parent
    cfg         = _load_config(config_path)
    base        = cfg.get("output", {}).get("base_dir", "outputs")
    base_dir    = (config_root / base).resolve()

    return {
        "a1":  base_dir / "stage_a1" / "final.json",
        "a2":  base_dir / "stage_a2" / "final.json",
        "out": base_dir / STAGE / "final.json",
    }


def _default_api_key(config_path: Path = _DEFAULT_CFG) -> Optional[str]:
    """
    Pull the Anthropic API key from config.yaml `api_keys.anthropic`,
    matching how stage_a1.py / stage_a2.py source their credentials.
    Returns None if PyYAML is missing or the key is absent/blank.
    """
    cfg = _load_config(config_path)
    key = (cfg.get("api_keys") or {}).get("anthropic")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return None


def _default_spec_config(config_path: Path = _DEFAULT_CFG) -> dict:
    """Read fetcher.url and fetcher.merge_threshold_words from config.yaml."""
    cfg = _load_config(config_path)
    fetcher_cfg = cfg.get("fetcher") or {}
    return {
        "url": fetcher_cfg.get("url", "https://a2a-protocol.org/latest/specification/"),
        "merge_threshold_words": int(fetcher_cfg.get("merge_threshold_words", 200)),
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STAGE_ORDER = {
    "discovery": 0,
    "authentication": 1,
    "initiation": 2,
    "task_execution": 3,
    "interruption": 4,
    "termination": 5,
}

VALID_SUBJECTS = {
    "client", "server", "agent_card", "message", "task",
    "part", "artifact", "streaming", "push_notification",
    "context", "extension",
}

VALID_STAGES = set(STAGE_ORDER.keys())

VALID_CATEGORIES = {
    "FSM_STATE", "FIELD_PRESENCE", "ONEOF_CONSTRAINT",
    "IMPLICIT_TRANSITION", "BEHAVIORAL",
}

TRANSITION_CATEGORIES = {"BEHAVIORAL", "IMPLICIT_TRANSITION"}
CONSTRAINT_CATEGORIES = {"FIELD_PRESENCE", "ONEOF_CONSTRAINT"}

ENRICH_FIELDS = [
    "from_state", "to_state", "pre_cond",
    "post_cond", "actors", "trigger",
]

NL_MARKERS = [
    "the ", "if ", "when ", "after ", "before ",
    "must ", "should ", "which ", "that ", "this ",
    "client ", "server ", "agent ", "task ",
]

# Tokens / symbols that indicate the value is a formal expression rather than
# prose. If any of these appear in a *_cond field, the natural-language check
# is skipped — the field is clearly a guard expression, not an English sentence.
FORMAL_EXPR_TOKENS = (
    "=", "!=", "<", ">", "&", "|", "(", ")",
    "true", "false", "null",
)


def _looks_like_formal_expr(val: str) -> bool:
    """True if a *_cond value uses formal-expression syntax."""
    if not val:
        return False
    low = val.lower()
    return any(tok in low for tok in FORMAL_EXPR_TOKENS)

THRESHOLD_HIGH = 0.92   # >= this → definite duplicate (code decides)
THRESHOLD_LOW  = 0.75   # >= this → ambiguous (LLM decides)
                        # <  this → definite different (code decides)


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def normalize_section(section: str) -> str:
    """Normalize section IDs to parent level. "7.4.1" → "7.4" """
    if not section:
        return ""
    parts = section.strip().split(".")
    return ".".join(parts[:2])


def normalize_desc(desc: str) -> str:
    """Lowercase, collapse whitespace, strip trailing punctuation."""
    if not desc:
        return ""
    d = desc.lower().strip()
    d = re.sub(r"\s+", " ", d)
    d = d.rstrip(".,;:")
    return d


def get_desc(stmt: dict) -> str:
    """Extract description from statement across possible field names."""
    return (
        stmt.get("description") or
        stmt.get("raw") or
        stmt.get("action") or
        ""
    )


def similarity(a: str, b: str) -> float:
    """String similarity ratio between 0 and 1."""
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Minimal LiteLLM wrapper.
    Only used for ambiguous dedup decisions — not the full merge.
    Token usage and cost are attached to each result under "_cost".
    """

    def __init__(self, api_key: str, model: str):
        self.model = model
        try:
            import litellm
            from litellm import completion_cost
            import os
            os.environ["ANTHROPIC_API_KEY"] = api_key
            self._litellm        = litellm
            self._completion_cost = completion_cost
        except ImportError:
            raise ImportError(
                "litellm is required for LLM validation. "
                "Install with: pip install litellm"
            )

    def validate_duplicate(self, s1: dict, s2: dict) -> dict:
        """
        Single LLM call to decide if two statements are duplicates.

        s1: IMPLICIT_TRANSITION from A1
        s2: BEHAVIORAL from A2

        Returns:
            {
                "are_duplicates": bool,
                "confidence": float,
                "reason": str,
                "recommendation": "merge|keep_separate|human_review",
                "enrich_from_s1": [field names to copy from s1 to s2]
            }
        """
        prompt = f"""You are validating a merge decision in a protocol
security analysis pipeline.

Two statements were extracted from the same A2A protocol specification.
Determine if they describe the SAME normative requirement.

Statement 1 — IMPLICIT_TRANSITION (structural extraction, no RFC 2119 keyword):
  description:    "{get_desc(s1)}"
  source_section: {s1.get("source_section")}
  from_state:     {s1.get("from_state")}
  to_state:       {s1.get("to_state")}
  trigger:        {s1.get("trigger")}
  pre_cond:       {s1.get("pre_cond")}
  post_cond:      {s1.get("post_cond")}

Statement 2 — BEHAVIORAL (RFC 2119 keyword extraction):
  description:    "{get_desc(s2)}"
  source_section: {s2.get("source_section")}
  normative_level:{s2.get("normative_level")}
  from_state:     {s2.get("from_state")}
  to_state:       {s2.get("to_state")}
  trigger:        {s2.get("trigger")}
  pre_cond:       {s2.get("pre_cond")}
  post_cond:      {s2.get("post_cond")}

RULES:
- They are duplicates if they describe the same protocol event or
  constraint, even if worded slightly differently.
- They are NOT duplicates if they describe different events,
  different actors, or different normative obligations.
- If duplicates: BEHAVIORAL (Statement 2) is kept as primary.
  Identify which fields from Statement 1 are non-null and would
  enrich null fields in Statement 2 (from_state, to_state,
  pre_cond, post_cond, actors, trigger).
- If keeping both: they remain as separate statements.

Return ONLY valid JSON, no explanation, no markdown:
{{
  "are_duplicates": true,
  "confidence": 0.95,
  "reason": "one sentence explanation",
  "recommendation": "merge|keep_separate|human_review",
  "enrich_from_s1": ["from_state", "trigger"]
}}"""

        import time
        t0 = time.time()
        try:
            response = self._litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                reasoning_effort="low",
            )
            elapsed = time.time() - t0
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)

            for field in ["are_duplicates", "confidence",
                          "reason", "recommendation"]:
                if field not in result:
                    raise ValueError(f"LLM response missing: {field}")

            result.setdefault("enrich_from_s1", [])

            usage         = getattr(response, "usage", None)
            input_tokens  = getattr(usage, "prompt_tokens",     0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            total_tokens  = getattr(usage, "total_tokens",      0) if usage else 0
            try:
                cost = self._completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

            result["_cost"] = {
                "model":           self.model,
                "input_tokens":    input_tokens,
                "output_tokens":   output_tokens,
                "total_tokens":    total_tokens,
                "cost_usd":        round(cost, 8),
                "elapsed_seconds": round(elapsed, 4),
                "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            return result

        except Exception as e:
            elapsed = time.time() - t0
            return {
                "are_duplicates":  False,
                "confidence":      0.0,
                "reason":          f"LLM call failed: {e}",
                "recommendation":  "human_review",
                "enrich_from_s1":  [],
                "error":           str(e),
                "_cost": {
                    "model":           self.model,
                    "input_tokens":    0,
                    "output_tokens":   0,
                    "total_tokens":    0,
                    "cost_usd":        0.0,
                    "elapsed_seconds": round(elapsed, 4),
                    "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "error":           str(e),
                },
            }


    def resolve_non_fsm_behavioral(self, stmt: dict, spec_section_text: str) -> dict:
        """
        Decide — using the raw spec section as ground truth — whether a
        BEHAVIORAL statement with no FSM fields is a genuine state-transition
        (enrich with inferred fields) or a global protocol constraint (mark it).

        Returns:
            {
                "is_global_constraint": bool,
                "from_state": str | None,
                "to_state":   str | None,
                "trigger":    str | None,
                "pre_cond":   str | None,
                "post_cond":  str | None,
                "confidence": float,
                "reason":     str,
            }
        """
        prompt = f"""You are resolving a validation warning in an A2A protocol
security analysis pipeline.

A BEHAVIORAL statement was extracted from the spec but has no FSM information
(from_state, to_state, trigger, pre_cond, post_cond are all null/empty).
You must decide — by reading the SOURCE SPEC TEXT below — whether this is:

  (A) A state-transition obligation: the spec describes a protocol event with
      a clear before/after state context. In this case, infer the FSM fields
      directly from the spec text (use null for fields that are genuinely
      indeterminate, not guessed).

  (B) A global protocol constraint: the obligation applies regardless of
      protocol state (e.g. schema rules, security best practices, build-time
      requirements, capability declarations, format requirements).
      Set is_global_constraint=true and leave FSM fields null.

STATEMENT:
  statement_id:    "{stmt.get('statement_id')}"
  description:     "{get_desc(stmt)}"
  normative_level: {stmt.get('normative_level')}
  subject:         {stmt.get('subject')}
  stage:           {stmt.get('stage')}
  source_section:  {stmt.get('source_section')}

SOURCE SPEC TEXT (section {stmt.get('source_section')}):
---
{spec_section_text}
---

RULES:
- Base your decision ONLY on the spec text above, not on the description alone.
- FSM field values must use short formal identifiers (e.g. "IDLE", "TASK_ACTIVE",
  "AUTHENTICATED"). Do NOT use prose sentences as field values.
- pre_cond / post_cond must be formal guard expressions (e.g. "task.state == WORKING")
  not English sentences.
- If the spec text does not provide enough context to infer a specific FSM field
  with confidence, set it to null rather than guessing.

Return ONLY valid JSON, no markdown:
{{
  "is_global_constraint": false,
  "from_state":  null,
  "to_state":    null,
  "trigger":     null,
  "pre_cond":    null,
  "post_cond":   null,
  "confidence":  0.92,
  "reason":      "one sentence citing the spec"
}}"""

        import time
        t0 = time.time()
        try:
            response = self._litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                reasoning_effort="low",
            )
            elapsed = time.time() - t0
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)

            for field in ["is_global_constraint", "confidence", "reason"]:
                if field not in result:
                    raise ValueError(f"LLM response missing: {field}")
            for f in ["from_state", "to_state", "trigger", "pre_cond", "post_cond"]:
                result.setdefault(f, None)

            usage         = getattr(response, "usage", None)
            input_tokens  = getattr(usage, "prompt_tokens",     0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            total_tokens  = getattr(usage, "total_tokens",      0) if usage else 0
            try:
                cost = self._completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

            result["_cost"] = {
                "model":           self.model,
                "input_tokens":    input_tokens,
                "output_tokens":   output_tokens,
                "total_tokens":    total_tokens,
                "cost_usd":        round(cost, 8),
                "elapsed_seconds": round(elapsed, 4),
                "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            return result

        except Exception as e:
            elapsed = time.time() - t0
            return {
                "is_global_constraint": True,
                "from_state":   None,
                "to_state":     None,
                "trigger":      None,
                "pre_cond":     None,
                "post_cond":    None,
                "confidence":   0.0,
                "reason":       f"LLM call failed: {e}",
                "error":        str(e),
                "_cost": {
                    "model":           self.model,
                    "input_tokens":    0,
                    "output_tokens":   0,
                    "total_tokens":    0,
                    "cost_usd":        0.0,
                    "elapsed_seconds": round(elapsed, 4),
                    "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "error":           str(e),
                },
            }

    def verify_needs_review_entry(
        self,
        entry: dict,
        kept_stmt: dict,
        spec_section_text: str,
    ) -> dict:
        """
        Verify a needs_review merge entry against the source spec section.

        Checks two things:
          1. Was the merge decision correct (are the two descriptions the same
             protocol requirement)?
          2. Are the FSM fields that were copied from the removed statement
             actually correct for the kept statement per the spec?

        Returns:
            {
                "merge_correct":  bool,
                "fields_correct": bool,
                "corrections":    {field: corrected_value | null},
                "confidence":     float,
                "reason":         str,
            }
        """
        enriched = entry.get("enriched_fields") or []
        kept_id  = entry.get("kept", "?")
        removed_id = entry.get("removed", "?")

        enriched_snapshot = {
            f: kept_stmt.get(f)
            for f in enriched
            if f in ("from_state", "to_state", "trigger", "pre_cond", "post_cond")
        }

        prompt = f"""You are verifying a merge decision in an A2A protocol security
analysis pipeline.

Two statements were found to be duplicates. The BEHAVIORAL statement (kept) was
enriched with FSM fields copied from the IMPLICIT_TRANSITION statement (removed).
Verify this against the SOURCE SPEC TEXT.

KEPT statement ({kept_id} — BEHAVIORAL):
  description:     "{get_desc(kept_stmt)}"
  subject:         {kept_stmt.get('subject')}
  stage:           {kept_stmt.get('stage')}
  source_section:  {kept_stmt.get('source_section')}
  normative_level: {kept_stmt.get('normative_level')}
  enriched_fields: {enriched}
  from_state:      {kept_stmt.get('from_state')}
  to_state:        {kept_stmt.get('to_state')}
  trigger:         {kept_stmt.get('trigger')}
  pre_cond:        {kept_stmt.get('pre_cond')}
  post_cond:       {kept_stmt.get('post_cond')}

REMOVED statement ({removed_id} — IMPLICIT_TRANSITION, provided the enriched values):
  description:  "{entry.get('s1_description', '')}"
  from_state:   {entry.get('s1_from_state')}
  to_state:     {entry.get('s1_to_state')}
  trigger:      {entry.get('s1_trigger')}
  pre_cond:     {entry.get('s1_pre_cond')}
  post_cond:    {entry.get('s1_post_cond')}

SOURCE SPEC TEXT (section {kept_stmt.get('source_section')}):
---
{spec_section_text}
---

TASK:
1. merge_correct: Do both descriptions refer to the same protocol requirement?
   Answer false only if they clearly describe different obligations.
2. fields_correct: Given the spec text, are the enriched FSM fields
   (listed in enriched_fields) accurate for the kept statement?
   Check each enriched field against the spec text.
3. corrections: For any enriched field that is wrong or misleading, provide
   the corrected value. Use null to clear a field. Leave out fields that are
   correct. Use short formal identifiers for states, not prose sentences.
   FSM cond fields must be formal guard expressions, not English sentences.

Return ONLY valid JSON, no markdown:
{{
  "merge_correct":  true,
  "fields_correct": true,
  "corrections":    {{}},
  "confidence":     0.92,
  "reason":         "one sentence citing the spec"
}}"""

        import time
        t0 = time.time()
        try:
            response = self._litellm.completion(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                reasoning_effort="low",
            )
            elapsed = time.time() - t0
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)

            for field in ["merge_correct", "fields_correct", "confidence", "reason"]:
                if field not in result:
                    raise ValueError(f"LLM response missing: {field}")
            result.setdefault("corrections", {})

            usage         = getattr(response, "usage", None)
            input_tokens  = getattr(usage, "prompt_tokens",     0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            total_tokens  = getattr(usage, "total_tokens",      0) if usage else 0
            try:
                cost = self._completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

            result["_cost"] = {
                "model":           self.model,
                "input_tokens":    input_tokens,
                "output_tokens":   output_tokens,
                "total_tokens":    total_tokens,
                "cost_usd":        round(cost, 8),
                "elapsed_seconds": round(elapsed, 4),
                "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            return result

        except Exception as e:
            elapsed = time.time() - t0
            return {
                "merge_correct":  True,
                "fields_correct": False,
                "corrections":    {},
                "confidence":     0.0,
                "reason":         f"LLM call failed: {e}",
                "error":          str(e),
                "_cost": {
                    "model":           self.model,
                    "input_tokens":    0,
                    "output_tokens":   0,
                    "total_tokens":    0,
                    "cost_usd":        0.0,
                    "elapsed_seconds": round(elapsed, 4),
                    "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "error":           str(e),
                },
            }


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load(path: str) -> list:
    """Load JSON file as flat list of statements."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        statements = data
    elif isinstance(data, dict):
        statements = None
        for key in ["statements", "results", "data", "output"]:
            if key in data and isinstance(data[key], list):
                statements = data[key]
                break
        if statements is None:
            statements = []
            for v in data.values():
                if isinstance(v, list):
                    statements.extend(v)
    else:
        raise ValueError(f"Unexpected format in {path}")

    # stage_a2's assign_ids writes the canonical ID into "id" (e.g. "N-1-4-001")
    # while the LLM also populates "statement_id" with dots (e.g. "N-1.4-001").
    # stage_a1 writes only "statement_id". Normalise here so the rest of the
    # pipeline always uses the dash-separated canonical form in statement_id.
    for stmt in statements:
        if stmt.get("id"):
            stmt["statement_id"] = stmt["id"]

    return statements


# ---------------------------------------------------------------------------
# ID collision check
# ---------------------------------------------------------------------------

def check_id_collisions(a1: list, a2: list) -> list:
    """Check for statement_id collisions between A1 and A2."""
    a1_ids    = {s.get("statement_id") for s in a1 if s.get("statement_id")}
    a2_ids    = {s.get("statement_id") for s in a2 if s.get("statement_id")}
    collisions = a1_ids & a2_ids

    if collisions:
        print(f"  WARNING: {len(collisions)} ID collision(s):")
        for sid in sorted(collisions)[:10]:
            print(f"    - {sid}")
    else:
        print(f"  No ID collisions")

    return list(collisions)


# ---------------------------------------------------------------------------
# Exact dedup within a single list
# ---------------------------------------------------------------------------

def _exact_dedup(statements: list, merge_log: list, source: str) -> list:
    """Fast O(n) exact dedup using (full_section, category, normalized_desc).

    Includes `category` in the key because two statements in the same section
    can share an identical description but represent different FSM artefacts
    (e.g. D-1-4-002 is an FSM_STATE while D-1-4-004 is an IMPLICIT_TRANSITION
    of the same prose sentence). Collapsing them would silently destroy
    structural information.

    Uses the full source_section (no parent-level collapse) to avoid
    incorrectly merging statements from sibling subsections that happen
    to share a description. normalize_section is reserved for the
    cross-stage pre-filter where a wider net is intentional.
    """
    seen   = {}
    result = []

    for s in statements:
        key = (
            s.get("source_section", ""),
            s.get("category", ""),
            normalize_desc(get_desc(s)),
        )
        if not key[2]:
            result.append(s)
            continue

        if key not in seen:
            seen[key] = s
            result.append(s)
        else:
            merge_log.append({
                "kept":            seen[key].get("statement_id"),
                "removed":         s.get("statement_id"),
                "action":          f"EXACT_DUPLICATE_WITHIN_{source}",
                "similarity":      1.0,
                "enriched_fields": [],
                "llm_used":        False,
                "warning":         None,
            })

    return result


# ---------------------------------------------------------------------------
# Enrich helper
# ---------------------------------------------------------------------------

def _is_missing(v) -> bool:
    """
    Treat None, empty string, empty list, and empty dict as missing.
    Stage A2 commonly emits actors=[] when no actor was identified, so
    `None`-only checks would refuse to enrich it from A1.
    """
    if v is None:
        return True
    if isinstance(v, (str, list, dict)) and len(v) == 0:
        return True
    return False


def _enrich(s2: dict, s1: dict, fields: list = None) -> list:
    """
    Copy fields from s1 into "missing" fields of s2.
    Returns list of field names that were enriched.
    """
    if fields is None:
        fields = ENRICH_FIELDS
    enriched = []
    for field in fields:
        if _is_missing(s2.get(field)) and not _is_missing(s1.get(field)):
            s2[field] = s1[field]
            enriched.append(field)
    return enriched


# ---------------------------------------------------------------------------
# Cross-stage dedup (IMPLICIT_TRANSITION vs BEHAVIORAL)
# ---------------------------------------------------------------------------

def _sections_compatible(sec1_full: str, sec2_full: str) -> bool:
    """
    Pre-filter: a candidate A1/A2 pair must share section context.

    Rule:
      - If either side has no source_section, REJECT (avoid stray matches
        from mis-tagged statements that have no anchor).
      - If full sections match exactly → accept.
      - Otherwise accept iff they share the same parent (first 2 segments),
        so e.g. "7.4.1" and "7.4.2" still pair up, but "7.4" and "8.1" do not.
        The similarity threshold + LLM tier handle the rest.
    """
    if not sec1_full or not sec2_full:
        return False
    if sec1_full == sec2_full:
        return True
    return normalize_section(sec1_full) == normalize_section(sec2_full)


def _build_candidate_pairs(
    a1_implicit: list,
    a2_behavioral: list,
    threshold_low: float,
) -> list:
    """
    Score every A1×A2 pair that passes the section pre-filter.
    Returns list of (sim, sid1, sid2, s1, s2) sorted by sim desc.
    Only pairs with sim >= threshold_low are returned.
    """
    a1_norms = {
        s.get("statement_id"): (
            s.get("source_section", ""),
            normalize_desc(get_desc(s)),
        )
        for s in a1_implicit
    }
    a2_norms = {
        s.get("statement_id"): (
            s.get("source_section", ""),
            normalize_desc(get_desc(s)),
        )
        for s in a2_behavioral
    }

    pairs = []
    for s1 in a1_implicit:
        sid1 = s1.get("statement_id")
        sec1, desc1 = a1_norms[sid1]
        if not desc1:
            continue
        for s2 in a2_behavioral:
            sid2 = s2.get("statement_id")
            sec2, desc2 = a2_norms[sid2]
            if not desc2:
                continue
            if not _sections_compatible(sec1, sec2):
                continue
            sim = similarity(desc1, desc2)
            if sim >= threshold_low:
                pairs.append((sim, sid1, sid2, s1, s2))

    pairs.sort(key=lambda p: p[0], reverse=True)
    return pairs


def cross_stage_dedup(
    a1_clean: list,
    a2_clean: list,
    merge_log: list,
    llm_client: Optional[LLMClient] = None,
    threshold_high: float = THRESHOLD_HIGH,
    threshold_low: float  = THRESHOLD_LOW,
    cost_records: Optional[list] = None,
) -> tuple[list, list]:
    """
    Deduplicate between A1's IMPLICIT_TRANSITION and A2's BEHAVIORAL.

    Strategy:
      1. Score every A1×A2 candidate pair that passes section pre-filter.
      2. For HIGH-tier (sim >= threshold_high): accept only mutual-best
         pairings. A pair (s1, s2) is taken only if s2 is s1's highest-sim
         partner AND s1 is s2's highest-sim partner. This prevents two A2
         statements from being greedily paired with the same A1 in the
         iteration order that just happens to come first.
      3. Anything left in the AMBIGUOUS range (and any high-tier candidates
         that lost mutual-best resolution) goes to the LLM if available, or
         to needs_review otherwise.

    Returns:
        (merged_statements, needs_review)
    """
    a1_implicit  = [s for s in a1_clean
                    if s.get("category") == "IMPLICIT_TRANSITION"]
    a1_other     = [s for s in a1_clean
                    if s.get("category") != "IMPLICIT_TRANSITION"]
    a2_behavioral= [s for s in a2_clean
                    if s.get("category") == "BEHAVIORAL"]
    a2_other     = [s for s in a2_clean
                    if s.get("category") != "BEHAVIORAL"]

    a1_ids_to_remove = set()
    needs_review     = []
    llm_calls        = 0

    # 1. Score all candidate pairs.
    pairs = _build_candidate_pairs(
        a1_implicit, a2_behavioral, threshold_low,
    )

    # 2. Find each side's best partner (highest-sim partner that's still
    #    in the candidate set, considering the full set, not a greedy walk).
    best_for_a1 = {}   # sid1 -> (sim, sid2)
    best_for_a2 = {}   # sid2 -> (sim, sid1)
    for sim, sid1, sid2, _s1, _s2 in pairs:
        if sid1 not in best_for_a1 or sim > best_for_a1[sid1][0]:
            best_for_a1[sid1] = (sim, sid2)
        if sid2 not in best_for_a2 or sim > best_for_a2[sid2][0]:
            best_for_a2[sid2] = (sim, sid1)

    a2_by_id = {s.get("statement_id"): s for s in a2_behavioral}
    a1_by_id = {s.get("statement_id"): s for s in a1_implicit}

    processed_pairs       = set()   # (sid1, sid2) we've already decided on
    a2_resolved           = set()   # sid2s already merged or kept-separate
    contention_warned     = set()   # sid1s where we already noted contention

    # 3. Walk pairs in descending similarity. Apply the decision tiers.
    for sim, sid1, sid2, s1, s2 in pairs:
        if (sid1, sid2) in processed_pairs:
            continue
        if sid1 in a1_ids_to_remove or sid2 in a2_resolved:
            continue

        # Mutual-best test: this pair survives as the top candidate only if
        # both sides agree it's their best match.
        a1_best_partner = best_for_a1.get(sid1, (0.0, None))[1]
        a2_best_partner = best_for_a2.get(sid2, (0.0, None))[1]
        mutual_best = (a1_best_partner == sid2 and a2_best_partner == sid1)

        if sim >= threshold_high:
            if mutual_best:
                # --- HIGH tier + mutual best: code decides ---
                enriched = _enrich(s2, s1)
                a1_ids_to_remove.add(sid1)
                a2_resolved.add(sid2)
                processed_pairs.add((sid1, sid2))

                entry = {
                    "kept":            sid2,
                    "removed":         sid1,
                    "action":          "CODE_DEFINITE_DUPLICATE",
                    "similarity":      round(sim, 4),
                    "enriched_fields": enriched,
                    "llm_used":        False,
                    "warning": (
                        f"REVIEW: enriched {sid2} with fields "
                        f"{enriched} from {sid1} — verify correctness"
                    ) if enriched else None,
                }
                merge_log.append(entry)
                if enriched:
                    needs_review.append({
                        **entry,
                        "s1_description": get_desc(s1),
                        "s2_description": get_desc(s2),
                        "s1_from_state":  s1.get("from_state"),
                        "s1_to_state":    s1.get("to_state"),
                        "s1_pre_cond":    s1.get("pre_cond"),
                        "s1_post_cond":   s1.get("post_cond"),
                        "s1_trigger":     s1.get("trigger"),
                    })
                continue

            # HIGH similarity but contention — fall through to LLM/review
            # below so the conflict gets resolved explicitly instead of by
            # iteration order.
            if sid1 not in contention_warned:
                contention_warned.add(sid1)
                merge_log.append({
                    "kept":            None,
                    "removed":         None,
                    "action":          "CONTENTION_DETECTED",
                    "similarity":      round(sim, 4),
                    "enriched_fields": [],
                    "llm_used":        False,
                    "warning": (
                        f"HIGH-similarity pair {sid1}↔{sid2} is not "
                        f"mutual-best (a1_best={a1_best_partner}, "
                        f"a2_best={a2_best_partner}); escalating to LLM"
                    ),
                })

        # AMBIGUOUS tier OR contended HIGH tier → LLM or human.
        if llm_client is None:
            entry = {
                "kept":            None,
                "removed":         None,
                "action":          "AMBIGUOUS_NO_LLM",
                "similarity":      round(sim, 4),
                "enriched_fields": [],
                "llm_used":        False,
                "warning":         "Human review required — no LLM provided",
                "s1_id":           sid1,
                "s2_id":           sid2,
                "s1_description":  get_desc(s1),
                "s2_description":  get_desc(s2),
            }
            merge_log.append(entry)
            needs_review.append(entry)
            processed_pairs.add((sid1, sid2))
            continue

        print(f"  LLM #{llm_calls + 1}: "
              f"sim={sim:.3f} | {sid1} vs {sid2}")
        result    = llm_client.validate_duplicate(s1, s2)
        llm_calls += 1
        processed_pairs.add((sid1, sid2))

        if cost_records is not None and result.get("_cost"):
            cost_records.append({
                "s1_id":     sid1,
                "s2_id":     sid2,
                "similarity": round(sim, 4),
                **result["_cost"],
            })

        rec           = result.get("recommendation", "human_review")
        are_dups      = result.get("are_duplicates", False)
        llm_conf      = result.get("confidence", 0.0)
        enrich_fields = result.get("enrich_from_s1", [])

        if rec == "merge" and are_dups:
            enriched = _enrich(
                s2, s1,
                fields=[f for f in enrich_fields if f in ENRICH_FIELDS],
            )
            a1_ids_to_remove.add(sid1)
            a2_resolved.add(sid2)

            entry = {
                "kept":            sid2,
                "removed":         sid1,
                "action":          "LLM_CONFIRMED_DUPLICATE",
                "similarity":      round(sim, 4),
                "enriched_fields": enriched,
                "llm_used":        True,
                "llm_confidence":  llm_conf,
                "llm_reason":      result.get("reason"),
                "warning": (
                    f"LLM_MERGE: enriched {sid2} with {enriched} "
                    f"(confidence={llm_conf:.2f})"
                ) if enriched else None,
            }
            merge_log.append(entry)
            if enriched or llm_conf < 0.9:
                needs_review.append({
                    **entry,
                    "s1_description": get_desc(s1),
                    "s2_description": get_desc(s2),
                })

        elif rec == "keep_separate":
            merge_log.append({
                "kept":            f"{sid1},{sid2}",
                "removed":         None,
                "action":          "LLM_CONFIRMED_DIFFERENT",
                "similarity":      round(sim, 4),
                "enriched_fields": [],
                "llm_used":        True,
                "llm_confidence":  llm_conf,
                "llm_reason":      result.get("reason"),
                "warning":         None,
            })

        else:
            entry = {
                "kept":            None,
                "removed":         None,
                "action":          "LLM_ESCALATED_TO_HUMAN",
                "similarity":      round(sim, 4),
                "enriched_fields": [],
                "llm_used":        True,
                "llm_confidence":  llm_conf,
                "llm_reason":      result.get("reason"),
                "warning":         "Human review required",
                "s1_id":           sid1,
                "s2_id":           sid2,
                "s1_description":  get_desc(s1),
                "s2_description":  get_desc(s2),
            }
            merge_log.append(entry)
            needs_review.append(entry)

    if llm_calls > 0:
        print(f"  LLM made {llm_calls} validation call(s)")

    # Unused locals (kept for symmetry / readability)
    _ = (a1_by_id, a2_by_id)

    a1_final = a1_other + [
        s for s in a1_implicit
        if s.get("statement_id") not in a1_ids_to_remove
    ]
    a2_final = a2_other + a2_behavioral

    return a1_final + a2_final, needs_review


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------

def _section_sort_key(sec: str) -> tuple:
    """Convert "10.4.2" → ((0,10),(0,4),(0,2)) so sections sort numerically.

    Each segment is wrapped as (0, int) for numeric or (1, str) for textual,
    which lets Python compare segments without raising TypeError on a mix of
    numeric and non-numeric source sections.
    """
    parts = sec.replace("-", ".").split(".")
    return tuple(
        (0, int(p)) if p.isdigit() else (1, p)
        for p in parts
    )


def sort_statements(statements: list) -> list:
    """Sort by stage → section (numeric) → statement_id."""
    return sorted(
        statements,
        key=lambda s: (
            STAGE_ORDER.get(s.get("stage", ""), 99),
            _section_sort_key(s.get("source_section", "")),
            s.get("statement_id", ""),
        ),
    )


# ---------------------------------------------------------------------------
# Spec section map (for warning resolution)
# ---------------------------------------------------------------------------

def _build_section_map(spec_url: str, merge_threshold: int = 200) -> dict:
    """Fetch the spec and return a dict keyed by section_id."""
    from fetcher import fetch_markdown, parse_sections
    markdown = fetch_markdown(spec_url)
    sections = parse_sections(markdown, merge_threshold_words=merge_threshold)
    return {s.section_id: s for s in sections}


# ---------------------------------------------------------------------------
# Resolve NON_FSM_BEHAVIORAL warnings with LLM + spec
# ---------------------------------------------------------------------------

def resolve_non_fsm_warnings(
    combined: list,
    warnings: list,
    llm_client: "LLMClient",
    cost_records: Optional[list],
    spec_url: str,
    merge_threshold: int = 200,
) -> list:
    """
    For every NON_FSM_BEHAVIORAL warning, fetch the source spec section and
    call the LLM to decide:
      - Global constraint → set stmt["is_global_constraint"] = True
        (validate() will skip the warning on re-run)
      - State transition  → enrich from_state/to_state/trigger/pre_cond/post_cond
        in-place (validate() won't warn because has_state_info becomes True)

    Returns a resolution_log list for the summary.
    """
    # Collect statement IDs flagged as NON_FSM_BEHAVIORAL
    flagged_ids: list[str] = []
    for w in warnings:
        if w.startswith("NON_FSM_BEHAVIORAL:"):
            sid = w.split(":", 1)[1].strip().split()[0]
            flagged_ids.append(sid)

    if not flagged_ids:
        return []

    print(f"  Fetching spec for section lookup ({spec_url})...")
    try:
        section_map = _build_section_map(spec_url, merge_threshold)
        print(f"  Spec loaded: {len(section_map)} sections")
    except Exception as e:
        print(f"  ⚠ Spec fetch failed: {e} — resolve step skipped")
        return []

    stmt_by_id = {s.get("statement_id"): s for s in combined}
    resolution_log = []
    call_n = 0

    for sid in flagged_ids:
        stmt = stmt_by_id.get(sid)
        if not stmt:
            continue

        src_sec = stmt.get("source_section", "")
        section = section_map.get(src_sec) or section_map.get(
            normalize_section(src_sec)
        )

        if not section:
            print(f"    ⚠ {sid}: spec section '{src_sec}' not found — skipping")
            resolution_log.append({
                "statement_id":      sid,
                "action":            "SKIPPED_NO_SPEC_SECTION",
                "source_section":    src_sec,
                "enriched_fields":   [],
                "llm_confidence":    None,
                "llm_reason":        f"Section '{src_sec}' not in spec map",
            })
            continue

        call_n += 1
        print(f"  LLM #{call_n}: {sid} (§{src_sec})")
        result = llm_client.resolve_non_fsm_behavioral(stmt, section.full_text)

        if cost_records is not None and result.get("_cost"):
            cost_records.append({
                "call_type":      "resolve_warning",
                "stmt_id":        sid,
                "source_section": src_sec,
                **result["_cost"],
            })

        is_global  = result.get("is_global_constraint", True)
        confidence = result.get("confidence", 0.0)
        reason     = result.get("reason", "")

        entry: dict = {
            "statement_id":      sid,
            "source_section":    src_sec,
            "enriched_fields":   [],
            "llm_confidence":    confidence,
            "llm_reason":        reason,
        }

        if is_global:
            stmt["is_global_constraint"] = True
            entry["action"] = "MARKED_GLOBAL_CONSTRAINT"
        else:
            fsm_fields = ["from_state", "to_state", "trigger", "pre_cond", "post_cond"]
            enriched = []
            for f in fsm_fields:
                val = result.get(f)
                if val and _is_missing(stmt.get(f)):
                    stmt[f] = val
                    enriched.append(f)
            entry["enriched_fields"] = enriched
            entry["action"] = "FSM_FIELDS_ENRICHED" if enriched else "NO_CHANGE"

        resolution_log.append(entry)

    n_global   = sum(1 for e in resolution_log if e["action"] == "MARKED_GLOBAL_CONSTRAINT")
    n_enriched = sum(1 for e in resolution_log if e["action"] == "FSM_FIELDS_ENRICHED")
    n_skipped  = sum(1 for e in resolution_log if e["action"] == "SKIPPED_NO_SPEC_SECTION")
    print(
        f"  Resolved {len(resolution_log)} warning(s): "
        f"{n_global} global, {n_enriched} enriched, {n_skipped} skipped"
    )
    return resolution_log


# ---------------------------------------------------------------------------
# Resolve needs_review entries with LLM + spec
# ---------------------------------------------------------------------------

NEEDS_REVIEW_AUTO_VERIFY_MIN_CONFIDENCE = 0.85


def resolve_needs_review(
    needs_review: list,
    combined: list,
    llm_client: "LLMClient",
    cost_records: Optional[list],
    spec_url: str,
    merge_threshold: int = 200,
    min_confidence: float = NEEDS_REVIEW_AUTO_VERIFY_MIN_CONFIDENCE,
) -> tuple[list, list]:
    """
    For every needs_review entry, fetch the kept statement's source spec section
    and ask the LLM to verify:
      - Was the merge decision correct?
      - Are the enriched FSM fields accurate per the spec?

    Entries that pass (merge_correct=true, fields_correct=true, conf>=min) are
    removed from needs_review and logged as AUTO_VERIFIED.
    Entries with corrections are applied in-place to combined, then removed
    from needs_review.
    Entries where merge_correct=false or confidence is too low remain in
    needs_review for human inspection.

    Returns:
        (updated_needs_review, review_resolution_log)
    """
    if not needs_review:
        return needs_review, []

    print(f"  Fetching spec for needs_review verification ({spec_url})...")
    try:
        section_map = _build_section_map(spec_url, merge_threshold)
        print(f"  Spec loaded: {len(section_map)} sections")
    except Exception as e:
        print(f"  ⚠ Spec fetch failed: {e} — needs_review resolve skipped")
        return needs_review, []

    stmt_by_id = {s.get("statement_id"): s for s in combined}
    still_needs_review = []
    review_resolution_log = []
    call_n = 0

    for entry in needs_review:
        kept_id  = entry.get("kept")
        kept_stmt = stmt_by_id.get(kept_id) if kept_id else None

        if not kept_stmt:
            still_needs_review.append(entry)
            review_resolution_log.append({
                "kept_id": kept_id,
                "action":  "SKIPPED_STMT_NOT_FOUND",
                "reason":  f"Statement '{kept_id}' not in combined list",
            })
            continue

        src_sec = kept_stmt.get("source_section", "")
        section = section_map.get(src_sec) or section_map.get(
            normalize_section(src_sec)
        )

        if not section:
            still_needs_review.append(entry)
            review_resolution_log.append({
                "kept_id":       kept_id,
                "action":        "SKIPPED_NO_SPEC_SECTION",
                "source_section": src_sec,
                "reason":        f"Section '{src_sec}' not in spec map",
            })
            continue

        call_n += 1
        print(f"  LLM #{call_n}: verify {kept_id} (§{src_sec})")
        result = llm_client.verify_needs_review_entry(entry, kept_stmt, section.full_text)

        if cost_records is not None and result.get("_cost"):
            cost_records.append({
                "call_type":      "verify_needs_review",
                "kept_id":        kept_id,
                "source_section": src_sec,
                **result["_cost"],
            })

        merge_ok    = result.get("merge_correct",  True)
        fields_ok   = result.get("fields_correct", False)
        confidence  = result.get("confidence",     0.0)
        corrections = result.get("corrections")    or {}
        reason      = result.get("reason",         "")

        log_entry = {
            "kept_id":       kept_id,
            "removed_id":    entry.get("removed"),
            "merge_correct": merge_ok,
            "fields_correct": fields_ok,
            "corrections":   corrections,
            "llm_confidence": confidence,
            "llm_reason":    reason,
        }

        if not merge_ok:
            # LLM disagrees with the merge decision — keep for human
            still_needs_review.append(entry)
            log_entry["action"] = "KEPT_MERGE_DISPUTED"
            review_resolution_log.append(log_entry)
            continue

        # Apply any field corrections in-place
        applied_corrections = []
        if corrections:
            fsm_fields = {"from_state", "to_state", "trigger", "pre_cond", "post_cond"}
            for field, val in corrections.items():
                if field in fsm_fields:
                    kept_stmt[field] = val  # None clears the field
                    applied_corrections.append(field)

        # Exact duplicates (similarity=1.0) already guarantee the merge is
        # correct — only the enriched field values are uncertain, so accept
        # them at a lower threshold.
        similarity_score = float(entry.get("similarity", 0.0))
        effective_min = 0.75 if similarity_score >= 1.0 else min_confidence

        if (fields_ok or applied_corrections) and confidence >= effective_min:
            # Auto-verified: remove from needs_review
            log_entry["action"] = "AUTO_VERIFIED"
            log_entry["applied_corrections"] = applied_corrections
            review_resolution_log.append(log_entry)
        else:
            # Low confidence — keep for human
            still_needs_review.append(entry)
            log_entry["action"] = "KEPT_LOW_CONFIDENCE"
            log_entry["applied_corrections"] = applied_corrections
            review_resolution_log.append(log_entry)

    n_verified  = sum(1 for e in review_resolution_log if e["action"] == "AUTO_VERIFIED")
    n_corrected = sum(
        1 for e in review_resolution_log
        if e["action"] == "AUTO_VERIFIED" and e.get("applied_corrections")
    )
    n_kept = len(still_needs_review)
    print(
        f"  needs_review: {len(needs_review)} → {n_kept} remaining "
        f"({n_verified} auto-verified, {n_corrected} with corrections)"
    )
    return still_needs_review, review_resolution_log


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def validate(statements: list) -> tuple[list, list]:
    """
    Validate merged statements for structural correctness.

    Returns:
        (errors, warnings)
        errors   — must be zero before running verify_stage_a
        warnings — review recommended, do not block pipeline
    """
    errors   = []
    warnings = []

    # Unique IDs
    all_ids  = [s.get("statement_id") for s in statements]
    seen_ids = set()
    for sid in all_ids:
        if sid in seen_ids:
            errors.append(f"DUPLICATE_ID: {sid}")
        seen_ids.add(sid)

    for s in statements:
        sid = s.get("statement_id", "UNKNOWN")

        # Required base fields
        for field in ["statement_id", "subject", "stage", "source_section"]:
            if not s.get(field):
                errors.append(f"MISSING_FIELD: {sid} missing '{field}'")

        # Valid subject
        subj = s.get("subject")
        if subj and subj not in VALID_SUBJECTS:
            errors.append(f"INVALID_SUBJECT: {sid} subject='{subj}'")

        # Valid stage
        stage = s.get("stage")
        if stage and stage not in VALID_STAGES:
            errors.append(f"INVALID_STAGE: {sid} stage='{stage}'")

        # Valid category
        cat = s.get("category")
        if cat and cat not in VALID_CATEGORIES:
            errors.append(f"INVALID_CATEGORY: {sid} category='{cat}'")

        # Natural language in *_cond fields.
        # Skip the check when the value is clearly a formal expression
        # (contains =, !=, &, |, parentheses, or literals like null/true/false).
        # Substring markers like "task " would otherwise fire on identifiers
        # such as `response.task != null` because of the space before `!=`.
        for field in ["pre_cond", "post_cond", "invariant_cond"]:
            val = s.get(field)
            if val and isinstance(val, str) and not _looks_like_formal_expr(val):
                if any(m in val.lower() for m in NL_MARKERS):
                    errors.append(
                        f"NL_IN_COND: {sid}.{field} = '{val[:80]}'"
                    )

        # FSM_STATE specific
        if cat == "FSM_STATE":
            if not s.get("state_name"):
                errors.append(f"FSM_STATE_MISSING_NAME: {sid}")
            if not s.get("semantic_type"):
                warnings.append(f"FSM_STATE_MISSING_SEMANTIC_TYPE: {sid}")
            if s.get("is_final") and s.get("semantic_type") \
                    not in ("terminal", "error"):
                warnings.append(
                    f"FSM_STATE_SEMANTIC_MISMATCH: {sid} "
                    f"is_final=true but semantic_type="
                    f"'{s.get('semantic_type')}'"
                )

        # Useless transitions: only warn when the statement has *no* FSM
        # information whatsoever. Many BEHAVIORAL statements are legitimate
        # non-transitional constraints (e.g. "the SDK MUST be regenerated")
        # and have pre_cond / post_cond / trigger that make them useful as
        # global rules even though from_state/to_state are null.
        if cat in TRANSITION_CATEGORIES and not s.get("is_global_constraint"):
            has_state_info = (
                s.get("from_state")
                or s.get("to_state")
                or s.get("trigger")
                or s.get("pre_cond")
                or s.get("post_cond")
            )
            if not has_state_info:
                warnings.append(
                    f"NON_FSM_BEHAVIORAL: {sid} has no FSM information "
                    f"(no from/to_state, trigger, or conditions) — "
                    f"treat as a global constraint, not a transition"
                )

    return errors, warnings


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def build_summary(
    a1: list, a2: list,
    statements: list,
    merge_log: list,
    needs_review: list,
    errors: list,
    warnings: list,
    resolution_log: Optional[list] = None,
    review_resolution_log: Optional[list] = None,
) -> dict:
    """Build summary statistics for the merged output."""

    resolution_log       = resolution_log or []
    review_resolution_log = review_resolution_log or []
    llm_calls  = sum(1 for e in merge_log if e.get("llm_used"))
    n_global   = sum(1 for e in resolution_log if e.get("action") == "MARKED_GLOBAL_CONSTRAINT")
    n_enriched = sum(1 for e in resolution_log if e.get("action") == "FSM_FIELDS_ENRICHED")
    n_auto_verified = sum(1 for e in review_resolution_log if e.get("action") == "AUTO_VERIFIED")
    n_corrected     = sum(
        1 for e in review_resolution_log
        if e.get("action") == "AUTO_VERIFIED" and e.get("applied_corrections")
    )

    return {
        "pipeline": {
            "a1_input_count":             len(a1),
            "a2_input_count":             len(a2),
            "combined_before_dedup":      len(a1) + len(a2),
            "duplicates_removed":         len([e for e in merge_log
                                              if e.get("removed")]),
            "llm_calls_made":             llm_calls,
            "needs_human_review":         len(needs_review),
            "total_after_merge":          len(statements),
            "warnings_resolved_total":    len(resolution_log),
            "warnings_marked_global":     n_global,
            "warnings_fsm_enriched":      n_enriched,
            "needs_review_auto_verified": n_auto_verified,
            "needs_review_corrected":     n_corrected,
        },
        "by_prefix": {
            "D": len([s for s in statements
                      if s.get("statement_id", "").startswith("D-")]),
            "N": len([s for s in statements
                      if s.get("statement_id", "").startswith("N-")]),
        },
        "by_category": {
            cat: len([s for s in statements if s.get("category") == cat])
            for cat in [
                "FSM_STATE", "FIELD_PRESENCE", "ONEOF_CONSTRAINT",
                "IMPLICIT_TRANSITION", "BEHAVIORAL",
            ]
        },
        "by_stage": {
            stage: len([s for s in statements if s.get("stage") == stage])
            for stage in STAGE_ORDER.keys()
        },
        "by_subject": {
            subj: len([s for s in statements if s.get("subject") == subj])
            for subj in sorted(VALID_SUBJECTS)
        },
        "by_modality": {
            mod: len([s for s in statements if s.get("modality") == mod])
            for mod in ["must", "shall", "should", "may", "can",
                        "other", "unspecified"]
            if any(s.get("modality") == mod for s in statements)
        },
        "validation": {
            "error_count":      len(errors),
            "warning_count":    len(warnings),
            "errors":           errors,
            "warnings":         warnings,
            "ready_for_verify": len(errors) == 0,
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def merge(
    a1_path: str,
    a2_path: str,
    out_path: str,
    api_key: Optional[str] = None,
    model: str = "anthropic/claude-sonnet-4-6",
    threshold_high: float = THRESHOLD_HIGH,
    threshold_low: float  = THRESHOLD_LOW,
    spec_url: str = "https://a2a-protocol.org/latest/specification/",
    spec_merge_threshold: int = 200,
    skip_resolve_warnings: bool = False,
) -> dict:
    """Full merge pipeline."""

    print(f"\n{'='*60}")
    print("  MERGE STAGE A1 + A2")
    print(f"{'='*60}\n")

    # Load
    print("[1/7] Loading inputs...")
    a1 = load(a1_path)
    a2 = load(a2_path)
    print(f"      A1: {len(a1)} statements")
    print(f"      A2: {len(a2)} statements")

    # ID collision check
    print(f"\n[2/7] Checking ID collisions...")
    check_id_collisions(a1, a2)

    # Exact dedup within each list
    print(f"\n[3/7] Exact dedup within A1 and A2...")
    merge_log = []
    a1_clean  = _exact_dedup(a1, merge_log, "A1")
    a2_clean  = _exact_dedup(a2, merge_log, "A2")
    print(f"      A1: {len(a1)} → {len(a1_clean)} "
          f"(-{len(a1) - len(a1_clean)})")
    print(f"      A2: {len(a2)} → {len(a2_clean)} "
          f"(-{len(a2) - len(a2_clean)})")

    # LLM client
    llm_client = None
    if api_key:
        print(f"\n[4/7] LLM client ready ({model})")
        print(f"      Ambiguous range: [{threshold_low}, {threshold_high})")
        llm_client = LLMClient(api_key=api_key, model=model)
    else:
        print(f"\n[4/7] No API key — ambiguous dedup + warning resolution skipped")

    # Cross-stage dedup
    print(f"\n[5/7] Cross-stage dedup "
          f"(IMPLICIT_TRANSITION vs BEHAVIORAL)...")
    cost_records: list = []
    combined, needs_review = cross_stage_dedup(
        a1_clean, a2_clean, merge_log,
        llm_client=llm_client,
        threshold_high=threshold_high,
        threshold_low=threshold_low,
        cost_records=cost_records,
    )
    combined = sort_statements(combined)
    print(f"      Total after dedup: {len(combined)}")
    print(f"      Needs human review: {len(needs_review)}")

    # LLM-based needs_review resolution with spec cross-check
    review_resolution_log: list = []
    if needs_review and llm_client and not skip_resolve_warnings:
        print(
            f"\n[6/8] Verifying {len(needs_review)} needs_review entry(s) "
            f"with LLM + spec..."
        )
        needs_review, review_resolution_log = resolve_needs_review(
            needs_review, combined, llm_client, cost_records,
            spec_url, spec_merge_threshold,
        )
    elif needs_review and not llm_client:
        print(f"\n[6/8] needs_review verification skipped (no API key)")
    elif skip_resolve_warnings:
        print(f"\n[6/8] needs_review verification skipped (--skip-resolve-warnings)")
    else:
        print(f"\n[6/8] No needs_review entries — step skipped")

    # Heuristic validate
    print(f"\n[7/8] Validating (heuristic)...")
    errors, warnings = validate(combined)
    print(f"      Errors:   {len(errors)}")
    print(f"      Warnings: {len(warnings)}")

    if errors:
        print(f"\n  ERRORS (fix before verify_stage_a):")
        for e in errors[:20]:
            print(f"    ✗ {e}")
    if warnings:
        print(f"\n  WARNINGS (before LLM resolve):")
        for w in warnings[:20]:
            print(f"    ⚠ {w}")

    # LLM-based warning resolution with spec cross-check
    resolution_log: list = []
    non_fsm_warnings = [w for w in warnings if w.startswith("NON_FSM_BEHAVIORAL:")]

    if non_fsm_warnings and llm_client and not skip_resolve_warnings:
        print(
            f"\n[8/8] Resolving {len(non_fsm_warnings)} NON_FSM_BEHAVIORAL "
            f"warning(s) with LLM + spec..."
        )
        resolution_log = resolve_non_fsm_warnings(
            combined, non_fsm_warnings, llm_client, cost_records,
            spec_url, spec_merge_threshold,
        )
        # Re-validate with enriched/marked statements
        errors, warnings = validate(combined)
        print(f"      After resolve — Errors: {len(errors)}, Warnings: {len(warnings)}")
        if warnings:
            for w in warnings[:20]:
                print(f"    ⚠ {w}")
    elif non_fsm_warnings and not llm_client:
        print(f"\n[8/8] Warning resolution skipped (no API key)")
    elif skip_resolve_warnings:
        print(f"\n[8/8] Warning resolution skipped (--skip-resolve-warnings)")
    else:
        print(f"\n[8/8] No NON_FSM_BEHAVIORAL warnings — resolve step skipped")

    # Build and write output
    summary = build_summary(
        a1, a2, combined, merge_log, needs_review,
        errors, warnings, resolution_log, review_resolution_log,
    )

    output = {
        "statements":          combined,
        "merge_log":           merge_log,
        "resolution_log":      resolution_log,
        "review_resolution_log": review_resolution_log,
        "summary":             summary,
    }

    out = Path(out_path)
    stage_dir = out.parent
    cost_dir  = stage_dir / "cost"
    stage_dir.mkdir(parents=True, exist_ok=True)
    cost_dir.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Per-call cost records
    for rec in cost_records:
        call_type = rec.get("call_type", "")
        if call_type == "resolve_warning":
            fname = f"resolve_{rec['stmt_id'].replace('/', '_')}.json"
        elif call_type == "verify_needs_review":
            fname = f"verify_review_{rec['kept_id'].replace('/', '_')}.json"
        else:
            fname = f"{rec['s1_id']}_vs_{rec['s2_id']}.json"
        with open(cost_dir / fname, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)

    total_in    = sum(r.get("input_tokens",  0) for r in cost_records)
    total_out   = sum(r.get("output_tokens", 0) for r in cost_records)
    total_tok   = sum(r.get("total_tokens",  0) for r in cost_records)
    total_cost  = sum(r.get("cost_usd",      0.0) for r in cost_records)

    dedup_calls         = sum(1 for r in cost_records
                              if r.get("call_type") not in ("resolve_warning", "verify_needs_review"))
    resolve_calls       = sum(1 for r in cost_records if r.get("call_type") == "resolve_warning")
    verify_review_calls = sum(1 for r in cost_records if r.get("call_type") == "verify_needs_review")

    cost_summary = {
        "stage":                    STAGE,
        "model":                    (cost_records[0]["model"] if cost_records else model),
        "llm_calls_made":           len(cost_records),
        "dedup_llm_calls":          dedup_calls,
        "resolve_warning_calls":    resolve_calls,
        "verify_needs_review_calls": verify_review_calls,
        "total_input_tokens":       total_in,
        "total_output_tokens":      total_out,
        "total_tokens":             total_tok,
        "total_cost_usd":           round(total_cost, 8),
        "per_call":                 cost_records,
    }
    cost_summary_path = stage_dir / f"{STAGE}_cost.json"
    with open(cost_summary_path, "w", encoding="utf-8") as f:
        json.dump(cost_summary, f, indent=2, ensure_ascii=False)

    # Write needs_review if any
    if needs_review:
        review_path = out.with_name(out.stem + "_needs_review.json")
        with open(review_path, "w", encoding="utf-8") as f:
            json.dump({
                "count": len(needs_review),
                "instruction": (
                    "Review before running verify_stage_a. "
                    "Each entry is either (a) an LLM-uncertain merge "
                    "decision, or (b) a CODE_DEFINITE_DUPLICATE that "
                    "enriched a BEHAVIORAL statement with inferred fields "
                    "from an IMPLICIT_TRANSITION. Verify that from_state, "
                    "to_state, pre_cond, post_cond, and trigger are correct "
                    "for each kept statement."
                ),
                "entries": needs_review,
            }, f, indent=2, ensure_ascii=False)
        print(f"\n  ⚠  {len(needs_review)} entries need review: "
              f"{review_path}")
    else:
        print(f"\n  ✓  No entries need human review")

    print(f"\n{'='*60}")
    print(f"  MERGE COMPLETE")
    print(f"  Total statements:   {len(combined)}")
    print(
        f"  LLM calls:          {len(cost_records)} "
        f"(dedup: {dedup_calls}, "
        f"verify-review: {verify_review_calls}, "
        f"resolve-warnings: {resolve_calls})"
    )
    print(f"  Total tokens:       {total_tok:,} "
          f"(in:{total_in:,} out:{total_out:,})")
    print(f"  Total cost:         ${total_cost:.6f}")
    print(f"  needs_review left:  {len(needs_review)}")
    print(f"  Ready for verify:   {summary['validation']['ready_for_verify']}")
    print(f"  Output:  {out_path}")
    print(f"  Costs:   {cost_summary_path}")
    print(f"{'='*60}\n")

    return output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    defaults = _default_paths()

    parser = argparse.ArgumentParser(
        description=(
            "Merge Stage A1 (structural) and Stage A2 (behavioral) "
            "outputs into a unified statement list for verify_stage_a. "
            "FSM tuple construction happens after verification."
        )
    )
    parser.add_argument(
        "--a1", default=str(defaults["a1"]),
        help=f"Path to Stage A1 output JSON (default: {defaults['a1']})",
    )
    parser.add_argument(
        "--a2", default=str(defaults["a2"]),
        help=f"Path to Stage A2 output JSON (default: {defaults['a2']})",
    )
    parser.add_argument(
        "--out", default=str(defaults["out"]),
        help=f"Path to write merged output JSON (default: {defaults['out']})",
    )
    parser.add_argument(
        "--api-key", default=_default_api_key(),
        dest="api_key",
        help=(
            "Anthropic API key for LLM validation of ambiguous cases. "
            "Defaults to api_keys.anthropic from config.yaml."
        ),
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Disable LLM validation even if a key is configured.",
    )
    parser.add_argument(
        "--model", default="anthropic/claude-sonnet-4-6",
        help="LiteLLM model string (default: anthropic/claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--similarity-threshold", type=float,
        default=THRESHOLD_HIGH, dest="threshold_high",
        help=f"sim >= this → definite duplicate (default: {THRESHOLD_HIGH})",
    )
    parser.add_argument(
        "--ambiguous-low", type=float,
        default=THRESHOLD_LOW, dest="threshold_low",
        help=f"sim >= this → LLM decides (default: {THRESHOLD_LOW})",
    )
    _spec_cfg = _default_spec_config()
    parser.add_argument(
        "--spec-url", default=_spec_cfg["url"],
        dest="spec_url",
        help=(
            "URL or local path to the A2A spec for warning resolution. "
            f"Defaults to fetcher.url in config.yaml ({_spec_cfg['url']})"
        ),
    )
    parser.add_argument(
        "--skip-resolve-warnings", action="store_true",
        dest="skip_resolve_warnings",
        help="Skip LLM-based NON_FSM_BEHAVIORAL warning resolution.",
    )

    args = parser.parse_args()
    merge(
        a1_path=args.a1,
        a2_path=args.a2,
        out_path=args.out,
        api_key=None if args.no_llm else args.api_key,
        model=args.model,
        threshold_high=args.threshold_high,
        threshold_low=args.threshold_low,
        spec_url=args.spec_url,
        spec_merge_threshold=_spec_cfg["merge_threshold_words"],
        skip_resolve_warnings=args.skip_resolve_warnings,
    )
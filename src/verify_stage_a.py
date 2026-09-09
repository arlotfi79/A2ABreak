#!/usr/bin/env python3
"""
verify_stage_a.py
-----------------
Verification stage for the merged Stage A output.

For each source_section in stage_a12/final.json:
  1. Re-fetch the corresponding section text from the A2A spec (via fetcher.py)
  2. Send section text + extracted statements to LLM
  3. LLM checks: existence, level, subject, stage, action clarity, missing
  4. Automated cross-check validates the LLM's own verification
  5. Apply corrections and produce verified output

Handles both N- (BEHAVIORAL) and D- (structural) statements:
  - N-: checked for RFC 2119 grounding, existence in spec text
  - D-: checked for grounding in spec definitions/tables/enumerations

Outputs:
  outputs/verify_stage_a12/final.json              ← corrected + cleaned statements
  outputs/verify_stage_a12/final_report.json        ← full verification report
  outputs/verify_stage_a12/verify_stage_a12_cost.json ← aggregate cost summary
  outputs/verify_stage_a12/cost/                    ← per-call cost records
"""

import json
import re
import time
import argparse
import logging
from collections import defaultdict
from pathlib import Path
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

from fetcher import fetch_markdown, parse_sections, Section

logger = logging.getLogger(__name__)

STAGE       = "verify_stage_a12"
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_CFG = _SCRIPT_DIR / "config.yaml"

# ---------------------------------------------------------------------------
# Config helpers (same pattern as merge_stage_a12.py)
# ---------------------------------------------------------------------------

def _load_config(config_path: Path = _DEFAULT_CFG) -> dict:
    if yaml is None or not config_path.exists():
        return {}
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _default_paths(config_path: Path = _DEFAULT_CFG) -> dict:
    config_root = config_path.parent
    cfg         = _load_config(config_path)
    base        = cfg.get("output", {}).get("base_dir", "outputs")
    base_dir    = (config_root / base).resolve()
    return {
        "input":  base_dir / "stage_a12"       / "final.json",
        "output": base_dir / STAGE             / "final.json",
    }


def _default_api_key(config_path: Path = _DEFAULT_CFG) -> Optional[str]:
    cfg = _load_config(config_path)
    key = (cfg.get("api_keys") or {}).get("anthropic")
    if isinstance(key, str) and key.strip():
        return key.strip()
    return None


def _default_spec_config(config_path: Path = _DEFAULT_CFG) -> dict:
    cfg         = _load_config(config_path)
    fetcher_cfg = cfg.get("fetcher") or {}
    return {
        "url":                   fetcher_cfg.get("url", "https://a2a-protocol.org/latest/specification/"),
        "merge_threshold_words": int(fetcher_cfg.get("merge_threshold_words", 200)),
    }


def _default_model(config_path: Path = _DEFAULT_CFG) -> str:
    cfg = _load_config(config_path)
    return (cfg.get("model") or {}).get("name", "anthropic/claude-sonnet-4-6")


def _default_max_tokens(config_path: Path = _DEFAULT_CFG) -> int:
    cfg = _load_config(config_path)
    return int((cfg.get("model") or {}).get("max_tokens", 16000))


def _load_prompt_templates(
    config_path: Path = _DEFAULT_CFG,
) -> tuple[str, str]:
    """
    Load N- (BEHAVIORAL) and D- (structural) prompt templates from config.yaml.

    Keys: system_prompts.verify_stage_a12_n
          system_prompts.verify_stage_a12_d

    The templates use .replace() placeholders:
        {section_id}, {title}, {section_text}, {statement_id}, {statement_json}
    """
    cfg     = _load_config(config_path)
    prompts = cfg.get("system_prompts") or {}

    n_tpl = prompts.get("verify_stage_a12_n")
    d_tpl = prompts.get("verify_stage_a12_d")

    if not n_tpl or not d_tpl:
        missing = []
        if not n_tpl:
            missing.append("system_prompts.verify_stage_a12_n")
        if not d_tpl:
            missing.append("system_prompts.verify_stage_a12_d")
        raise ValueError(
            f"Missing prompt template(s) in config.yaml: {', '.join(missing)}"
        )

    return n_tpl.strip(), d_tpl.strip()


def _default_section_delay(config_path: Path = _DEFAULT_CFG) -> float:
    cfg = _load_config(config_path)
    return float((cfg.get("model") or {}).get("section_delay_seconds", 5))


def _default_max_retries(config_path: Path = _DEFAULT_CFG) -> int:
    cfg = _load_config(config_path)
    return int((cfg.get("model") or {}).get("max_retries", 5))


def _default_retry_wait(config_path: Path = _DEFAULT_CFG) -> int:
    cfg = _load_config(config_path)
    return int((cfg.get("model") or {}).get("rate_limit_retry_wait_seconds", 60))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RFC_KEYWORD_MAP = {
    "MUST NOT":    ["MUST_NOT", "SHALL_NOT"],
    "SHALL NOT":   ["MUST_NOT", "SHALL_NOT"],
    "MUST":        ["MUST", "SHALL", "REQUIRED"],
    "SHALL":       ["MUST", "SHALL", "REQUIRED"],
    "REQUIRED":    ["MUST", "SHALL", "REQUIRED"],
    "SHOULD NOT":  ["SHOULD_NOT"],
    "SHOULD":      ["SHOULD", "RECOMMENDED"],
    "RECOMMENDED": ["SHOULD", "RECOMMENDED"],
    "MAY":         ["MAY", "OPTIONAL"],
    "OPTIONAL":    ["MAY", "OPTIONAL"],
}

BEHAVIORAL_CATS = {"BEHAVIORAL"}
STRUCTURAL_CATS = {"FSM_STATE", "FIELD_PRESENCE", "ONEOF_CONSTRAINT", "IMPLICIT_TRANSITION"}

# LLM may suggest new values for these keys; statement_id is never applied from corrections.
_CORRECTABLE_STATEMENT_FIELDS = frozenset({
    "normative_level", "category", "subject", "stage", "description",
    "modality", "actors", "from_state", "to_state", "trigger",
    "pre_cond", "post_cond", "actions", "is_inferred", "confidence",
    "source_section", "is_global_constraint", "id",
    "state_name", "enum_name", "semantic_type", "is_initial", "is_final",
    "invariant_cond", "object_name", "field_name", "field_type", "required",
    "allowed_values", "guard_semantics",
})

_PROTECTED_FROM_LLM = frozenset({"statement_id"})

# ---------------------------------------------------------------------------
# Spec section map (uses fetcher.py — same as merge_stage_a12.py)
# ---------------------------------------------------------------------------

def _build_section_map(spec_url: str, merge_threshold: int = 200) -> dict[str, Section]:
    """Fetch spec and return {section_id: Section}. Works for URLs and local paths."""
    logger.info(f"Fetching spec from {spec_url}...")
    markdown = fetch_markdown(spec_url)
    sections = parse_sections(markdown, merge_threshold_words=merge_threshold)
    section_map = {s.section_id: s for s in sections}
    logger.info(f"Parsed {len(section_map)} sections")
    return section_map


def find_section(
    section_map: dict[str, Section],
    source_section: str,
) -> Optional[Section]:
    """
    Find a Section by ID, trying progressively shorter prefixes.
    "3.1.4" → try "3.1.4", then "3.1", then "3"
    """
    if source_section in section_map:
        return section_map[source_section]
    clean = source_section.rstrip(".")
    if clean in section_map:
        return section_map[clean]
    parts = clean.split(".")
    while len(parts) > 1:
        parts.pop()
        candidate = ".".join(parts)
        if candidate in section_map:
            return section_map[candidate]
    return None


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _section_sort_key(sec: str) -> tuple:
    """Numeric sort key: '10.2' sorts after '2.1', not before."""
    parts = sec.replace("-", ".").split(".")
    return tuple(
        (0, int(p)) if p.isdigit() else (1, p)
        for p in parts
    )


def group_by_section(statements: list[dict]) -> dict[str, list[dict]]:
    """Group statements by source_section, sort D- before N-, then by statement_id."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for s in statements:
        sec = str(s.get("source_section", "unknown")).strip().rstrip(".")
        groups[sec].append(s)

    def sort_key(s):
        sid    = s.get("statement_id", "")
        prefix = 0 if sid.startswith("D-") else 1
        return (prefix, sid)

    return {
        k: sorted(v, key=sort_key)
        for k, v in sorted(groups.items(), key=lambda kv: _section_sort_key(kv[0]))
    }


# ---------------------------------------------------------------------------
# Slim statement for LLM (unchanged from new version)
# ---------------------------------------------------------------------------

def slim(s: dict) -> dict:
    """Send fields the LLM needs to verify (includes nulls when key exists)."""
    keep = [
        "statement_id", "normative_level", "category", "subject",
        "stage", "source_section", "description",
        "from_state", "to_state", "trigger",
        "pre_cond", "post_cond", "invariant_cond",
        "modality", "state_name", "enum_name", "semantic_type",
        "is_initial", "is_final", "is_inferred", "confidence",
        "allowed_values", "object_name", "field_name", "required",
        "guard_semantics", "actors", "actions",
        "is_global_constraint", "id",
    ]
    return {k: s[k] for k in keep if k in s}


# Prompt templates are now stored in config.yaml under:
#   system_prompts.verify_stage_a12_n  (BEHAVIORAL / N- statements)
#   system_prompts.verify_stage_a12_d  (structural / D- statements)
# Load them at runtime via _load_prompt_templates().


# ---------------------------------------------------------------------------
# Automated cross-check (unchanged from new version)
# ---------------------------------------------------------------------------

def automated_cross_check(
    llm_results: list[dict],
    section_text: str,
    statements: list[dict],
) -> list[dict]:
    """Programmatically validate LLM verification output. Returns list of issue dicts."""
    issues = []
    stmt_map     = {s["statement_id"]: s for s in statements}
    source_lower = section_text.lower()

    for r in llm_results:
        sid       = r.get("statement_id", "")
        existence = r.get("existence", "")
        location  = (r.get("location_in_text") or "").lower().strip()
        stmt      = stmt_map.get(sid)
        if not stmt:
            continue

        desc_lower = stmt.get("description", "").strip().lower()

        # 1. False hallucination: LLM said not_found but snippet IS in source
        if existence == "not_found":
            snippet = desc_lower[:50]
            if snippet and snippet in source_lower:
                issues.append({
                    "statement_id": sid,
                    "check_type":   "false_hallucination",
                    "detail": (
                        f"LLM marked not_found but '{snippet[:40]}' "
                        f"IS in source text"
                    ),
                })

        # 2. False verification: LLM said found but location NOT in source
        if existence == "found" and location and len(location) > 5:
            if location[:40] not in source_lower:
                issues.append({
                    "statement_id": sid,
                    "check_type":   "false_verification",
                    "detail": (
                        f"LLM claimed location '{location[:40]}' "
                        f"NOT found in source"
                    ),
                })

        # 3. RFC 2119 keyword mismatch (BEHAVIORAL only)
        if stmt.get("category") == "BEHAVIORAL":
            desc_upper   = stmt.get("description", "").upper()
            recorded_lvl = stmt.get("normative_level", "")
            for keyword, valid_levels in RFC_KEYWORD_MAP.items():
                if keyword in desc_upper:
                    if recorded_lvl not in valid_levels:
                        issues.append({
                            "statement_id": sid,
                            "check_type":   "keyword_mismatch",
                            "detail": (
                                f"description contains '{keyword}' but "
                                f"normative_level='{recorded_lvl}' "
                                f"(expected one of {valid_levels})"
                            ),
                        })
                    break

    return issues


# ---------------------------------------------------------------------------
# LLM client (with cost tracking — same pattern as merge_stage_a12.py)
# ---------------------------------------------------------------------------

class LLMClient:
    def __init__(
        self,
        api_key:     str,
        model:       str,
        max_tokens:  int = 16000,
        max_retries: int = 5,
        retry_wait:  int = 60,
    ):
        self.model       = model
        self.max_tokens  = max_tokens
        self.max_retries = max_retries
        self.retry_wait  = retry_wait
        try:
            import litellm
            from litellm import completion_cost
            import os
            os.environ["ANTHROPIC_API_KEY"] = api_key
            litellm.suppress_debug_info = True
            litellm.set_verbose         = False
            logging.getLogger("LiteLLM").setLevel(logging.WARNING)
            self._litellm         = litellm
            self._completion_cost = completion_cost
        except ImportError:
            raise ImportError("pip install litellm")

    def call(self, prompt: str) -> tuple[dict, dict]:
        """
        Call the LLM and return (result_dict, cost_record).

        The prompt is sent as a single user message — no separate system
        message, because the N- and D- prompt templates are fully
        self-contained (loaded from config.yaml).
        """
        messages = [{"role": "user", "content": prompt}]

        t0 = time.time()
        last_err = None
        for attempt in range(self.max_retries):
            try:
                response = self._litellm.completion(
                    model            = self.model,
                    messages         = messages,
                    max_tokens       = self.max_tokens,
                    reasoning_effort = "low",
                )
                break
            except Exception as e:
                last_err = e
                err_lower = str(e).lower()
                if "rate_limit" in err_lower or "rate limit" in err_lower:
                    wait = self.retry_wait * (attempt + 1)
                    logger.warning(
                        f"Rate limit hit (attempt {attempt + 1}/{self.max_retries}), "
                        f"waiting {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    raise
        else:
            raise RuntimeError(
                f"Exceeded {self.max_retries} retries due to rate limits: {last_err}"
            )

        elapsed = time.time() - t0

        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$',    '', raw)
        result = json.loads(raw)

        usage         = getattr(response, "usage", None)
        input_tokens  = getattr(usage, "prompt_tokens",     0) if usage else 0
        output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        total_tokens  = getattr(usage, "total_tokens",      0) if usage else 0
        try:
            cost = self._completion_cost(completion_response=response)
        except Exception:
            cost = 0.0

        cost_record = {
            "model":           self.model,
            "input_tokens":    input_tokens,
            "output_tokens":   output_tokens,
            "total_tokens":    total_tokens,
            "cost_usd":        round(cost, 8),
            "elapsed_seconds": round(elapsed, 4),
            "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return result, cost_record


# ---------------------------------------------------------------------------
# Verify one section
# ---------------------------------------------------------------------------

def verify_section(
    section_id:    str,
    section:       Optional[Section],
    statements:    list[dict],
    llm:           Optional[LLMClient],
    sections_dir:  Optional[Path],
    cost_records:  list,
    n_prompt_tpl:  str = "",
    d_prompt_tpl:  str = "",
    cost_dir:      Optional[Path] = None,
) -> dict:
    """
    Verify all statements for one section.
    Appends cost records to cost_records in-place.
    Returns a section result dict.
    """
    safe_id      = section_id.replace(".", "_").replace("/", "_")
    section_path = sections_dir / f"{safe_id}.json" if sections_dir else None

    # Resume from existing section file
    if section_path and section_path.exists():
        logger.info(f"  [CACHED] {section_id}")
        return json.loads(section_path.read_text())

    if section is None:
        logger.warning(f"  [SKIP]  {section_id} — not found in spec")
        return {
            "section_id":       section_id,
            "skipped":          True,
            "reason":           "section_not_in_spec",
            "results":          [],
            "missing":          [],
            "automated_issues": [],
        }

    title        = section.title
    section_text = section.full_text   # includes parent breadcrumb headers

    all_results = []
    all_auto    = []

    n_total = len(statements)
    logger.info(f"  {section_id} '{title}' ({n_total} statements)")

    for idx, stmt in enumerate(statements, 1):
        sid      = stmt.get("statement_id", f"stmt-{idx}")
        cat      = stmt.get("category", "")
        is_behav = cat in BEHAVIORAL_CATS
        tpl      = n_prompt_tpl if is_behav else d_prompt_tpl
        label    = "N" if is_behav else "D"

        if llm is None:
            all_results.append({
                "statement_id": sid,
                "status":       "unverified",
                "existence":    "unchecked",
            })
            continue

        prompt = (
            tpl
            .replace("{section_id}",    section_id)
            .replace("{title}",         title)
            .replace("{section_text}",  section_text)
            .replace("{statement_id}",  sid)
            .replace("{statement_json}", json.dumps(slim(stmt), indent=2))
        )

        try:
            result, cost_rec = llm.call(prompt)
            full_rec = {
                "section_id":   section_id,
                "statement_id": sid,
                "label":        label,
                **cost_rec,
            }
            cost_records.append(full_rec)
            logger.info(
                f"  [{label}] {sid}  {cost_rec.get('total_tokens', 0):,} tok"
                f"  ${cost_rec.get('cost_usd', 0.0):.6f}"
            )
            if cost_dir:
                safe_sid  = sid.replace("-", "_")
                cost_file = cost_dir / f"{safe_sid}.json"
                cost_file.write_text(
                    json.dumps(full_rec, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as e:
            logger.error(f"  [{label}] {sid} — LLM error: {e}")
            all_results.append({
                "statement_id": sid,
                "status":       "error",
                "existence":    "unchecked",
                "error":        str(e),
            })
            continue

        # result is now a single statement result dict
        all_results.append(result)

        auto = automated_cross_check([result], section_text, [stmt])
        all_auto.extend(auto)
        if auto:
            logger.info(f"    → {len(auto)} automated issue(s)")

    status_counts = defaultdict(int)
    for r in all_results:
        status_counts[r.get("status", "unknown")] += 1

    logger.info(
        f"  {section_id}: verified={status_counts['verified']} "
        f"corrected={status_counts['corrected']} "
        f"hallucinated={status_counts['hallucinated']} "
        f"vague={status_counts['vague']} "
        f"auto_issues={len(all_auto)}"
    )

    section_result = {
        "section_id":       section_id,
        "title":            title,
        "statement_count":  len(statements),
        "results":          all_results,
        "missing":          [],
        "automated_issues": all_auto,
        "status_counts":    dict(status_counts),
    }

    if section_path:
        section_path.parent.mkdir(parents=True, exist_ok=True)
        section_path.write_text(
            json.dumps(section_result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"  Saved section: {section_path.name}")

    return section_result


# ---------------------------------------------------------------------------
# Apply corrections (unchanged from new version)
# ---------------------------------------------------------------------------

def apply_corrections(
    statements:      list[dict],
    section_results: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Apply LLM corrections to any allowed statement field.

    Rules:
    - verified    → keep as-is
    - corrected   → apply corrections dict
    - hallucinated → remove
    - vague       → keep with warning tag
    - missing     → add with new IDs (N- for behavioral, D- for structural)

    Returns (verified_statements, removed, corrections_log)
    """
    result_map: dict[str, dict] = {}
    for sec in section_results:
        for r in sec.get("results", []):
            sid = r.get("statement_id", "")
            if sid:
                result_map[sid] = r

    verified        = []
    removed         = []
    corrections_log = []

    seq_counter: dict[str, int] = {}
    for s in statements:
        sid = s.get("statement_id", "")
        m   = re.match(r"^[ND]-(.+)-(\d{3})$", sid)
        if m:
            key = m.group(1)
            seq = int(m.group(2))
            seq_counter[key] = max(seq_counter.get(key, 0), seq)

    def next_seq(key):
        seq_counter[key] = seq_counter.get(key, 0) + 1
        return seq_counter[key]

    for stmt in statements:
        sid = stmt.get("statement_id", "")
        vr  = result_map.get(sid)

        if vr is None:
            verified.append(stmt)
            continue

        status = vr.get("status", "verified")

        if status == "hallucinated":
            removed.append({"statement_id": sid, "reason": "LLM: hallucinated"})
            continue

        corrected = dict(stmt)

        if vr.get("corrections"):
            for field, correction in vr["corrections"].items():
                if field in _PROTECTED_FROM_LLM:
                    continue
                if field not in _CORRECTABLE_STATEMENT_FIELDS:
                    continue
                if not isinstance(correction, dict):
                    continue
                if "should_be" not in correction:
                    continue
                new_val = correction["should_be"]
                old_val = corrected.get(field)
                if new_val == old_val:
                    continue
                corrected[field] = new_val
                corrections_log.append({
                    "statement_id": sid,
                    "field":        field,
                    "was":          old_val,
                    "now":          new_val,
                    "reason":       correction.get("reason", ""),
                })

        if status == "vague":
            corrected["_verification_note"] = "vague_action"

        verified.append(corrected)

    # Add missing statements
    for sec in section_results:
        sec_id   = sec.get("section_id", "unknown")
        safe_key = sec_id.replace(".", "-")

        for missing in sec.get("missing", []):
            cat    = missing.get("category", "BEHAVIORAL")
            prefix = "N" if cat == "BEHAVIORAL" else "D"
            seq    = next_seq(safe_key)
            new_id = f"{prefix}-{safe_key}-{seq:03d}"

            new_stmt = {
                "statement_id":    new_id,
                "normative_level": missing.get("normative_level", "MUST"),
                "category":        cat,
                "subject":         missing.get("subject", "server"),
                "stage":           missing.get("stage", "task_execution"),
                "source_section":  sec_id,
                "description":     missing.get("description", ""),
                "actions":         missing.get("actions", []),
                "object_name":     missing.get("object_name"),
                "field_name":      missing.get("field_name"),
                "_source":         "added_by_verification",
            }
            verified.append(new_stmt)

    return verified, removed, corrections_log


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def verify(
    input_path:      str,
    output_path:     str,
    api_key:         Optional[str] = None,
    model:           str = "anthropic/claude-sonnet-4-6",
    max_tokens:      int = 16000,
    max_retries:     int = 5,
    retry_wait:      int = 60,
    section_delay:   float = 5.0,
    resume:          bool = True,
    dry_run:         bool = False,
    spec_url:        str = "https://a2a-protocol.org/latest/specification/",
    merge_threshold: int = 200,
    config_path:     Path = _DEFAULT_CFG,
) -> dict:

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s %(levelname)s %(message)s",
        datefmt = "%H:%M:%S",
    )

    print(f"\n{'='*60}")
    print("  VERIFY STAGE A  (spec-grounded)")
    print(f"{'='*60}\n")

    # Load merged output
    print("[1/7] Loading merged output...")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    statements = data.get("statements", [])
    print(f"      Statements: {len(statements)}")

    # Group by source_section
    print("[2/7] Grouping by source_section...")
    groups = group_by_section(statements)
    print(f"      Sections: {len(groups)}")

    # Fetch spec (fetcher.py handles both URLs and local paths)
    print("[3/7] Fetching spec sections...")
    try:
        section_map = _build_section_map(spec_url, merge_threshold)
        print(f"      Sections parsed: {len(section_map)}")
    except Exception as e:
        print(f"      ERROR fetching spec: {e}")
        print("      Use --spec-url <local-path> to load a local copy, or")
        print("      --dry-run to skip spec fetch entirely.")
        raise

    # Load prompt templates from config
    print("[4/6] Loading prompt templates from config.yaml...")
    n_prompt_tpl, d_prompt_tpl = _load_prompt_templates(config_path)
    print(f"      N-prompt: {len(n_prompt_tpl)} chars  "
          f"D-prompt: {len(d_prompt_tpl)} chars")

    # LLM client
    llm = None
    if not dry_run and api_key:
        llm = LLMClient(
            api_key     = api_key,
            model       = model,
            max_tokens  = max_tokens,
            max_retries = max_retries,
            retry_wait  = retry_wait,
        )
        print(f"      LLM: {model}  max_tokens={max_tokens}")
    elif dry_run:
        print("      DRY RUN — skipping LLM calls")
    else:
        print("      No API key — LLM calls skipped")

    # Output dirs
    out          = Path(output_path)
    sections_dir = out.parent / "sections" if resume else None
    cost_dir     = out.parent / "cost"
    cost_dir.mkdir(parents=True, exist_ok=True)
    if sections_dir:
        sections_dir.mkdir(parents=True, exist_ok=True)
        print(f"      Sections dir: {sections_dir}")
    print(f"      Cost dir:     {cost_dir}")

    # Verify each section
    print(f"\n[5/7] Verifying {len(groups)} sections...")
    section_results: list[dict] = []
    cost_records:    list[dict] = []
    total = len(groups)

    for i, (sec_id, stmts) in enumerate(groups.items(), 1):
        section = find_section(section_map, sec_id)
        if section is None:
            logger.warning(f"Section {sec_id} not found in spec")

        result = verify_section(
            section_id   = sec_id,
            section      = section,
            statements   = stmts,
            llm          = llm,
            sections_dir = sections_dir,
            cost_records = cost_records,
            n_prompt_tpl = n_prompt_tpl,
            d_prompt_tpl = d_prompt_tpl,
            cost_dir     = cost_dir,
        )
        section_results.append(result)

        if i % 10 == 0:
            print(f"      {i}/{total} sections processed...")
        if llm and i < total:
            time.sleep(section_delay)

    # Apply corrections
    print(f"\n[6/7] Applying corrections...")
    verified_stmts, removed, corrections_log = apply_corrections(
        statements, section_results
    )

    # Aggregate stats
    total_verified    = sum(r.get("status_counts", {}).get("verified",    0) for r in section_results)
    total_corrected   = sum(r.get("status_counts", {}).get("corrected",   0) for r in section_results)
    total_hallucinated = len(removed)
    total_missing     = sum(len(r.get("missing", []))          for r in section_results)
    total_auto        = sum(len(r.get("automated_issues", [])) for r in section_results)
    total_vague       = sum(r.get("status_counts", {}).get("vague", 0)    for r in section_results)

    total_in   = sum(r.get("input_tokens",  0)   for r in cost_records)
    total_out  = sum(r.get("output_tokens", 0)   for r in cost_records)
    total_tok  = sum(r.get("total_tokens",  0)   for r in cost_records)
    total_cost = sum(r.get("cost_usd",      0.0) for r in cost_records)

    # Build outputs
    print(f"\n[7/7] Writing outputs...")

    report = {
        "summary": {
            "input_file":           input_path,
            "spec_url":             spec_url,
            "input_statements":     len(statements),
            "output_statements":    len(verified_stmts),
            "sections_checked":     len(section_results),
            "verified":             total_verified,
            "corrected":            total_corrected,
            "hallucinated_removed": total_hallucinated,
            "vague":                total_vague,
            "missing_added":        total_missing,
            "automated_issues":     total_auto,
            "corrections_applied":  len(corrections_log),
            "llm_calls":            len(cost_records),
            "total_tokens":         total_tok,
            "total_cost_usd":       round(total_cost, 8),
            "ready_for_stage_b":    total_auto == 0 and total_hallucinated < 10,
        },
        "removed":          removed,
        "corrections_log":  corrections_log,
        "sections":         section_results,
    }

    # Verified output reuses the merged.json structure
    verified_output = {
        "statements":          verified_stmts,
        "verification_report": report["summary"],
        "merge_log":           data.get("merge_log",           []),
        "resolution_log":      data.get("resolution_log",      []),
        "review_resolution_log": data.get("review_resolution_log", []),
        "summary":             data.get("summary",             {}),
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(verified_output, f, indent=2, ensure_ascii=False)

    report_path = out.with_name(out.stem + "_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Aggregate cost summary (per-section files already written during processing)
    cost_summary = {
        "stage":               STAGE,
        "model":               model,
        "llm_calls":           len(cost_records),
        "total_input_tokens":  total_in,
        "total_output_tokens": total_out,
        "total_tokens":        total_tok,
        "total_cost_usd":      round(total_cost, 8),
        "per_call":            cost_records,
    }
    aggregate_cost_path = out.parent / f"{STAGE}_cost.json"
    with open(aggregate_cost_path, "w", encoding="utf-8") as f:
        json.dump(cost_summary, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"  VERIFY COMPLETE")
    print(f"  Statements in:     {len(statements)}")
    print(f"  Statements out:    {len(verified_stmts)}")
    print(f"  Verified:          {total_verified}")
    print(f"  Corrected:         {total_corrected}")
    print(f"  Hallucinated:      {total_hallucinated} (removed)")
    print(f"  Vague:             {total_vague}")
    print(f"  Missing added:     {total_missing}")
    print(f"  Automated issues:  {total_auto}")
    print(f"  LLM calls:         {len(cost_records)}")
    print(f"  Total tokens:      {total_tok:,} (in:{total_in:,} out:{total_out:,})")
    print(f"  Total cost:        ${total_cost:.6f}")
    print(f"  Output:  {output_path}")
    print(f"  Report:  {report_path}")
    print(f"  Costs:   {aggregate_cost_path}")
    print(f"{'='*60}\n")

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _defaults      = _default_paths()
    _spec_cfg      = _default_spec_config()

    parser = argparse.ArgumentParser(
        description=(
            "Verify Stage A12 merged output against the live A2A spec. "
            "Re-fetches each source section and checks existence, "
            "level, subject, stage, and finds missing statements. "
            "API key, model, and system prompt are loaded automatically "
            "from config.yaml — no credentials needed on the command line."
        )
    )
    parser.add_argument(
        "--input", default=str(_defaults["input"]),
        help=f"Path to stage_a12/final.json (default: {_defaults['input']})",
    )
    parser.add_argument(
        "--output", default=str(_defaults["output"]),
        help=f"Path to write verified final.json (default: {_defaults['output']})",
    )
    parser.add_argument(
        "--model", default=_default_model(),
        help=f"LiteLLM model string (default: {_default_model()})",
    )
    parser.add_argument(
        "--spec-url", default=_spec_cfg["url"], dest="spec_url",
        help=(
            "URL or local path to the A2A spec. "
            f"(default: fetcher.url from config.yaml = {_spec_cfg['url']})"
        ),
    )
    parser.add_argument(
        "--no-resume", action="store_true", dest="no_resume",
        help="Force re-processing even for cached sections.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="Fetch spec + group only, no LLM calls",
    )

    args = parser.parse_args()
    verify(
        input_path      = args.input,
        output_path     = args.output,
        api_key         = _default_api_key(),
        model           = args.model,
        max_tokens      = _default_max_tokens(),
        max_retries     = _default_max_retries(),
        retry_wait      = _default_retry_wait(),
        section_delay   = _default_section_delay(),
        resume          = not args.no_resume,
        dry_run         = args.dry_run,
        spec_url        = args.spec_url,
        merge_threshold = _spec_cfg["merge_threshold_words"],
    )

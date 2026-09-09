"""
stage_a2.py
-----------
Stage A2: Normative Statement Extraction

For each section chunk:
  1. Feed section text to LLM
  2. Extract all normative statements (MUST/SHOULD/MAY/etc.)
  3. Save per-section JSON  → outputs/stage_a2/sections/
  4. Save per-section cost  → outputs/stage_a2/cost/
  5. Aggregate statements   → outputs/stage_a2/final.json
  6. Aggregate cost summary → outputs/stage_a2/stage_a2_cost.json

System prompt loaded from config.yaml system_prompts.stage_a2
resume=True skips sections that already have output files.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
import yaml

from fetcher import fetch_markdown, parse_sections, Section
from model_interface import ModelInterface, CallResult

_DEFAULT_CONFIG = str(Path(__file__).parent / "config.yaml")

logger = logging.getLogger(__name__)

STAGE = "stage_a2"

EXTRACTION_PROMPT_TEMPLATE = """Extract every normative statement from the \
following A2A protocol specification section.

RULES:
- Include EVERY sentence containing: MUST, MUST NOT, REQUIRED, SHALL, \
SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY, OPTIONAL
- Do NOT skip conditional normative statements (e.g. "If X, the server MUST...")
- Do NOT skip normative statements embedded in tables or lists
- Do NOT infer or paraphrase — use only what is explicitly stated
- If a sentence has no normative keyword, skip it entirely
- Each statement gets its own object — do not combine multiple statements
- Do NOT extract rows from definition tables or enumeration values

CRITICAL — PRE_COND AND POST_COND FORMAT:
pre_cond and post_cond are NEVER natural language sentences.
They are ALWAYS one of:
(a) null — if the condition cannot be formally expressed
(b) A logical expression using ONLY:
    - dot notation:   entity.attribute
    - equality:       entity.attribute = VALUE
    - logical AND:    &
    - logical OR:     |
    - logical NOT:    !
    - parentheses:    ( )

WRONG: "A2A service parameters are used"
WRONG: "when the client sends a request"
WRONG: "if streaming is supported"

RIGHT: "request.transport = HTTP"
RIGHT: "agent_card.capabilities.streaming = true"
RIGHT: "task.state = TASK_STATE_WORKING & stream.open = true"
RIGHT: null

If you cannot express the condition as a logical expression
using the notation above, set it to null. Never write a
sentence inside pre_cond or post_cond.

OUTPUT: Return ONLY a valid JSON array. No explanation, no markdown fences.

Each object MUST have exactly these fields:
{{
  "statement_id": "N-{section_id}-PLACEHOLDER",
  "normative_level": "<MUST|MUST NOT|SHALL|SHALL NOT|SHOULD|SHOULD NOT|RECOMMENDED|MAY|OPTIONAL|REQUIRED>",
  "category": "BEHAVIORAL",
  "subject": "<client|server|agent_card|message|task|part|artifact|streaming|push_notification|context|extension>",
  "stage": "<discovery|authentication|initiation|task_execution|interruption|termination>",
  "description": "<verbatim original sentence containing the RFC 2119 keyword>",
  "is_inferred": false,
  "confidence": 1.0,
  "source_section": "{section_id}",
  "actors": ["<list of actors involved>"],
  "from_state": "<complete state name or null>",
  "to_state": "<complete state name or null>",
  "trigger": "<concise event name or null>",
  "pre_cond": "<logical expression using &, |, ! or null>",
  "post_cond": "<logical expression using &, |, ! or null>",
  "actions": ["<ordered list of effects the rule mandates>"],
  "modality": "<must|shall|should|may|can|other|unspecified>"
}}

MODALITY MAPPING:
MUST / MUST NOT / REQUIRED        → must
SHALL / SHALL NOT                 → shall
SHOULD / SHOULD NOT / RECOMMENDED → should
MAY / OPTIONAL                    → may

Section ID: {section_id}
Section path: {parent_path}

Section text:
{section_text}"""


# ── ID assignment ─────────────────────────────────────────────────────────────

def assign_ids(statements: list[dict], section_id: str) -> list[dict]:
    safe_id = section_id.replace(".", "-")
    for i, stmt in enumerate(statements, start=1):
        stmt["id"] = f"N-{safe_id}-{i:03d}"
    return statements


# ── File paths ────────────────────────────────────────────────────────────────

def _safe_filename(section: Section) -> str:
    safe_id = section.section_id.replace(".", "_")
    name = f"{safe_id}_{section.title[:40].replace(' ', '_')}"
    return re.sub(r"[^\w\-_]", "", name)


def section_output_path(sections_dir: Path, section: Section) -> Path:
    return sections_dir / f"{_safe_filename(section)}.json"


def section_cost_path(cost_dir: Path, section: Section) -> Path:
    return cost_dir / f"{_safe_filename(section)}.json"


# ── Persistence helpers ───────────────────────────────────────────────────────

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _read_json(path: Path) -> Optional[dict]:
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def _append_log(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Cost helpers ──────────────────────────────────────────────────────────────

def _build_cost_record(section: Section, result: CallResult) -> dict:
    """Build the cost record for one section's LLM call."""
    return {
        "section_id":       section.section_id,
        "title":            section.title,
        "model":            result.model,
        "stage":            result.stage,
        "input_tokens":     result.input_tokens,
        "output_tokens":    result.output_tokens,
        "total_tokens":     result.total_tokens,
        "cost_usd":         result.cost_usd,
        "elapsed_seconds":  result.elapsed_seconds,
        "timestamp":        result.timestamp,
    }


def _save_stage_cost_summary(
    cost_dir: Path,
    all_cost_records: list[dict],
    model_name: str,
) -> None:
    """Write the aggregated cost summary for the whole stage."""
    total_input    = sum(r["input_tokens"]  for r in all_cost_records)
    total_output   = sum(r["output_tokens"] for r in all_cost_records)
    total_tokens   = sum(r["total_tokens"]  for r in all_cost_records)
    total_cost_usd = sum(r["cost_usd"]      for r in all_cost_records)

    summary = {
        "stage":          STAGE,
        "model":          model_name,
        "sections_processed": len(all_cost_records),
        "total_input_tokens":  total_input,
        "total_output_tokens": total_output,
        "total_tokens":        total_tokens,
        "total_cost_usd":      round(total_cost_usd, 8),
        "per_section":         all_cost_records,
    }
    _write_json(cost_dir.parent / f"{STAGE}_cost.json", summary)
    logger.info(
        f"Cost summary — tokens: {total_tokens:,} "
        f"(in: {total_input:,} / out: {total_output:,}) "
        f"| cost: ${total_cost_usd:.6f}"
    )


# ── Core extraction ───────────────────────────────────────────────────────────

def extract_section(
    section: Section,
    model: ModelInterface,
    sections_dir: Path,
    cost_dir: Path,
    log_path: Path,
    resume: bool = True,
) -> tuple[list[dict], Optional[dict]]:
    """
    Extract normative statements from one section.
    Returns (statements, cost_record).
    cost_record is None if section was loaded from cache.
    """
    out_path  = section_output_path(sections_dir, section)
    cost_path = section_cost_path(cost_dir, section)

    # ── Resume: load from cache if already done ───────────────────────────────
    if resume:
        existing = _read_json(out_path)
        if existing:
            count = len(existing.get("statements", []))
            logger.info(
                f"  [SKIP] {section.section_id} '{section.title}' "
                f"(cached: {count} statements)"
            )
            # Also load cached cost record if it exists
            cached_cost = _read_json(cost_path)
            return existing["statements"], cached_cost

    logger.info(
        f"  [RUN]  {section.section_id} '{section.title}' "
        f"({section.word_count} words)"
    )

    prompt = EXTRACTION_PROMPT_TEMPLATE.format(
        section_id=section.section_id,
        parent_path=" > ".join(section.parent_path),
        section_text=section.full_text,
    )

    try:
        statements, result = model.call_json(prompt=prompt, stage=STAGE)

        if not isinstance(statements, list):
            statements = [statements] if statements else []

        statements = assign_ids(statements, section.section_id)

        # ── Save section output ───────────────────────────────────────────────
        _write_json(out_path, {
            "section_id":      section.section_id,
            "title":           section.title,
            "depth":           section.depth,
            "parent_path":     section.parent_path,
            "url_anchor":      section.url_anchor,
            "word_count":      section.word_count,
            "statement_count": len(statements),
            "elapsed_seconds": result.elapsed_seconds,
            "statements":      statements,
        })
        logger.info(f"  Saved section: {out_path.name}")

        # ── Save per-section cost ─────────────────────────────────────────────
        cost_record = _build_cost_record(section, result)
        _write_json(cost_path, cost_record)
        logger.info(
            f"  Cost: {result.total_tokens:,} tokens "
            f"(in:{result.input_tokens:,} out:{result.output_tokens:,}) "
            f"| ${result.cost_usd:.6f}"
        )

        _append_log(log_path, {
            "event":      "section_done",
            "statements": len(statements),
            **cost_record,
        })

        return statements, cost_record

    except Exception as e:
        logger.error(f"  [ERROR] {section.section_id}: {e}")
        _append_log(log_path, {
            "event":      "section_error",
            "section_id": section.section_id,
            "title":      section.title,
            "error":      str(e),
            "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        return [], None


# ── Aggregation ───────────────────────────────────────────────────────────────

def aggregate(all_statements: list[dict]) -> dict:
    seen_raw = set()
    unique, duplicates = [], []

    for stmt in all_statements:
        raw = stmt.get("description", "").strip().lower()
        if raw and raw in seen_raw:
            duplicates.append(stmt.get("id") or stmt.get("statement_id", ""))
        else:
            seen_raw.add(raw)
            unique.append(stmt)

    level_counts   = {}
    subject_counts = {}
    stage_counts   = {}         

    for s in unique:
        lvl = s.get("normative_level", s.get("level", "unknown"))
        level_counts[lvl]            = level_counts.get(lvl, 0) + 1
        subject_counts[s["subject"]] = subject_counts.get(s["subject"], 0) + 1
        stage_counts[s.get("stage", "unknown")] = \
            stage_counts.get(s.get("stage", "unknown"), 0) + 1   # ← add this

    return {
        "meta": {
            "total_statements":   len(unique),
            "duplicates_removed": len(duplicates),
            "by_level":           level_counts,
            "by_subject":         subject_counts,
            "by_stage":           stage_counts,   
        },
        "statements": unique,
    }


# ── Main entry point ──────────────────────────────────────────────────────────

def run_stage_a2(config_path: str = _DEFAULT_CONFIG, resume: bool = True) -> dict:
    config_file = Path(config_path).resolve()
    config_root = config_file.parent

    with open(config_file) as f:
        config = yaml.safe_load(f)

    url             = config["fetcher"]["url"]
    merge_threshold = config["fetcher"]["merge_threshold_words"]
    section_delay   = config["model"].get("section_delay_seconds", 5)
    base_dir        = config_root / config["output"]["base_dir"]
    stage_dir       = base_dir / STAGE
    sections_dir    = stage_dir / "sections"
    logs_dir        = stage_dir / "logs"
    cost_dir        = stage_dir / "cost"

    for d in [sections_dir, logs_dir, cost_dir]:
        d.mkdir(parents=True, exist_ok=True)

    run_id     = time.strftime("%Y%m%d_%H%M%S")
    log_path   = logs_dir / f"{STAGE}_{run_id}.log.jsonl"
    final_path = stage_dir / "final.json"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("=" * 60)
    logger.info("STAGE A2: Normative Statement Extraction")
    logger.info("=" * 60)

    markdown = fetch_markdown(url)
    sections = parse_sections(markdown, merge_threshold_words=merge_threshold)
    logger.info(f"Found {len(sections)} section chunks to process")

    _append_log(log_path, {
        "event":         "run_start",
        "url":           url,
        "section_count": len(sections),
        "resume":        resume,
        "run_id":        run_id,
        "timestamp":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    model = ModelInterface(config_path)
    logger.info(f"Model: {model.model_name} [effort: {model._get_effort(STAGE)}]")

    all_statements  = []
    all_cost_records = []

    for i, section in enumerate(sections, start=1):
        logger.info(f"[{i}/{len(sections)}]")
        statements, cost_record = extract_section(
            section=section,
            model=model,
            sections_dir=sections_dir,
            cost_dir=cost_dir,
            log_path=log_path,
            resume=resume,
        )
        all_statements.extend(statements)
        if cost_record:
            all_cost_records.append(cost_record)
        if i < len(sections):
            time.sleep(section_delay)

    # ── Aggregate statements ──────────────────────────────────────────────────
    logger.info("Aggregating statements...")
    final = aggregate(all_statements)
    _write_json(final_path, final)

    # ── Aggregate cost summary ────────────────────────────────────────────────
    if all_cost_records:
        _save_stage_cost_summary(cost_dir, all_cost_records, model.model_name)

    _append_log(log_path, {
        "event":              "run_complete",
        "total_statements":   final["meta"]["total_statements"],
        "duplicates_removed": final["meta"]["duplicates_removed"],
        "by_level":           final["meta"]["by_level"],
        "timestamp":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    logger.info("=" * 60)
    logger.info("STAGE A2 COMPLETE")
    logger.info(f"  Total statements:   {final['meta']['total_statements']}")
    logger.info(f"  Duplicates removed: {final['meta']['duplicates_removed']}")
    logger.info(f"  By level:    {final['meta']['by_level']}")
    logger.info(f"  By subject:  {final['meta']['by_subject']}")
    logger.info(f"  Output:  {final_path}")
    logger.info(f"  Costs:   {cost_dir.parent / (STAGE + '_cost.json')}")
    logger.info("=" * 60)

    return final


if __name__ == "__main__":
    run_stage_a2(resume=True)
#!/usr/bin/env python3
"""
stage_b1.py
-----------
Filters verify_stage_a12/final.json down to only the statements
needed for FSM construction in Stage B, then splits by stage.

IN:  FSM_STATE              (all)
IN:  IMPLICIT_TRANSITION    (all)
IN:  BEHAVIORAL             where any of from_state, to_state, trigger
                            is non-null (pre_cond/post_cond alone are
                            insufficient — they don't define transitions)

No changes to any statement. Pure filter + split.

Each output file contains two representations:
  statements      ← full original statements (for traceability)
  fsm_input       ← slimmed format for Opus (only FSM-relevant fields)

Stage mapping:
  in_task_authorization      → interruption
  interruption_and_resumption → interruption
  deprecation_lifecycle      → dropped (not a protocol session stage)
  unknown                    → dropped

Paths come from config.yaml (output.base_dir).

Outputs (under outputs/stage_b1/):
    all.json
    discovery.json
    authentication.json
    initiation.json
    task_execution.json
    interruption.json
    termination.json
"""

import json
import logging
import yaml
from pathlib import Path
from collections import defaultdict

logger = logging.getLogger(__name__)

STAGE     = "stage_b1"
_CFG_PATH = Path(__file__).parent / "config.yaml"

STAGES = [
    "discovery",
    "authentication",
    "initiation",
    "task_execution",
    "interruption",
    "termination",
]

# Stages not in the canonical list — map to an existing stage or None to drop
STAGE_MAP = {
    "in_task_authorization":       "interruption",
    "interruption_and_resumption": "interruption",
    "deprecation_lifecycle":       None,
    "unknown":                     None,
}


def _load_paths() -> tuple[Path, Path]:
    with open(_CFG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    base_dir   = Path(__file__).parent / config["output"]["base_dir"]
    input_path = base_dir / "verify_stage_a12" / "final.json"
    outdir     = base_dir / STAGE
    return input_path, outdir


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def keep(s: dict) -> bool:
    cat = s.get("category", "")

    if cat in ("FSM_STATE", "IMPLICIT_TRANSITION"):
        return True

    if cat == "BEHAVIORAL":
        return any([
            s.get("from_state"),
            s.get("to_state"),
            s.get("trigger"),
        ])

    return False


def resolve_stage(s: dict) -> str | None:
    """
    Returns the canonical stage name for a statement, or None if it
    should be dropped.
    """
    stage = s.get("stage") or "unknown"
    if stage in STAGES:
        return stage
    return STAGE_MAP.get(stage, None)


# ---------------------------------------------------------------------------
# Slim format for Opus
# ---------------------------------------------------------------------------

def slim_state(s: dict) -> dict:
    return {
        "id":             s.get("statement_id"),
        "state_name":     s.get("state_name"),
        "semantic_type":  s.get("semantic_type"),
        "is_initial":     s.get("is_initial", False),
        "is_final":       s.get("is_final", False),
        "invariant_cond": s.get("invariant_cond"),
        "stage":          s.get("stage"),
        "source_section": s.get("source_section"),  # ← added
    }


def slim_transition(s: dict) -> dict:
    return {
        "id":             s.get("statement_id"),
        "category":       s.get("category"),
        "from_state":     s.get("from_state"),
        "to_state":       s.get("to_state"),
        "trigger":        s.get("trigger"),
        "pre_cond":       s.get("pre_cond"),
        "post_cond":      s.get("post_cond"),
        "modality":       s.get("modality"),
        "actors":         s.get("actors"),
        "source_section": s.get("source_section"),  # ← added
    }


def to_fsm_input(statements: list[dict]) -> dict:
    states      = []
    transitions = []
    for s in statements:
        if s.get("category") == "FSM_STATE":
            states.append(slim_state(s))
        else:
            transitions.append(slim_transition(s))
    return {
        "states":      states,
        "transitions": transitions,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_stage_b1() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    input_path, outdir = _load_paths()

    logger.info("=" * 60)
    logger.info("STAGE B1: FSM Statement Filter + Split")
    logger.info("=" * 60)
    logger.info(f"Input:  {input_path}")

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    statements = data.get("statements", [])

    # Filter
    filtered = [s for s in statements if keep(s)]
    dropped  = len(statements) - len(filtered)

    logger.info(f"Input statements:    {len(statements)}")
    logger.info(f"Filtered statements: {len(filtered)}")
    logger.info(f"Dropped:             {dropped}")

    # Group by resolved stage
    by_stage:      dict[str, list] = defaultdict(list)
    dropped_stage: dict[str, int]  = defaultdict(int)
    remapped:      dict[str, int]  = defaultdict(int)

    for s in filtered:
        resolved = resolve_stage(s)
        if resolved is None:
            dropped_stage[s.get("stage") or "unknown"] += 1
        else:
            if resolved != (s.get("stage") or "unknown"):
                remapped[f"{s.get('stage')} → {resolved}"] += 1
            by_stage[resolved].append(s)

    if remapped:
        for mapping, count in sorted(remapped.items()):
            logger.info(f"Remapped: {mapping}  ({count} statements)")

    if dropped_stage:
        for stage, count in sorted(dropped_stage.items()):
            logger.info(f"Dropped stage '{stage}': {count} statements")

    outdir.mkdir(parents=True, exist_ok=True)

    # all.json — everything that passed the filter (before stage mapping)
    all_stmts = [s for stage_stmts in by_stage.values()
                 for s in stage_stmts]
    all_path  = outdir / "all.json"
    with open(all_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "fsm_input":  to_fsm_input(all_stmts),
                "statements": all_stmts,
            },
            f, indent=2, ensure_ascii=False,
        )
    logger.info(f"Wrote: {all_path}  ({len(all_stmts)} statements)")

    # per-stage files
    for stage in STAGES:
        stmts = by_stage.get(stage, [])

        by_cat: dict[str, int] = defaultdict(int)
        for s in stmts:
            by_cat[s.get("category", "?")] += 1

        path = outdir / f"{stage}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "stage":      stage,
                    "fsm_input":  to_fsm_input(stmts),
                    "statements": stmts,
                },
                f, indent=2, ensure_ascii=False,
            )

        cat_str = "  ".join(f"{k}:{v}" for k, v in sorted(by_cat.items()))
        logger.info(f"Wrote: {path}  ({len(stmts):3d} statements)   {cat_str}")

    logger.info(f"Output dir: {outdir}")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_stage_b1()
#!/usr/bin/env python3
"""
stage_b4.py
-----------
Deterministic merge: all six Stage B2 phase FSMs + Stage B3 inter-stage edges
into one unified graph JSON (no LLM).

Input:
  outputs/stage_b2/<phase>.json   for each protocol phase
  outputs/stage_b3/inter_stage.json
    OR outputs/stage_b3/inter_stage_transitions.json
       (object with key \"inter_stage_transitions\", or a bare JSON array)

Output:
  outputs/stage_b4/unified_fsm.json

Each state is tagged with ``protocol_phase`` and ``state_id``. After ``states`` and
``transitions``, ``structural_issues`` aggregates all B2 ``gaps`` / ``unresolved``
(per phase) and B3 ``gaps`` / ``unresolved``, each row tagged with
``source_pipeline_stage`` (``stage_b2`` or ``stage_b3``).

Usage:
    python stage_b4.py
    python stage_b4.py --strict
    python stage_b4.py --b3 outputs/stage_b3/inter_stage.json \\
        --out outputs/stage_b4/unified_fsm.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

from stage_b1 import STAGES as PROTOCOL_STAGES

SCRIPT_DIR   = Path(__file__).parent
CONFIG_PATH  = SCRIPT_DIR / "config.yaml"
B2_STAGE_TAG = "stage_b2"
B3_STAGE_TAG = "stage_b3"
B4_STAGE_TAG = "stage_b4"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _default_paths(cfg: dict) -> tuple[Path, Path, Path]:
    base = SCRIPT_DIR / cfg["output"]["base_dir"]
    b2   = base / B2_STAGE_TAG
    b3   = base / B3_STAGE_TAG
    b4   = base / B4_STAGE_TAG
    return b2, b3, b4 / "unified_fsm.json"


def _resolve_b3_transitions_path(b3_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"B3 transitions file not found: {explicit}")
        return explicit
    cand_trans = b3_dir / "inter_stage_transitions.json"
    cand_full  = b3_dir / "inter_stage.json"
    if cand_trans.is_file():
        return cand_trans
    if cand_full.is_file():
        return cand_full
    raise FileNotFoundError(
        f"Expected {cand_trans} or {cand_full} — run stage_b3.py first."
    )


def _load_b3_transitions(path: Path) -> tuple[list[dict], dict | None]:
    """
    Returns (inter_stage_transitions list, full B3 dict or None).
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return raw, None
    if isinstance(raw, dict):
        full = raw
        arr = full.get("inter_stage_transitions")
        if isinstance(arr, list):
            return arr, full
    raise ValueError(
        f"{path}: expected a JSON array or object with "
        f"'inter_stage_transitions' array"
    )


def _load_b2_phase(b2_dir: Path, phase: str) -> dict:
    p = b2_dir / f"{phase}.json"
    if not p.is_file():
        raise FileNotFoundError(f"Missing B2 file: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: root must be an object")
    if not data.get("states"):
        raise ValueError(f"{p}: missing or empty 'states'")
    return data


def _state_id(phase: str, name: str) -> str:
    return f"{phase}:{name}"


def _as_issue_list(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw if isinstance(x, dict)]


def _build_structural_issues(
    b2_by_phase: dict[str, dict],
    b3_full: dict | None,
) -> dict:
    """
    Merge B2 per-phase gaps/unresolved and B3 gaps/unresolved into one object.
    Each row is copied with source_pipeline_stage (and protocol_phase for B2).
    """
    gaps: list[dict] = []
    unresolved: list[dict] = []

    for phase in PROTOCOL_STAGES:
        bucket = b2_by_phase.get(phase) or {}
        for g in bucket.get("gaps") or []:
            row = dict(g)
            row["source_pipeline_stage"] = "stage_b2"
            row["protocol_phase"] = phase
            gaps.append(row)
        for u in bucket.get("unresolved") or []:
            row = dict(u)
            row["source_pipeline_stage"] = "stage_b2"
            row["protocol_phase"] = phase
            unresolved.append(row)

    if b3_full:
        for g in _as_issue_list(b3_full.get("gaps")):
            row = dict(g)
            row["source_pipeline_stage"] = "stage_b3"
            gaps.append(row)
        for u in _as_issue_list(b3_full.get("unresolved")):
            row = dict(u)
            row["source_pipeline_stage"] = "stage_b3"
            unresolved.append(row)

    b2_gaps = sum(len((b2_by_phase.get(p) or {}).get("gaps") or []) for p in PROTOCOL_STAGES)
    b2_unr  = sum(
        len((b2_by_phase.get(p) or {}).get("unresolved") or []) for p in PROTOCOL_STAGES
    )
    b3_gaps = len(_as_issue_list(b3_full.get("gaps"))) if b3_full else 0
    b3_unr  = len(_as_issue_list(b3_full.get("unresolved"))) if b3_full else 0

    return {
        "gaps":       gaps,
        "unresolved": unresolved,
        "summary": {
            "gap_count":       len(gaps),
            "unresolved_count": len(unresolved),
            "stage_b2": {"gaps": b2_gaps, "unresolved": b2_unr},
            "stage_b3": {"gaps": b3_gaps, "unresolved": b3_unr},
        },
    }


def build_unified_fsm(
    *,
    b2_dir: Path,
    b3_transitions_path: Path,
    strict: bool = False,
) -> tuple[dict, list[str]]:
    """
    Returns (unified_fsm dict, warnings).
    """
    warnings: list[str] = []

    inter_list, b3_full = _load_b3_transitions(b3_transitions_path)

    states_out: list[dict] = []
    transitions_out: list[dict] = []
    b2_by_phase: dict[str, dict] = {}

    state_keys: set[tuple[str, str]] = set()

    for phase in PROTOCOL_STAGES:
        data = _load_b2_phase(b2_dir, phase)
        file_stage = str(data.get("stage") or phase).strip()
        if file_stage != phase:
            warnings.append(
                f"B2 file {phase}.json has root 'stage'={file_stage!r} "
                f"(using directory name {phase!r} as protocol_phase)"
            )

        for s in data.get("states") or []:
            name = s.get("state_name")
            if not name:
                warnings.append(f"{phase}: skipping state without state_name")
                continue
            key = (phase, str(name))
            if key in state_keys:
                warnings.append(f"duplicate state key {key!r} — skipping duplicate")
                continue
            state_keys.add(key)

            row = dict(s)
            row["protocol_phase"] = phase
            row["state_id"] = _state_id(phase, str(name))
            states_out.append(row)

        for t in data.get("transitions") or []:
            fs, ts = t.get("from_state"), t.get("to_state")
            if not fs or not ts:
                warnings.append(
                    f"{phase}: skipping transition missing from_state/to_state: "
                    f"{str(t)[:200]!r}"
                )
                continue
            tr = dict(t)
            tr["kind"] = "intra_stage"
            tr["protocol_phase"] = phase
            tr["from_state_id"] = _state_id(phase, str(fs))
            tr["to_state_id"] = _state_id(phase, str(ts))
            transitions_out.append(tr)

        gaps_raw = data.get("gaps")
        unr_raw = data.get("unresolved")
        b2_by_phase[phase] = {
            "gaps": _as_issue_list(gaps_raw),
            "unresolved": _as_issue_list(unr_raw),
        }
    seen_inter: set[tuple] = set()
    for t in inter_list:
        if not isinstance(t, dict):
            warnings.append(f"B3: skipping non-object transition {t!r}")
            continue
        fs_st = t.get("from_stage")
        fs_nm = t.get("from_state")
        ts_st = t.get("to_stage")
        ts_nm = t.get("to_state")
        if not all(isinstance(x, str) and x.strip() for x in (fs_st, fs_nm, ts_st, ts_nm)):
            warnings.append(f"B3: skipping incomplete inter transition {str(t)[:200]!r}")
            continue
        fs_st, fs_nm, ts_st, ts_nm = fs_st.strip(), fs_nm.strip(), ts_st.strip(), ts_nm.strip()
        dedup_key = (fs_st, fs_nm, ts_st, ts_nm, t.get("trigger") or "")
        if dedup_key in seen_inter:
            warnings.append(
                f"B3: duplicate inter transition {dedup_key!r} — skipping"
            )
            continue
        seen_inter.add(dedup_key)

        fk = (fs_st, fs_nm)
        tk = (ts_st, ts_nm)
        if fk not in state_keys:
            msg = f"B3 edge unknown from vertex {fk!r}"
            warnings.append(msg)
            if strict:
                raise ValueError(msg)
        if tk not in state_keys:
            msg = f"B3 edge unknown to vertex {tk!r}"
            warnings.append(msg)
            if strict:
                raise ValueError(msg)

        tr = dict(t)
        tr["kind"] = "inter_stage"
        tr["from_state_id"] = _state_id(fs_st, fs_nm)
        tr["to_state_id"] = _state_id(ts_st, ts_nm)
        transitions_out.append(tr)

    try:
        b3_src = str(b3_transitions_path.resolve().relative_to(SCRIPT_DIR.resolve()))
    except ValueError:
        b3_src = str(b3_transitions_path)

    structural_issues = _build_structural_issues(b2_by_phase, b3_full)

    unified: dict = {
        "schema_version":     "stage_b4_v1",
        "protocol_phases":    list(PROTOCOL_STAGES),
        "b3_source":          b3_src,
        "state_count":        len(states_out),
        "transition_count":   len(transitions_out),
        "intra_stage_count":  sum(1 for x in transitions_out if x.get("kind") == "intra_stage"),
        "inter_stage_count":  sum(1 for x in transitions_out if x.get("kind") == "inter_stage"),
        "states":             states_out,
        "transitions":        transitions_out,
        "structural_issues":  structural_issues,
        "merge_warnings":     warnings,
    }

    return unified, warnings


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage B4: merge six B2 FSMs + B3 inter-stage transitions.",
    )
    p.add_argument(
        "--b3",
        type=Path,
        default=None,
        help="B3 JSON (default: inter_stage_transitions.json or inter_stage.json "
        "under output base).",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path (default: <base>/stage_b4/unified_fsm.json).",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any B3 transition references a missing (phase, state).",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = _load_config()
    b2_dir, b3_dir, default_out = _default_paths(cfg)

    try:
        b3_path = _resolve_b3_transitions_path(b3_dir, args.b3)
        out_path = args.out if args.out is not None else default_out
        unified, warnings = build_unified_fsm(
            b2_dir=b2_dir,
            b3_transitions_path=b3_path,
            strict=args.strict,
        )
    except Exception as e:
        logger.exception("Stage B4 merge failed")
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(unified, f, indent=2, ensure_ascii=False)

    logger.info("Wrote %s", out_path)
    logger.info(
        "States=%d  transitions=%d (intra=%d, inter=%d)  "
        "structural_issues: gaps=%d unresolved=%d",
        unified["state_count"],
        unified["transition_count"],
        unified["intra_stage_count"],
        unified["inter_stage_count"],
        unified["structural_issues"]["summary"]["gap_count"],
        unified["structural_issues"]["summary"]["unresolved_count"],
    )
    if warnings:
        logger.warning("%d merge warning(s):", len(warnings))
        for w in warnings[:20]:
            logger.warning("  %s", w)
        if len(warnings) > 20:
            logger.warning("  ... and %d more", len(warnings) - 20)


if __name__ == "__main__":
    main()

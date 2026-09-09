#!/usr/bin/env python3
"""
evaluation.py
-------------
Scores our exported FSMs with the benchmark's own, unmodified evaluator.

METHOD.md:
  * `eval_workspace/` holds symlinks to the pristine `RFC_PSM_Benchmark/<PROTOCOL>`
    directories (ground truth is read from the clone, never copied or edited) and a
    real `fsm/` directory with our exported FSMs.
  * `RFC_PSM_Benchmark/eval_fsm_sim.py` is imported via importlib with
    `sys.dont_write_bytecode = True` so the clone never gains a __pycache__.
  * Official numbers come from the two entry points the evaluator's own __main__
    uses, with the same arguments (threshold 0.5, if_partial=False).
  * Everything else in eval.json is explicitly labelled supplementary.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

THIS_DIR = Path(__file__).resolve().parent
PIPELINE_DIR = THIS_DIR.parent
BENCHMARK_CLONE = PIPELINE_DIR / "RFC_PSM_Benchmark"
WORKSPACE = THIS_DIR / "eval_workspace"   # internal scorer mirror, package root

LOGGER = logging.getLogger("psmbench.eval")

STATE_THRESHOLD = 0.5
TRANS_THRESHOLD = 0.5
SENSITIVITY_THRESHOLDS = (0.4, 0.5, 0.6)

_module_cache: Any = None


def _runner():
    import runner
    return runner


def build_workspace(protocols: Iterable[str]) -> Path:
    """Symlink each protocol dir; keep a real fsm/ dir for our exports."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "fsm").mkdir(exist_ok=True)
    for protocol in protocols:
        link = WORKSPACE / protocol
        target = Path("..") / ".." / "RFC_PSM_Benchmark" / protocol
        if link.is_symlink():
            if os.readlink(link) != str(target):
                link.unlink()
            else:
                continue
        elif link.exists():
            raise RuntimeError(f"{link} exists and is not a symlink")
        link.symlink_to(target, target_is_directory=True)
    return WORKSPACE


def load_evaluator() -> Any:
    """Import the clone's evaluator without writing anything into the clone."""
    global _module_cache
    if _module_cache is not None:
        return _module_cache
    sys.dont_write_bytecode = True
    path = BENCHMARK_CLONE / "eval_fsm_sim.py"
    spec = importlib.util.spec_from_file_location("eval_fsm_sim_readonly", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _module_cache = module
    return module


@contextmanager
def _in_workspace():
    old = Path.cwd()
    os.chdir(WORKSPACE)
    try:
        yield
    finally:
        os.chdir(old)


def _assert_single_match(fsm_dir: Path, protocol: str, model_name: str) -> Path:
    """Mirror the evaluator's substring lookup and require exactly one hit."""
    hits = [
        p for p in sorted(fsm_dir.iterdir())
        if protocol in p.name and model_name in p.name and p.name.endswith(".json")
    ]
    if len(hits) != 1:
        raise RuntimeError(
            f"expected exactly one FSM file for ({protocol}, {model_name}) in "
            f"{fsm_dir}, found {[p.name for p in hits]}"
        )
    return hits[0]


def _df_rows(df) -> list[dict[str, Any]]:
    return json.loads(df.to_json(orient="records"))


# ---------------------------------------------------------------------------
# Supplementary metrics (clearly labelled; never replace the official numbers)
# ---------------------------------------------------------------------------

def _embed(module: Any, texts: list[str]):
    import numpy as np
    if not texts:
        return np.zeros((0, 384))
    return module.model.encode(texts, normalize_embeddings=True)


def _sim_matrix(module: Any, left: list[str], right: list[str]):
    import numpy as np
    if not left or not right:
        return np.zeros((len(left), len(right)))
    a = _embed(module, left)
    b = _embed(module, right)
    return np.asarray(a) @ np.asarray(b).T


def one_to_one_states(
    module: Any, gt_states: list[str], ext_states: list[str], threshold: float
) -> dict[str, Any]:
    from scipy.optimize import linear_sum_assignment
    gt_clean = [module.preprocess_state_name(s) for s in gt_states]
    ext_clean = [module.preprocess_state_name(s) for s in ext_states]
    sim = _sim_matrix(module, gt_clean, ext_clean)
    if sim.size == 0:
        matched = 0
        pairs: list[dict[str, Any]] = []
    else:
        rows, cols = linear_sum_assignment(-sim)
        pairs = [
            {"gt": gt_states[r], "extracted": ext_states[c], "score": round(float(sim[r, c]), 4)}
            for r, c in zip(rows, cols) if sim[r, c] >= threshold
        ]
        matched = len(pairs)
    precision = matched / len(ext_states) if ext_states else 0.0
    recall = matched / len(gt_states) if gt_states else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "threshold": threshold,
        "matched": matched,
        "total_gt": len(gt_states),
        "total_extracted": len(ext_states),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "pairs": pairs,
    }


def _transition_labels(transitions: list[dict[str, Any]]) -> list[str]:
    # if_partial=False in the evaluator concatenates event+action with no separator.
    return [str(t.get("event", "")) + str(t.get("action", "")) for t in transitions]


def one_to_one_transitions(
    module: Any,
    gt_transitions: list[dict[str, Any]],
    ext_transitions: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    if not gt_transitions or not ext_transitions:
        return {
            "threshold": threshold, "matched": 0,
            "total_gt": len(gt_transitions), "total_extracted": len(ext_transitions),
            "precision": 0.0, "recall": 0.0, "f1": 0.0, "pairs": [],
        }

    ext_from = _sim_matrix(module, [str(t.get("from", "")) for t in ext_transitions],
                           [str(t.get("from", "")) for t in gt_transitions])
    ext_to = _sim_matrix(module, [str(t.get("to", "")) for t in ext_transitions],
                         [str(t.get("to", "")) for t in gt_transitions])
    labels = _sim_matrix(module, _transition_labels(ext_transitions),
                         _transition_labels(gt_transitions))

    eligible = (ext_from >= threshold) & (ext_to >= threshold) & (labels >= threshold)
    weights = np.where(eligible, labels, -1.0)
    rows, cols = linear_sum_assignment(-weights)
    pairs = [
        {
            "extracted": ext_transitions[r],
            "gt": gt_transitions[c],
            "label_score": round(float(labels[r, c]), 4),
        }
        for r, c in zip(rows, cols) if eligible[r, c]
    ]
    matched = len(pairs)
    precision = matched / len(ext_transitions)
    recall = matched / len(gt_transitions)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "threshold": threshold,
        "matched": matched,
        "total_gt": len(gt_transitions),
        "total_extracted": len(ext_transitions),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "pairs": pairs,
    }


def ground_truth_path(protocol: str) -> Path:
    """
    Locate a protocol's ground-truth FSM. This function, and the scoring code that
    calls it, are the ONLY places in the wrapper that know a *_state_machine.json
    exists: protocol discovery, config generation and every extraction stage work
    from the segment files alone.
    """
    candidates = sorted((BENCHMARK_CLONE / protocol).glob("*_state_machine.json"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one ground-truth file for {protocol}, "
            f"found {[c.name for c in candidates]}"
        )
    return candidates[0]


def _load_ground_truth(protocol: str) -> tuple[dict[str, Any], dict[str, Any]]:
    runner = _runner()
    info = dict(runner.protocol_info(protocol))
    info["ground_truth_file"] = ground_truth_path(protocol)
    return runner.read_json(info["ground_truth_file"]), info


# ---------------------------------------------------------------------------
# Official + diagnostic evaluation for one protocol
# ---------------------------------------------------------------------------

def evaluate_protocol(
    *,
    protocol: str,
    exported_fsm: Path,
    output_base: Path,
    model_name: str,
    force: bool = False,
) -> dict[str, Any]:
    runner = _runner()
    runner.assert_clone_clean("before eval")
    build_workspace([protocol])

    fsm_dir = (WORKSPACE / "fsm").resolve()
    target = fsm_dir / f"{protocol}_{model_name}_final_fsm.json"
    target.write_bytes(Path(exported_fsm).read_bytes())
    _assert_single_match(fsm_dir, protocol, model_name)

    gt_data, info = _load_ground_truth(protocol)
    fingerprint = {
        "evaluator_sha256": runner.sha256_file(BENCHMARK_CLONE / "eval_fsm_sim.py"),
        "ground_truth_sha256": runner.sha256_file(info["ground_truth_file"]),
        "exported_fsm_sha256": runner.sha256_file(exported_fsm),
        "minilm": runner.minilm_snapshot(),
        "packages": runner.package_versions(),
    }
    eval_path = Path(output_base) / "eval.json"
    if eval_path.is_file() and not force:
        cached = runner.read_json(eval_path)
        if cached.get("fingerprint") == fingerprint:
            LOGGER.info("Reusing cached eval.json for %s", protocol)
            return cached

    module = load_evaluator()
    ext_data = runner.read_json(exported_fsm)

    with _in_workspace():
        all_matches, states_df = module.match_all_states(
            [model_name], [protocol], fsm_dir=str(fsm_dir), threshold=STATE_THRESHOLD
        )
        transitions_df = module.batch_evaluate_transitions_combined(
            protocols=[protocol], models=[model_name],
            fsm_dir=str(fsm_dir), if_partial=False,
        )
        state_rows = _df_rows(states_df)
        transition_rows = _df_rows(transitions_df)
        if len(state_rows) != 1:
            raise RuntimeError(f"expected 1 state row, got {len(state_rows)}")
        if len(transition_rows) != 1:
            raise RuntimeError(f"expected 1 transition row, got {len(transition_rows)}")

        detail = all_matches[(protocol, model_name)]
        gt_states = gt_data.get("states", [])
        ext_states = ext_data.get("states", [])
        gt_transitions = gt_data.get("transitions", [])
        ext_transitions = ext_data.get("transitions", [])

        # match_states(source=GT, target=extracted) -> (per-GT matches, extracted
        # states no GT state selected)
        matched_pairs, unselected_extracted = module.match_states(
            gt_states, ext_states, threshold=STATE_THRESHOLD
        )
        matched_trans, unmatched_ext_trans, _ = (
            module.match_transitions_combined_event_action(
                ext_transitions, gt_transitions,
                threshold=TRANS_THRESHOLD, if_partial=False,
            )
        )
        matched_gt_keys = {json.dumps(t2, sort_keys=True) for _, t2 in matched_trans}
        unmatched_gt_trans = [
            t for t in gt_transitions
            if json.dumps(t, sort_keys=True) not in matched_gt_keys
        ]

        supplementary = {
            "note": (
                "Supplementary only — the official numbers above come from the "
                "benchmark's own entry points. The official state matcher is "
                "many-to-one and the official transition matcher is greedy and "
                "order-sensitive; these one-to-one numbers bound that effect."
            ),
            "one_to_one": {
                "states": one_to_one_states(module, gt_states, ext_states, STATE_THRESHOLD),
                "transitions": one_to_one_transitions(
                    module, gt_transitions, ext_transitions, TRANS_THRESHOLD
                ),
            },
            "threshold_sensitivity": {
                str(t): {
                    "states_official_style": _official_style_states(
                        module, gt_states, ext_states, t
                    ),
                    "states_one_to_one": one_to_one_states(module, gt_states, ext_states, t),
                    "transitions_one_to_one": one_to_one_transitions(
                        module, gt_transitions, ext_transitions, t
                    ),
                }
                for t in SENSITIVITY_THRESHOLDS
            },
        }

    result = {
        "protocol": protocol,
        "model": model_name,
        "fingerprint": fingerprint,
        "official": {
            "states": state_rows[0],
            "transitions": transition_rows[0],
            "call": {
                "match_all_states": {"threshold": STATE_THRESHOLD},
                "batch_evaluate_transitions_combined": {
                    "if_partial": False,
                    "state_threshold": STATE_THRESHOLD,
                    "trans_threshold": TRANS_THRESHOLD,
                },
            },
        },
        "diagnostics": {
            "state_matches": [
                {"gt": src, "extracted": tgt, "score": float(score)}
                for src, tgt, score in matched_pairs
            ],
            "unmatched_gt_states": [
                s for s, t, _ in matched_pairs if t is None
            ],
            "unselected_extracted_states": unselected_extracted,
            "matched_transitions": [
                {"extracted": t1, "gt": t2} for t1, t2 in matched_trans
            ],
            "unmatched_gt_transitions": unmatched_gt_trans,
            "unmatched_extracted_transitions": unmatched_ext_trans,
            "precision": detail["precision"],
            "recall": detail["recall"],
            "f1_score": detail["f1_score"],
        },
        "supplementary": supplementary,
    }
    runner.write_json(eval_path, result)
    runner.assert_clone_clean("after eval")
    LOGGER.info("Wrote %s", eval_path)
    return result


def _official_style_states(
    module: Any, gt_states: list[str], ext_states: list[str], threshold: float
) -> dict[str, Any]:
    matches, _ = module.match_states(gt_states, ext_states, threshold=threshold)
    matched = len([m for m in matches if m[1] is not None])
    precision = round(matched / len(ext_states), 3) if ext_states else 0
    recall = round(matched / len(gt_states), 3) if gt_states else 0
    f1 = round((2 * precision * recall) / (precision + recall), 3) if (precision + recall) else 0
    return {"matched": matched, "precision": precision, "recall": recall, "f1": f1}


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def evaluate_baselines(*, protocols: list[str] | None = None) -> dict[str, Any]:
    runner = _runner()
    runner.assert_clone_clean("before baseline eval")
    all_protocols = list(runner.discover_protocols())
    protocols = protocols or all_protocols
    build_workspace(protocols)

    module = load_evaluator()
    models = list(module.model_name_mapping)
    baseline_fsm_dir = str((BENCHMARK_CLONE / "fsm").resolve())

    with _in_workspace():
        _, states_df = module.match_all_states(
            models, protocols, fsm_dir=baseline_fsm_dir, threshold=STATE_THRESHOLD
        )
        transitions_df = module.batch_evaluate_transitions_combined(
            protocols=protocols, models=models,
            fsm_dir=baseline_fsm_dir, if_partial=False,
        )

    result = {
        "protocols": protocols,
        "models": models,
        "model_name_mapping": module.model_name_mapping,
        "states": _df_rows(states_df),
        "transitions": _df_rows(transitions_df),
        "fingerprint": {
            "evaluator_sha256": runner.sha256_file(BENCHMARK_CLONE / "eval_fsm_sim.py"),
            "minilm": runner.minilm_snapshot(),
            "packages": runner.package_versions(),
        },
    }
    out_path = THIS_DIR / "results" / "baselines.json"
    runner.write_json(out_path, result)
    runner.assert_clone_clean("after baseline eval")
    LOGGER.info("Wrote %s", out_path)
    return result

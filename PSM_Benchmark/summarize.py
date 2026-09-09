#!/usr/bin/env python3
"""
summarize.py
------------
Builds the comparison tables (markdown, CSV) from the per-protocol eval.json
files and results/baselines.json.

Per protocol and per model: GT / extracted / matched / precision / recall / F1
for states and for transitions. Aggregates: macro mean computed from UNROUNDED
per-protocol values, a micro row (sum matched / sum extracted, sum matched /
sum GT), and the median.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
OFFICIAL_V = "pipeline output"


OURS = "a2a-pipeline"


def _prf(matched: int, extracted: int, gt: int) -> tuple[float, float, float]:
    precision = matched / extracted if extracted else 0.0
    recall = matched / gt if gt else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def _row(protocol: str, model: str, kind: str, matched: int, extracted: int, gt: int) -> dict:
    precision, recall, f1 = _prf(matched, extracted, gt)
    return {
        "protocol": protocol, "model": model, "kind": kind,
        "gt": gt, "extracted": extracted, "matched": matched,
        "precision": precision, "recall": recall, "f1": f1,
    }


def current_run_key(protocol: str) -> str | None:
    """The run_key the CURRENT generated config would produce for this protocol."""
    import runner

    try:
        import yaml
        cfg_path = runner.config_path_for(protocol)
        if not cfg_path.is_file():
            return None
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        return Path(cfg["output"]["base_dir"]).name
    except Exception:                                    # noqa: BLE001
        return None


def namespace_index(namespace: str = "generic") -> dict[str, dict[str, Any]]:
    """
    Per protocol: which completed namespace is authoritative, and which are superseded.

    Runs made under different prompt hashes are NOT comparable and must never share a
    table. The authoritative row is the namespace whose run_key matches the current
    generated config; anything else is reported separately and labelled.
    """
    import runner

    index: dict[str, dict[str, Any]] = {}
    if namespace in (None, "generic"):
        roots = sorted((THIS_DIR / "protocols").glob("*/output/*/eval.json"))
    else:
        roots = sorted((THIS_DIR / namespace).glob("*/*/eval.json"))
    for eval_path in roots:
        run_dir = eval_path.parent
        # protocols/<P>/output/<key>/eval.json  or  superseded_runs/<P>/<key>/eval.json
        protocol = run_dir.parent.parent.name if run_dir.parent.name == "output" \
            else run_dir.parent.name
        manifest = run_dir / "manifest.json"
        run_key_file = run_dir / "RUN_KEY"
        run_key = (run_key_file.read_text(encoding="utf-8").strip()
                   if run_key_file.is_file() else run_dir.name)
        entry = {
            "run_key": run_key,
            "eval_path": eval_path,
            "prompt_template_sha256": None,
            "mtime": eval_path.stat().st_mtime,
        }
        if manifest.is_file():
            data = runner.read_json(manifest)
            entry["prompt_template_sha256"] = (
                (data.get("prompts") or {}).get("prompt_template_sha256"))
            entry["cost_usd_total"] = data.get("cost_usd_total")
        index.setdefault(protocol, {"candidates": []})["candidates"].append(entry)

    for protocol, info in index.items():
        wanted = current_run_key(protocol)
        candidates = info["candidates"]
        match = next((c for c in candidates if c["run_key"] == wanted), None)
        if match:
            info["current"] = match
            info["status"] = "current"
        else:
            newest = max(candidates, key=lambda c: c["mtime"])
            info["current"] = newest
            info["status"] = "superseded"
            info["expected_run_key"] = wanted
        info["superseded"] = [c for c in candidates
                              if c["run_key"] != info["current"]["run_key"]]
    return index


def collect(namespace: str = "generic") -> list[dict[str, Any]]:
    import runner

    rows: list[dict[str, Any]] = []
    index = namespace_index(namespace)
    if index:
        for protocol, info in sorted(index.items()):
            eval_path = info["current"]["eval_path"]
            data = runner.read_json(eval_path)
            states = data["official"]["states"]
            transitions = data["official"]["transitions"]
            supp = (data.get("supplementary") or {}).get("one_to_one") or {}
            state_row = _row(protocol, OURS, "states",
                             int(states["Matched"]), int(states["Total Extracted"]),
                             int(states["Total GT"]))
            state_row["one_to_one_f1"] = (supp.get("states") or {}).get("f1")
            trans_row = _row(protocol, OURS, "transitions",
                             int(transitions["Matched"]), int(transitions["TotalExtracted"]),
                             int(transitions["TotalGT"]))
            trans_row["one_to_one_f1"] = (supp.get("transitions") or {}).get("f1")
            for row in (state_row, trans_row):
                row["run_key"] = info["current"]["run_key"]
                row["run_status"] = info["status"]
                row["prompt_template_sha256"] = info["current"]["prompt_template_sha256"]
            rows.extend([state_row, trans_row])

    baseline_path = THIS_DIR / "results" / "baselines.json"
    if baseline_path.is_file():
        baselines = runner.read_json(baseline_path)
        for entry in baselines["states"]:
            row = _row(entry["Protocol"], entry["Model"], "states",
                       int(entry["Matched"]), int(entry["Total Extracted"]),
                       int(entry["Total GT"]))
            row["one_to_one_f1"] = None
            row["run_key"] = row["run_status"] = row["prompt_template_sha256"] = None
            rows.append(row)
        for entry in baselines["transitions"]:
            row = _row(entry["Protocol"], entry["Model"], "transitions",
                       int(entry["Matched"]), int(entry["TotalExtracted"]),
                       int(entry["TotalGT"]))
            row["one_to_one_f1"] = None
            row["run_key"] = row["run_status"] = row["prompt_template_sha256"] = None
            rows.append(row)
    return rows


def impossible_precision_rows() -> list[dict[str, Any]]:
    """
    Rows where the benchmark's official state metric exceeds 1.0.

    match_all_states is many-to-one — several ground-truth states may match the
    same extracted state — but precision divides that GT-side match count by the
    number of EXTRACTED states, so precision (and F1) can exceed 1. This is why
    the one-to-one column exists next to the official numbers.
    """
    import runner

    path = THIS_DIR / "results" / "baselines.json"
    if not path.is_file():
        return []
    baselines = runner.read_json(path)
    out = []
    for entry in baselines["states"]:
        if entry["Precision"] > 1 or entry["Recall"] > 1 or entry["F1-Score"] > 1:
            out.append({
                "protocol": entry["Protocol"], "model": entry["Model"],
                "extracted": entry["Total Extracted"], "gt": entry["Total GT"],
                "matched": entry["Matched"], "precision": entry["Precision"],
                "recall": entry["Recall"], "f1": entry["F1-Score"],
            })
    return out


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    models = sorted({r["model"] for r in rows})
    for kind in ("states", "transitions"):
        for model in models:
            subset = [r for r in rows if r["model"] == model and r["kind"] == kind]
            if not subset:
                continue
            macro = {
                metric: statistics.fmean(r[metric] for r in subset)
                for metric in ("precision", "recall", "f1")
            }
            median = {
                metric: statistics.median(r[metric] for r in subset)
                for metric in ("precision", "recall", "f1")
            }
            matched = sum(r["matched"] for r in subset)
            extracted = sum(r["extracted"] for r in subset)
            gt = sum(r["gt"] for r in subset)
            micro_p, micro_r, micro_f1 = _prf(matched, extracted, gt)
            out.append({
                "kind": kind, "model": model, "protocols": len(subset),
                "macro_precision": macro["precision"], "macro_recall": macro["recall"],
                "macro_f1": macro["f1"],
                "micro_precision": micro_p, "micro_recall": micro_r, "micro_f1": micro_f1,
                "median_precision": median["precision"], "median_recall": median["recall"],
                "median_f1": median["f1"],
            })
    return out


def _md_table(headers: list[str], body: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def write_costs_csv(out_dir: Path) -> Path:
    """
    results/costs.csv — one row per protocol for its CURRENT run, plus a TOTAL row.

    Only live runs are counted. Archived, superseded, smoke and legacy runs are
    deliberately excluded: this file answers "what does the reported result cost",
    not "what has ever been spent".
    """
    import runner

    rows = []
    for cost_path in sorted((THIS_DIR / "protocols").glob("*/output/cost.json")):
        data = runner.read_json(cost_path)
        row = {"protocol": data.get("protocol"), "run_key": data.get("run_key"),
               "workers": data.get("workers"),
               "llm_calls": data.get("total_llm_calls"),
               "total_tokens": data.get("total_tokens"),
               "cost_usd": data.get("total_cost_usd"),
               "wall_clock_seconds": data.get("total_wall_clock_s")}
        for stage, stats in (data.get("stages") or {}).items():
            row[f"{stage}_cost_usd"] = stats.get("cost_usd")
        rows.append(row)

    path = out_dir / "costs.csv"
    if not rows:
        path.write_text("protocol,run_key,cost_usd\n", encoding="utf-8")
        return path
    ordered = ["protocol", "run_key", "workers", "llm_calls", "total_tokens",
               "cost_usd", "wall_clock_seconds"]
    fieldnames = ordered + sorted(
        {k for row in rows for k in row} - set(ordered))
    total = {"protocol": "TOTAL",
             "llm_calls": sum(r.get("llm_calls") or 0 for r in rows),
             "total_tokens": sum(r.get("total_tokens") or 0 for r in rows),
             "cost_usd": round(sum(r.get("cost_usd") or 0 for r in rows), 6),
             "wall_clock_seconds": round(
                 sum(r.get("wall_clock_seconds") or 0 for r in rows), 2)}
    for stage in (k for k in fieldnames if k.endswith("_cost_usd")):
        total[stage] = round(sum(r.get(stage) or 0 for r in rows), 6)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        # Cross-protocol supplementary work belongs to no single run.
        writer.writerow({"protocol": "semantic judge (14 protocols, supplementary metric)",
                         "cost_usd": JUDGE_COST_USD})
        writer.writerow(total)
    return path


SUPPLEMENTARY_NOTE = (
    "Fixed in METHOD.md §5 before any of these numbers were seen. Official "
    "PSMBench results are never altered by anything in this section; each analysis is "
    "reported separately so a reader can accept or discard it on its own terms."
)


def _fmt(value: Any, spec: str = ".3f") -> str:
    return "—" if value is None else format(value, spec)


def _supplementary_sections(out_dir: Path) -> list[str]:
    """Supplementary: the semantic judge, clearly labelled as a different metric."""
    import runner

    parts: list[str] = []
    supp = THIS_DIR / "results" / "supplementary"


    # --- semantic label normalization: both numbers kept --------------------------
    norm_path = supp / "normalized" / "normalized_summary.json"
    if norm_path.is_file():
        norm = runner.read_json(norm_path)
        rows = norm.get("rows") or []
        body = [[r["protocol"],
                 f"{r['states_matched_raw']}/{r['states_gt']} ({r['states_extracted']})", _fmt(r["states_F1_raw"]), _fmt(r["states_F1_norm"]),
                 f"{r['trans_matched_raw']}/{r['trans_gt']} ({r['trans_extracted']})", _fmt(r["trans_F1_raw"]),
                 f"{r['trans_matched_norm']}/{r['trans_gt']}", _fmt(r["trans_P_norm"]), _fmt(r["trans_R_norm"]), _fmt(r["trans_F1_norm"]),
                 str(r["states_renamed"]), str(r["transitions_relabelled"])] for r in rows]
        m = (norm.get("macro") or {}).get("ours") or {}
        parts += [
            "## Semantic label normalization, re-scored with the benchmark evaluator (both numbers kept)", "",
            "The benchmark evaluator compares label strings. To measure the score without the wording "
            "barrier, a copy of each of our exports is made in which every state and transition the "
            "semantic judge matched to the ground truth by meaning (confidence >= 0.6) is renamed to the "
            "ground truth's wording. Nothing is added or removed: every unmatched state and edge stays "
            "exactly as extracted, so extra edges still count against precision. The copies are then "
            "scored with the unmodified evaluator. The original exports and their official scores are "
            "unchanged and reported above; the baselines are not modified. Normalized copies, scores and "
            "the list of every changed label are in `results/supplementary/normalized/` "
            "(`label_changes.md`).", "",
            _md_table(["Protocol", "states m/GT (ext)", "S-F1 raw", "S-F1 norm.", "transitions m/GT (ext)",
                       "T-F1 raw", "m norm.", "T-P norm.", "T-R norm.", "T-F1 norm.", "states renamed",
                       "edges relabelled"], body), "",
            f"Matched over 14 protocols: states {sum(r['states_matched_raw'] for r in rows)}/{sum(r['states_gt'] for r in rows)} raw → "
            f"{sum(r['states_matched_norm'] for r in rows)}/{sum(r['states_gt'] for r in rows)} normalized; transitions "
            f"{sum(r['trans_matched_raw'] for r in rows)}/{sum(r['trans_gt'] for r in rows)} raw → "
            f"{sum(r['trans_matched_norm'] for r in rows)}/{sum(r['trans_gt'] for r in rows)} normalized. "
            f"Macro over 14 protocols: states F1 {_fmt(m.get('states_F1_raw'))} raw → {_fmt(m.get('states_F1_norm'))} normalized; "
            f"transitions P {_fmt(m.get('trans_P_raw'))} → {_fmt(m.get('trans_P_norm'))}, "
            f"R {_fmt(m.get('trans_R_raw'))} → {_fmt(m.get('trans_R_norm'))}, "
            f"F1 {_fmt(m.get('trans_F1_raw'))} → {_fmt(m.get('trans_F1_norm'))}.", "",
        ]

    # --- C: semantic judge, a DIFFERENT metric (not the benchmark evaluator) -------
    judge_path = supp / "judge" / "semantic_summary.json"
    if judge_path.is_file():
        judge = runner.read_json(judge_path)
        macro = judge.get("macro") or {}
        order = ["ours"]
        body = [[
            system,
            _fmt(macro[system].get("states_F1")),
            _fmt(macro[system].get("trans_P")),
            _fmt(macro[system].get("trans_R")),
            _fmt(macro[system].get("trans_F1")),
        ] for system in order if system in macro]
        parts += [
            "## Semantic judge (supplementary metric, not the benchmark evaluator)", "",
            "A different question from the tables above: an Opus 4.6 judge at "
            "temperature 0 decides, one call per protocol, whether each "
            "extracted state or transition MEANS the same thing as a ground-truth one, "
            "matched one-to-one at confidence ≥ 0.6. It is reported because the "
            "official metric scores string similarity, and an extracted edge that is "
            "correct but worded unlike the diagram is counted as a false positive. "
            "These numbers are not comparable to the official ones and do not replace "
            "them; the judge prompt is `judge_prompt.txt`. " + SUPPLEMENTARY_NOTE, "",
            _md_table(["System", "states F1", "trans P", "trans R", "trans F1"], body),
            "",
        ]
    return parts


# The semantic judge is a cross-protocol analysis, not attributable to any one run,
# so its cost is recorded here rather than in a protocol's cost.json.
JUDGE_COST_USD = 2.02


def _write_supplementary_csv(out_dir: Path) -> None:
    """Machine-readable companion to the semantic-judge section."""
    import runner

    supp = THIS_DIR / "results" / "supplementary"
    target = out_dir / "supplementary"
    target.mkdir(parents=True, exist_ok=True)

    judge_path = supp / "judge" / "semantic_summary.json"
    if judge_path.is_file():
        macro = (runner.read_json(judge_path).get("macro") or {})
        with (target / "judge.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["system", "n", "states_P", "states_R", "states_F1",
                             "transitions_P", "transitions_R", "transitions_F1"])
            for system, m in macro.items():
                writer.writerow([system, m.get("n"), m.get("states_P"),
                                 m.get("states_R"), m.get("states_F1"),
                                 m.get("trans_P"), m.get("trans_R"),
                                 m.get("trans_F1")])


def _score_block(raw: Any) -> dict[str, Any] | None:
    """Normalise one evaluator row (either spelling of the extracted-count key)."""
    if not isinstance(raw, dict):
        return None
    extracted = raw.get("TotalExtracted", raw.get("Total Extracted"))
    gt = raw.get("TotalGT", raw.get("Total GT"))
    if extracted is None or gt is None:
        return None
    return {"matched": raw.get("Matched"), "extracted": extracted, "gt": gt,
            "P": raw.get("Precision"), "R": raw.get("Recall"),
            "F1": raw.get("F1-Score")}


def _variant_rows(index: dict[str, dict[str, Any]],
                  costs: dict[str, float]) -> list[dict[str, Any]]:
    """One row per (protocol, variant) for the headline table."""
    import runner

    rows: list[dict[str, Any]] = []
    for protocol, info in sorted(index.items()):
        data = runner.read_json(info["current"]["eval_path"])["official"]
        official = {"states": _score_block(data["states"]),
                    "transitions": _score_block(data["transitions"])}
        rows.append({"protocol": protocol, "variant": OFFICIAL_V,
                     **official, "cost_usd": costs.get(protocol)})
    return rows


def _aggregate_variant(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    """Macro (mean of per-protocol values) and micro (pooled counts) for one variant."""
    subset = [r for r in rows if r["variant"] == variant]
    if not subset:
        return {}
    out: dict[str, Any] = {"n": len(subset)}
    for kind in ("states", "transitions"):
        blocks = [r[kind] for r in subset if r.get(kind)]
        if not blocks:
            continue
        out[f"macro_{kind}"] = {
            key: statistics.fmean([b[key] for b in blocks if b.get(key) is not None])
            for key in ("P", "R", "F1")
        }
        matched = sum(b["matched"] or 0 for b in blocks)
        extracted = sum(b["extracted"] or 0 for b in blocks)
        gt = sum(b["gt"] or 0 for b in blocks)
        precision, recall, f1 = _prf(matched, extracted, gt)
        out[f"micro_{kind}"] = {"matched": matched, "extracted": extracted, "gt": gt,
                                "P": precision, "R": recall, "F1": f1}
    return out


def render(namespace: str = "generic") -> dict[str, Path]:
    import runner

    rows = collect(namespace)
    if not rows:
        raise RuntimeError("no eval.json or baselines.json found — nothing to summarize")
    aggregates = aggregate(rows)
    out_dir = THIS_DIR / "results"
    out_dir.mkdir(parents=True, exist_ok=True)

    index = namespace_index(namespace)
    costs: dict[str, float] = {}
    for cost_path in sorted((THIS_DIR / "protocols").glob("*/output/cost.json")):
        data = runner.read_json(cost_path)
        if data.get("protocol"):
            costs[data["protocol"]] = data.get("total_cost_usd")

    csv_path = out_dir / "per_protocol.csv"
    variant_for_csv = _variant_rows(index, costs)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["protocol", "variant",
                         "states_matched", "states_gt", "states_extracted",
                         "states_P", "states_R", "states_F1",
                         "transitions_matched", "transitions_gt",
                         "transitions_extracted", "transitions_P", "transitions_R",
                         "transitions_F1", "cost_usd"])
        for row in variant_for_csv:
            st, tr = row.get("states") or {}, row.get("transitions") or {}
            writer.writerow([row["protocol"], row["variant"],
                             st.get("matched"), st.get("gt"), st.get("extracted"),
                             st.get("P"), st.get("R"), st.get("F1"),
                             tr.get("matched"), tr.get("gt"), tr.get("extracted"),
                             tr.get("P"), tr.get("R"), tr.get("F1"),
                             row.get("cost_usd")])
    baselines_csv = out_dir / "baselines_comparison.csv"
    with baselines_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    agg_csv = out_dir / "aggregates.csv"
    with agg_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(aggregates[0]))
        writer.writeheader()
        writer.writerows(aggregates)

    md_parts: list[str] = ["# PSMBench results", ""]
    banner = []
    for protocol, info in sorted(index.items()):
        current = info["current"]
        if info["status"] == "current":
            banner.append(f"- **{protocol}**: `{current['run_key']}` "
                          f"(prompt_template `{str(current['prompt_template_sha256'])[:8]}`)")
        else:
            banner.append(
                f"- **{protocol}**: `{current['run_key']}` — **SUPERSEDED / OLDER "
                f"PROMPT HASH** (`{str(current['prompt_template_sha256'])[:8]}`); the "
                f"current config expects `{info.get('expected_run_key')}`, whose run "
                f"is not finished (no eval.json). The row below is read from the "
                f"archived namespace and is NOT the current configuration's result.")
    if banner:
        md_parts += ["## Run provenance", "",
                     "Each protocol's row comes from exactly one namespace. Runs made "
                     "under different prompt hashes are not comparable and are never "
                     "mixed in one table.", ""] + banner + [""]
        superseded_rows = []
        for protocol, info in sorted(index.items()):
            for entry in info["superseded"]:
                data = runner.read_json(entry["eval_path"])
                official = data["official"]
                superseded_rows.append([
                    protocol, entry["run_key"],
                    str(entry["prompt_template_sha256"])[:8],
                    f"{official['states']['F1-Score']:.3f}",
                    f"{official['transitions']['F1-Score']:.3f}",
                    str(official["transitions"]["TotalExtracted"]),
                ])
        if superseded_rows:
            md_parts += ["## Superseded runs (not comparable to the table above)", "",
                         _md_table(["Protocol", "run_key", "prompt_template",
                                    "states F1", "transitions F1", "extracted"],
                                   superseded_rows), ""]
    variant_rows = _variant_rows(index, costs)

    def _cells(row: dict[str, Any]) -> list[str]:
        st, tr = row.get("states") or {}, row.get("transitions") or {}
        cost = row.get("cost_usd")
        return [
            row["protocol"], row["variant"],
            f"{st.get('matched')}/{st.get('gt')} ({st.get('extracted')})",
            _fmt(st.get("P")), _fmt(st.get("R")), _fmt(st.get("F1")),
            f"{tr.get('matched')}/{tr.get('gt')} ({tr.get('extracted')})",
            _fmt(tr.get("P")), _fmt(tr.get("R")), _fmt(tr.get("F1")),
            "—" if cost is None else f"{cost:.2f}",
        ]

    headline_header = ["Protocol", "Variant", "states m/GT (ext)", "S-P", "S-R", "S-F1",
                       "transitions m/GT (ext)", "T-P", "T-R", "T-F1", "Run cost (USD)"]
    md_parts += [
        "## Results", "",
        "The A2ABreak pipeline on all 14 PSMBench protocols, scored with the benchmark's "
        "unmodified evaluator (threshold 0.5, `if_partial=False`). Each row is the "
        "export exactly as the pipeline produced it. Cost is the cost of producing that "
        "protocol's run.", "",
    ]
    md_parts += [_md_table(headline_header, [_cells(r) for r in variant_rows]), ""]

    agg_body = []
    for variant in (OFFICIAL_V,):
        agg = _aggregate_variant(variant_rows, variant)
        if not agg:
            continue
        for scope in ("macro", "micro"):
            st = agg.get(f"{scope}_states") or {}
            tr = agg.get(f"{scope}_transitions") or {}
            agg_body.append([
                variant, f"{scope} (n={agg['n']})",
                _fmt(st.get("P")), _fmt(st.get("R")), _fmt(st.get("F1")),
                _fmt(tr.get("P")), _fmt(tr.get("R")), _fmt(tr.get("F1"))])
    md_parts += [
        "### Aggregates", "",
        "Macro is the mean of the per-protocol values; micro pools the counts across "
        "protocols before computing P/R/F1, so it weights larger state machines more "
        "heavily.", "",
        _md_table(["Variant", "Scope", "S-P", "S-R", "S-F1", "T-P", "T-R", "T-F1"],
                  agg_body), "",
    ]

    # --- baselines, kept out of the headline ------------------------------------
    baseline_body = []
    for variant in (OFFICIAL_V,):
        agg = _aggregate_variant(variant_rows, variant)
        if not agg:
            continue
        baseline_body.append([
            "**A2ABreak**", str(agg["n"]),
            _fmt((agg.get("macro_states") or {}).get("F1")),
            _fmt((agg.get("macro_transitions") or {}).get("P")),
            _fmt((agg.get("macro_transitions") or {}).get("R")),
            _fmt((agg.get("macro_transitions") or {}).get("F1"))])
    baseline_macros = [a for a in aggregates if a["model"] != OURS]
    by_model: dict[str, dict[str, Any]] = {}
    for entry in baseline_macros:
        by_model.setdefault(entry["model"], {})[entry["kind"]] = entry
    for model, kinds in sorted(by_model.items(),
                               key=lambda kv: -(kv[1].get("transitions", {})
                                                .get("macro_f1") or 0)):
        states, transitions = kinds.get("states", {}), kinds.get("transitions", {})
        baseline_body.append([
            model, str(states.get("protocols", transitions.get("protocols", ""))),
            _fmt(states.get("macro_f1")), _fmt(transitions.get("macro_precision")),
            _fmt(transitions.get("macro_recall")), _fmt(transitions.get("macro_f1"))])
    md_parts += [
        "## Comparison with PSMBench baselines", "",
        "The nine baseline rows are the FSMs **shipped with PSMBench**, scored here by "
        "the same unmodified evaluator on the same ground truth. They are not models we "
        "ran: no prompt, model version or decoding setting of theirs is ours to report, "
        "and any difference in those is a difference between the published artifacts and "
        "our pipeline, not a controlled comparison. Macro values, all 14 protocols.", "",
        _md_table(["System", "n", "states F1", "trans P", "trans R", "trans F1"],
                  baseline_body), "",
    ]

    md_parts += _supplementary_sections(out_dir)
    md_path = out_dir / "summary.md"
    md_path.write_text("\n".join(md_parts), encoding="utf-8")

    runner.write_json(out_dir / "rows.json", {"rows": rows, "aggregates": aggregates})

    _write_supplementary_csv(out_dir)
    write_costs_csv(out_dir)
    impossible = impossible_precision_rows()
    footnote_path = out_dir / "metric_caveat_precision_gt_1.json"
    runner.write_json(footnote_path, {
        "caveat": (
            "eval_fsm_sim.match_all_states matches states many-to-one (several "
            "ground-truth states can select the same extracted state) but divides the "
            "resulting match count by the number of extracted states, so the official "
            "state Precision and F1 can exceed 1.0. The rows below are the shipped "
            "baselines where that happens; the one-to-one column in the tables is the "
            "bounded alternative."
        ),
        "affected_row_count": len(impossible),
        "total_baseline_state_rows": len([r for r in rows
                                          if r["kind"] == "states" and r["model"] != OURS]),
        "rows": impossible,
    })
    return {"markdown": md_path, "csv": csv_path, "aggregates": agg_csv,
            "metric_caveat": footnote_path,
            "costs": out_dir / "costs.csv",
            "baselines_csv": out_dir / "baselines_comparison.csv",
            "supplementary_csv": out_dir / "supplementary"}

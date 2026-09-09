#!/usr/bin/env python3
"""
fsm_to_dot.py
-------------
Converts a stage_b2 FSM JSON file (or stage_b4 unified_fsm.json) to Graphviz
.dot, then renders PNG via `dot` and opens it (macOS: `open`). Optionally
renders a vector PDF with `--pdf`.

Paths come from config.yaml (`output.base_dir`).

Node styling:
  initial state    → doublecircle, green fill
  terminal state   → rectangle, red fill
  error state      → diamond, orange fill
  intermediate     → circle
  inferred         → dashed border (any type)

Edge styling:
  explicit (is_inferred=False) → solid
  inferred (is_inferred=True)  → dashed

Per-phase B2 only: inter-stage markers → hexagon nodes (__ENTRY__ / __EXIT__).

Unified (``--unified``): nodes keyed by ``state_id``; states emitted in
protocol phase order with phase in the label. Inter-stage edges (purple) vs
intra-phase (modality colors).

Usage:
    python FSM_to_DOT.py --stage discovery
    python FSM_to_DOT.py --stage authentication
    python FSM_to_DOT.py --unified
    python FSM_to_DOT.py --unified --input outputs/stage_b4/unified_fsm_dedup.json
    python FSM_to_DOT.py --unified --input outputs/stage_b4/unified_fsm_llm_dedup.json --pdf

Requires Graphviz (`dot`) on PATH for PNG/PDF rendering.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Optional

import yaml

from stage_b1 import STAGES

SCRIPT_DIR  = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node styling by semantic_type
# ---------------------------------------------------------------------------

def node_attrs(state: dict) -> dict:
    semantic  = state.get("semantic_type", "")
    is_inf    = state.get("is_inferred", False)
    is_final  = state.get("is_final", False)

    attrs = {"fontname": "Helvetica", "fontsize": "24"}

    if semantic == "initial":
        attrs["shape"]     = "doublecircle"
        attrs["style"]     = "filled,dashed" if is_inf else "filled"
        attrs["fillcolor"] = "#d4edda"
        attrs["color"]     = "#28a745"

    elif semantic == "error":
        attrs["shape"]     = "diamond"
        attrs["style"]     = "filled,dashed" if is_inf else "filled"
        attrs["fillcolor"] = "#fff3cd"
        attrs["color"]     = "#fd7e14"

    elif semantic == "interrupted":
        attrs["shape"]     = "ellipse"
        attrs["style"]     = "filled,dashed" if is_inf else "filled"
        attrs["fillcolor"] = "#cce5ff"
        attrs["color"]     = "#004085"

    elif semantic in ("terminal",) or is_final:
        attrs["shape"]     = "rectangle"
        attrs["style"]     = "filled,dashed" if is_inf else "filled"
        attrs["fillcolor"] = "#f8d7da"
        attrs["color"]     = "#dc3545"

    else:
        attrs["shape"] = "circle"
        attrs["style"] = "dashed" if is_inf else "solid"

    return attrs


def format_attrs(attrs: dict) -> str:
    parts = [f'{k}="{v}"' for k, v in attrs.items()]
    return "[" + ", ".join(parts) + "]"


def escape(s: str) -> str:
    """Escape for use inside Graphviz quoted node IDs / label fragments."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    # Backslashes first, then double quotes / newlines
    return (
        s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    )


def format_guard_dot(guard: str, width: int = 72) -> str:
    """
    Full guard text for edge labels — no truncation. Soft-wrap wide lines only
    (Graphviz becomes unreadable on one infinitely long line).
    """
    g = guard.replace("\r", "").replace("\n", " ").strip()
    if not g:
        return ""
    lines = textwrap.wrap(g, width=width, break_long_words=True)
    return "\\n".join(escape(line) for line in lines)


def sanitize_graph_id(stage: str) -> str:
    """
    Unquoted DIGRAPH identifiers must match ID / alphanumeric + underscores.
    """
    raw = str(stage).strip()
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    if not safe:
        safe = "fsm"
    if safe[0].isdigit():
        safe = "g_" + safe
    return safe


def _inter_stage_name(raw) -> Optional[str]:
    """Return a non-empty state name string, or None."""
    if raw is None:
        return None
    name = raw if isinstance(raw, str) else str(raw)
    name = name.strip()
    return name or None


def short_label(name: str, max_len: int = 20) -> str:
    """Break long state names with line breaks for readable node labels."""
    if not isinstance(name, str):
        name = str(name)
    if len(name) <= max_len:
        return name
    parts = name.split("_")
    lines, current = [], ""
    for p in parts:
        if current and len(current) + 1 + len(p) > max_len:
            lines.append(current)
            current = p
        else:
            current = current + "_" + p if current else p
    if current:
        lines.append(current)
    return "\\n".join(lines)


def _is_unified_fsm(data: dict) -> bool:
    """True for stage_b4 merged graph (unique node ids per phase)."""
    if str(data.get("schema_version") or "").strip() == "stage_b4_v1":
        return True
    states = data.get("states") or []
    if not states:
        return False
    return all(isinstance(s, dict) and s.get("state_id") for s in states)


def _state_protocol_phases(state: dict) -> list[str]:
    phases = state.get("protocol_phases")
    if isinstance(phases, list):
        normalized = [str(p).strip() for p in phases if str(p).strip()]
        if normalized:
            return normalized

    phase = str(state.get("protocol_phase") or "").strip()
    if phase:
        return [phase]

    return ["?"]


def _unified_phase_order(data: dict) -> list[str]:
    """Canonical STAGES order, then any extra protocol phase values from data."""
    out: list[str] = list(STAGES)
    seen = set(out)
    for s in data.get("states") or []:
        if not isinstance(s, dict):
            continue
        for ph in _state_protocol_phases(s):
            if ph and ph not in seen:
                out.append(ph)
                seen.add(ph)
    return out


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def _fsm_to_dot_b1(data: dict) -> str:
    stage       = str(data.get("stage", "unknown")).strip()
    graph_name  = sanitize_graph_id(stage)
    states      = data.get("states") or []
    transitions = data.get("transitions") or []
    inter_raw   = data.get("inter_stage") or {}
    inter       = inter_raw if isinstance(inter_raw, dict) else {}
    entry_states = inter.get("entry_from_previous") or []
    exit_states  = inter.get("exit_to_next") or []

    lines = []
    lines.append(f"digraph {graph_name} {{")
    lines.append('    rankdir=LR;')
    lines.append('    size="4,8";')
    lines.append('    ratio=compress;')
    lines.append('    margin=0.05;')
    lines.append('    dpi=300;')
    lines.append('    nodesep=0.30;')
    lines.append('    ranksep=0.55;')
    lines.append('    fontname="Helvetica";')
    lines.append('    node [fontname="Helvetica", fontsize=24];')
    lines.append('    edge [fontname="Helvetica", fontsize=18];')
    lines.append('')

    # --- States ---
    lines.append('    // States')
    state_names = set()
    for s in states:
        name = s.get("state_name")
        if not name:
            continue
        name = name if isinstance(name, str) else str(name)
        state_names.add(name)
        attrs  = node_attrs(s)
        label  = short_label(name)
        attrs["label"] = label
        lines.append(f'    "{escape(name)}" {format_attrs(attrs)};')

    lines.append('')

    referenced: set[str] = set()
    for t in transitions:
        frm, to = t.get("from_state"), t.get("to_state")
        if frm:
            referenced.add(str(frm).strip())
        if to:
            referenced.add(str(to).strip())
    for raw in entry_states:
        if isinstance(raw, dict):
            n = raw.get("from_state") or raw.get("state_name")
        else:
            n = _inter_stage_name(raw)
        if n:
            referenced.add(n)
    for raw in exit_states:
        if isinstance(raw, dict):
            n = raw.get("from_state")
        else:
            n = _inter_stage_name(raw)
        if n:
            referenced.add(n)

    ghost_states = sorted(referenced - state_names)
    if ghost_states:
        lines.append("    // Referenced only on edges (omit from explicit states)")
        for g in ghost_states:
            lab = short_label(g)
            lines.append(
                f'    "{escape(g)}" [shape=box, label="{escape(lab)}", '
                'style="dashed,filled", fillcolor="#eeeeee", color="#888888"];'
            )
        lines.append('')

    # --- Inter-stage marker nodes ---
    if entry_states:
        lines.append('    // Inter-stage: entry from previous stage')
        lines.append(
            '    "__ENTRY__" [shape=hexagon, label="← prev stage", '
            'style=filled, fillcolor="#cce5ff", color="#004085", '
            'fontname="Helvetica", fontsize=18];'
        )
        for s in entry_states:
            if isinstance(s, dict):
                n = s.get("from_state") or s.get("state_name")
            else:
                n = _inter_stage_name(s)

            if not n:
                continue
            lines.append(
                f'    "__ENTRY__" -> "{escape(n)}" '
                f'[style=dotted, color="#004085", arrowsize=0.7];'
            )
        lines.append('')

    if exit_states:
        lines.append('    // Inter-stage: exit to next stage')
        lines.append(
            '    "__EXIT__" [shape=hexagon, label="next stage →", '
            'style=filled, fillcolor="#fff3cd", color="#856404", '
            'fontname="Helvetica", fontsize=18];'
        )
        for s in exit_states:
            if isinstance(s, dict):
                n          = s.get("from_state")
                to_stage   = s.get("to_stage", "")
                edge_label = escape(to_stage) if to_stage else ""
            else:
                n          = _inter_stage_name(s)
                edge_label = ""

            if not n:
                continue

            label_attr = f', label="{edge_label}"' if edge_label else ""
            lines.append(
                f'    "{escape(n)}" -> "__EXIT__" '
                f'[style=dotted, color="#856404", arrowsize=0.7{label_attr}];'
            )
        lines.append('')

    # --- Transitions ---
    lines.append('    // Transitions')
    for t in transitions:
        frm     = t.get("from_state")
        to      = t.get("to_state")
        trigger = t.get("trigger") or ""
        guard   = t.get("guard")   or ""
        is_inf  = t.get("is_inferred", False)
        mod     = str(t.get("modality") or "").lower()

        if not frm or not to:
            continue

        label = escape(trigger)
        if guard:
            label += f"\\n[{format_guard_dot(str(guard))}]"

        edge_style = "dashed" if is_inf else "solid"
        edge_color = "#666666" if is_inf else "#333333"

        # Color edges by modality (maps common RFC / pipeline spellings)
        if mod in ("must", "required", "shall"):
            edge_color = "#dc3545"
        elif mod in ("should", "recommended"):
            edge_color = "#fd7e14"
        elif mod in ("may", "optional"):
            edge_color = "#28a745"

        lines.append(
            f'    "{escape(frm)}" -> "{escape(to)}" '
            f'[label="{label}", style={edge_style}, '
            f'color="{edge_color}", fontcolor="{edge_color}"];'
        )

    lines.append('')

    lines.append('}')
    return "\n".join(lines)


def _fsm_to_dot_unified(data: dict) -> str:
    """Graphviz for stage_b4 unified_fsm.json (flat graph; node id = state_id)."""
    graph_name  = sanitize_graph_id(str(data.get("stage") or "unified_fsm"))
    states      = data.get("states") or []
    transitions = data.get("transitions") or []

    phase_order   = _unified_phase_order(data)
    phase_index = {phase: i for i, phase in enumerate(phase_order)}
    state_rows = [s for s in states if isinstance(s, dict)]
    state_rows.sort(
        key=lambda s: (
            min(phase_index.get(p, len(phase_index)) for p in _state_protocol_phases(s)),
            str(s.get("state_name") or ""),
        )
    )

    lines = []
    lines.append(f"digraph {graph_name} {{")
    lines.append('    rankdir=LR;')
    lines.append('    size="4,8";')
    lines.append('    ratio=compress;')
    lines.append('    margin=0.05;')
    lines.append('    dpi=300;')
    lines.append('    nodesep=0.30;')
    lines.append('    ranksep=0.55;')
    lines.append('    fontname="Helvetica";')
    lines.append('    node [fontname="Helvetica", fontsize=24];')
    lines.append('    edge [fontname="Helvetica", fontsize=18];')
    lines.append('')

    lines.append("    // States")
    state_ids: set[str] = set()
    for s in state_rows:
        sid = s.get("state_id")
        name = s.get("state_name")
        if not sid or not name:
            continue
        sid_s, name_s = str(sid), str(name)
        state_ids.add(sid_s)
        attrs  = node_attrs(s)
        attrs["label"] = short_label(name_s)
        lines.append(f'    "{escape(sid_s)}" {format_attrs(attrs)};')

    lines.append('')

    referenced: set[str] = set()
    for t in transitions:
        for key in ("from_state_id", "to_state_id"):
            v = t.get(key)
            if v:
                referenced.add(str(v).strip())
    ghost = sorted(referenced - state_ids)
    if ghost:
        lines.append("    // Referenced on edges but missing from states[]")
        for g in ghost:
            lines.append(
                f'    "{escape(g)}" [shape=box, label="{escape(short_label(g))}", '
                'style="dashed,filled", fillcolor="#eeeeee", color="#888888"];'
            )
        lines.append('')

    lines.append("    // Transitions (inter_stage = purple; intra = modality colors)")
    for t in transitions:
        frm = t.get("from_state_id")
        to  = t.get("to_state_id")
        if not frm or not to:
            continue
        frm_s, to_s = str(frm), str(to)

        trigger = t.get("trigger") or ""
        guard   = t.get("guard") or ""
        is_inf  = t.get("is_inferred", False)
        mod     = str(t.get("modality") or "").lower()
        inter   = t.get("kind") == "inter_stage"

        label = escape(str(trigger))
        if guard:
            label += f"\\n[{format_guard_dot(str(guard))}]"

        edge_style = "dashed" if is_inf else "solid"
        edge_color = "#666666" if is_inf else "#333333"
        penwidth   = "1"

        if inter:
            edge_color = "#5b21b6" if not is_inf else "#9333ea"
            penwidth = "2.2"
        else:
            if mod in ("must", "required", "shall"):
                edge_color = "#dc3545"
            elif mod in ("should", "recommended"):
                edge_color = "#fd7e14"
            elif mod in ("may", "optional"):
                edge_color = "#28a745"

        lines.append(
            f'    "{escape(frm_s)}" -> "{escape(to_s)}" [label="{label}", style={edge_style}, '
            f'color="{edge_color}", fontcolor="{edge_color}", penwidth={penwidth}];'
        )

    lines.append('')

    lines.append('}')
    return "\n".join(lines)


def fsm_to_dot(data: dict) -> str:
    if _is_unified_fsm(data):
        return _fsm_to_dot_unified(data)
    return _fsm_to_dot_b1(data)


def _preview_image(path: Path) -> None:
    """Best-effort open the rendered image."""
    path = path.resolve()
    if not path.is_file():
        return
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as e:
        logger.warning("Could not open preview (%s): %s", path, e)


def _render_png(dot_path: Path, png_path: Path) -> bool:
    try:
        subprocess.run(
            ["dot", "-Tpng", str(dot_path), "-o", str(png_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except FileNotFoundError:
        logger.error(
            "Graphviz `dot` not found on PATH — install Graphviz "
            "(https://graphviz.org) and retry."
        )
        return False
    except subprocess.CalledProcessError as e:
        logger.error("dot failed:\n%s", (e.stderr or e.stdout or str(e)))
        return False


def _render_pdf(dot_path: Path, pdf_path: Path) -> bool:
    try:
        subprocess.run(
            ["dot", "-Tpdf", str(dot_path), "-o", str(pdf_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except FileNotFoundError:
        logger.error(
            "Graphviz `dot` not found on PATH — install Graphviz "
            "(https://graphviz.org) and retry."
        )
        return False
    except subprocess.CalledProcessError as e:
        logger.error("dot failed:\n%s", (e.stderr or e.stdout or str(e)))
        return False


def paths_for_stage(
    protocol_stage: str,
    config_path: Path = CONFIG_PATH,
) -> tuple[Path, Path]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    base = SCRIPT_DIR / (cfg.get("output") or {}).get("base_dir", "outputs")
    stage_dir = base / "stage_b2"
    return (
        stage_dir / f"{protocol_stage}.json",
        stage_dir / f"{protocol_stage}.dot",
    )


def paths_for_unified(
    config_path: Path = CONFIG_PATH,
) -> tuple[Path, Path]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    base = SCRIPT_DIR / (cfg.get("output") or {}).get("base_dir", "outputs")
    d = base / "stage_b4"
    return d / "unified_fsm.json", d / "unified_fsm.dot"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render FSM JSON to Graphviz DOT and PNG/PDF (B1 per-phase or B3 unified).",
    )
    mx = parser.add_mutually_exclusive_group(required=True)
    mx.add_argument(
        "--stage",
        choices=list(STAGES),
        help="Protocol phase: outputs/stage_b2/<stage>.json",
    )
    mx.add_argument(
        "--unified",
        action="store_true",
        help="Render outputs/stage_b4/unified_fsm.json (run stage_b4.py first).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the PNG after rendering.",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also render a PDF next to the DOT/PNG output.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help=(
            "Unified FSM JSON override. Only valid with --unified. "
            "Default: outputs/stage_b4/unified_fsm.json."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "DOT output path override. Only valid with --unified. "
            "Default: <input>.dot when --input is set."
        ),
    )
    return parser.parse_args()


def run_fsm_to_dot(
    protocol_stage: str,
    *,
    open_preview: bool = True,
    render_pdf: bool = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    in_path, dot_path = paths_for_stage(protocol_stage)

    logger.info("=" * 60)
    logger.info("FSM → Graphviz DOT + PNG%s (%s)", " + PDF" if render_pdf else "", protocol_stage)
    logger.info("=" * 60)
    logger.info("Input JSON: %s", in_path)
    logger.info("Output DOT: %s", dot_path)

    if not in_path.is_file():
        logger.error("Input file not found: %s", in_path)
        sys.exit(1)

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    dot = fsm_to_dot(data)

    dot_path.parent.mkdir(parents=True, exist_ok=True)
    dot_path.write_text(dot, encoding="utf-8")
    logger.info("Written: %s", dot_path)

    png_path = dot_path.with_suffix(".png")
    if _render_png(dot_path, png_path):
        logger.info("Rendered: %s", png_path)
        if open_preview:
            _preview_image(png_path)
            logger.info("Preview opened (if supported on this OS).")
    if render_pdf:
        pdf_path = dot_path.with_suffix(".pdf")
        if _render_pdf(dot_path, pdf_path):
            logger.info("Rendered: %s", pdf_path)


def run_fsm_to_dot_unified(
    *,
    open_preview: bool = True,
    input_path: Path | None = None,
    dot_path: Path | None = None,
    render_pdf: bool = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if input_path is None:
        in_path, default_dot_path = paths_for_unified()
        dot_path = dot_path or default_dot_path
    else:
        in_path = input_path
        if not in_path.is_absolute():
            in_path = SCRIPT_DIR / in_path
        dot_path = dot_path or in_path.with_suffix(".dot")

    if dot_path is not None and not dot_path.is_absolute():
        dot_path = SCRIPT_DIR / dot_path

    logger.info("=" * 60)
    logger.info("FSM → Graphviz DOT + PNG%s (unified / stage_b4)", " + PDF" if render_pdf else "")
    logger.info("=" * 60)
    logger.info("Input JSON: %s", in_path)
    logger.info("Output DOT: %s", dot_path)

    if not in_path.is_file():
        logger.error("Input file not found: %s — run stage_b4.py first.", in_path)
        sys.exit(1)

    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not _is_unified_fsm(data):
        logger.error(
            "%s does not look like a stage_b4 unified FSM "
            "(expected schema_version stage_b4_v1 or state_id on states).",
            in_path,
        )
        sys.exit(1)

    dot = fsm_to_dot(data)

    dot_path.parent.mkdir(parents=True, exist_ok=True)
    dot_path.write_text(dot, encoding="utf-8")
    logger.info("Written: %s", dot_path)

    png_path = dot_path.with_suffix(".png")
    if _render_png(dot_path, png_path):
        logger.info("Rendered: %s", png_path)
        if open_preview:
            _preview_image(png_path)
            logger.info("Preview opened (if supported on this OS).")
    if render_pdf:
        pdf_path = dot_path.with_suffix(".pdf")
        if _render_pdf(dot_path, pdf_path):
            logger.info("Rendered: %s", pdf_path)


if __name__ == "__main__":
    args = _parse_args()
    open_p = not args.no_open
    if not args.unified and (args.input is not None or args.output is not None):
        raise SystemExit("--input and --output are only valid with --unified")
    if args.unified:
        run_fsm_to_dot_unified(
            open_preview=open_p,
            input_path=args.input,
            dot_path=args.output,
            render_pdf=args.pdf,
        )
    else:
        run_fsm_to_dot(args.stage, open_preview=open_p, render_pdf=args.pdf)
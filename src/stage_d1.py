#!/usr/bin/env python3
"""
A2A Protocol FSM Deduplication — Deterministic Pass

This script handles all mechanical/algorithmic cleanup that requires
zero semantic judgment. Run this FIRST, then pass the output to the
LLM for semantic analysis (Rules 5-8).

Rules implemented here:
  1. Canonical name normalization
  2. Merge cross-phase duplicate states
  3. Remove inter-stage identity transitions
  4. Merge duplicate transitions (with configurable trigger equivalence map)
  9. Validate structural integrity

Usage:
  python3 fsm_dedup_deterministic.py input.json output.json
"""

import json
import copy
import sys
from collections import defaultdict
from typing import Any

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION — extend these as needed for new protocol versions
# ═══════════════════════════════════════════════════════════════

# Rule 1: Canonical name fixes (old_name → canonical_name)
NAME_FIXES = {
    "submitted": "TASK_STATE_SUBMITTED",
    "STREAM_OPEN": "STREAM_STATE_OPEN",
    "STREAM_STATE_CLOSED": "STREAM_CLOSED",
}

# Rule 4: Trigger equivalence classes (variant → canonical)
# Triggers mapping to the same canonical form are considered duplicates
TRIGGER_EQUIVALENCE = {
    "task_processing_failed": "task_failed",
    "cancel_task": "task_canceled",
    "client_sends_input": "message_received",
    "credential_received": "credentials_received",
    "statusUpdate": "task_status_update",
    "message_send": "task_status_update",
}

# Modality strictness ranking (lower = stricter)
MODALITY_RANK = {
    "must": 0,
    "should": 1,
    "can": 2,
    "may": 3,
    "unspecified": 4,
}


# ═══════════════════════════════════════════════════════════════
# RULE 1: Canonical Name Normalization
# ═══════════════════════════════════════════════════════════════

def canonicalize_name(name: str) -> str:
    """Apply canonical name mapping."""
    return NAME_FIXES.get(name, name)


def apply_name_normalization(data: dict) -> dict:
    """Normalize all state name references throughout the FSM."""
    fixes_applied = {}

    # Fix states
    for s in data["states"]:
        old = s["state_name"]
        new = canonicalize_name(old)
        if old != new:
            fixes_applied[old] = new
        s["state_name"] = new
        s["state_id"] = new

    # Fix transitions
    for t in data["transitions"]:
        t["from_state"] = canonicalize_name(t.get("from_state", ""))
        t["to_state"] = canonicalize_name(t.get("to_state", ""))
        t["from_state_id"] = t["from_state"]
        t["to_state_id"] = t["to_state"]

    return fixes_applied


# ═══════════════════════════════════════════════════════════════
# RULE 2: Merge Cross-Phase Duplicate States
# ═══════════════════════════════════════════════════════════════

def merge_duplicate_states(data: dict) -> tuple[list[dict], dict]:
    """Group states by name and merge duplicates across phases."""
    groups = defaultdict(list)
    for s in data["states"]:
        groups[s["state_name"]].append(s)

    merged_states = []
    merge_info = {}

    for name, group in groups.items():
        # Sort by confidence desc, then by source_id count desc
        group.sort(key=lambda x: (-x.get("confidence", 0), -len(x.get("source_ids", []))))
        base = copy.deepcopy(group[0])

        # Collect all phases
        phases = sorted(set(g.get("protocol_phase", "unknown") for g in group))
        base["protocol_phases"] = phases

        # Merge source_ids
        all_sources = []
        for g in group:
            all_sources.extend(g.get("source_ids", []))
        base["source_ids"] = sorted(set(all_sources))

        # Unified state_id
        base["state_id"] = name

        # Remove old single-phase field
        base.pop("protocol_phase", None)

        # Union of initial/final flags
        base["is_initial"] = any(g.get("is_initial", False) for g in group)
        base["is_final"] = any(g.get("is_final", False) for g in group)

        # Best invariant_cond
        for g in group:
            if g.get("invariant_cond"):
                base["invariant_cond"] = g["invariant_cond"]
                break

        # Traceability
        if len(group) > 1:
            base["merged_from_phases"] = phases
            base["pre_merge_count"] = len(group)
            merge_info[name] = {
                "phases": phases,
                "copies_merged": len(group),
            }

        merged_states.append(base)

    return merged_states, merge_info


# ═══════════════════════════════════════════════════════════════
# RULE 3: Remove Inter-Stage Identity Transitions
# ═══════════════════════════════════════════════════════════════

def remove_identity_transitions(transitions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Remove inter-stage transitions where from_state == to_state."""
    kept = []
    removed = []

    for t in transitions:
        if t.get("kind") == "inter_stage" and t["from_state"] == t["to_state"]:
            removed.append(t)
        else:
            kept.append(t)

    return kept, removed


# ═══════════════════════════════════════════════════════════════
# RULE 4: Merge Duplicate Transitions
# ═══════════════════════════════════════════════════════════════

def trigger_class(trigger: str) -> str:
    """Map trigger to its equivalence class."""
    return TRIGGER_EQUIVALENCE.get(trigger, trigger)


def merge_duplicate_transitions(transitions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge transitions with same (from, to) and equivalent triggers."""
    # Group by (from_state, to_state)
    pair_groups = defaultdict(list)
    for t in transitions:
        pair_groups[(t["from_state"], t["to_state"])].append(t)

    result = []
    merge_log = []

    for (fs, ts), group in pair_groups.items():
        if len(group) == 1:
            result.append(group[0])
            continue

        # Sub-group by trigger equivalence class
        trigger_subgroups = defaultdict(list)
        for t in group:
            tc = trigger_class(t.get("trigger", ""))
            trigger_subgroups[tc].append(t)

        for tc, subgroup in trigger_subgroups.items():
            if len(subgroup) == 1:
                result.append(subgroup[0])
                continue

            # Merge the subgroup
            subgroup.sort(key=lambda x: (
                -x.get("confidence", 0),
                0 if not x.get("is_inferred", True) else 1,
                -len(x.get("source_ids", []))
            ))
            base = copy.deepcopy(subgroup[0])

            # Merge source_ids
            all_src = []
            for s in subgroup:
                all_src.extend(s.get("source_ids", []))
            base["source_ids"] = sorted(set(all_src))

            # Merge phases
            phases = sorted(set(
                s.get("protocol_phase", s.get("from_stage", "unknown"))
                for s in subgroup
            ))
            base["protocol_phases"] = phases

            # Merge actors
            all_actors = set()
            for s in subgroup:
                all_actors.update(s.get("actors", []))
            base["actors"] = sorted(all_actors)

            # Strictest modality
            base["modality"] = min(
                (s.get("modality", "unspecified") for s in subgroup),
                key=lambda m: MODALITY_RANK.get(m, 5)
            )

            base["merged_count"] = len(subgroup)
            base["kind"] = "unified"

            # Clean up inter-stage-specific fields
            for field in ["from_stage", "to_stage", "actor"]:
                base.pop(field, None)

            merge_log.append({
                "pair": f"{fs} → {ts}",
                "trigger_class": tc,
                "merged": len(subgroup),
                "phases": phases,
            })

            result.append(base)

    return result, merge_log


# ═══════════════════════════════════════════════════════════════
# RULE 9: Structural Validation
# ═══════════════════════════════════════════════════════════════

def validate(states: list[dict], transitions: list[dict]) -> dict:
    """Run structural validations AND flag candidates for LLM semantic analysis."""
    state_names = {s["state_name"] for s in states}
    state_map = {s["state_name"]: s for s in states}
    initial_states = {s["state_name"] for s in states if s.get("is_initial")}
    final_states = {s["state_name"] for s in states if s.get("is_final")}

    # Incoming = transitions from OTHER states (self-loops don't count)
    has_incoming_real = {t["to_state"] for t in transitions if t["from_state"] != t["to_state"]}
    has_outgoing = {t["from_state"] for t in transitions}

    # --- Structural checks ---

    # Check 1: Orphaned states (no real incoming, not initial)
    orphaned = state_names - has_incoming_real - initial_states

    # Check 2: Dead-end non-terminals (no outgoing, not final)
    dead_ends = (state_names - has_outgoing) - final_states

    # Check 3: Referential integrity
    ref_errors = []
    for t in transitions:
        if t["from_state"] not in state_names:
            ref_errors.append(f"Unknown from_state: {t['from_state']}")
        if t["to_state"] not in state_names:
            ref_errors.append(f"Unknown to_state: {t['to_state']}")

    # Check 4: Duplicate state names
    name_counts = defaultdict(int)
    for s in states:
        name_counts[s["state_name"]] += 1
    duplicates = {n: c for n, c in name_counts.items() if c > 1}

    # Check 5: Remaining identity inter-stage transitions
    identity_interstage = [
        t for t in transitions
        if t.get("kind") == "inter_stage" and t["from_state"] == t["to_state"]
    ]

    # --- LLM semantic candidates (heuristic detection) ---

    # Candidate meta-states: disjunctive invariant + only self-loops as "incoming"
    candidate_meta_states = []
    for s in states:
        inv = s.get("invariant_cond") or ""
        name = s["state_name"]
        if " | " in inv:
            # Check: does it only have self-loop incoming?
            real_incoming = [t for t in transitions
                            if t["to_state"] == name and t["from_state"] != name]
            if len(real_incoming) == 0:
                candidate_meta_states.append({
                    "state": name,
                    "invariant": inv,
                    "reason": "Disjunctive invariant with no real incoming transitions (only self-loops). "
                              "Likely an abstract grouping of concrete terminal states.",
                })

    # Candidate absence states: name/invariant suggests non-existence
    candidate_absence_states = []
    for s in states:
        name = s["state_name"]
        inv = (s.get("invariant_cond") or "").lower()
        if ("nonexistent" in name.lower() or "not_exist" in name.lower()
                or "exists = false" in inv or "exists=false" in inv):
            candidate_absence_states.append({
                "state": name,
                "invariant": s.get("invariant_cond"),
                "reason": "Models the absence of a resource as a state. "
                          "Consider removing and connecting parent states directly to the 'active' state.",
            })

    # Candidate non-protocol states: no incoming from protocol operations,
    # isolated sub-graphs with no connection to core task/auth/discovery flow
    core_states = {
        "AGENT_CARD_RECEIVED", "TRANSPORT_SELECTED", "TRANSPORT_CONNECTED",
        "CLIENT_AUTH_DISCOVERY", "CLIENT_CREDENTIAL_ACQUISITION",
        "TLS_HANDSHAKE_INITIATED", "SERVER_IDENTITY_VERIFIED",
        "CLIENT_CREDENTIAL_TRANSMISSION", "SERVER_VALIDATING_REQUEST",
        "authenticated", "AUTHENTICATION_FAILED", "REQUEST_AUTHORIZED",
        "TASK_STATE_NONEXISTENT", "TASK_STATE_SUBMITTED", "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED", "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED",
        "STREAM_STATE_OPEN", "STREAM_ACTIVE", "STREAM_CLOSED",
    }
    # Find states reachable from core states via BFS
    reachable = set(core_states & state_names)
    frontier = list(reachable)
    while frontier:
        current = frontier.pop()
        for t in transitions:
            if t["from_state"] == current and t["to_state"] not in reachable:
                reachable.add(t["to_state"])
                frontier.append(t["to_state"])
            if t["to_state"] == current and t["from_state"] not in reachable:
                reachable.add(t["from_state"])
                frontier.append(t["from_state"])

    candidate_non_protocol = []
    for s in states:
        name = s["state_name"]
        if name not in reachable:
            candidate_non_protocol.append({
                "state": name,
                "reason": "Not reachable from any core protocol state via any transition path. "
                          "May represent a deployment/administrative concern rather than protocol behavior.",
            })

    all_passed = (
        len(orphaned) == 0
        and len(dead_ends) == 0
        and len(ref_errors) == 0
        and len(duplicates) == 0
        and len(identity_interstage) == 0
    )

    return {
        "orphaned_states": sorted(orphaned),
        "dead_end_non_terminals": sorted(dead_ends),
        "reference_errors": ref_errors,
        "duplicate_state_names": duplicates,
        "identity_interstage_remaining": len(identity_interstage),
        "all_passed": all_passed,
        "llm_candidates": {
            "meta_states": candidate_meta_states,
            "absence_states": candidate_absence_states,
            "non_protocol_states": candidate_non_protocol,
            "total_candidates": (len(candidate_meta_states)
                                 + len(candidate_absence_states)
                                 + len(candidate_non_protocol)),
        },
    }


# ═══════════════════════════════════════════════════════════════
# Clean up transition phase fields
# ═══════════════════════════════════════════════════════════════

def normalize_transition_phases(transitions: list[dict]):
    """Ensure all transitions have protocol_phases array, not singular."""
    for t in transitions:
        if "protocol_phases" not in t and "protocol_phase" in t:
            t["protocol_phases"] = [t["protocol_phase"]]
        t.pop("protocol_phase", None)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main(input_path: str, output_path: str):
    with open(input_path) as f:
        data = json.load(f)

    original_state_count = len(data["states"])
    original_transition_count = len(data["transitions"])

    print(f"Input: {original_state_count} states, {original_transition_count} transitions")

    # Rule 1: Canonical names
    name_fixes = apply_name_normalization(data)
    print(f"Rule 1 — Name fixes applied: {name_fixes if name_fixes else 'none needed'}")

    # Rule 2: Merge duplicate states
    data["states"], state_merge_info = merge_duplicate_states(data)
    print(f"Rule 2 — States merged: {len(data['states'])} "
          f"({original_state_count - len(data['states'])} eliminated)")
    for name, info in state_merge_info.items():
        print(f"         {name}: {info['copies_merged']} copies → 1 ({info['phases']})")

    # Rule 3: Remove identity transitions
    data["transitions"], removed_identity = remove_identity_transitions(data["transitions"])
    print(f"Rule 3 — Identity transitions removed: {len(removed_identity)}")

    # Rule 4: Merge duplicate transitions
    data["transitions"], transition_merge_log = merge_duplicate_transitions(data["transitions"])
    print(f"Rule 4 — Transition groups merged: {len(transition_merge_log)}")
    for entry in transition_merge_log:
        print(f"         {entry['pair']} [{entry['trigger_class']}]: "
              f"{entry['merged']} → 1")

    # Normalize phase fields on transitions
    normalize_transition_phases(data["transitions"])

    # Rule 9: Validate
    validation = validate(data["states"], data["transitions"])
    print(f"\nRule 9 — Validation:")
    print(f"  Orphaned states:         {validation['orphaned_states'] or '✓ none'}")
    print(f"  Dead-end non-terminals:  {validation['dead_end_non_terminals'] or '✓ none'}")
    print(f"  Reference errors:        {validation['reference_errors'] or '✓ none'}")
    print(f"  Duplicate names:         {validation['duplicate_state_names'] or '✓ none'}")
    print(f"  All passed:              {validation['all_passed']}")

    # LLM candidates
    llm = validation["llm_candidates"]
    if llm["total_candidates"] > 0:
        print(f"\n  LLM semantic candidates detected ({llm['total_candidates']} total):")
        for c in llm["meta_states"]:
            print(f"    [meta-state]      {c['state']} — {c['reason'][:80]}")
        for c in llm["absence_states"]:
            print(f"    [absence-state]   {c['state']} — {c['reason'][:80]}")
        for c in llm["non_protocol_states"]:
            print(f"    [non-protocol]    {c['state']} — {c['reason'][:80]}")

    # Build output
    data["schema_version"] = "deterministic_pass_v1"
    data["state_count"] = len(data["states"])
    data["transition_count"] = len(data["transitions"])
    data["deterministic_summary"] = {
        "original_state_count": original_state_count,
        "final_state_count": len(data["states"]),
        "states_eliminated": original_state_count - len(data["states"]),
        "original_transition_count": original_transition_count,
        "final_transition_count": len(data["transitions"]),
        "transitions_eliminated": original_transition_count - len(data["transitions"]),
        "name_fixes_applied": name_fixes,
        "state_merges": state_merge_info,
        "identity_transitions_removed": len(removed_identity),
        "transition_groups_merged": transition_merge_log,
    }
    data["validation"] = validation

    # Flag issues for LLM pass
    needs_llm = not validation["all_passed"] or llm["total_candidates"] > 0
    if needs_llm:
        data["requires_llm_analysis"] = True
        data["llm_analysis_needed"] = {
            "orphaned_states": validation["orphaned_states"],
            "dead_end_non_terminals": validation["dead_end_non_terminals"],
            "candidates": llm,
            "instructions": (
                "Structural issues and/or semantic candidates remain. "
                "Apply Rules 5-8 from the LLM prompt to resolve them."
            ),
        }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nOutput: {output_path}")
    print(f"  {len(data['states'])} states, {len(data['transitions'])} transitions")

    if needs_llm:
        print(f"\n⚠ Requires LLM semantic analysis (Rules 5-8):")
        if validation["orphaned_states"]:
            print(f"  - {len(validation['orphaned_states'])} orphaned states")
        if validation["dead_end_non_terminals"]:
            print(f"  - {len(validation['dead_end_non_terminals'])} dead-end non-terminals")
        if llm["total_candidates"]:
            print(f"  - {llm['total_candidates']} semantic candidates "
                  f"({len(llm['meta_states'])} meta, "
                  f"{len(llm['absence_states'])} absence, "
                  f"{len(llm['non_protocol_states'])} non-protocol)")
    else:
        print(f"\n✓ Fully clean — no LLM pass needed")

    return not needs_llm


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input.json> <output.json>")
        sys.exit(1)
    success = main(sys.argv[1], sys.argv[2])
    sys.exit(0 if success else 1)
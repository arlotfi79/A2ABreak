#!/usr/bin/env python3
"""Validate an Alloy `exec` receipt.json against an expectation map.

Usage: validate_alloy.py <receipt.json> "Cmd=verdict Cmd=verdict ..."
verdict in {holds, counterexample, instance, no-instance}.
Exits non-zero (and prints the mismatch) if any expected command is missing,
unparseable, or has an unexpected verdict. On success prints one line per
command: "<Cmd>  [<type>]  -> <verdict>".
"""
import json, sys

def verdict(cmd):
    typ = cmd["type"]
    has_instance = any(s.get("instances") for s in cmd.get("solution", []))
    if typ == "run":
        return "instance" if has_instance else "no-instance"
    # check: an instance is a counterexample
    return "counterexample" if has_instance else "holds"

def main():
    receipt, expect_str = sys.argv[1], sys.argv[2]
    data = json.load(open(receipt))            # raises -> non-zero on parse failure
    cmds = data["commands"]
    expect = dict(p.split("=", 1) for p in expect_str.split())
    errors, lines = [], []

    # Set equality: the receipt's command set MUST equal the expected set, so an
    # extra unreviewed check/run in the .als cannot silently escape validation.
    missing = sorted(set(expect) - set(cmds))
    unexpected = sorted(set(cmds) - set(expect))
    for m in missing:
        errors.append(f"missing command: {m}")
    for u in unexpected:
        errors.append(f"UNEXPECTED command not in expectation map: {u} (verdict {verdict(cmds[u])})")

    for name, exp in expect.items():
        if name not in cmds:
            continue
        got = verdict(cmds[name])
        mark = "" if got == exp else f"  != expected {exp}"
        lines.append(f"{name:30s}[{cmds[name]['type']:5s}] -> {got}{mark}")
        if got != exp:
            errors.append(f"{name}: expected {exp}, got {got}")
    print("\n".join(lines))
    if errors:
        print("MISMATCH: " + "; ".join(errors), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

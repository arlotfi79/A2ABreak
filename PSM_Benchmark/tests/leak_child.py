#!/usr/bin/env python3
"""
Child process used by the redaction test: emits an absolute path through every
channel a child can write to — a logging record, a print(), and an uncaught
exception traceback — the same way a run-many child would.
"""
import logging
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
for path in (str(PKG.parent), str(PKG)):
    if path not in sys.path:
        sys.path.insert(0, path)

import anonymity  # noqa: E402

anonymity.install_stream_redaction()
logging.basicConfig(level=logging.INFO, format="%(message)s")
anonymity.install_log_redaction()

target = PKG / "protocols" / "MQTT" / "output" / "177c362fdd" / "stage_a12" / "final.json"
logging.getLogger("runner").info("Merge is current — reusing %s (no LLM calls)", target)
logging.getLogger("stage_a1").info("  Output:  %s", str(target))
print(f"      Sections dir: {target.parent}")      # the parent's print() banners
print(f"      Costs:   {target}")
raise RuntimeError(f"boom while reading {target}")  # traceback frames carry paths too

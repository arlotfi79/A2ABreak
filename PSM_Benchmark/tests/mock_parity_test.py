#!/usr/bin/env python3
"""
Deterministic, offline parity harness for the concurrency layer.

No API calls: ModelInterface.call_json and verify_stage_a.LLMClient.call are replaced
with canned, content-addressed responses, and every request payload is captured. The
POP3 6-section smoke config is then driven twice — sequentially (workers=1) and in a
pool (workers=6, with completion order deliberately reversed) — and the two runs are
compared artifact by artifact.

Covers the parity assertions C1-C8 and the regression tests D1-D5.

Note on wording: workers=1 and workers=N are payload- and ordering-identical — the
same requests in the same canonical order producing the same artifacts. They are not
byte-identical runs: logs, timestamps and manifest fields legitimately differ.

    python3 PSM_Benchmark/tests/mock_parity_test.py   (from the repository root)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

# Offline before anything imports litellm: the local cost map stops it fetching
# pricing data at import time, and no test here may touch the network.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_DONT_SHOW_FEEDBACK_BOX", "True")
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.dont_write_bytecode = True

THIS = Path(__file__).resolve()
PKG = THIS.parent.parent
REPO = PKG.parent
for path in (str(REPO), str(PKG)):
    if path not in sys.path:
        sys.path.insert(0, path)

import concurrency                                   # noqa: E402
import model_interface                               # noqa: E402
import runner                                        # noqa: E402
import stage_a1                                      # noqa: E402
import stage_a2                                      # noqa: E402
import verify_stage_a                                # noqa: E402

PROTOCOL = "POP3"
MAX_SECTIONS = 6
# A fresh directory per invocation, owned by this run alone: it never reads, reuses
# or deletes a pre-existing workdir, so concurrent or read-only runs cannot collide.
_SCRATCH_ROOT = Path(os.environ.get("MOCK_PARITY_DIR", str(THIS.parent / "_scratch")))
WORKDIR = _SCRATCH_ROOT / f"run_{os.getpid()}_{int(time.time())}"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(passed), detail))
    print(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Canned model
# ---------------------------------------------------------------------------

class MockModel:
    """
    Deterministic canned responses keyed by prompt content, plus a request log.

    reverse_delay: makes later-submitted sections finish first, so any dependence
    on completion order shows up as a difference against the sequential baseline.
    """

    def __init__(self, *, reverse_delay: bool = False, fail_once_on: str | None = None,
                 rate_limit_on: set[str] | None = None, fatal_on: str | None = None):
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.reverse_delay = reverse_delay
        self.fail_once_on = fail_once_on
        self.failed_already: set[str] = set()
        self.rate_limit_on = rate_limit_on or set()
        self.rate_limited_already: set[str] = set()
        self.fatal_on = fatal_on
        self.call_index = 0
        self.completions: list[str] = []      # order responses were RETURNED in

    def _record(self, kind: str, key: str, payload: dict) -> int:
        with self.lock:
            self.call_index += 1
            self.requests.append({"kind": kind, "key": key, "seq": self.call_index,
                                  **payload})
            return self.call_index

    def _pace(self, ordinal: int) -> None:
        if self.reverse_delay:
            time.sleep(max(0.0, 0.30 - 0.04 * ordinal))

    def _complete(self, key: str) -> None:
        with self.lock:
            self.completions.append(key)

    # -- Stage A ----------------------------------------------------------------
    def call_json(self, inner_self, prompt: str, stage: str, **kwargs):
        section_id = _section_id_from_prompt(prompt)
        ordinal = _ordinal(section_id)
        self._record("stage_a", f"{stage}:{section_id}", {
            "model": inner_self.model_name,
            "stage": stage,
            "effort": inner_self._get_effort(stage),
            "max_tokens": inner_self.max_tokens,
            "timeout": inner_self.config["model"].get("request_timeout_seconds"),
            "system_sha": runner.sha256_text(inner_self.get_system_prompt(stage)),
            "prompt_sha": runner.sha256_text(prompt),
            "prompt_len": len(prompt),
        })
        if self.fatal_on and section_id == self.fatal_on:
            raise RuntimeError(
                'litellm.BadRequestError: AnthropicException - {"type":"error","error":'
                '{"type":"invalid_request_error","message":"You have reached your '
                'specified API usage limits. You will regain access on 2026-09-01 at '
                '00:00 UTC."}}'
            )
        if self.fail_once_on and section_id == self.fail_once_on:
            with self.lock:
                first = section_id not in self.failed_already
                self.failed_already.add(section_id)
            if first:
                raise RuntimeError("litellm.InternalServerError: [Errno 54] "
                                   "Connection reset by peer")
        if section_id in self.rate_limit_on:
            with self.lock:
                first = section_id not in self.rate_limited_already
                self.rate_limited_already.add(section_id)
            if first:
                import litellm
                raise litellm.RateLimitError(
                    message="rate_limit_error", llm_provider="anthropic",
                    model=inner_self.model_name,
                )
        self._pace(ordinal)
        self._complete(f"{stage}:{section_id}")
        statements = _canned_statements(stage, section_id)
        result = model_interface.CallResult(
            content=json.dumps(statements), model=inner_self.model_name, stage=stage,
            input_tokens=100 + ordinal, output_tokens=50 + ordinal,
            total_tokens=150 + 2 * ordinal, cost_usd=round(0.001 * (ordinal + 1), 6),
            elapsed_seconds=0.01,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(0)),
        )
        return statements, result

    # -- verify -----------------------------------------------------------------
    def call(self, inner_self, prompt: str):
        statement_id = _statement_id_from_prompt(prompt)
        ordinal = _ordinal(statement_id)
        self._record("verify", statement_id, {
            "model": inner_self.model,
            "max_tokens": inner_self.max_tokens,
            "prompt_sha": runner.sha256_text(prompt),
            "prompt_len": len(prompt),
        })
        self._pace(ordinal)
        verdict = {"statement_id": statement_id, "status": "verified",
                   "existence": "found", "location_in_text": "mock",
                   "corrections": {}}
        cost = {"model": inner_self.model, "input_tokens": 10, "output_tokens": 5,
                "total_tokens": 15, "cost_usd": 0.0001, "elapsed_seconds": 0.01,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(0))}
        return verdict, cost


def _section_id_from_prompt(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("Section ID:"):
            return line.split(":", 1)[1].strip()
    return "S000"


def _statement_id_from_prompt(prompt: str) -> str:
    marker = '"statement_id": "'
    index = prompt.rfind(marker)
    if index < 0:
        return "UNKNOWN"
    return prompt[index + len(marker):].split('"', 1)[0]


def _ordinal(identifier: str) -> int:
    digits = "".join(ch for ch in identifier if ch.isdigit())
    return int(digits) if digits else 0


def _canned_statements(stage: str, section_id: str) -> list[dict]:
    """Two statements per section: one FSM_STATE, one transition."""
    if stage == "stage_a1":
        return [
            {"statement_id": "PLACEHOLDER", "normative_level": "DEFINITION",
             "category": "FSM_STATE", "subject": "server", "stage": "pop3_fsm",
             "description": f"State defined in {section_id}.", "is_inferred": False,
             "confidence": 1.0, "source_section": section_id,
             "state_name": f"State{_ordinal(section_id)}", "enum_name": "states",
             "semantic_type": "active", "is_initial": False, "is_final": False,
             "invariant_cond": None},
            {"statement_id": "PLACEHOLDER", "normative_level": "DEFINITION",
             "category": "IMPLICIT_TRANSITION", "subject": "server", "stage": "pop3_fsm",
             "description": f"Transition described in {section_id}.",
             "is_inferred": False, "confidence": 0.0, "source_section": section_id,
             "actors": ["client"], "from_state": f"State{_ordinal(section_id)}",
             "to_state": f"State{_ordinal(section_id) + 1}", "trigger": "receive CMD",
             "pre_cond": None, "post_cond": None, "actions": ["reply +OK"],
             "modality": "unspecified"},
        ]
    return [
        {"statement_id": "PLACEHOLDER", "normative_level": "MUST",
         "category": "BEHAVIORAL", "subject": "server", "stage": "pop3_fsm",
         "description": f"The server MUST behave as described in {section_id}.",
         "is_inferred": False, "confidence": 1.0, "source_section": section_id,
         "actors": ["server"], "from_state": f"State{_ordinal(section_id)}",
         "to_state": None, "trigger": "receive CMD", "pre_cond": None,
         "post_cond": None, "actions": ["reply +OK"], "modality": "must"},
    ]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _MockUsage:
    prompt_tokens = 100
    completion_tokens = 50
    total_tokens = 150


class _MockResponse:
    """Minimal stand-in for a litellm ModelResponse."""

    def __init__(self, content: str):
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message, "finish_reason": "stop"})()]
        self.usage = _MockUsage()


def run_once_raw(*, tag: str, workers: int) -> dict:
    """Like run_once but with the real ModelInterface.call path in place."""
    return run_once(tag=tag, workers=workers, mock=None)


def install(mock: MockModel):
    """Patch both model entry points; return a restore callable."""
    original_call_json = model_interface.ModelInterface.call_json
    original_verify_call = verify_stage_a.LLMClient.call
    original_llm_init = verify_stage_a.LLMClient.__init__

    def call_json(self, prompt, stage, **kwargs):
        return mock.call_json(self, prompt, stage, **kwargs)

    def verify_call(self, prompt):
        return mock.call(self, prompt)

    def llm_init(self, api_key, model, max_tokens=16000, max_retries=5, retry_wait=60):
        self.model, self.max_tokens = model, max_tokens
        self.max_retries, self.retry_wait = max_retries, retry_wait

    model_interface.ModelInterface.call_json = call_json
    verify_stage_a.LLMClient.call = verify_call
    verify_stage_a.LLMClient.__init__ = llm_init

    def restore():
        model_interface.ModelInterface.call_json = original_call_json
        verify_stage_a.LLMClient.call = original_verify_call
        verify_stage_a.LLMClient.__init__ = original_llm_init
    return restore


def prepare_namespace(tag: str) -> tuple[Path, Path]:
    """A private config + output namespace so no real artifact is touched."""
    import yaml

    base = WORKDIR / tag
    if base.exists():
        # Only ever inside this invocation's own directory, which nothing else uses.
        if WORKDIR not in base.parents:
            raise RuntimeError(f"refusing to remove {base}: outside {WORKDIR}")
        shutil.rmtree(base)
    base.mkdir(parents=True)
    source_config = runner.config_path_for(PROTOCOL)
    cfg = yaml.safe_load(source_config.read_text(encoding="utf-8"))
    cfg["output"] = {"base_dir": str(base), "sections_dir": str(base / "sections"),
                     "logs_dir": str(base / "logs")}
    # Keep the parent's retry LOOP intact but make its wait cheap, so C7 exercises
    # real backoff without a 60s sleep per injected 429.
    cfg["model"]["rate_limit_retry_wait_seconds"] = 1
    cfg["model"]["section_delay_seconds"] = 0
    config_path = base / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False, width=100000),
                           encoding="utf-8")
    return config_path, base


def stage_a_snapshot(base: Path) -> dict:
    out = {}
    for stage in ("stage_a1", "stage_a2"):
        sections_dir = base / stage / "sections"
        files = sorted(p.name for p in sections_dir.glob("*.json"))
        final = json.loads((base / stage / "final.json").read_text())
        out[stage] = {
            "files": files,
            "cost_files": sorted(p.name for p in (base / stage / "cost").glob("*.json")),
            "statement_ids": [s["statement_id"] for s in final["statements"]],
            "meta": final["meta"],
        }
    return out


def run_once(*, tag: str, workers: int, mock: MockModel) -> dict:
    config_path, base = prepare_namespace(tag)
    import yaml
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    sections = runner.expected_sections(cfg, MAX_SECTIONS)
    gate = concurrency.FatalGate(runner._is_fatal_api_error)
    bench = cfg["benchmark"]

    with runner.patches.stage_a_user_templates(
        bench["a1_user_template"], bench["a2_user_template"]
    ), runner.patches.limited_sections(MAX_SECTIONS):
        for label, module, run_fn in (("stage_a1", stage_a1, stage_a1.run_stage_a1),
                                      ("stage_a2", stage_a2, stage_a2.run_stage_a2)):
            for attempt in range(3):
                if workers > 1:
                    pool = concurrency.parallel_stage_a_sections(
                        stage_module=module, config_path=config_path, sections=sections,
                        workers=workers, logger=runner.LOGGER, gate=gate)
                    if pool["cache_complete"]:
                        with concurrency.no_inter_section_sleep(module):
                            run_fn(config_path=str(config_path), resume=True)
                        break
                else:
                    with concurrency.no_inter_section_sleep(module):
                        run_fn(config_path=str(config_path), resume=True)
                    if not concurrency.stage_a_artifact_problems(
                        stage_module=module, sections=sections,
                        sections_dir=base / label / "sections",
                        cost_dir=base / label / "cost",
                    ):
                        break
    return {"config_path": config_path, "base": base, "sections": sections}


def run_verify_once(*, tag: str, workers: int, merged_source: Path,
                    reuse: bool = False, full_loop: bool = False) -> dict:
    """Drive the verify pool + the parent's aggregation, then read back the totals."""
    import yaml

    if reuse:
        base = WORKDIR / tag
        config_path = base / "config.yaml"
    else:
        config_path, base = prepare_namespace(tag)
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    merged_path = base / "stage_a12" / "final.json"
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    if not merged_path.is_file():
        shutil.copyfile(merged_source, merged_path)
    verify_dir = base / "verify_stage_a12"
    gate = concurrency.FatalGate(runner._is_fatal_api_error)

    if workers > 1:
        concurrency.parallel_verify_sections(
            config_path=config_path, merged_path=merged_path,
            spec_url=cfg["fetcher"]["url"], merge_threshold=0, api_key="mock",
            output_path=verify_dir / "final.json", workers=workers,
            logger=runner.LOGGER, gate=gate)

    if full_loop:
        gate = concurrency.FatalGate(runner._is_fatal_api_error)
        outcome = {"completed": False, "error": "", "unverifiable": [], "attempts": [],
                   "verify_dir": str(verify_dir)}
        try:
            _, retry = runner.run_verify(
                cfg=cfg, config_path=config_path, output_base=base,
                merged_path=merged_path, api_key="mock", resume=True,
                max_sections=None, passes=2, workers=1, gate=gate)
            outcome["completed"] = True
            outcome["unverifiable"] = retry.get("unverifiable_statements") or []
            outcome["attempts"] = retry.get("attempts") or []
        except Exception as exc:                              # noqa: BLE001
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        return outcome

    with concurrency.verify_cost_replay(verify_dir / "cost"):
        verify_stage_a.verify(
            input_path=str(merged_path), output_path=str(verify_dir / "final.json"),
            api_key="mock", model=cfg["model"]["name"],
            max_tokens=int(cfg["model"]["max_tokens"]), max_retries=1, retry_wait=0,
            section_delay=0.0, resume=True, dry_run=False,
            spec_url=cfg["fetcher"]["url"], merge_threshold=0, config_path=config_path)

    report = json.loads((verify_dir / "final_report.json").read_text())["summary"]
    cost_summary = json.loads(
        (verify_dir / "verify_stage_a12_cost.json").read_text())
    per_call = runner.aggregate_cost_dir(verify_dir / "cost")
    final = json.loads((verify_dir / "final.json").read_text())
    try:
        runner.assert_verify_totals_agree(verify_dir)
        assertion_ok, detail = True, ""
    except runner.RunError as exc:
        assertion_ok, detail = False, str(exc)
    return {
        "report_calls": int(report.get("llm_calls", 0)),
        "report_tokens": int(report.get("total_tokens", 0)),
        "summary_calls": int(cost_summary.get("llm_calls", 0)),
        "per_call_calls": per_call["llm_calls"],
        "per_call_tokens": per_call["total_tokens"],
        "statement_ids": [s["statement_id"] for s in final["statements"]],
        "assertion_ok": assertion_ok,
        "assertion_detail": detail,
    }


def assert_offline() -> None:
    """Fail loudly if any test tries to open a socket."""
    import socket

    def refuse(*args, **kwargs):                         # noqa: ANN002, ANN003
        raise AssertionError("network access attempted in an offline harness")

    socket.create_connection = refuse
    socket.socket.connect = refuse                       # type: ignore[method-assign]


def main() -> int:
    assert_offline()
    merged_source = next((runner.THIS_DIR / "protocols" / PROTOCOL / "output").glob("*/stage_a12/final.json"))
    print("=" * 72)
    print("Mocked concurrency parity harness — no API calls")
    print("=" * 72)

    # --- C1/C2/C3: sequential vs reversed-order parallel -----------------------
    seq_mock = MockModel()
    restore = install(seq_mock)
    try:
        seq = run_once(tag="sequential", workers=1, mock=seq_mock)
    finally:
        restore()

    par_mock = MockModel(reverse_delay=True)
    restore = install(par_mock)
    try:
        par = run_once(tag="parallel", workers=6, mock=par_mock)
    finally:
        restore()

    seq_requests = {f"{r['kind']}|{r['key']}": r for r in seq_mock.requests}
    par_requests = {f"{r['kind']}|{r['key']}": r for r in par_mock.requests}
    check("C1 same set of requests issued", set(seq_requests) == set(par_requests),
          f"{len(seq_requests)} vs {len(par_requests)}")
    fields = ("model", "stage", "effort", "max_tokens", "timeout", "system_sha",
              "prompt_sha", "prompt_len")
    mismatched = [
        key for key in seq_requests
        if any(seq_requests[key].get(f) != par_requests[key].get(f) for f in fields)
    ]
    check("C1 identical payloads (model/effort/tokens/timeout/prompt bytes)",
          not mismatched, f"{len(mismatched)} differing")

    seq_snap, par_snap = stage_a_snapshot(seq["base"]), stage_a_snapshot(par["base"])
    check("C2 identical artifact filename sets",
          all(seq_snap[s]["files"] == par_snap[s]["files"] for s in seq_snap))
    check("C2 every section has a cost record",
          all(len(seq_snap[s]["cost_files"]) == len(seq_snap[s]["files"])
              for s in seq_snap))
    check("C3 final.json statement IDs and order identical",
          all(seq_snap[s]["statement_ids"] == par_snap[s]["statement_ids"]
              for s in seq_snap))
    check("C3 stage meta identical",
          all(seq_snap[s]["meta"] == par_snap[s]["meta"] for s in seq_snap))
    seq_done = [k for k in seq_mock.completions if k.startswith("stage_a1:")]
    par_done = [k for k in par_mock.completions if k.startswith("stage_a1:")]
    check("C3 completion order really was reversed",
          seq_done != par_done and sorted(seq_done) == sorted(par_done),
          f"sequential {[k.split(':')[1] for k in seq_done]} vs parallel "
          f"{[k.split(':')[1] for k in par_done]}")

    # --- C4: verify accounting agrees across report / cost summary / per-call --
    if merged_source.is_file():
        verify_results = {}
        for tag, workers in (("verify_seq", 1), ("verify_par", 6)):
            vm = MockModel(reverse_delay=(workers > 1))
            restore = install(vm)
            try:
                verify_results[tag] = run_verify_once(tag=tag, workers=workers,
                                                      merged_source=merged_source)
            finally:
                restore()
        seq_v, par_v = verify_results["verify_seq"], verify_results["verify_par"]
        check("C4 verify report calls == cost summary == per-call files (parallel)",
              par_v["report_calls"] == par_v["summary_calls"] == par_v["per_call_calls"],
              f"report {par_v['report_calls']} / summary {par_v['summary_calls']} / "
              f"files {par_v['per_call_calls']}")
        check("C4 verify token totals agree (parallel)",
              par_v["report_tokens"] == par_v["per_call_tokens"],
              f"{par_v['report_tokens']} vs {par_v['per_call_tokens']}")
        check("C4 sequential and parallel verify totals identical",
              (seq_v["report_calls"], seq_v["report_tokens"]) ==
              (par_v["report_calls"], par_v["report_tokens"]),
              f"{seq_v['report_calls']}/{seq_v['report_tokens']} vs "
              f"{par_v['report_calls']}/{par_v['report_tokens']}")
        check("C4 verified statement IDs and order identical",
              seq_v["statement_ids"] == par_v["statement_ids"])
        check("C4 runner assertion passes on the pooled run",
              par_v["assertion_ok"], par_v.get("assertion_detail", ""))
    else:
        check("C4 verify accounting", False, f"missing fixture {merged_source}")

    # --- C5: concurrent JSONL appends ----------------------------------------
    lines, terminal = [], {}
    for log in sorted((par["base"] / "stage_a1" / "logs").glob("*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            lines.append(line)
            entry = json.loads(line)
            if entry.get("event") in {"section_done", "section_error"}:
                terminal[entry.get("section_id")] = terminal.get(entry.get("section_id"), 0) + 1
    check("C5 every concurrent JSONL line parses", all(json.loads(l) for l in lines),
          f"{len(lines)} lines")
    check("C5 exactly one terminal entry per section",
          bool(terminal) and set(terminal.values()) == {1}, str(terminal))

    # --- C6: one transient failure -> only that section retried ---------------
    fail_mock = MockModel(fail_once_on="S003")
    restore = install(fail_mock)
    try:
        run_once(tag="transient", workers=6, mock=fail_mock)
    finally:
        restore()
    a1_calls = [r["key"] for r in fail_mock.requests if r["key"].startswith("stage_a1:")]
    retried = [k for k in set(a1_calls) if a1_calls.count(k) > 1]
    check("C6 only the failed section is retried", retried == ["stage_a1:S003"],
          f"retried={retried}")
    check("C6 no in-aggregation re-call storm", len(a1_calls) == len(set(a1_calls)) + 1,
          f"{len(a1_calls)} calls for {len(set(a1_calls))} sections")

    # --- C7: rate limits are counted and actually backed off ------------------
    # Patched at the litellm boundary, NOT at call_json: the retry loop we need to
    # exercise lives inside ModelInterface.call (model_interface.py:177-190), so a
    # mock that replaces call_json would bypass the very thing under test.
    counter = concurrency.RateLimitCounter()
    slept: list[float] = []
    rl_state = {"seen": set(), "calls": 0}
    original_completion = model_interface.litellm.completion
    original_sleep = model_interface.time.sleep
    lock = threading.Lock()

    def fake_completion(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        section_id = _section_id_from_prompt(prompt)
        with lock:
            rl_state["calls"] += 1
            first = section_id not in rl_state["seen"]
            rl_state["seen"].add(section_id)
        if first and section_id in {"S002", "S004"}:
            import litellm
            raise litellm.RateLimitError(message="rate_limit_error",
                                         llm_provider="anthropic",
                                         model=kwargs.get("model", "mock"))
        stage = "stage_a1" if "structural" in prompt.lower() else "stage_a2"
        return _MockResponse(json.dumps(_canned_statements(stage, section_id)))

    model_interface.litellm.completion = fake_completion
    model_interface.time.sleep = lambda seconds: slept.append(seconds)
    try:
        with counter.attached():
            run_once_raw(tag="ratelimit", workers=6)
    finally:
        model_interface.litellm.completion = original_completion
        model_interface.time.sleep = original_sleep
    check("C7 rate_limit_retries counted exactly", counter.count == 2,
          f"counted {counter.count}, injected 2")
    check("C7 backoff actually executed", len(slept) == 2 and all(x > 0 for x in slept),
          f"sleeps={slept}")

    # --- C8: fatal error cancels the pool -------------------------------------
    # Injected at the litellm boundary, where a real quota failure surfaces — that is
    # also where the gate now lives, precisely so retries cannot slip past it.
    import litellm as _lit
    issued_before_abort = {"count": 0}
    original_completion = _lit.completion
    fatal_lock = threading.Lock()

    def fatal_completion(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        section_id = _section_id_from_prompt(prompt)
        with fatal_lock:
            issued_before_abort["count"] += 1
        if section_id == "S002":
            raise RuntimeError(
                'litellm.BadRequestError: AnthropicException - {"type":"error","error":'
                '{"type":"invalid_request_error","message":"You have reached your '
                'specified API usage limits. You will regain access on 2026-09-01 at '
                '00:00 UTC."}}')
        time.sleep(0.15)                                  # keep other workers in flight
        stage = "stage_a1" if "structural" in prompt.lower() else "stage_a2"
        return _MockResponse(json.dumps(_canned_statements(stage, section_id)))

    _lit.completion = fatal_completion
    fatal_raised = False
    try:
        run_once_raw(tag="fatal", workers=2)
    except concurrency.PoolFatalError as exc:
        fatal_raised = runner._is_fatal_api_error(exc.detail)
    finally:
        _lit.completion = original_completion
    check("C8 fatal error escapes the parent's broad except", fatal_raised)
    check("C8 pending sections cancelled (not all 6 issued)",
          issued_before_abort["count"] < 6,
          f"{issued_before_abort['count']} requests issued before the abort")

    # === D1-D5: regressions for the round-2 defects ==========================
    import yaml

    # D1 an already-complete cache must return the full result schema
    d1_mock = MockModel()
    restore = install(d1_mock)
    try:
        d1 = run_once(tag="d1_complete", workers=6, mock=d1_mock)
        cfg = yaml.safe_load(Path(d1["config_path"]).read_text())
        gate = concurrency.FatalGate(runner._is_fatal_api_error)
        again = concurrency.parallel_stage_a_sections(
            stage_module=stage_a1, config_path=d1["config_path"],
            sections=d1["sections"], workers=6, logger=runner.LOGGER, gate=gate)
    finally:
        restore()
    check("D1 complete cache returns the full schema",
          again["submitted"] == 0 and again["cache_complete"] is True
          and "unusable_after_pool" in again and "evicted" in again,
          f"keys={sorted(again)}")

    # D2 a malformed cost file must be evicted and the section re-called
    d2_mock = MockModel()
    restore = install(d2_mock)
    try:
        d2 = run_once(tag="d2_malformed", workers=6, mock=d2_mock)
        base = d2["base"]
        victim = d2["sections"][2]
        cost_path = stage_a1.section_cost_path(base / "stage_a1" / "cost", victim)
        cost_path.write_text("[\"not an object\"]", encoding="utf-8")
        before = len([r for r in d2_mock.requests if r["kind"] == "stage_a"])
        gate = concurrency.FatalGate(runner._is_fatal_api_error)
        pool = concurrency.parallel_stage_a_sections(
            stage_module=stage_a1, config_path=d2["config_path"],
            sections=d2["sections"], workers=6, logger=runner.LOGGER, gate=gate)
        after = len([r for r in d2_mock.requests if r["kind"] == "stage_a"])
    finally:
        restore()
    check("D2 malformed cost record detected and evicted",
          pool["submitted"] == 1 and len(pool["evicted"]) == 2,
          f"submitted={pool['submitted']} evicted={pool['evicted']}")
    check("D2 the evicted section is actually re-called", after - before == 1,
          f"{after - before} new request(s)")
    check("D2 cache reported complete again", pool["cache_complete"])

    # D3 once the gate trips, no further completion may begin — retries included
    calls_after_trip = {"count": 0}
    gate = concurrency.FatalGate(runner._is_fatal_api_error)
    import litellm as _litellm
    original_completion = _litellm.completion

    def tripping_completion(**kwargs):
        if gate.tripped:
            calls_after_trip["count"] += 1
        raise RuntimeError(
            'litellm.BadRequestError: {"message":"You have reached your specified '
            'API usage limits. You will regain access on 2026-09-01 at 00:00 UTC."}')

    _litellm.completion = tripping_completion
    try:
        with concurrency.model_call_boundary(gate=gate):
            for _ in range(4):
                try:
                    _litellm.completion(model="m", messages=[{"role": "user",
                                                              "content": "x"}])
                except (concurrency.PoolFatalError, RuntimeError):
                    pass
    finally:
        _litellm.completion = original_completion
    check("D3 gate blocks every attempt after the trip, including retries",
          gate.tripped and calls_after_trip["count"] == 0,
          f"{calls_after_trip['count']} request(s) reached the API after the trip")

    # D6 gate race: workers blocked at a barrier, one trips — none may be admitted
    import litellm as _lit6
    original6 = _lit6.completion
    gate6 = concurrency.FatalGate(runner._is_fatal_api_error)
    WORKERS = 8
    barrier = threading.Barrier(WORKERS)
    admitted_after_trip = {"count": 0}
    admit_lock = threading.Lock()

    def racing_completion(**kwargs):
        # Every worker arrives here together; one raises a fatal error and the rest
        # must be refused admission rather than reaching this point again.
        if gate6.tripped:
            with admit_lock:
                admitted_after_trip["count"] += 1
        if kwargs["messages"][-1]["content"] == "trip":
            raise RuntimeError(
                'litellm.BadRequestError: {"message":"You have reached your specified '
                'API usage limits."}')
        time.sleep(0.05)
        return _MockResponse('{"ok": true}')

    _lit6.completion = racing_completion
    results6 = []

    def racer(index: int) -> None:
        barrier.wait()
        try:
            with_gate = "trip" if index == 0 else f"work {index}"
            _lit6.completion(model="m", messages=[{"role": "user", "content": with_gate}])
            results6.append("ok")
        except concurrency.PoolFatalError:
            results6.append("refused")
        except RuntimeError:
            results6.append("raised")

    try:
        with concurrency.model_call_boundary(gate=gate6):
            threads = [threading.Thread(target=racer, args=(i,)) for i in range(WORKERS)]
            [t.start() for t in threads]
            [t.join() for t in threads]
    finally:
        _lit6.completion = original6
    check("D6 gate trips under contention", gate6.tripped)
    check("D6 no completion admitted after the trip",
          admitted_after_trip["count"] == 0,
          f"{admitted_after_trip['count']} admitted after trip; outcomes={sorted(set(results6))}")

    # D4 the audit records every completion boundary and the manifest can be built
    audit: dict = {}
    original_completion = _litellm.completion

    def audited_completion(**kwargs):
        return _MockResponse('{"ok": true}')

    _litellm.completion = audited_completion
    try:
        with concurrency.model_call_boundary(audit=audit):
            for label in ("stage_a", "merge", "verify", "stage_b"):
                _litellm.completion(model="m", messages=[
                    {"role": "system", "content": f"sys {label}"},
                    {"role": "user", "content": f"user {label}"}])
    finally:
        _litellm.completion = original_completion
    check("D4 audit records one entry per distinct request", len(audit) == 4,
          f"{len(audit)} recorded")
    check("D4 each entry carries system + request hashes",
          all({"system_sha256", "request_sha256", "model"} <= set(v) for v in audit.values()))

    # D5 workers=1 resume must still satisfy the accounting assertion
    if merged_source.is_file():
        vm = MockModel()
        restore = install(vm)
        try:
            first = run_verify_once(tag="d5_first", workers=1,
                                    merged_source=merged_source)
            resumed = run_verify_once(tag="d5_first", workers=1,
                                      merged_source=merged_source, reuse=True)
        finally:
            restore()
        check("D5 workers=1 resume replays costs and passes accounting",
              resumed["assertion_ok"] and resumed["report_calls"] == first["report_calls"],
              resumed.get("assertion_detail") or
              f"{resumed['report_calls']} == {first['report_calls']}")

    # D7 a section that never recovers must hard-fail, even though a stale final.json
    #    from the earlier successful pass is sitting right there
    d7_mock = MockModel()
    restore = install(d7_mock)
    raised = None
    try:
        d7 = run_once(tag="d7_stale", workers=6, mock=d7_mock)
        base, sections = d7["base"], d7["sections"]
        final_before = (base / "stage_a1" / "final.json").read_text()
        victim = sections[1]
        # Corrupt the cost record and make every re-call fail, so the section can never
        # become usable again within the pass budget.
        stage_a1.section_cost_path(base / "stage_a1" / "cost", victim).write_text(
            '{"input_tokens": "not-a-number"}', encoding="utf-8")
        d7_mock.fail_once_on = victim.section_id
        d7_mock.failed_already = set()                   # fail on EVERY attempt

        def always_fail(inner_self, prompt, stage, **kwargs):
            if _section_id_from_prompt(prompt) == victim.section_id:
                raise RuntimeError("litellm.InternalServerError: still broken")
            return d7_mock.call_json(inner_self, prompt, stage, **kwargs)

        model_interface.ModelInterface.call_json = always_fail
        gate = concurrency.FatalGate(runner._is_fatal_api_error)
        try:
            runner._run_stage_a_one(
                label="stage_a1", stage_module=stage_a1,
                runner_fn=stage_a1.run_stage_a1, config_path=d7["config_path"],
                stage_dir=base / "stage_a1", sections=sections, passes=2,
                workers=6, gate=gate)
        except runner.RunError as exc:
            raised = str(exc)
        final_after = (base / "stage_a1" / "final.json").read_text()
    finally:
        restore()
    check("D7 unrecoverable section hard-fails instead of accepting a stale final.json",
          raised is not None and victim.section_id in raised,
          (raised or "no RunError raised")[:110])
    check("D7 the stale final.json is not silently reused as this run's result",
          raised is not None and final_before == final_after,
          "final.json untouched, and the run refused rather than reporting success")

    # D8 a multi-turn Stage B must report one call per turn, not one call total
    import tempfile
    with tempfile.TemporaryDirectory(dir=str(WORKDIR)) as tmp:
        cost_dir = Path(tmp) / "cost"
        cost_dir.mkdir()
        turns = [
            {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
             "cost_usd": 1.0},
            {"input_tokens": 200, "output_tokens": 75, "total_tokens": 275,
             "cost_usd": 2.5},
        ]
        for i, turn in enumerate(turns, start=1):
            (cost_dir / f"phase_turn_{i:02d}.json").write_text(json.dumps(turn))
        (cost_dir / "phase.json").write_text(json.dumps(
            {"input_tokens": 300, "output_tokens": 125, "total_tokens": 425,
             "cost_usd": 3.5}))
        (cost_dir / "phase_turns.json").write_text(json.dumps({"per_turn": turns}))
        agg = runner.aggregate_cost_dir(cost_dir)
    check("D8 two-turn Stage B reports 2 calls", agg["llm_calls"] == 2,
          f"reported {agg['llm_calls']}")
    check("D8 two-turn Stage B cost equals the aggregate, not double it",
          abs(agg["cost_usd"] - 3.5) < 1e-9 and agg["total_tokens"] == 425,
          f"${agg['cost_usd']} / {agg['total_tokens']} tokens")

    # D9 a child process must not leak paths through ANY channel: logging, print(),
    #    or an interpreter traceback — captured exactly the way run-many captures it
    import subprocess as _sp

    child = THIS.parent / "leak_child.py"
    proc = _sp.run([sys.executable, str(child)], stdout=_sp.PIPE, stderr=_sp.STDOUT,
                   text=True, check=False)
    raw = proc.stdout or ""
    home = os.path.expanduser("~")
    check("D9 child leaks nothing through logging, print or traceback",
          "/Users/" not in raw and "/home/" not in raw and home not in raw,
          f"{raw.count('/Users/')} /Users hits, {raw.count(home)} home hits in "
          f"{len(raw.splitlines())} lines")
    check("D9 the child really did emit all three channels",
          "Merge is current" in raw and "Sections dir" in raw and "RuntimeError" in raw,
          "logging + print + traceback all present")
    import anonymity as _anon
    check("D9 parent-side redaction of captured output is idempotent",
          "/Users/" not in _anon.redact_text(raw))

    # D10 run-many must SKIP a protocol that already completed and still run the rest,
    #     rather than refusing the whole batch (which just makes the caller retype it)
    stub = WORKDIR / "stub_main.py"
    stub.write_text("import sys; print('child ran', sys.argv[2:]); raise SystemExit(0)\n",
                    encoding="utf-8")
    namespaces = WORKDIR / "namespaces"
    (namespaces / "DONE").mkdir(parents=True, exist_ok=True)
    (namespaces / "TODO").mkdir(parents=True, exist_ok=True)
    complete_manifest = {
        "run_key": "abc1234567", "cost_usd_total": 9.01,
        "stages": {name: {"llm_calls": 1} for name in
                   concurrency.REQUIRED_TERMINAL_STAGES},
        "eval": {"states": {"F1-Score": 0.489}, "transitions": {"F1-Score": 0.108}},
    }
    complete_manifest["stages"]["export"] = {"states": 37}
    (namespaces / "DONE" / "manifest.json").write_text(json.dumps(complete_manifest))

    summary = concurrency.run_many(
        protocols=["DONE", "TODO"], workers=2, extra_args=[],
        python_executable=sys.executable, main_path=stub,
        log_dir=WORKDIR / "run_many_logs",
        resolve_namespace=lambda p: namespaces / p,
        logger=runner.LOGGER, rerun=False, require_eval=True)
    check("D10 completed protocol is skipped, not a batch-stopper",
          summary["skipped_already_complete"] == ["DONE"],
          f"skipped={summary['skipped_already_complete']}")
    check("D10 the remaining protocol still runs", summary["ok"] == ["TODO"],
          f"ok={summary['ok']} ran={summary['ran']}")
    check("D10 exit code is 0 when everything skipped or succeeded",
          summary["exit_code"] == 0, f"exit_code={summary['exit_code']}")
    skipped_row = next(r for r in summary["results"] if r["protocol"] == "DONE")
    check("D10 the skipped row keeps its run_key, cost and scores",
          skipped_row["run_key"] == "abc1234567" and skipped_row["cost_usd"] == 9.01
          and skipped_row["states_f1"] == 0.489,
          f"{skipped_row['run_key']} ${skipped_row['cost_usd']} F1 {skipped_row['states_f1']}")

    summary_rerun = concurrency.run_many(
        protocols=["DONE"], workers=2, extra_args=[],
        python_executable=sys.executable, main_path=stub,
        log_dir=WORKDIR / "run_many_logs",
        resolve_namespace=lambda p: namespaces / p,
        logger=runner.LOGGER, rerun=True, require_eval=True)
    check("D10 --rerun still forces a completed protocol to run",
          summary_rerun["ran"] == ["DONE"] and not summary_rerun["skipped_already_complete"],
          f"ran={summary_rerun['ran']}")

    # D11 a child exiting 2 must not stop the batch — the sibling still launches
    stub2 = WORKDIR / "stub_exit.py"
    stub2.write_text(
        "import sys\n"
        "proto = sys.argv[sys.argv.index('--protocol') + 1]\n"
        "print('ran', proto)\n"
        "raise SystemExit(2 if proto == 'FIRST' else 0)\n", encoding="utf-8")
    ns2 = WORKDIR / "ns2"
    for name in ("FIRST", "SECOND"):
        (ns2 / name).mkdir(parents=True, exist_ok=True)
    summary2 = concurrency.run_many(
        protocols=["FIRST", "SECOND"], workers=2, extra_args=[],
        python_executable=sys.executable, main_path=stub2,
        log_dir=WORKDIR / "logs2", resolve_namespace=lambda p: ns2 / p,
        logger=runner.LOGGER, rerun=False, require_eval=True)
    statuses = {r["protocol"]: r["status"] for r in summary2["results"]}
    check("D11 a non-fatal child failure does not stop the batch",
          statuses.get("FIRST") == "failed" and statuses.get("SECOND") == "ok",
          f"{statuses}")
    check("D11 batch exit code is 2 when a child failed",
          summary2["exit_code"] == 2, f"exit_code={summary2['exit_code']}")
    check("D11 the summary records a bare log filename, never a path",
          all("/" not in (r.get("log") or "") for r in summary2["results"]),
          str([r.get("log") for r in summary2["results"]]))

    # D12 unverifiable statements are excluded from the FSM input, and the ceiling bites
    import tempfile as _tf
    with _tf.TemporaryDirectory(dir=str(WORKDIR)) as tmp:
        base = Path(tmp)
        verified = {"statements": [
            {"statement_id": f"N-S{i:03d}-001", "category": "BEHAVIORAL",
             "from_state": "A", "to_state": "B", "trigger": "t"} for i in range(1, 6)]}
        (base / "verify_stage_a12").mkdir(parents=True)
        vp = base / "verify_stage_a12" / "final.json"
        vp.write_text(json.dumps(verified))
        _, stats = runner.run_stage_b0(
            input_path=vp, output_base=base, stage_label="x_fsm",
            exclude_ids={"N-S002-001", "N-S004-001"})
        b0 = json.loads((base / "stage_b0" / "x_fsm.json").read_text())
        ids = {t["id"] for t in b0["fsm_input"]["transitions"]}
    check("D12 unverifiable statements never reach fsm_input",
          not ({"N-S002-001", "N-S004-001"} & ids) and len(ids) == 3,
          f"fsm_input ids={sorted(ids)}")
    check("D12 the exclusion is recorded, not silent",
          sorted(stats["excluded_unverifiable"]) == ["N-S002-001", "N-S004-001"],
          str(stats["excluded_unverifiable"]))
    check("D12 the ceiling is a fraction of merged statements, set to 1%",
          runner.MAX_UNVERIFIABLE_FRACTION == 0.01
          and runner.FALLBACK_TEMPERATURE == 0.2,
          f"ceiling {runner.MAX_UNVERIFIABLE_FRACTION}, temp {runner.FALLBACK_TEMPERATURE}")

    # D13 reproduce the SIP sequence exactly: one statement errors on every pass AND
    #     on the temperature fallback -> the run must PROCEED with it excluded, and a
    #     verification report must exist (the failure mode was "report missing")
    if merged_source.is_file():
        victim = {"id": None}

        class FailingOne(MockModel):
            def call(self, inner_self, prompt: str):
                sid = _statement_id_from_prompt(prompt)
                if victim["id"] is None:
                    victim["id"] = sid                # first statement seen is cursed
                if sid == victim["id"]:
                    raise RuntimeError(
                        "Expecting ',' delimiter: line 5 column 54 (char 135)")
                return MockModel.call(self, inner_self, prompt)

        fm = FailingOne()
        restore = install(fm)
        try:
            result = run_verify_once(tag="d13_unverifiable", workers=1,
                                     merged_source=merged_source, full_loop=True)
        finally:
            restore()
        report = Path(result["verify_dir"]) / "final_report.json"
        check("D13 a permanently failing statement does not abort the run",
              result["completed"], result.get("error", "")[:120])
        check("D13 the verification report exists (the old failure was its absence)",
              report.is_file())
        check("D13 the statement is recorded as unverifiable",
              victim["id"] in {u["statement_id"] for u in result["unverifiable"]},
              f"unverifiable={[u['statement_id'] for u in result['unverifiable']]}")
        check("D13 the fallback attempt actually ran",
              any(a.get("pass") == "fallback_temperature"
                  for a in result["attempts"]),
              str([a.get("pass") for a in result["attempts"]]))

    # D14 the anonymity lint must not fire on RFC prose that merely CONTAINS the
    #     login name as a substring, and must still fire on a real home path
    import getpass as _gp
    import anonymity as _an

    login = _gp.getuser()
    sandbox = WORKDIR / "anon" / "protocols" / "FAKE"
    (sandbox / "input").mkdir(parents=True, exist_ok=True)
    (sandbox / "output" / "run").mkdir(parents=True, exist_ok=True)
    # segments.md stands in for the benchmark's RFC text and quotes the name as a word
    (sandbox / "input" / "segments.md").write_text(
        f"## FAKE\n### S001 1. Introduction\nThe {login} parameter is described here.\n",
        encoding="utf-8")
    quoting = sandbox / "output" / "run" / "quotes_rfc.json"
    quoting.write_text(json.dumps({
        "description": f"The {login} parameter is described here.",
        "url_anchor": f"#s088-65-client-commands---experimental{login}expansion",
    }), encoding="utf-8")
    leaking = sandbox / "output" / "run" / "real_leak.json"
    leaking.write_text(json.dumps(
        {"input_file": f"{os.path.expanduser('~')}/Documents/x/final.json"}),
        encoding="utf-8")

    original_this = _an.THIS_DIR
    try:
        _an.THIS_DIR = sandbox.parent.parent          # the fake package root
        findings = _an.scan(sandbox.parent.parent)
    finally:
        _an.THIS_DIR = original_this
    by_file = {Path(f["file"]).name: f["kind"] for f in findings}
    check("D14 RFC prose quoting the login name is not a leak",
          "quotes_rfc.json" not in by_file,
          f"findings={by_file}")
    check("D14 a real home path is still a leak",
          any(name == "real_leak.json" for name in by_file), f"findings={by_file}")
    check("D14 the substring-inside-a-word case no longer fires",
          not any("login name" in k for k in by_file.values()), str(by_file))

    print("=" * 72)
    failures = [name for name, ok, _ in RESULTS if not ok]
    print(f"{len(RESULTS) - len(failures)}/{len(RESULTS)} checks passed")
    if failures:
        for name in failures:
            print(f"  FAILED: {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        # Remove this invocation's own workdir. It holds copies of real configs whose
        # absolute paths would otherwise sit under the package, and the anonymity rule
        # has no exceptions beyond the benchmark's own RFC text.
        if WORKDIR.exists() and _SCRATCH_ROOT in WORKDIR.parents:
            shutil.rmtree(WORKDIR, ignore_errors=True)
        if _SCRATCH_ROOT.is_dir() and not any(_SCRATCH_ROOT.iterdir()):
            _SCRATCH_ROOT.rmdir()

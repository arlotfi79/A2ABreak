#!/usr/bin/env python3
"""
concurrency.py
--------------
Wrapper-only concurrency for the per-section stages. No parent file is edited.

The parent pipeline processes sections strictly one at a time with a 5s pause
between them, which dominates wall clock (TCP: 75 sections x 2 stages x (call +
5s) ~ 100 min before verify even starts). This module drives the parent's OWN
per-section functions from a thread pool, then hands control back to the parent's
`run_stage_a1` / `run_stage_a2` / `verify` so that every aggregate artifact —
final.json, the cost summary, the JSONL logs, the verification report — is still
produced by the parent's code path, running on 100% cache hits.

Nothing about what is sent to the model changes; only how many sections are in
flight at once.

Thread safety (verified by inspection):
  * ModelInterface sets every attribute in __init__ (model_interface.py:93-101)
    and mutates none of them afterwards; `call` builds only local state.
  * verify_stage_a.LLMClient is the same shape (verify_stage_a.py:355-375).
  * litellm.completion is thread-safe.
  So ONE shared instance is correct; per-worker instances would only duplicate
  config parsing. Per-section output paths are distinct, so artifact writes never
  collide. The one genuinely shared mutable sink is the parent's `_append_log`
  JSONL appender, which is serialized with a lock below.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

DEFAULT_WORKERS = 6


class PoolFatalError(BaseException):
    """
    A fatal API failure raised from inside a worker.

    Deliberately derived from BaseException, not Exception: stage_a1.py:216-225,
    stage_a2.py:283-292 and verify_stage_a.py:536-543 all catch `Exception` and
    turn it into an empty extraction or an `error` statement. A quota or auth
    failure must NOT be swallowed like that — it has to escape those handlers so
    the pool can cancel everything else instead of hammering the API once per
    remaining section.
    """

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class FatalGate:
    """Shared trip-switch: the first fatal error stops every worker from calling again."""

    def __init__(self, is_fatal: Callable[[str], bool]):
        self._is_fatal = is_fatal
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._in_flight = 0
        self.detail: str | None = None

    @property
    def tripped(self) -> bool:
        return self._event.is_set()

    def trip(self, detail: str) -> None:
        # Under the same lock admit() uses, so no worker can slip through mid-trip.
        with self._lock:
            if self.detail is None:
                self.detail = detail
            self._event.set()

    def guard(self) -> None:
        """Refuse to start a request after a trip. Prefer admit() at the call site."""
        if self._event.is_set():
            raise PoolFatalError(self.detail or "fatal API error")

    def admit(self) -> None:
        """
        Atomic check-and-admit, taken immediately before the request leaves.

        Holding the lock across the check means a worker cannot be admitted while
        another is in the middle of tripping the gate: trip() takes the same lock, so
        the two orderings are serialized and a request admitted here provably began
        before the trip completed.
        """
        with self._lock:
            if self._event.is_set():
                raise PoolFatalError(self.detail or "fatal API error")
            self._in_flight += 1

    def finish(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def classify_and_raise(self, exc: BaseException) -> None:
        """Convert a fatal API exception into the sentinel; re-raise anything else."""
        if isinstance(exc, PoolFatalError):
            raise exc
        if self._is_fatal(str(exc)):
            self.trip(str(exc))
            raise PoolFatalError(str(exc)) from exc


@contextlib.contextmanager
def model_call_boundary(
    *,
    gate: "FatalGate | None" = None,
    audit: dict[str, dict[str, Any]] | None = None,
) -> Iterable[None]:
    """
    The single choke point every model request passes through, wrapped once.

    All four call sites resolve `completion` on the same litellm module object at
    call time — model_interface.py:178 and stage_b2.py:551 call `litellm.completion`
    directly, merge_stage_a12.py:296 and verify_stage_a.py:391 call it via
    `self._litellm` — so patching it here covers Stage A, merge, verify and Stage B
    with one wrapper, and covers every RETRY as well.

    That last point is why the guard cannot live at the outer call: the parents'
    retry loops (model_interface.py:177-190, verify_stage_a.py:389-409) sit INSIDE
    the outer call, so a worker already past an outer-level check would keep firing
    retries after another worker tripped the gate.

    gate  — refuse to start a request once tripped; classify fatal failures.
    audit — record sha256 of the system prompt and of the full outbound payload.
    """
    import hashlib
    import json as _json

    import litellm

    lock = threading.Lock()
    original = litellm.completion

    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def record(kwargs: dict) -> None:
        if audit is None:
            return
        messages = kwargs.get("messages") or []
        system = kwargs.get("system")
        if system is None:
            system = next((m.get("content", "") for m in messages
                           if isinstance(m, dict) and m.get("role") == "system"), "")
        payload = {k: v for k, v in kwargs.items() if k != "messages"}
        payload["messages"] = [
            {"role": m.get("role"), "content_sha256": digest(str(m.get("content", "")))}
            for m in messages if isinstance(m, dict)
        ]
        user_text = "".join(str(m.get("content", "")) for m in messages
                            if isinstance(m, dict) and m.get("role") != "system")
        key = digest(user_text)[:16]
        entry = {
            "model": kwargs.get("model"),
            "system_sha256": digest(str(system) if system is not None else ""),
            "request_sha256": digest(
                _json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
            ),
        }
        with lock:
            audit[key] = entry

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        # Order matters. Hashing the payload takes long enough for another worker to
        # trip the gate in between, so the admission check is done LAST, atomically,
        # immediately before the call — never before the work that precedes it.
        record(kwargs)
        if gate is not None:
            gate.admit()
        try:
            return original(*args, **kwargs)
        except PoolFatalError:
            raise
        except Exception as exc:                         # noqa: BLE001 - reclassified
            if gate is not None:
                gate.classify_and_raise(exc)
            raise
        finally:
            if gate is not None:
                gate.finish()

    litellm.completion = wrapped
    try:
        yield
    finally:
        litellm.completion = original


class _NoSleepTime:
    """Proxy for the time module whose sleep() is a no-op."""

    def __init__(self, inner: Any):
        self._inner = inner

    def sleep(self, _seconds: float) -> None:      # noqa: D401 - deliberate no-op
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@contextlib.contextmanager
def no_inter_section_sleep(*modules: Any) -> Iterable[None]:
    """
    Drop the parent's inter-section pause. Used ONLY around aggregation passes over a
    cache proven complete, where the pause protects nothing because no request is
    issued; live extraction keeps the parent's throttling behaviour. Never applied to
    verify_stage_a, whose module-level `time` is also what LLMClient.call sleeps on
    for rate-limit backoff — that path passes section_delay=0 instead.
    """
    saved = [(m, m.time) for m in modules]
    for module, original in saved:
        module.time = _NoSleepTime(original)
    try:
        yield
    finally:
        for module, original in saved:
            module.time = original


@contextlib.contextmanager
def serialized_append_log(*modules: Any) -> Iterable[None]:
    """Serialize the parent's JSONL appender so concurrent writes cannot interleave."""
    lock = threading.Lock()
    saved = [(m, m._append_log) for m in modules]

    def make_locked(original: Callable[..., None]) -> Callable[..., None]:
        def locked(*args: Any, **kwargs: Any) -> None:
            with lock:
                original(*args, **kwargs)
        return locked

    for module, original in saved:
        module._append_log = make_locked(original)
    try:
        yield
    finally:
        for module, original in saved:
            module._append_log = original


@contextlib.contextmanager
def sampling_temperature(value: float) -> Iterable[None]:
    """
    Force a sampling temperature for the calls made inside this block.

    Used only for the last-ditch re-attempt at statements whose verification keeps
    coming back as malformed JSON at an identical byte offset — a deterministic
    decoding rut that an identical request cannot escape. This is an operational
    knob, not a prompt change: the prompt bytes and the prompt-set hash are untouched.
    """
    import litellm

    original = litellm.completion

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("temperature", value)
        return original(*args, **kwargs)

    litellm.completion = wrapped
    try:
        yield
    finally:
        litellm.completion = original


class RateLimitCounter(logging.Handler):
    """
    Counts the parent's rate-limit retries. Both retry loops announce themselves
    with "Rate limit hit" (model_interface.py:183, verify_stage_a.py:402), and
    neither exposes a counter, so we observe the log instead of touching them.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:                               # pragma: no cover
            return
        if "rate limit hit" in message.lower():
            with self._lock:
                self.count += 1

    @contextlib.contextmanager
    def attached(self) -> Iterable["RateLimitCounter"]:
        root = logging.getLogger()
        root.addHandler(self)
        try:
            yield self
        finally:
            root.removeHandler(self)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_config(config_path: Path) -> tuple[dict[str, Any], Path]:
    config_file = Path(config_path).resolve()
    with config_file.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f), config_file.parent


def _run_pool(
    *,
    items: list[Any],
    work: Callable[[Any], Any],
    workers: int,
    label: str,
    logger: logging.Logger,
) -> tuple[int, list[str]]:
    """Run `work` over `items`; return (completed, per-item error strings)."""
    errors: list[str] = []
    completed = 0
    if not items:
        return 0, errors
    fatal: PoolFatalError | None = None
    cancelled = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                future.result()
                completed += 1
            except PoolFatalError as exc:
                fatal = exc
                for other in futures:
                    if other is not future and other.cancel():
                        cancelled += 1
                logger.error(
                    "%s: fatal API error on %s — cancelled %d queued section(s)",
                    label, item, cancelled,
                )
                break
            except Exception as exc:                    # noqa: BLE001 - reported, not hidden
                errors.append(f"{item}: {exc}")
                logger.error("%s: worker failed on %s: %s", label, item, exc)
    if fatal is not None:
        raise fatal
    return completed, errors


# ---------------------------------------------------------------------------
# Stage A
# ---------------------------------------------------------------------------

def _valid_json(path: Path) -> Any | None:
    """Parsed JSON, or None if the file is missing or unreadable."""
    import json
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _valid_object(path: Path) -> dict | None:
    """Parsed JSON that is actually an object — a list would crash every .get()."""
    data = _valid_json(path)
    return data if isinstance(data, dict) else None


_COST_NUMERIC_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


def _cost_record_ok(path: Path) -> bool:
    """A cost record must be an object carrying numeric token counts."""
    data = _valid_object(path)
    if data is None:
        return False
    return all(isinstance(data.get(field), (int, float))
               for field in _COST_NUMERIC_FIELDS)


def _evict(paths: Iterable[Path]) -> list[str]:
    """
    Delete an unusable artifact AND its cost record.

    Resubmitting is not enough on its own: the parents' resume accepts any existing
    section output (stage_a1.py:157-166, verify_stage_a.py:466-469) and would return
    the broken file without re-calling. The cache entry has to be gone.
    """
    removed = []
    for path in paths:
        try:
            if path.is_file():
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue
    return removed


def stage_a_artifact_problems(
    *, stage_module: Any, sections: list[Any], sections_dir: Path, cost_dir: Path
) -> dict[str, str]:
    """
    Per-section reasons the cache is not usable. Empty dict == every expected
    artifact exists, parses, has a statements list, and has its cost record — the
    precondition for letting the parent aggregate without making a single call.
    """
    problems: dict[str, str] = {}
    for section in sections:
        path = stage_module.section_output_path(sections_dir, section)
        if not path.is_file():
            problems[section.section_id] = "missing"
            continue
        data = _valid_object(path)
        if data is None:
            problems[section.section_id] = "unparseable or not a JSON object"
            continue
        if not isinstance(data.get("statements"), list):
            problems[section.section_id] = "no statements list"
            continue
        if not isinstance(data.get("section_id"), str):
            problems[section.section_id] = "no section_id"
            continue
        if not _cost_record_ok(stage_module.section_cost_path(cost_dir, section)):
            problems[section.section_id] = "cost record missing or malformed"
    return problems


def unusable_statements_from_sections(
    *, groups: dict[str, list[dict]], sections_dir: Path
) -> list[dict[str, Any]]:
    """
    Per-statement failures read straight from the cached SECTION artifacts.

    Deliberately independent of verify's final_report.json: when the last pass leaves
    the cache incomplete, aggregation is skipped and no report exists, so any check
    that reads the report sees nothing wrong and the fallback it guards never runs.
    The section artifacts are always there — the pool writes them even when the result
    is an error.
    """
    out: list[dict[str, Any]] = []
    for section_id, statements in groups.items():
        safe_id = section_id.replace(".", "_").replace("/", "_")
        data = _valid_object(sections_dir / f"{safe_id}.json")
        if data is None:
            continue
        for result in data.get("results") or []:
            if (result.get("status") in {"error", "unverified"}
                    or result.get("existence") == "unchecked"):
                out.append({
                    "statement_id": result.get("statement_id"),
                    "section_id": section_id,
                    "status": result.get("status"),
                    "existence": result.get("existence"),
                    "error": str(result.get("error") or ""),
                })
    return out


def verify_artifact_problems(
    *, groups: dict[str, list[dict]], sections_dir: Path, cost_dir: Path,
    ignore_statement_ids: set[str] | None = None,
) -> dict[str, str]:
    """Same idea for verify: one artifact per section group, one cost file per statement."""
    problems: dict[str, str] = {}
    for section_id, statements in groups.items():
        safe_id = section_id.replace(".", "_").replace("/", "_")
        path = sections_dir / f"{safe_id}.json"
        if not path.is_file():
            problems[section_id] = "missing"
            continue
        data = _valid_object(path)
        if data is None:
            problems[section_id] = "unparseable or not a JSON object"
            continue
        if data.get("skipped"):
            problems[section_id] = "section skipped (not found in spec)"
            continue
        results = data.get("results")
        if not isinstance(results, list) or len(results) != len(statements):
            problems[section_id] = (
                f"results {len(results) if isinstance(results, list) else 'n/a'} "
                f"!= statements {len(statements)}"
            )
            continue
        ignored = ignore_statement_ids or set()
        bad = [r for r in results
               if r.get("statement_id") not in ignored
               and (r.get("status") in {"error", "unverified"}
                    or r.get("existence") == "unchecked")]
        if bad:
            problems[section_id] = f"{len(bad)} unusable statement result(s)"
            continue
        bad_cost = [
            r.get("statement_id") for r in results
            if r.get("statement_id") not in (ignore_statement_ids or set())
            and not _cost_record_ok(
                cost_dir / f"{str(r.get('statement_id')).replace('-', '_')}.json")
        ]
        if bad_cost:
            problems[section_id] = f"cost record missing/malformed for {bad_cost[:3]}"
    return problems


def parallel_stage_a_sections(
    *,
    stage_module: Any,
    config_path: Path,
    sections: list[Any],
    workers: int,
    logger: logging.Logger,
    gate: FatalGate,
) -> dict[str, Any]:
    """
    Fill the parent's per-section cache concurrently, using the parent's own
    `extract_section`. Does NOT aggregate — the caller then invokes the parent's
    run_stage_aN(resume=True), which sees a full cache and writes final.json,
    the cost summary and the run log exactly as it always does.
    """
    from model_interface import ModelInterface

    config, config_root = _load_config(config_path)
    base_dir = config_root / config["output"]["base_dir"]
    stage_dir = base_dir / stage_module.STAGE
    sections_dir = stage_dir / "sections"
    cost_dir = stage_dir / "cost"
    logs_dir = stage_dir / "logs"
    for directory in (sections_dir, cost_dir, logs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    problems = stage_a_artifact_problems(
        stage_module=stage_module, sections=sections,
        sections_dir=sections_dir, cost_dir=cost_dir,
    )
    pending = [s for s in sections if s.section_id in problems]
    evicted: list[str] = []
    for section in pending:
        evicted.extend(_evict([
            stage_module.section_output_path(sections_dir, section),
            stage_module.section_cost_path(cost_dir, section),
        ]))
    if evicted:
        logger.info("%s: evicted %d unusable cache file(s) before resubmission",
                    stage_module.STAGE, len(evicted))
    logger.info(
        "%s: %d/%d sections need extraction; running %d at a time",
        stage_module.STAGE, len(pending), len(sections), workers,
    )
    if not pending:
        # A complete cache still has to answer the full contract: callers branch on
        # cache_complete, and an early return without it crashed them.
        return {"submitted": 0, "completed": 0, "errors": [], "workers": workers,
                "unusable_after_pool": {}, "cache_complete": True,
                "evicted": [], "sections": len(sections)}

    log_path = logs_dir / (
        f"{stage_module.STAGE}_{time.strftime('%Y%m%d_%H%M%S')}_parallel.log.jsonl"
    )
    model = ModelInterface(str(config_path))

    def work(section: Any) -> None:
        stage_module.extract_section(
            section=section,
            model=model,
            sections_dir=sections_dir,
            cost_dir=cost_dir,
            log_path=log_path,
            resume=True,
        )

    with serialized_append_log(stage_module), model_call_boundary(gate=gate):
        completed, errors = _run_pool(
            items=pending,
            work=work,
            workers=workers,
            label=stage_module.STAGE,
            logger=logger,
        )
    remaining = stage_a_artifact_problems(
        stage_module=stage_module, sections=sections,
        sections_dir=sections_dir, cost_dir=cost_dir,
    )
    return {
        "submitted": len(pending),
        "completed": completed,
        "errors": errors,
        "workers": workers,
        "unusable_after_pool": remaining,
        "cache_complete": not remaining,
        "evicted": evicted,
        "sections": len(sections),
    }


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def parallel_verify_sections(
    *,
    config_path: Path,
    merged_path: Path,
    spec_url: str,
    merge_threshold: int,
    api_key: str,
    output_path: Path,
    workers: int,
    logger: logging.Logger,
    gate: FatalGate,
    max_sections: int | None = None,
    ignore_statement_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    Same pattern for verification: fill the per-section cache concurrently with
    the parent's own `verify_section`, then let the parent's `verify(resume=True)`
    group, apply corrections and write the report.
    """
    import json

    import verify_stage_a

    config, _ = _load_config(config_path)
    model_cfg = config.get("model") or {}

    with Path(merged_path).open("r", encoding="utf-8") as f:
        statements = json.load(f).get("statements", [])
    groups = verify_stage_a.group_by_section(statements)
    section_map = verify_stage_a._build_section_map(spec_url, merge_threshold)
    n_tpl, d_tpl = verify_stage_a._load_prompt_templates(Path(config_path))

    out = Path(output_path)
    sections_dir = out.parent / "sections"
    cost_dir = out.parent / "cost"
    for directory in (sections_dir, cost_dir):
        directory.mkdir(parents=True, exist_ok=True)

    problems = verify_artifact_problems(
        groups=groups, sections_dir=sections_dir, cost_dir=cost_dir,
        ignore_statement_ids=ignore_statement_ids,
    )
    pending = [section_id for section_id in groups if section_id in problems]
    if max_sections:
        pending = pending[:max_sections]
    evicted: list[str] = []
    for section_id in pending:
        safe_id = section_id.replace(".", "_").replace("/", "_")
        targets = [sections_dir / f"{safe_id}.json"]
        targets += [
            cost_dir / f"{str(stmt.get('statement_id')).replace('-', '_')}.json"
            for stmt in groups.get(section_id, [])
        ]
        evicted.extend(_evict(targets))
    if evicted:
        logger.info("verify: evicted %d unusable cache file(s) before resubmission",
                    len(evicted))

    logger.info(
        "verify: %d/%d sections need verification; running %d at a time",
        len(pending), len(groups), workers,
    )
    if not pending:
        return {"submitted": 0, "completed": 0, "errors": [], "workers": workers,
                "llm_calls_in_pool": 0, "unusable_after_pool": {},
                "cache_complete": True, "evicted": [], "groups": len(groups)}

    llm = verify_stage_a.LLMClient(
        api_key=api_key,
        model=model_cfg.get("name", "anthropic/claude-sonnet-4-6"),
        max_tokens=int(model_cfg.get("max_tokens", 16000)),
        max_retries=int(model_cfg.get("max_retries", 5)),
        retry_wait=int(model_cfg.get("rate_limit_retry_wait_seconds", 60)),
    )
    cost_records: list[dict] = []
    records_lock = threading.Lock()

    def work(section_id: str) -> None:
        local_records: list[dict] = []
        verify_stage_a.verify_section(
            section_id=section_id,
            section=verify_stage_a.find_section(section_map, section_id),
            statements=groups[section_id],
            llm=llm,
            sections_dir=sections_dir,
            cost_records=local_records,
            n_prompt_tpl=n_tpl,
            d_prompt_tpl=d_tpl,
            cost_dir=cost_dir,
        )
        with records_lock:
            cost_records.extend(local_records)

    with model_call_boundary(gate=gate):
        completed, errors = _run_pool(
            items=pending,
            work=work,
            workers=workers,
            label="verify",
            logger=logger,
        )
    remaining = verify_artifact_problems(
        groups=groups, sections_dir=sections_dir, cost_dir=cost_dir,
        ignore_statement_ids=ignore_statement_ids,
    )
    return {
        "submitted": len(pending),
        "completed": completed,
        "errors": errors,
        "workers": workers,
        "llm_calls_in_pool": len(cost_records),
        "unusable_after_pool": remaining,
        "cache_complete": not remaining,
        "evicted": evicted,
        "groups": len(groups),
    }


@contextlib.contextmanager
def verify_cost_replay(cost_dir: Path) -> Iterable[None]:
    """
    Make the parent's aggregation see the pool's costs.

    verify_section returns early on a cache hit (verify_stage_a.py:466-469) without
    touching `cost_records`, so an aggregation pass over a pool-filled cache would
    report 0 calls and $0 while per-statement cost files sit on disk — the report,
    the cost summary and the manifest would contradict each other. This wrapper
    replays each cached section's per-statement cost records, in statement_id order
    so the aggregate is deterministic regardless of the order the pool finished in.
    """
    import json

    import verify_stage_a

    original = verify_stage_a.verify_section

    def wrapped(**kwargs: Any) -> Any:
        sections_dir = kwargs.get("sections_dir")
        section_id = kwargs.get("section_id", "")
        safe_id = str(section_id).replace(".", "_").replace("/", "_")
        was_cached = bool(
            sections_dir and (Path(sections_dir) / f"{safe_id}.json").is_file()
        )
        result = original(**kwargs)
        if was_cached:
            records = kwargs.get("cost_records")
            if isinstance(records, list):
                for entry in sorted(
                    result.get("results", []) or [],
                    key=lambda r: str(r.get("statement_id")),
                ):
                    sid = str(entry.get("statement_id"))
                    path = Path(cost_dir) / f"{sid.replace('-', '_')}.json"
                    if path.is_file():
                        try:
                            with path.open("r", encoding="utf-8") as f:
                                records.append(json.load(f))
                        except (OSError, ValueError):
                            continue
        return result

    verify_stage_a.verify_section = wrapped
    try:
        yield
    finally:
        verify_stage_a.verify_section = original


# ---------------------------------------------------------------------------
# run-many: one subprocess per protocol
# ---------------------------------------------------------------------------

REQUIRED_TERMINAL_STAGES = ("stage_a1", "stage_a2", "stage_a12",
                            "verify_stage_a12", "stage_b0", "stage_b", "export")


def already_completed(
    namespace_dir: Path, *, require_eval: bool = True
) -> dict[str, Any] | None:
    """
    The manifest of a namespace whose run actually finished, else None.

    Presence of a manifest is NOT enough: runner writes it before evaluation, and a
    stage entry may be a `{"skipped": true}` placeholder. Completion means every
    required terminal stage ran for real, the export produced states, and — unless
    the caller asked for --no-eval — the evaluator wrote its rows.
    """
    manifest = Path(namespace_dir) / "manifest.json"
    if not manifest.is_file():
        return None
    import json
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stages = data.get("stages") or {}
    for name in REQUIRED_TERMINAL_STAGES:
        entry = stages.get(name)
        if not isinstance(entry, dict) or entry.get("skipped"):
            return None
    if not (stages.get("export") or {}).get("states"):
        return None
    if require_eval:
        evaluation = data.get("eval") or {}
        if not (evaluation.get("states") and evaluation.get("transitions")):
            return None
    return data


def _partial_manifest(namespace_dir: Path) -> dict[str, Any] | None:
    """The manifest of a run that did not finish, if one was written."""
    import json

    manifest = Path(namespace_dir) / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_many(
    *,
    protocols: list[str],
    workers: int,
    extra_args: list[str],
    python_executable: str,
    main_path: Path,
    log_dir: Path,
    resolve_namespace: Callable[[str], Path],
    logger: logging.Logger,
    rerun: bool = False,
    require_eval: bool = True,
) -> dict[str, Any]:
    """
    Run several protocols as independent subprocesses, one per protocol, each with
    its own namespace and log file. Protocol-level execution is sequential: the
    concurrency that matters is already inside a run (sections), and stacking
    protocols would multiply rate-limit pressure against the same account.

    A fatal API error in any child stops further launches — that class of error
    would hit every remaining protocol immediately.
    """
    import subprocess

    log_dir.mkdir(parents=True, exist_ok=True)

    if len(set(protocols)) != len(protocols):
        duplicates = sorted({p for p in protocols if protocols.count(p) > 1})
        raise RuntimeError(f"duplicate protocols requested: {duplicates}")
    if workers < 1:
        raise RuntimeError(f"--workers must be >= 1, got {workers}")

    # Protocols that already completed are SKIPPED, not a reason to refuse the batch.
    # The point of the guard is "do not pay twice", not "do not proceed": refusing the
    # whole run because one protocol finished earlier just makes the caller retype the
    # list. --rerun still forces everything requested to run again.
    results: list[dict[str, Any]] = []
    to_run: list[str] = []
    for protocol in protocols:
        manifest = None if rerun else already_completed(resolve_namespace(protocol),
                                                        require_eval=require_eval)
        if manifest is None:
            to_run.append(protocol)
            continue
        evaluation = manifest.get("eval") or {}
        cost = round(float(manifest.get("cost_usd_total") or 0), 2)
        logger.info(
            "run-many: skipping %s — already completed as %s ($%.2f); pass --rerun to "
            "run it again", protocol, manifest.get("run_key"), cost)
        results.append({
            "protocol": protocol,
            "status": "skipped_already_complete",
            "run_key": manifest.get("run_key"),
            "cost_usd": cost,
            "states_f1": (evaluation.get("states") or {}).get("F1-Score"),
            "transitions_f1": (evaluation.get("transitions") or {}).get("F1-Score"),
            "note": "existing result reused; no money spent",
        })
    if not to_run:
        logger.info("run-many: nothing to do — every requested protocol already has a "
                    "completed run")

    stopped_early: str | None = None

    for protocol in to_run:
        if stopped_early:
            results.append({"protocol": protocol, "status": "not_started",
                            "reason": stopped_early})
            continue
        log_path = log_dir / f"{protocol}.log"
        if log_path.exists():                            # never clobber a previous child log
            n = 1
            while (log_dir / f"{protocol}.{n}.log").exists():
                n += 1
            log_path = log_dir / f"{protocol}.{n}.log"
        cmd = [python_executable, str(main_path), "run", "--protocol", protocol,
               "--workers", str(workers), *extra_args]
        if rerun and "--no-resume" not in cmd:
            # --rerun means recompute, not resume into a finished namespace.
            cmd.append("--no-resume")
        logger.info("run-many: starting %s -> %s", protocol, log_path.name)
        # Belt and braces: the child redacts its own output, and the parent redacts
        # again before anything reaches disk. A child that dies before installing its
        # redaction (an import error, say) would otherwise write raw paths here.
        import anonymity

        completed = subprocess.run(cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, check=False, text=True)
        log_path.write_text(anonymity.redact_text(completed.stdout or ""),
                            encoding="utf-8")
        entry: dict[str, Any] = {"protocol": protocol,
                                 "exit_code": completed.returncode,
                                 "log": log_path.name}
        if completed.returncode == 3:                      # wrapper's fatal-API exit code
            entry["status"] = "fatal_api_error"
            stopped_early = f"{protocol} hit a fatal API error"
            logger.error("run-many: %s — halting; no further protocols will start",
                         stopped_early)
        elif completed.returncode == 0:
            entry["status"] = "ok"
        else:
            entry["status"] = "failed"
            logger.error("run-many: %s exited %d — continuing with the rest",
                         protocol, completed.returncode)

        # Summary material, read back from the namespace. A FAILED child still has a
        # manifest for every stage it completed, so read that too rather than
        # reporting a blank row for the protocol that most needs looking at.
        namespace = resolve_namespace(protocol)
        manifest = already_completed(namespace, require_eval=require_eval)
        if manifest is None:
            manifest = _partial_manifest(namespace)
            if manifest:
                entry["completed"] = False
        if manifest:
            evaluation = manifest.get("eval") or {}
            entry.update({
                "run_key": manifest.get("run_key"),
                "cost_usd": round(float(manifest.get("cost_usd_total") or 0), 2),
                "states_f1": (evaluation.get("states") or {}).get("F1-Score"),
                "transitions_f1": (evaluation.get("transitions") or {}).get("F1-Score"),
                "stages_completed": sorted(manifest.get("stages") or {}),
            })
        results.append(entry)

    return {
        "protocols": protocols,
        "ran": to_run,
        "batch": "+".join(protocols),
        "workers": workers,
        "results": results,
        "ok": [r["protocol"] for r in results if r.get("status") == "ok"],
        "skipped_already_complete": [
            r["protocol"] for r in results
            if r.get("status") == "skipped_already_complete"
        ],
        "failed": [r["protocol"] for r in results if r.get("status") == "failed"],
        "fatal": [r["protocol"] for r in results if r.get("status") == "fatal_api_error"],
        "not_started": [r["protocol"] for r in results if r.get("status") == "not_started"],
        "stopped_early": stopped_early,
        "exit_code": (
            3 if any(r.get("status") == "fatal_api_error" for r in results)
            else 2 if any(r.get("status") in {"failed", "not_started"} for r in results)
            else 0
        ),
    }


def format_run_many_summary(summary: dict[str, Any]) -> str:
    lines = [f"{'protocol':8s} {'run_key':12s} {'exit':>4s} {'cost':>8s} "
             f"{'S-F1':>6s} {'T-F1':>6s}  status"]
    for entry in summary["results"]:
        cost = entry.get("cost_usd")
        s_f1 = entry.get("states_f1")
        t_f1 = entry.get("transitions_f1")
        lines.append(
            f"{entry['protocol']:8s} {str(entry.get('run_key') or '-'):12s} "
            f"{str(entry.get('exit_code', '-')):>4s} "
            f"{('$' + format(cost, '.2f')) if cost is not None else '-':>8s} "
            f"{format(s_f1, '.3f') if s_f1 is not None else '-':>6s} "
            f"{format(t_f1, '.3f') if t_f1 is not None else '-':>6s}  "
            f"{entry.get('status')}"
        )
    if summary.get("stopped_early"):
        lines.append(f"halted early: {summary['stopped_early']}")
    return "\n".join(lines)

#!/usr/bin/env python3
"""
main.py
-------
Protocol-generic PSMBench runner for the A2ABreak FSM extraction pipeline.

One untailored configuration for all 14 PSMBench protocols. The benchmark's own
segment text is the only input, the benchmark's own evaluator produces the
numbers, and the only model-visible protocol parameter is the protocol name.

Requires Python 3.11+ with the pipeline's dependencies installed.

    python3 main.py prepare-all
    python3 main.py prepare --protocol BGP
    python3 main.py run --protocol TCP
    python3 main.py run --protocol POP3 --max-sections 1 --namespace smoke
    python3 main.py eval --protocol TCP
    python3 main.py eval-baselines
    python3 main.py summarize
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import sys
from pathlib import Path

# Before importing anything of ours: compiled bytecode embeds the absolute path it
# was built from, which would defeat the double-blind guarantee. Setting this inside
# main() would be too late — the imports below would already have written .pyc files.
sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import anonymity  # noqa: E402
import runner  # noqa: E402

LOGGER = logging.getLogger("psmbench.cli")


def _redact_output() -> None:
    """
    Redact identifying paths on the way out, at two levels.

    Streams first and before logging is configured: print() and interpreter
    tracebacks never touch logging, and a StreamHandler captures sys.stderr when it
    is constructed, so wrapping afterwards would leave it holding the raw stream.
    The logging filter stays as well — it scrubs record args before formatting, which
    keeps structured messages tidy rather than relying on the stream as a last resort.
    """
    anonymity.install_stream_redaction()


def _use_repo_root() -> None:
    """
    Run from the repository root. Persisted configs store fetcher.url relative to it
    (absolute paths would identify the machine, and the paper is double-blind), and
    fetcher.py resolves that against the working directory.
    """
    os.chdir(runner.PIPELINE_DIR)


def cmd_anonymity_check(args: argparse.Namespace) -> None:
    removed = anonymity.purge_bytecode()
    if removed:
        print(f"removed {removed} compiled-bytecode artifact(s) before checking")
    findings = anonymity.scan()
    if not findings:
        print("anonymity check: clean — no user paths, identity strings or key "
              "patterns under PSM_Benchmark/")
        return
    for finding in findings:
        print(f"  {finding['file']}: {finding['kind']} — {finding['detail']}")
    raise SystemExit(f"{len(findings)} anonymity violation(s)")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_prepare(args: argparse.Namespace) -> None:
    stats = runner.prepare_protocol(args.protocol)
    print(json.dumps(stats, indent=2))


def cmd_prepare_all(args: argparse.Namespace) -> None:
    protocols = sorted(runner.discover_protocols())
    results = []
    for protocol in protocols:
        stats = runner.prepare_protocol(protocol)
        results.append(stats)
        print(
            f"{protocol:6s} segments={stats['segment_count']:3d} "
            f"chunks={stats['chunk_count']:4d} "
            f"max={stats['max_chunk_chars']:6d} "
            f"median={stats['median_chunk_chars']:5d} "
            f"blank={stats['blank_chunks']:2d} "
            f"escaped={stats['escaped_heading_lines']:2d} "
            f"run_key={stats['run_key']}"
        )
    prompt_hashes = {s["protocol"]: s["prompt_set_sha256"] for s in results}
    out_path = runner.results_dir() / "prepare_all.json"
    runner.write_json(out_path, {
        "protocols": results,
        "prompt_set_sha256": prompt_hashes,
        "total_chunks": sum(s["chunk_count"] for s in results),
    })
    print(f"\nTotal chunks: {sum(s['chunk_count'] for s in results)}")
    print(f"Wrote {out_path}")


def cmd_run(args: argparse.Namespace) -> None:
    manifest = runner.run_protocol(
        protocol=args.protocol,
        resume=not args.no_resume,
        skip=[value for group in (args.skip or []) for value in group],
        max_sections=args.max_sections,
        namespace=args.namespace,
        run_eval=not args.no_eval,
        stage_a_passes=args.stage_a_passes,
        workers=args.workers,
    )
    print(json.dumps({
        "protocol": manifest["protocol"],
        "run_key": manifest["run_key"],
        "run_dir": manifest["run_dir"],
        "stages": manifest["stages"],
        "eval": manifest.get("eval"),
        "cost_usd_total": manifest.get("cost_usd_total"),
        "cost_report": manifest.get("cost_report", {}).get("total_cost_usd"),
        "wall_clock_seconds": manifest["wall_clock_seconds"],
    }, indent=2))


def cmd_eval(args: argparse.Namespace) -> None:
    import evaluation

    output_base = Path(args.output_base) if args.output_base else None
    if output_base is None:
        prep = runner.prepare_protocol(args.protocol)
        output_base = runner.run_base_dir(
            args.protocol, prep["run_key"], namespace=args.namespace
        )
    exported = args.export_path or (
        output_base / "fsm" / f"{args.protocol}_{args.model_name}_final_fsm.json"
    )
    result = evaluation.evaluate_protocol(
        protocol=args.protocol,
        exported_fsm=Path(exported),
        output_base=output_base,
        model_name=args.model_name,
        force=args.force,
    )
    print(json.dumps(result["official"], indent=2))


def cmd_eval_baselines(args: argparse.Namespace) -> None:
    import evaluation

    result = evaluation.evaluate_baselines(protocols=args.protocols)
    states = {(r["Protocol"], r["Model"]): r for r in result["states"]}
    print(f"{'Protocol':8s} {'Model':28s} {'S-F1':>6s} {'T-F1':>6s}")
    for row in result["transitions"]:
        key = (row["Protocol"], row["Model"])
        state_row = states.get(key, {})
        print(
            f"{row['Protocol']:8s} {row['Model']:28s} "
            f"{state_row.get('F1-Score', 0):6.3f} {row['F1-Score']:6.3f}"
        )
    print(f"\nWrote {runner.results_dir() / 'baselines.json'}")


def cmd_run_many(args: argparse.Namespace) -> None:
    import concurrency

    # Resolve every run_key up front: prepare_protocol runs the anonymity gate over
    # the tree, and a concurrent batch must not be able to fail this one mid-loop.
    run_keys = {protocol: runner.prepare_protocol(protocol)["run_key"]
                for protocol in args.protocols}

    extra: list[str] = []
    if args.no_resume:
        extra.append("--no-resume")
    if args.no_eval:
        extra.append("--no-eval")
    try:
        summary = concurrency.run_many(
            protocols=args.protocols,
            workers=args.workers,
            extra_args=extra,
            python_executable=sys.executable,
            main_path=Path(__file__).resolve(),
            # Operational output, not a result: results/ holds only reportable
            # artifacts, so child logs and batch summaries live in the archive.
            log_dir=runner.archive_dir() / "run_logs" / "logs",
            resolve_namespace=lambda protocol: runner.run_base_dir(
                protocol, run_keys[protocol], namespace=None,
            ),
            logger=LOGGER,
            rerun=args.rerun,
            require_eval=not args.no_eval,
        )
    except RuntimeError as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(2) from exc
    # One summary file per batch: concurrent parents writing a single shared file
    # raced, and the last writer erased the others' results.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    label = "-".join(args.protocols)[:60]
    summary_path = (runner.archive_dir() / "run_logs" / "summaries" /
                    f"run_many_summary_{label}_{stamp}.json")
    runner.write_json(summary_path, json.loads(anonymity.redact_text(
        json.dumps(summary, ensure_ascii=False))))
    print()
    print(concurrency.format_run_many_summary(summary))
    print(f"\nSummary: {runner.repo_relative(summary_path)}")

    # The per-run gates are scoped to their own namespaces so siblings cannot fail
    # each other; the whole-tree check happens here, once the batch is done.
    findings = anonymity.scan()
    if findings:
        LOGGER.error("anonymity: %d finding(s) across the package — results must not "
                     "be reported until these are cleared", len(findings))
        for finding in findings[:10]:
            LOGGER.error("  %s: %s", finding["file"], finding["kind"])
    exit_code = summary.get("exit_code", 0)
    if exit_code:
        raise SystemExit(exit_code)


def cmd_archive(args: argparse.Namespace) -> None:
    protocols = args.protocols or sorted(runner.discover_protocols())
    moved = []
    for protocol in protocols:
        moved.extend(runner.archive_superseded_runs(protocol))
        runner.refresh_cost_report(protocol)
    runner.write_archive_index()
    if moved:
        for entry in moved:
            print(f"archived {entry['protocol']} {entry['run_key']} -> {entry['moved_to']}")
    else:
        print("nothing to archive: every protocol's output/ already holds exactly "
              "one run — its current one")



def cmd_summarize(args: argparse.Namespace) -> None:
    import summarize

    paths = summarize.render(args.namespace)
    for name, path in paths.items():
        print(f"{name:12s} {path}")
    print()
    print(paths["markdown"].read_text(encoding="utf-8"))


def concurrency_default_workers() -> int:
    import concurrency

    return concurrency.DEFAULT_WORKERS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the A2ABreak FSM pipeline over the PSMBench protocols.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="Build segments.md + chunk_map + config for one protocol"
    )
    prepare.add_argument("--protocol", required=True)
    prepare.set_defaults(func=cmd_prepare)

    prepare_all = subparsers.add_parser(
        "prepare-all", help="prepare every discovered protocol"
    )
    prepare_all.set_defaults(func=cmd_prepare_all)

    run = subparsers.add_parser("run", help="Run the full pipeline for one protocol")
    run.add_argument("--protocol", required=True)
    run.add_argument("--no-resume", action="store_true",
                     help="Archive the existing namespace to attempt_<n>/ and start clean")
    run.add_argument("--skip", nargs="+", action="append", default=[], metavar="STAGE")
    run.add_argument("--max-sections", type=int, default=None,
                     help="Smoke only: truncate the section list (forces --namespace smoke)")
    run.add_argument("--namespace", default=None,
                     help="Output namespace; 'smoke' keeps a run out of outputs/generic/")
    run.add_argument("--no-eval", action="store_true")
    run.add_argument("--workers", type=int, default=1,
                     help="Sections processed concurrently (default 1 = the parent's "
                          "sequential behaviour). Aggregation is unchanged.")
    run.add_argument("--stage-a-passes", type=int, default=3,
                     help="Bounded retry passes for Stage A and verify sections "
                          "whose model call failed transiently (backoff 30/60/120s)")
    run.set_defaults(func=cmd_run)

    evaluate = subparsers.add_parser("eval", help="Score one protocol's exported FSM")
    evaluate.add_argument("--protocol", required=True)
    evaluate.add_argument("--model-name", default=runner.MODEL_NAME)
    evaluate.add_argument("--output-base", default=None)
    evaluate.add_argument("--export-path", default=None)
    evaluate.add_argument("--namespace", default=None)
    evaluate.add_argument("--force", action="store_true")
    evaluate.set_defaults(func=cmd_eval)

    baselines = subparsers.add_parser(
        "eval-baselines", help="Score the 9 shipped baselines on all protocols"
    )
    baselines.add_argument("--protocols", nargs="+", default=None)
    baselines.set_defaults(func=cmd_eval_baselines)

    run_many = subparsers.add_parser(
        "run-many", help="Run several protocols, one subprocess each"
    )
    run_many.add_argument("--protocols", nargs="+", required=True)
    run_many.add_argument("--workers", type=int, default=concurrency_default_workers())
    run_many.add_argument("--no-resume", action="store_true")
    run_many.add_argument("--no-eval", action="store_true")
    run_many.add_argument("--rerun", action="store_true",
                          help="Re-run protocols that already completed: archives each "
                               "namespace to attempt_<n>/ and recomputes from scratch "
                               "(implies --no-resume, and pays the full cost again)")
    run_many.set_defaults(func=cmd_run_many)

    archive = subparsers.add_parser(
        "archive", help="Move superseded runs to archive_output/ and refresh cost.json")
    archive.add_argument("--protocols", nargs="+", default=None)
    archive.set_defaults(func=cmd_archive)

    anonymity_check = subparsers.add_parser(
        "anonymity-check",
        help="Fail if anything under PSM_Benchmark/ identifies a person or machine")
    anonymity_check.set_defaults(func=cmd_anonymity_check)


    summary = subparsers.add_parser("summarize", help="Build the comparison tables")
    summary.add_argument("--namespace", default="generic")
    summary.set_defaults(func=cmd_summarize)

    return parser


def main(argv: list[str] | None = None) -> None:
    _redact_output()          # must precede _setup_logging: see the docstring
    _setup_logging()
    anonymity.install_log_redaction()
    _use_repo_root()
    args = build_parser().parse_args(argv)
    if getattr(args, "max_sections", None) and args.namespace != "smoke":
        LOGGER.info("--max-sections given: forcing --namespace smoke")
        args.namespace = "smoke"
    try:
        args.func(args)
    except runner.FatalApiError as exc:
        if runner._ACTIVE_OUTPUT_BASE is not None:
            runner._write_abort_record(
                runner._ACTIVE_OUTPUT_BASE, exc,
                rate_limit_retries=runner.LAST_RATE_LIMIT_COUNT["value"],
            )
            LOGGER.error("Abort record: %s",
                         runner._ACTIVE_OUTPUT_BASE / "abort.json")
        LOGGER.error("FATAL API ERROR — aborting without further retries: %s", exc)
        if exc.regain_access:
            LOGGER.error("Access returns %s. Re-run the same command then; every "
                         "completed stage is cached and will not be recomputed.",
                         exc.regain_access)
        raise SystemExit(3) from exc
    except runner.RunError as exc:
        LOGGER.error("HARD FAIL: %s", exc)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()

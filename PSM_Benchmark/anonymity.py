#!/usr/bin/env python3
"""
anonymity.py
------------
Double-blind guard. The paper is submitted anonymously, so nothing committed under
PSM_Benchmark/ may identify a person or a machine.

Every identity string is obtained AT RUNTIME — from git config, the OS user database
and the hostname — and never hardcoded here or anywhere else. That is deliberate: a
lint that spelled out the name it was hiding would itself leak it.

Checks:
  a. home-directory or user-directory paths (`~`'s runtime value, /Users/, /home/)
  b. the configured git user name and email, and the email's domain
  c. the local hostname (and its short form)
  d. API-key shapes (sk-ant-…, sk-…, AKIA…, an assigned ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent

# The only exclusions are paths that are not ours to police: git internals, the
# scorer mirror (symlinks into the read-only benchmark clone) and the RFC-derived
# segment text, which is public protocol prose. There is deliberately NO exception for
# scratch or build directories — a file that exists under the package gets checked.
SKIP_DIRS = {"eval_workspace", ".git"}
SKIP_SUFFIXES = {".png", ".pdf", ".jpg", ".jpeg", ".gz", ".zip"}
SKIP_NAMES = {"segments.md"}      # benchmark RFC text, byte-identical by contract

KEY_PATTERNS = (
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{8,}")),
    ("openai-style key", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}")),
    ("aws access key", re.compile(r"AKIA[0-9A-Z]{12,}")),
    ("assigned api key", re.compile(r"(?i)(ANTHROPIC|OPENAI|GEMINI|MISTRAL)_API_KEY\s*[=:]\s*\S+")),
)


class PathRedactingFilter(logging.Filter):
    """
    Rewrite identifying paths in log records before they are emitted.

    The parent pipeline logs absolute paths at every stage boundary
    ("Output: <abs>/final.json", "Costs: …"), and those lines land in run-many's
    per-child log files. Sanitising afterwards is not enough — the file must never
    contain them — so the rewrite happens at the logging boundary, on the record
    itself, for both the message and its arguments.
    """

    def __init__(self) -> None:
        super().__init__()
        home = os.path.expanduser("~")
        self._home = home if home and home not in ("/", "") else None
        self._repo = str(REPO_ROOT)
        self._user_dir = re.compile(r"(?:/Users/|/home/)[A-Za-z0-9._-]+")

    def _scrub(self, value: Any) -> Any:
        # Path objects are the reason the logger-level filter missed our own lines:
        # LOGGER.info("… %s", some_path) passes a PurePath, not a str, so a
        # str-only check skipped it and the formatter stringified it afterwards.
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if not isinstance(value, str):
            return value
        text = value
        if self._repo in text:
            text = text.replace(self._repo + "/", "").replace(self._repo, ".")
        if self._home and self._home in text:
            text = text.replace(self._home, "~")
        text = self._user_dir.sub("~", text)
        return text.replace("~/", "")

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._scrub(a) for a in record.args)
        return True


class RedactingStream:
    """
    A write-through proxy that redacts identifying paths on the way out.

    Logging filters cannot cover everything: the parent pipeline uses print() for its
    stage banners, and a traceback is written by the interpreter itself. Both bypass
    logging entirely. Wrapping the streams catches print, tracebacks and any logging
    handler that writes to them, so redaction does not depend on how a line was
    produced.

    Writes are buffered to line boundaries, because a path can be split across two
    write() calls and a naive per-write substitution would miss it.
    """

    def __init__(self, stream: Any, redactor: "PathRedactingFilter"):
        self._stream = stream
        self._redact = redactor._scrub
        self._pending = ""

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            return self._stream.write(text)
        self._pending += text
        if "\n" not in self._pending:
            return len(text)
        head, _, self._pending = self._pending.rpartition("\n")
        self._stream.write(self._redact(head + "\n"))
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self._stream.write(self._redact(self._pending))
            self._pending = ""
        self._stream.flush()

    def isatty(self) -> bool:
        return getattr(self._stream, "isatty", lambda: False)()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def install_stream_redaction() -> "PathRedactingFilter":
    """
    Wrap stdout and stderr. Must run BEFORE logging.basicConfig, because a
    StreamHandler captures sys.stderr at construction time and would otherwise hold a
    reference to the raw stream.
    """
    import atexit

    redactor = PathRedactingFilter()
    sys.stdout = RedactingStream(sys.stdout, redactor)          # type: ignore[assignment]
    sys.stderr = RedactingStream(sys.stderr, redactor)          # type: ignore[assignment]
    atexit.register(lambda: (sys.stdout.flush(), sys.stderr.flush()))
    return redactor


def redact_text(text: str) -> str:
    """Redact an arbitrary block of text with the same rules (used by run-many)."""
    return PathRedactingFilter()._scrub(text)


def install_log_redaction() -> PathRedactingFilter:
    """
    Attach the filter to the root logger AND to every root handler.

    Both are needed: a Logger-level filter only sees records created by that logger,
    while a Handler-level filter sees everything that handler emits, including records
    propagated up from the parent pipeline's module loggers.
    """
    log_filter = PathRedactingFilter()
    root = logging.getLogger()
    root.addFilter(log_filter)
    for handler in root.handlers:
        handler.addFilter(log_filter)
    return log_filter


class AnonymityError(RuntimeError):
    """A file under PSM_Benchmark/ carries identifying information."""


def _git_config(key: str) -> str:
    try:
        return subprocess.run(["git", "config", "--get", key], capture_output=True,
                              text=True, check=False, cwd=str(REPO_ROOT)).stdout.strip()
    except OSError:
        return ""


# How each identity value is matched. A short login name occurs inside ordinary
# English words and generated slugs, so names are matched as whole words,
# case-sensitively. Paths and email addresses are substring matches: any occurrence
# of those is identifying regardless of what surrounds it.
WORD_MATCHED = {"login name", "hostname", "hostname (short)", "git user.name"}


def identity_strings() -> dict[str, str]:
    """Runtime identity values to search for. Never persisted, never printed."""
    values: dict[str, str] = {}
    home = os.path.expanduser("~")
    if home and home not in ("/", ""):
        values["home directory"] = home
    for label, key in (("git user.name", "user.name"), ("git user.email", "user.email")):
        value = _git_config(key)
        if value:
            values[label] = value
            if label.endswith("email") and "@" in value:
                values["email domain"] = value.split("@", 1)[1]
    try:
        login = getpass.getuser()
        if login and len(login) > 2:
            values["login name"] = login
    except Exception:                                    # noqa: BLE001
        pass
    hostname = socket.gethostname()
    if hostname and len(hostname) > 2:
        values["hostname"] = hostname
        short = hostname.split(".")[0]
        if len(short) > 2:
            values["hostname (short)"] = short
    return values


def candidate_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES:
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            yield path                                   # reported as a violation below
            continue
        yield path


def _segments_for(path: Path) -> Path | None:
    """The benchmark segment text a protocol artifact was derived from, if any."""
    parts = path.parts
    if "protocols" not in parts:
        return None
    index = parts.index("protocols")
    if index + 1 >= len(parts):
        return None
    return THIS_DIR / "protocols" / parts[index + 1] / "input" / "segments.md"


def _quotes_benchmark_text(path: Path, value: str) -> bool:
    """
    True when the hit is the benchmark's own RFC prose rather than anything of ours.

    Stage A and verify quote the specification verbatim by design, so a name that also
    occurs as a word in an RFC — an author, an example mailbox — would otherwise be
    reported as a leak forever. The test is deliberately narrow: the value must appear
    in that protocol's own segments.md, which is byte-identical to the benchmark text
    and is itself excluded from scanning for the same reason.
    """
    segments = _segments_for(path)
    if segments is None or not segments.is_file():
        return False
    try:
        return value in segments.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def scan(root: Path | None = None) -> list[dict[str, Any]]:
    """Return a list of findings; empty means clean."""
    root = root or THIS_DIR
    identities = identity_strings()
    findings: list[dict[str, Any]] = []
    user_dir_re = re.compile(r"(?:/Users/|/home/)[A-Za-z0-9._-]+")
    # `~/…` is just as identifying in context as an absolute path, and compiled
    # bytecode embeds the absolute build path, so .pyc under the package is a
    # violation in itself rather than something to skip.
    tilde_re = re.compile(r"~/[A-Za-z0-9._/-]+")
    home_basename = Path(os.path.expanduser("~")).name

    for path in candidate_files(root):
        relative = path.relative_to(REPO_ROOT)
        if path.name == ".env" or path.name.startswith(".env."):
            findings.append({"file": str(relative), "kind": "env file present",
                             "detail": "credentials must never live under this package"})
            continue
        if path.suffix == ".pyc":
            findings.append({"file": str(relative), "kind": "compiled bytecode",
                             "detail": "embeds the absolute path it was compiled from"})
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Report counts, never the matched text: findings get written into abort
        # records, and quoting the path there would reproduce the very leak being
        # reported. The file and kind are enough to locate it.
        user_hits = user_dir_re.findall(text)
        if user_hits:
            findings.append({"file": str(relative), "kind": "user-directory path",
                             "detail": f"{len(user_hits)} occurrence(s), text withheld"})
        tilde_hits = tilde_re.findall(text)
        if tilde_hits:
            findings.append({"file": str(relative), "kind": "home-relative path",
                             "detail": f"{len(tilde_hits)} occurrence(s), text withheld"})
        if home_basename and len(home_basename) > 2:
            component = re.findall(
                r"[/\\]" + re.escape(home_basename) + r"(?=[/\\\s\"']|$)", text)
            if component and not _quotes_benchmark_text(path, home_basename):
                findings.append({"file": str(relative),
                                 "kind": "home directory name as a path component",
                                 "detail": f"{len(component)} occurrence(s)"})
        for label, value in identities.items():
            if not value:
                continue
            if label in WORD_MATCHED:
                pattern = (r"(?<![A-Za-z0-9_-])" + re.escape(value)
                           + r"(?![A-Za-z0-9_-])")
                hits = re.findall(pattern, text)          # case-SENSITIVE by design
            else:
                hits = [value] * text.count(value)
            if not hits:
                continue
            if _quotes_benchmark_text(path, value):
                continue
            findings.append({"file": str(relative), "kind": f"identity: {label}",
                             "detail": f"{len(hits)} occurrence(s)"})
        for label, pattern in KEY_PATTERNS:
            if pattern.search(text):
                findings.append({"file": str(relative), "kind": f"credential: {label}",
                                 "detail": "redacted"})
    # De-duplicate while preserving order.
    seen, unique = set(), []
    for finding in findings:
        key = (finding["file"], finding["kind"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def purge_bytecode(root: Path | None = None) -> int:
    """
    Delete compiled bytecode under the package before checking.

    .pyc files embed the absolute path they were built from, so they are a genuine
    anonymity violation — but they are also regenerable build output that nothing
    depends on, and any stray tool invocation recreates them. Failing a finished run
    over one would be theatre; removing it is both safe and complete. Anything that
    survives this purge is still reported.
    """
    import shutil

    root = root or THIS_DIR
    removed = 0
    for cache in sorted(root.rglob("__pycache__")):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
            removed += 1
    for compiled in sorted(root.rglob("*.pyc")):
        try:
            compiled.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def assert_clean(root: Path | None = None) -> None:
    # Only purge bytecode on a whole-package check: a scoped call may run while a
    # sibling process is importing, and its __pycache__ is none of our business.
    if root is None:
        purge_bytecode(root)
    findings = scan(root)
    if findings:
        lines = [f"  {f['file']}: {f['kind']} — {f['detail']}" for f in findings[:20]]
        more = "" if len(findings) <= 20 else f"\n  … and {len(findings) - 20} more"
        raise AnonymityError(
            f"{len(findings)} anonymity violation(s) under {(root or THIS_DIR).name}:\n"
            + "\n".join(lines) + more
        )

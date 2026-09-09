#!/usr/bin/env python3
"""
chunking.py
-----------
Turns the PSMBench per-protocol segment files into the subsection-granularity
markdown the A2ABreak pipeline expects, WITHOUT altering a single byte of the
benchmark text.

Rule (identical for all 14 protocols, METHOD.md):
  * A sub-heading is a line matching ^(\\d+(?:\\.\\d+)+)\\.?\\s+(\\S.{0,100})$
    at column 0 of a segment's `content`.
  * The text before the first sub-heading becomes a "preamble" chunk carrying
    the segment's own section_number/section_name.
  * Segments with no sub-heading stay whole.
  * Chunks are lossless contiguous spans: the matched heading line stays inside
    the chunk body, and the spans tile [0, len(content)) exactly.

No token-based splitting, no per-protocol handling, no omitted content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]   # PSM_Benchmark/ -> repo root


def _repo_relative(path: Any) -> str:
    """Repo-root-relative string: nothing persisted may identify a machine or user."""
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


SUBHEADING_RE = re.compile(r"^(\d+(?:\.\d+)+)\.?\s+(\S.{0,100})$")


@dataclass
class Chunk:
    chunk_id: str                 # S001, S002, ...
    segment_index: int
    segment_tag: str
    heading: str                  # "3.3.2 State Machine Overview" (no S-id)
    start_offset: int
    end_offset: int
    text: str = field(repr=False)

    @property
    def title(self) -> str:
        return f"{self.chunk_id} {self.heading}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "segment_index": self.segment_index,
            "segment_tag": self.segment_tag,
            "heading": self.heading,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "char_len": len(self.text),
            "sha256": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }


class ChunkingError(RuntimeError):
    """Raised when a chunking invariant fails (hard fail, never a warning)."""


def _line_spans(content: str) -> list[tuple[int, int, str]]:
    """(start, end_exclusive_of_newline, line) for every line, offsets into content."""
    spans: list[tuple[int, int, str]] = []
    pos = 0
    for line in content.split("\n"):
        spans.append((pos, pos + len(line), line))
        pos += len(line) + 1
    return spans


def split_segment(content: str) -> list[tuple[str | None, int, int]]:
    """
    Split one segment body into (heading_or_None, start_offset, end_offset) spans
    that tile the whole content. heading is None for the preamble span.
    """
    spans = _line_spans(content)
    heads = [(i, m) for i, (_, _, line) in enumerate(spans)
             if (m := SUBHEADING_RE.match(line))]
    if not heads:
        return [(None, 0, len(content))]

    out: list[tuple[str | None, int, int]] = []
    first_start = spans[heads[0][0]][0]
    if first_start > 0:
        out.append((None, 0, first_start))
    for pos, (line_idx, match) in enumerate(heads):
        start = spans[line_idx][0]
        if pos + 1 < len(heads):
            end = spans[heads[pos + 1][0]][0]
        else:
            end = len(content)
        out.append((f"{match.group(1)} {match.group(2)}".strip(), start, end))
    return out


def build_chunks(segments: list[dict[str, Any]]) -> list[Chunk]:
    """Chunk every segment; assign running S-ids across the whole protocol."""
    chunks: list[Chunk] = []
    counter = 0
    for seg_index, segment in enumerate(segments):
        content = segment["content"]
        spans = split_segment(content)

        # Tiling invariant, per segment, on the raw spans (before any escaping).
        rebuilt = "".join(content[a:b] for _, a, b in spans)
        if rebuilt != content:
            raise ChunkingError(
                f"segment {seg_index} ({segment.get('tag')!r}): span concatenation "
                f"does not reproduce content ({len(rebuilt)} vs {len(content)} chars)"
            )
        cursor = 0
        for _, a, b in spans:
            if a != cursor:
                raise ChunkingError(f"segment {seg_index}: spans are not contiguous")
            cursor = b
        if cursor != len(content):
            raise ChunkingError(f"segment {seg_index}: spans do not cover the content")

        seg_number = str(segment.get("section_number", "")).strip()
        seg_name = str(segment.get("section_name", "")).strip()
        seg_tag = str(segment.get("tag", "")).strip()

        for heading, a, b in spans:
            text = content[a:b]
            if heading is None and not text:
                continue          # segment starts on a sub-heading: nothing to emit
            counter += 1
            chunks.append(Chunk(
                chunk_id=f"S{counter:03d}",
                segment_index=seg_index,
                segment_tag=seg_tag,
                heading=heading if heading is not None
                        else f"{seg_number} {seg_name}".strip(),
                start_offset=a,
                end_offset=b,
                text=text,
            ))
    return chunks


def _escape_body(text: str) -> tuple[str, int]:
    """Markdown-protect content lines that would parse as headings. Expected: 0 hits."""
    escaped = 0
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("#"):
            lines[i] = " " + line
            escaped += 1
    return "\n".join(lines), escaped


def render_markdown(protocol: str, chunks: list[Chunk]) -> tuple[str, int]:
    """
    Render the sectioned markdown fetcher.parse_sections consumes.

    The top heading is only the protocol name — no RFC number, no title
    (METHOD.md: the sole model-visible protocol parameter is the protocol name).
    """
    parts: list[str] = [f"## {protocol}", ""]
    escaped_total = 0
    for chunk in chunks:
        body, escaped = _escape_body(chunk.text)
        escaped_total += escaped
        parts.append(f"### {chunk.title}")
        if not body.endswith("\n"):
            body += "\n"
        parts.append(body)
    return "\n".join(parts), escaped_total


def verify_parse(markdown: str, chunks: list[Chunk], protocol: str) -> None:
    """
    Hard-fail invariants against the real parser the pipeline uses
    (fetcher.parse_sections), not a local re-implementation.
    """
    from fetcher import parse_sections           # parent module, imported read-only

    sections = parse_sections(markdown, merge_threshold_words=0)
    if len(sections) != len(chunks):
        raise ChunkingError(
            f"{protocol}: parse_sections produced {len(sections)} sections, "
            f"expected {len(chunks)}"
        )

    seen_ids: dict[str, str] = {}
    for section, chunk in zip(sections, chunks):
        if section.depth != 3:
            raise ChunkingError(
                f"{protocol}: section {section.section_id} has depth {section.depth}, expected 3"
            )
        if section.title != chunk.title:
            raise ChunkingError(
                f"{protocol}: section title {section.title!r} != chunk title {chunk.title!r}"
            )
        if section.section_id != chunk.chunk_id:
            raise ChunkingError(
                f"{protocol}: fetcher derived section_id {section.section_id!r} "
                f"from title {section.title!r}, expected {chunk.chunk_id!r}"
            )
        if section.section_id in seen_ids:
            raise ChunkingError(
                f"{protocol}: duplicate section_id {section.section_id!r} "
                f"({seen_ids[section.section_id]!r} and {section.title!r})"
            )
        seen_ids[section.section_id] = section.title
        if section.content.strip() != chunk.text.strip():
            raise ChunkingError(
                f"{protocol}: parsed content of {chunk.chunk_id} differs from its source span"
            )


def prepare_protocol_text(
    *,
    protocol: str,
    segments_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """
    Materialize data/<PROTOCOL>/segments.md + chunk_map.json and check every
    invariant. Returns a stats dict for the manifest.
    """
    with segments_path.open("r", encoding="utf-8") as f:
        segments = json.load(f)
    if not isinstance(segments, list) or not segments:
        raise ChunkingError(f"{protocol}: {segments_path} is not a non-empty list")

    chunks = build_chunks(segments)
    markdown, escaped = render_markdown(protocol, chunks)
    verify_parse(markdown, chunks, protocol)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "segments.md"
    md_path.write_text(markdown, encoding="utf-8")
    map_path = out_dir / "chunk_map.json"
    map_path.write_text(
        json.dumps(
            {
                "protocol": protocol,
                "segments_file": _repo_relative(segments_path),
                "segments_sha256": hashlib.sha256(
                    segments_path.read_bytes()
                ).hexdigest(),
                "segment_count": len(segments),
                "chunk_count": len(chunks),
                "escaped_heading_lines": escaped,
                "chunks": [c.to_dict() for c in chunks],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    sizes = sorted(len(c.text) for c in chunks)
    blank = sum(1 for c in chunks if not c.text.strip())
    return {
        "protocol": protocol,
        "segments_file": _repo_relative(segments_path),
        "markdown_path": _repo_relative(md_path),
        "chunk_map_path": _repo_relative(map_path),
        "segment_count": len(segments),
        "chunk_count": len(chunks),
        "escaped_heading_lines": escaped,
        "blank_chunks": blank,
        "max_chunk_chars": sizes[-1] if sizes else 0,
        "median_chunk_chars": sizes[len(sizes) // 2] if sizes else 0,
        "total_chunk_chars": sum(sizes),
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
    }

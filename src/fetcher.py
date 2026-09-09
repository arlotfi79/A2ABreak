"""
fetcher.py
----------
Fetches the A2A spec from a URL and parses it into a section hierarchy.

Splitting strategy:
  - ## (depth 2)  = top-level section
  - ### (depth 3) = subsection  ← primary split unit
  - #### (depth 4) = sub-subsection, merged up if < merge_threshold_words

Each chunk fed to the LLM carries its full parent breadcrumb as context.
"""

import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import requests

logger = logging.getLogger(__name__)


@dataclass
class Section:
    """One parsed section chunk, ready to be fed to the LLM."""
    section_id: str          # e.g. "7.1"
    title: str               # e.g. "Protocol Security"
    depth: int               # 2 = ##, 3 = ###, 4 = ####
    parent_path: list[str]   # breadcrumb, e.g. ["7. Authentication", "7.1 Protocol Security"]
    content: str             # full markdown text of this section
    url_anchor: str          # e.g. "#71-protocol-security"
    word_count: int = field(init=False)

    def __post_init__(self):
        self.word_count = len(self.content.split())

    @property
    def context_header(self) -> str:
        """Returns parent breadcrumb as markdown headers for LLM context."""
        lines = []
        for i, p in enumerate(self.parent_path[:-1]):
            prefix = "#" * (2 + i)
            lines.append(f"{prefix} {p}")
        return "\n".join(lines)

    @property
    def full_text(self) -> str:
        """Full text including parent context headers, fed to the LLM."""
        parts = []
        if self.context_header:
            parts.append(self.context_header)
        depth_prefix = "#" * self.depth
        parts.append(f"{depth_prefix} {self.title}")
        parts.append(self.content)
        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "depth": self.depth,
            "parent_path": self.parent_path,
            "url_anchor": self.url_anchor,
            "word_count": self.word_count,
            "content": self.content,
        }


def _heading_depth(line: str) -> Optional[int]:
    """Return heading depth (2/3/4) if line is a heading, else None."""
    match = re.match(r"^(#{2,4})\s+", line)
    if match:
        return len(match.group(1))
    return None


def _clean_title(title: str) -> str:
    """
    Clean a heading title:
    - Strip [¶](#anchor) MkDocs links
    - Strip &para; entity (before or after decoding)
    - Strip the ¶ character itself
    - Remove backslash escapes (e.g. \. added by some converters)
    """
    title = re.sub(r"\[¶\]\(#[^)]+\)", "", title)  # [¶](#anchor) links
    title = title.replace("&para;", "")              # HTML entity form
    title = title.replace("¶", "")                  # decoded character form
    title = title.replace("\\\\.", ".")                # backslash-escaped dots
    return title.strip()


def _heading_title(line: str) -> str:
    """Extract and clean title text from a heading line."""
    title = re.sub(r"^#{2,4}\s+", "", line).strip()
    return _clean_title(title)


def _title_to_anchor(title: str) -> str:
    """Convert heading title to URL anchor format."""
    anchor = title.lower()
    anchor = re.sub(r"[^\w\s-]", "", anchor)
    anchor = re.sub(r"\s+", "-", anchor).strip("-")
    return f"#{anchor}"


def _extract_section_id(title: str) -> str:
    """
    Extract numeric or alphanumeric section ID from title.
    e.g. "7.1. Protocol Security" -> "7.1"
         "Appendix A. Migration"  -> "A"
    """
    match = re.match(r"^([A-Z0-9]+(?:\.[0-9]+)*\.?)\s+", title)
    if match:
        return match.group(1).rstrip(".")
    return title[:20]  # fallback: first 20 chars


def _strip_tags(text: str) -> str:
    """Strip all HTML tags from a string and collapse whitespace."""
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def fetch_markdown(url_or_path: str) -> str:
    """
    Fetch spec from a URL or load from a local file.
    Supports .md files directly or HTML pages (spec website).

    Args:
        url_or_path: Either a URL (https://...) or a local file path
    """
    # ── Local file ──
    if not url_or_path.startswith("http"):
        local_path = Path(url_or_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Local spec file not found: {local_path}")
        logger.info(f"Loading spec from local file: {local_path}")
        return local_path.read_text(encoding="utf-8")

    # ── URL (HTML) ──
    logger.info(f"Fetching spec from {url_or_path}")
    headers = {"User-Agent": "A2A-Pipeline-Researcher/1.0"}
    resp = requests.get(url_or_path, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.text

    # Find the start of the actual spec content, skip nav/header boilerplate
    content_markers = [
        r"# Agent2Agent \(A2A\) Protocol Specification",
        r"## 1\. Introduction",
    ]
    start_pos = 0
    for marker in content_markers:
        m = re.search(marker, text)
        if m:
            start_pos = m.start()
            break

    if start_pos:
        text = text[start_pos:]
        logger.info(f"Extracted {len(text)} chars of spec content")
    else:
        logger.warning("Could not find spec content markers, using full page")

    # Convert HTML headings → markdown headings before stripping other tags
    text = re.sub(r"<h2[^>]*>(.*?)</h2>", lambda m: f"\n\n## {_strip_tags(m.group(1))}\n\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h3[^>]*>(.*?)</h3>", lambda m: f"\n\n### {_strip_tags(m.group(1))}\n\n", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<h4[^>]*>(.*?)</h4>", lambda m: f"\n\n#### {_strip_tags(m.group(1))}\n\n", text, flags=re.DOTALL | re.IGNORECASE)

    # Convert block elements to newlines so content isn't run together
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Decode HTML entities — &para; must come before the generic &#...; pattern
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&para;", "")   # pilcrow — MkDocs section anchor symbol
    text = re.sub(r"&#\d+;", "", text)  # any remaining numeric entities
    text = text.replace("¶", "")        # decoded pilcrow character (safety net)

    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def parse_sections(
    markdown: str,
    merge_threshold_words: int = 200,
) -> list[Section]:
    """
    Parse markdown into Section objects.

    Pass 1: Walk lines, detect heading levels 2/3/4, collect content blocks.
    Pass 2: Build hierarchy (## > ### > ####), merge short #### into parent ###,
            attach parent breadcrumbs to every chunk.
    """
    lines = markdown.split("\n")

    # ── Pass 1: raw blocks ────────────────────────────────────────────────────
    raw_blocks = []
    current_depth = None
    current_title = None
    current_lines = []

    for line in lines:
        depth = _heading_depth(line)
        if depth is not None and depth <= 4:
            if current_title is not None:
                raw_blocks.append((
                    current_depth,
                    current_title,
                    "\n".join(current_lines).strip()
                ))
            current_depth = depth
            current_title = _heading_title(line)
            current_lines = []
        else:
            if current_title is not None:
                current_lines.append(line)

    if current_title is not None:
        raw_blocks.append((
            current_depth,
            current_title,
            "\n".join(current_lines).strip()
        ))

    # ── Pass 2: build hierarchy ───────────────────────────────────────────────
    sections = []
    current_h2_title = None
    current_h3_title = None
    current_h3_content_parts = []

    def flush_h3():
        if current_h3_title is None:
            return
        content = "\n\n".join(current_h3_content_parts).strip()
        parent_path = []
        if current_h2_title:
            parent_path.append(current_h2_title)
        parent_path.append(current_h3_title)
        sections.append(Section(
            section_id=_extract_section_id(current_h3_title),
            title=current_h3_title,
            depth=3,
            parent_path=parent_path,
            content=content,
            url_anchor=_title_to_anchor(current_h3_title),
        ))

    for depth, title, content in raw_blocks:
        if depth == 2:
            flush_h3()
            current_h2_title = title
            current_h3_title = None
            current_h3_content_parts = []
            # Keep ## intro text as its own chunk if substantial
            if len(content.split()) > 20:
                sections.append(Section(
                    section_id=_extract_section_id(title),
                    title=title,
                    depth=2,
                    parent_path=[title],
                    content=content,
                    url_anchor=_title_to_anchor(title),
                ))

        elif depth == 3:
            flush_h3()
            current_h3_title = title
            current_h3_content_parts = [content] if content else []

        elif depth == 4:
            current_h3_content_parts.append(f"#### {title}\n\n{content}")

    flush_h3()

    logger.info(f"Parsed {len(sections)} section chunks")
    return sections
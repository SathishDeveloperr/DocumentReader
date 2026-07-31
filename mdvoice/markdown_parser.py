"""Turn a Markdown document into speech-ready 'chapters' (playlist tracks).

The parser is deliberately hand-rolled instead of using a full Markdown AST:
we only care about *what should be spoken*, which is a much smaller problem
than rendering HTML. Block elements that make bad audio (code fences, tables,
images, horizontal rules) are dropped and optionally announced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

# --------------------------------------------------------------------------
# Block-level patterns
# --------------------------------------------------------------------------
FRONTMATTER_RE = re.compile(r"\A(?:---|\+\+\+)\r?\n.*?\r?\n(?:---|\+\+\+)\s*\r?\n", re.DOTALL)
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
HR_RE = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")
TABLE_ROW_RE = re.compile(r"^\s{0,3}\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s{0,3}\|?[\s:|-]*-[\s:|-]*\|?\s*$")
LIST_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?")
BLOCKQUOTE_RE = re.compile(r"^\s*>+\s?")
FOOTNOTE_DEF_RE = re.compile(r"^\s{0,3}\[\^[^\]]+\]:")
LINK_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*\S+")

# --------------------------------------------------------------------------
# Inline patterns
# --------------------------------------------------------------------------
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
INLINE_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
REF_LINK_RE = re.compile(r"\[([^\]]*)\]\[[^\]]*\]")
AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+|mailto:[^>\s]+)>")
BARE_URL_RE = re.compile(r"\bhttps?://\S+")
INLINE_CODE_RE = re.compile(r"`([^`]*)`")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
FOOTNOTE_REF_RE = re.compile(r"\[\^[^\]]+\]")
EMPHASIS_RE = [
    (re.compile(r"\*\*\*(.+?)\*\*\*", re.DOTALL), r"\1"),
    (re.compile(r"___(.+?)___", re.DOTALL), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"__(.+?)__", re.DOTALL), r"\1"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.DOTALL), r"\1"),
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", re.DOTALL), r"\1"),
    (re.compile(r"~~(.+?)~~", re.DOTALL), r"\1"),
    (re.compile(r"==(.+?)==", re.DOTALL), r"\1"),
]
ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|])")

# Small pronunciation dictionary so common written shorthand reads naturally.
SPOKEN_REPLACEMENTS = [
    (re.compile(r"\be\.g\.", re.I), "for example"),
    (re.compile(r"\bi\.e\.", re.I), "that is"),
    (re.compile(r"\betc\.", re.I), "et cetera"),
    (re.compile(r"\bvs\.?\b", re.I), "versus"),
    (re.compile(r"\bapprox\.", re.I), "approximately"),
    (re.compile(r"&amp;"), " and "),
    (re.compile(r"&nbsp;"), " "),
    (re.compile(r"&lt;"), " less than "),
    (re.compile(r"&gt;"), " greater than "),
    (re.compile(r"(?<=\w)&(?=\w)"), " and "),
]


@dataclass
class Chapter:
    """One playlist track: a heading plus everything under it."""

    index: int
    title: str
    level: int
    text: str = ""

    @property
    def chars(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "level": self.level,
            "chars": self.chars,
            "preview": (self.text[:180] + "…") if len(self.text) > 180 else self.text,
            "text": self.text,
        }


@dataclass
class ParsedDocument:
    title: str
    chapters: List[Chapter] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(c.chars for c in self.chapters)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "total_chars": self.total_chars,
            "chapters": [c.to_dict() for c in self.chapters],
        }


# --------------------------------------------------------------------------
# Inline cleanup
# --------------------------------------------------------------------------
def clean_inline(text: str, read_urls: bool = False) -> str:
    """Strip Markdown/HTML markup from a run of text, leaving speakable words."""
    text = HTML_COMMENT_RE.sub(" ", text)
    text = IMAGE_RE.sub(lambda m: f" {m.group(1)} " if m.group(1).strip() else " ", text)
    text = INLINE_LINK_RE.sub(r"\1", text)
    text = REF_LINK_RE.sub(r"\1", text)
    text = AUTOLINK_RE.sub(" link " if not read_urls else r"\1", text)
    if not read_urls:
        text = BARE_URL_RE.sub(" link ", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = FOOTNOTE_REF_RE.sub("", text)
    for pattern, repl in EMPHASIS_RE:
        text = pattern.sub(repl, text)
    text = HTML_TAG_RE.sub(" ", text)
    text = ESCAPE_RE.sub(r"\1", text)
    for pattern, repl in SPOKEN_REPLACEMENTS:
        text = pattern.sub(repl, text)
    text = text.replace("|", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _ends_sentence(line: str) -> bool:
    return bool(line) and line[-1] in ".!?:;,"


def _punctuate(line: str) -> str:
    """Give the synthesiser a hint to breathe at the end of a block."""
    line = line.strip()
    if not line:
        return line
    return line if _ends_sentence(line) else line + "."


# --------------------------------------------------------------------------
# Main parser
# --------------------------------------------------------------------------
def parse_markdown(
    source: str,
    *,
    doc_title: str = "Untitled document",
    split_level: int = 2,
    skip_code: bool = True,
    skip_tables: bool = True,
    announce_skips: bool = True,
    speak_headings: bool = True,
    read_urls: bool = False,
) -> ParsedDocument:
    """Parse Markdown text into chapters ready for text-to-speech.

    split_level -- start a new chapter at headings of this level or shallower.
    skip_code / skip_tables -- drop those blocks from the audio.
    announce_skips -- say "Code block skipped." instead of silently dropping.
    """
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = FRONTMATTER_RE.sub("", source)
    source = HTML_COMMENT_RE.sub(" ", source)
    lines = source.split("\n")

    chapters: List[Chapter] = []
    buffer: List[str] = []
    current = Chapter(index=0, title=doc_title, level=0)
    first_h1: str | None = None

    def flush_chapter() -> None:
        nonlocal current, buffer
        body = "\n".join(p for p in buffer if p).strip()
        current.text = body
        if current.text:
            current.index = len(chapters)
            chapters.append(current)
        buffer = []

    in_fence = False
    fence_marker = ""
    i = 0
    while i < len(lines):
        raw = lines[i]

        # ---- fenced code blocks -------------------------------------------
        fence = FENCE_RE.match(raw)
        if in_fence:
            if fence and raw.strip().startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            elif not skip_code:
                buffer.append(clean_inline(raw, read_urls))
            i += 1
            continue
        if fence:
            in_fence = True
            fence_marker = fence.group(1)[0] * 3
            if skip_code and announce_skips:
                buffer.append("Code block skipped.")
            i += 1
            continue

        stripped = raw.strip()

        # ---- headings ------------------------------------------------------
        heading = HEADING_RE.match(raw)
        if heading:
            level = len(heading.group(1))
            title = clean_inline(heading.group(2), read_urls) or "Untitled section"
            if level == 1 and first_h1 is None:
                first_h1 = title
            if level <= split_level:
                flush_chapter()
                current = Chapter(index=len(chapters), title=title, level=level)
                if speak_headings:
                    buffer.append(_punctuate(title))
            else:
                if speak_headings:
                    buffer.append(_punctuate(title))
            i += 1
            continue

        # ---- setext headings (Title\n=====) --------------------------------
        if (
            stripped
            and i + 1 < len(lines)
            and re.fullmatch(r"\s{0,3}(=+|-{2,})\s*", lines[i + 1] or "")
            and not LIST_RE.match(raw)
            and not TABLE_ROW_RE.match(raw)
        ):
            level = 1 if lines[i + 1].strip().startswith("=") else 2
            title = clean_inline(stripped, read_urls)
            if title:
                if level == 1 and first_h1 is None:
                    first_h1 = title
                if level <= split_level:
                    flush_chapter()
                    current = Chapter(index=len(chapters), title=title, level=level)
                if speak_headings:
                    buffer.append(_punctuate(title))
                i += 2
                continue

        # ---- tables --------------------------------------------------------
        if TABLE_ROW_RE.match(raw) or (
            TABLE_SEP_RE.match(raw) and stripped.count("-") >= 3 and "|" in stripped
        ):
            block: List[str] = []
            while i < len(lines) and (
                TABLE_ROW_RE.match(lines[i]) or TABLE_SEP_RE.match(lines[i])
            ) and lines[i].strip():
                block.append(lines[i])
                i += 1
            if skip_tables:
                if announce_skips:
                    buffer.append("Table skipped.")
            else:
                for row in block:
                    if TABLE_SEP_RE.match(row) and set(row.strip()) <= set("|-: "):
                        continue
                    cells = [clean_inline(c, read_urls) for c in row.strip().strip("|").split("|")]
                    cells = [c for c in cells if c]
                    if cells:
                        buffer.append(_punctuate(", ".join(cells)))
            continue

        # ---- throwaway lines ------------------------------------------------
        if HR_RE.match(raw) or FOOTNOTE_DEF_RE.match(raw) or LINK_DEF_RE.match(raw):
            i += 1
            continue

        if not stripped:
            if buffer and buffer[-1] != "":
                buffer.append("")
            i += 1
            continue

        # ---- ordinary text --------------------------------------------------
        line = BLOCKQUOTE_RE.sub("", raw)
        was_list = bool(LIST_RE.match(line))
        line = LIST_RE.sub("", line)
        spoken = clean_inline(line, read_urls)
        if spoken:
            buffer.append(_punctuate(spoken) if was_list else spoken)
        i += 1

    flush_chapter()

    # Collapse blank markers into paragraph breaks and tidy each chapter.
    for chapter in chapters:
        paragraphs = [p.strip() for p in chapter.text.split("\n")]
        merged: List[str] = []
        run: List[str] = []
        for para in paragraphs:
            if para:
                run.append(para)
            elif run:
                merged.append(" ".join(run))
                run = []
        if run:
            merged.append(" ".join(run))
        cleaned = "\n\n".join(_punctuate(m) for m in merged if m)
        chapter.text = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    chapters = [c for c in chapters if c.text]
    for n, chapter in enumerate(chapters):
        chapter.index = n

    title = first_h1 or (chapters[0].title if chapters else doc_title)
    if title in ("", "Untitled section"):
        title = doc_title
    return ParsedDocument(title=title, chapters=chapters)

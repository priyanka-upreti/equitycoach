#!/usr/bin/env python3
"""Parse printed TOCs from books, resolve book-page to PDF-page via offset detection.

For Selected Issues and Consider Your Options — the two books where content-scanning
produced unreliable maps — we parse their printed TOC directly, then find the
book-page-to-PDF-page offset by searching for a specific known heading in content.

The result replaces the content-scan entries in chapter_maps.json for these books.
"""

import json
import re
import sys
import warnings
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

warnings.filterwarnings("ignore")

MATERIALS_DIR = Path("/Users/kepant/Documents/CEPI/Study Materials New")
MAP_PATH = Path(__file__).parent.parent / "corpus" / "chapter_maps.json"

# Per-book config for TOC parsing
BOOKS = {
    "selected_issues": {
        "file": "Selected Issues 22nd ed.pdf",
        "toc_pages": range(3, 16),
        "anchor_text": "federal securities law considerations",
        "anchor_book_page": 47,
        "content_scan_skip": 20,
        "max_chapter": 20,  # Selected Issues has 14 chapters; reject markers > 20
    },
    "consider_your_options": {
        "file": "Consider Your Options 2026.pdf",
        "toc_pages": range(4, 8),
        "anchor_text": "the big picture",
        "anchor_book_page": 3,
        "content_scan_skip": 10,
        "max_chapter": 40,  # CVO has 34 chapters
    },
}

# Words that, if immediately before a digit, indicate the digit is embedded
# in the title (Section 423) not a page number
EMBEDDED_DIGIT_PREFIXES = (
    "Section", "IRC", "Rule", "Article", "Reg.", "Regulation", "Part",
    "Chapter", "§", "Sec.", "Form", "Code", "Level", "SB", "SFAS", "ASC",
)

# Match a TOC line: marker (Chapter N: / N.N.N / Appendix X:) + title + trailing page
LINE_ENTRY = re.compile(
    r"^\s*(?:Chapter\s+(\d+)[:.]?|(\d+(?:\.\d+){0,4})|Appendix\s+([A-Z0-9][A-Z0-9\-]*):?)\s+(.+?)\s+(\d{1,3})\s*$"
)
# Marker-only (line starts with a marker but title/page continues on next line)
LINE_MARKER_ONLY = re.compile(
    r"^\s*(?:Chapter\s+(\d+)[:.]?|(\d+(?:\.\d+){0,4})|Appendix\s+([A-Z0-9][A-Z0-9\-]*):?)\s+(.+?)\s*$"
)
# Continuation line ending with page number (no marker)
LINE_TRAILING_PAGE = re.compile(r"^(.*?)\s+(\d{1,3})\s*$")

# Headers/footers to skip
SKIP_PATTERNS = [
    re.compile(r"^Contents\s*\|", re.IGNORECASE),
    re.compile(r"^[ivxlcdm]+\s*\|", re.IGNORECASE),
    re.compile(r"\|\s*[ivxlcdm]+\s*$", re.IGNORECASE),
    re.compile(r"^SELECTED ISSUES", re.IGNORECASE),
    re.compile(r"^CONSIDER YOUR OPTIONS", re.IGNORECASE),
    re.compile(r"^\s*Kaye A\. Thomas", re.IGNORECASE),
    # Author byline patterns: e.g., "Barbara Baksa", "Eric Orsic" - short 2-3 word names
]


def is_skip_line(line: str) -> bool:
    line = line.strip()
    if not line:
        return True
    return any(p.match(line) for p in SKIP_PATTERNS)


def is_embedded_digit(text_before_digit: str) -> bool:
    """Is this digit part of the title (e.g., 'Section 423') rather than a page number?"""
    tail = text_before_digit.rstrip()
    for prefix in EMBEDDED_DIGIT_PREFIXES:
        if tail.endswith(prefix) or tail.endswith(prefix + " "):
            return True
    return False


def valid_chapter_number(chapter_word: Optional[str], chapter_dotted: Optional[str], max_chapter: int, appendix: Optional[str] = None) -> bool:
    """Sanity check: chapter's first component must be <= max_chapter. Appendix always valid."""
    if appendix is not None:
        return True
    try:
        if chapter_word is not None:
            return 1 <= int(chapter_word) <= max_chapter
        if chapter_dotted is not None:
            first = int(chapter_dotted.split(".")[0])
            return 1 <= first <= max_chapter
    except ValueError:
        return False
    return False


def marker_key(chapter_word, chapter_dotted, appendix):
    """Turn the matched groups into a canonical chapter map key."""
    if chapter_word:
        return f"Chapter {chapter_word}"
    if appendix:
        return f"Appendix {appendix}"
    return chapter_dotted


def parse_toc(reader: PdfReader, toc_pages: range, max_chapter: int = 20) -> list:
    """Return list of (chapter_marker, title, book_page).

    Handles multi-line TOC entries by tracking a "pending" chapter marker across
    lines until we find a line ending with a page number.
    """
    all_lines = []
    for p in toc_pages:
        if p > len(reader.pages):
            continue
        text = reader.pages[p - 1].extract_text() or ""
        all_lines.extend(text.split("\n"))

    entries = []
    pending_marker = None
    pending_title_parts = []

    for raw in all_lines:
        line = raw.strip()
        if is_skip_line(line):
            continue

        # Try full pattern: marker + title + page (single-line entry)
        m_full = LINE_ENTRY.match(line)
        if m_full:
            chapter_word, chapter_dotted, appendix, title, page = m_full.groups()

            # Sanity: valid chapter number (appendix always valid)
            if not valid_chapter_number(chapter_word, chapter_dotted, max_chapter, appendix):
                # Not a real marker — could be a continuation line whose trailing
                # digit was misread. If we have pending, treat as continuation.
                if pending_marker is not None:
                    m_pg = LINE_TRAILING_PAGE.match(line)
                    if m_pg:
                        title_end, page_str = m_pg.groups()
                        if not is_embedded_digit(title_end):
                            page_int = int(page_str)
                            if 1 <= page_int <= 500:
                                joined = " ".join(pending_title_parts + [title_end.strip()])
                                entries.append((pending_marker, joined.strip(), page_int))
                                pending_marker = None
                                pending_title_parts = []
                                continue
                    pending_title_parts.append(line)
                continue

            # Reject if the "page" is actually embedded in title (e.g., "Section 423")
            if is_embedded_digit(title):
                pending_marker = marker_key(chapter_word, chapter_dotted, appendix)
                pending_title_parts = [f"{title} {page}".strip()]
                continue

            # Save any pending incomplete entry (drop it — likely garbage)
            pending_marker = None
            pending_title_parts = []

            chapter = marker_key(chapter_word, chapter_dotted, appendix)
            page_int = int(page)
            if 1 <= page_int <= 500:
                entries.append((chapter, title.strip(), page_int))
            continue

        # Try marker-only pattern: line starts with marker, no trailing page
        m_marker = LINE_MARKER_ONLY.match(line)
        if m_marker:
            chapter_word, chapter_dotted, appendix, title_start = m_marker.groups()

            # Sanity: valid chapter number
            if not valid_chapter_number(chapter_word, chapter_dotted, max_chapter, appendix):
                if pending_marker is not None:
                    pending_title_parts.append(line)
                continue

            # Save any pending incomplete entry (drop)
            pending_marker = None
            pending_title_parts = []

            pending_marker = marker_key(chapter_word, chapter_dotted, appendix)
            pending_title_parts = [title_start.strip()]
            continue

        # If we have a pending marker, check if this line completes it (has page)
        if pending_marker is not None:
            m_pg = LINE_TRAILING_PAGE.match(line)
            if m_pg:
                title_end, page = m_pg.groups()
                page_int = int(page)
                if 1 <= page_int <= 500:
                    title = " ".join(pending_title_parts + [title_end.strip()])
                    entries.append((pending_marker, title.strip(), page_int))
                    pending_marker = None
                    pending_title_parts = []
                    continue
            # No page yet — this is a continuation of the title
            pending_title_parts.append(line)

    return entries


def find_offset(
    reader: PdfReader, anchor_text: str, anchor_book_page: int, skip: int
) -> Optional[int]:
    """Case-insensitive search for anchor_text; return offset = pdf_page - book_page."""
    anchor_lower = anchor_text.lower()
    for page_idx in range(skip, len(reader.pages)):
        text = (reader.pages[page_idx].extract_text() or "").lower()
        if anchor_lower in text:
            return (page_idx + 1) - anchor_book_page
    return None


def is_descendant(child: str, parent: str) -> bool:
    return child.startswith(parent + ".")


def build_map_from_toc(entries, offset: int, total_pages: int) -> dict:
    """Convert (chapter, title, book_page) entries into ranged chapter map.

    Sort by PDF page, then for each entry:
    - start = book_page + offset
    - end   = (next non-descendant entry's start) - 1
    """
    # Compute PDF pages
    resolved = []
    for chapter, title, book_page in entries:
        pdf_page = book_page + offset
        if pdf_page < 1 or pdf_page > total_pages:
            continue
        resolved.append((chapter, title, pdf_page))

    # Sort by PDF page
    resolved.sort(key=lambda x: x[2])

    chapter_map = {}

    for i, (chapter, title, page) in enumerate(resolved):
        # Determine end page:
        # If this is a numeric marker (like "1.5"), end at next non-descendant.
        # If this is a "Chapter N" marker, end at next "Chapter N+1" or next top-level.
        end_page = total_pages
        for j in range(i + 1, len(resolved)):
            next_chapter, _, next_page = resolved[j]
            if not is_descendant(next_chapter, chapter):
                # Also skip "Chapter N" -> "N.M" continuation (e.g., Chapter 2 followed by 2.1)
                if chapter.startswith("Chapter "):
                    ch_num = chapter.split()[-1]
                    if next_chapter.startswith(ch_num + "."):
                        continue  # 2.1 is child of Chapter 2, keep searching
                end_page = max(page, next_page - 1)
                break

        if chapter not in chapter_map:
            chapter_map[chapter] = {
                "start": page,
                "end": end_page,
                "title": title,
                "kind": "chapter_word" if chapter.startswith("Chapter ") else "numeric",
                "detection": "toc_parse",
            }

    return chapter_map


def process_book(book_id: str, cfg: dict) -> dict:
    pdf_path = MATERIALS_DIR / cfg["file"]
    reader = PdfReader(str(pdf_path), strict=False)
    total_pages = len(reader.pages)

    entries = parse_toc(reader, cfg["toc_pages"], cfg.get("max_chapter", 20))
    print(f"  Parsed {len(entries)} TOC entries", file=sys.stderr)

    offset = find_offset(
        reader,
        cfg["anchor_text"],
        cfg["anchor_book_page"],
        cfg["content_scan_skip"],
    )
    if offset is None:
        print(
            f"  [WARN] Could not find anchor '{cfg['anchor_text']}' — skipping",
            file=sys.stderr,
        )
        return {}
    print(
        f"  Offset detected: book_page + {offset} = PDF_page  "
        f"(anchor '{cfg['anchor_text']}' book_page={cfg['anchor_book_page']})",
        file=sys.stderr,
    )

    book_map = build_map_from_toc(entries, offset, total_pages)
    return book_map


def main() -> int:
    with open(MAP_PATH) as f:
        maps = json.load(f)

    for book_id, cfg in BOOKS.items():
        print(f"\n=== {book_id} ===", file=sys.stderr)
        book_map = process_book(book_id, cfg)
        if book_map:
            # Replace the content-scan garbage entirely for these books
            maps[book_id] = book_map
            # For chapter maps built from TOC, add "Chapter N" entries as chapters
            # AND alias them without prefix (2 -> Chapter 2 alias) so syllabus refs
            # like "13" can resolve to "Chapter 13"
            aliases = {}
            for key, val in book_map.items():
                if key.startswith("Chapter "):
                    num = key.split()[-1]
                    if num not in book_map:
                        aliases[num] = val
            book_map.update(aliases)
            maps[book_id] = book_map
            print(f"  Wrote {len(book_map)} entries", file=sys.stderr)

    MAP_PATH.write_text(json.dumps(maps, indent=2))
    print(f"\nSaved: {MAP_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

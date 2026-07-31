#!/usr/bin/env python3
"""Content-based chapter detection for books without usable PDF outlines.

For Selected Issues, GPS Publications (ESPP sub-book), and Consider Your Options,
scans every content page for lines beginning with chapter/section number patterns
like "1.1", "3.4.2", or "Chapter 2:". Records the first-occurrence page for each
marker. Merges results into chapter_maps.json alongside the outline-extracted maps.

Skips the first `TOC_SKIP` pages of each book to avoid capturing TOC hits as chapter
starts.
"""

import json
import re
import sys
import warnings
from pathlib import Path

from pypdf import PdfReader

warnings.filterwarnings("ignore")

MATERIALS_DIR = Path("/Users/kepant/Documents/CEPI/Study Materials New")
MAP_PATH = Path(__file__).parent.parent / "corpus" / "chapter_maps.json"

# Per-book scan config
# - skip_first: pages to skip (TOC + preface region)
# - scan_end: last page to scan (0 = to end of book)
# - book_id in chapter_maps.json
BOOKS = {
    "selected_issues": {
        "file": "Selected Issues 22nd ed.pdf",
        "skip_first": 20,
        "scan_end": 0,
    },
    "gps_publications": {
        # GPS 4-in-1 has 4 sub-books; L1 only references GPS ESPP (pages ~10-90)
        "file": "GPS 4-in-1 2024.pdf",
        "skip_first": 10,
        "scan_end": 90,
    },
    "consider_your_options": {
        "file": "Consider Your Options 2026.pdf",
        "skip_first": 15,
        "scan_end": 0,
    },
}

# Matches lines starting with "1.1", "1.1.", "10.4.2", "1.1 Title", or "1.1. Title"
# Allows up to 4 dot-separated levels
NUMERIC_LINE_RE = re.compile(r"^(\d+(?:\.\d+){0,3})[\.\s]", re.MULTILINE)

# Matches "Chapter N:" or "Chapter N " or "Chapter N."
CHAPTER_WORD_RE = re.compile(r"^Chapter\s+(\d+)[:\.\s]", re.MULTILINE | re.IGNORECASE)

# Matches "Appendix X" as a standalone heading line
APPENDIX_LINE_RE = re.compile(r"^Appendix\s+([A-Z0-9][A-Z0-9\-]*)", re.MULTILINE)

# Matches "Exhibit X-Y" heading
EXHIBIT_LINE_RE = re.compile(r"^Exhibit\s+(\S+)", re.MULTILINE)

# Matches "Glossary" as heading
GLOSSARY_LINE_RE = re.compile(r"^Glossary\b", re.MULTILINE | re.IGNORECASE)


def scan_book(pdf_path: Path, skip_first: int, scan_end: int) -> dict:
    """Return dict of {marker: first_page} found in the book content."""
    reader = PdfReader(str(pdf_path), strict=False)
    total = len(reader.pages)
    end_page = scan_end if scan_end else total

    first_seen: dict[str, int] = {}

    for page_idx in range(skip_first, min(end_page, total)):
        text = reader.pages[page_idx].extract_text() or ""
        page_num = page_idx + 1

        # Numeric markers (1.1, 3.4.2, etc.)
        for m in NUMERIC_LINE_RE.finditer(text):
            marker = m.group(1)
            if marker not in first_seen:
                first_seen[marker] = page_num

        # "Chapter N" markers
        for m in CHAPTER_WORD_RE.finditer(text):
            marker = m.group(1)  # just the number, e.g. "2"
            if marker not in first_seen:
                first_seen[marker] = page_num

        # Special sections
        for m in APPENDIX_LINE_RE.finditer(text):
            marker = f"Appendix {m.group(1)}"
            if marker not in first_seen:
                first_seen[marker] = page_num

        for m in EXHIBIT_LINE_RE.finditer(text):
            marker = f"Exhibit {m.group(1)}"
            if marker not in first_seen:
                first_seen[marker] = page_num

        if "Glossary" not in first_seen:
            for _ in GLOSSARY_LINE_RE.finditer(text):
                first_seen["Glossary"] = page_num
                break

    return first_seen


def is_descendant(child: str, parent: str) -> bool:
    return child.startswith(parent + ".")


def build_map_from_first_seen(first_seen: dict, total_pages: int) -> dict:
    """Convert {marker: first_page} into ranged chapter map."""
    # Separate numeric vs special
    numeric_markers = []
    special_markers = []
    for marker, page in first_seen.items():
        try:
            # If marker consists only of digits and dots, it's numeric
            _ = [int(p) for p in marker.split(".")]
            numeric_markers.append((marker, page))
        except ValueError:
            special_markers.append((marker, page))

    # Sort numeric markers by page
    numeric_markers.sort(key=lambda x: x[1])
    special_markers.sort(key=lambda x: x[1])
    all_sorted = sorted(numeric_markers + special_markers, key=lambda x: x[1])

    chapter_map: dict = {}

    # Numeric: end at next non-descendant marker's page - 1
    for i, (num, page) in enumerate(numeric_markers):
        end_page = total_pages
        for j in range(i + 1, len(numeric_markers)):
            next_num, next_page = numeric_markers[j]
            if not is_descendant(next_num, num):
                end_page = max(page, next_page - 1)
                break
        chapter_map[num] = {
            "start": page,
            "end": end_page,
            "title": f"(content-scanned) {num}",
            "kind": "numeric",
            "detection": "content_scan",
        }

    # Special: 5-page span default, or until next any marker
    for marker, page in special_markers:
        default_span = 30 if marker in ("Glossary", "Index") else 5
        next_starts = [p for m, p in all_sorted if p > page]
        if next_starts:
            end = min(min(next_starts) - 1, page + default_span)
        else:
            end = min(page + default_span, total_pages)
        chapter_map[marker] = {
            "start": page,
            "end": end,
            "title": marker,
            "kind": marker.split(" ")[0].lower() if " " in marker else marker.lower(),
            "detection": "content_scan",
        }

    return chapter_map


def main() -> int:
    with open(MAP_PATH) as f:
        maps = json.load(f)

    for book_id, cfg in BOOKS.items():
        pdf_path = MATERIALS_DIR / cfg["file"]
        if not pdf_path.exists():
            print(f"[SKIP] {book_id}: file not found", file=sys.stderr)
            continue

        reader = PdfReader(str(pdf_path), strict=False)
        total_pages = len(reader.pages)

        first_seen = scan_book(pdf_path, cfg["skip_first"], cfg["scan_end"])
        book_map = build_map_from_first_seen(first_seen, total_pages)

        # Merge: outline-based entries take precedence when key collides
        existing = maps.get(book_id, {})
        merged = dict(book_map)  # start from content scan
        merged.update(existing)  # outline entries overwrite

        maps[book_id] = merged
        added = len(book_map) - sum(1 for k in book_map if k in existing)
        print(
            f"{book_id:25s}  scanned={len(book_map):4d}  outline_existing={len(existing):4d}  "
            f"merged_total={len(merged):4d}  content_only_added={added}",
            file=sys.stderr,
        )

    MAP_PATH.write_text(json.dumps(maps, indent=2))
    print(f"\nUpdated: {MAP_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

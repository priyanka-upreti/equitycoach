#!/usr/bin/env python3
"""Build chapter -> page range mappings from PDF outlines.

For each of the 4 L1 textbooks with clean PDF outlines, walks the outline tree
and produces a mapping like:

    {
      "stock_options_book": {
        "1.1": {"start": 5, "end": 15, "title": "1.1 What Is a Stock Option?"},
        "10.4": {"start": 265, "end": 280, "title": "10.4 Liability Awards"},
        "Exhibit 10-2": {...},
        "Glossary": {...},
        ...
      },
      ...
    }

Consider Your Options 2026 has no PDF outline — handled separately.
"""

import json
import re
import sys
import warnings
from pathlib import Path

from pypdf import PdfReader

warnings.filterwarnings("ignore")

MATERIALS_DIR = Path("/Users/kepant/Documents/CEPI/Study Materials New")
OUT = Path(__file__).parent.parent / "corpus" / "chapter_maps.json"

# Book ID (matches syllabus_l1.json) -> filename
BOOKS = {
    "stock_options_book": "Stock Options Book 26th ed.pdf",
    "selected_issues": "Selected Issues 22nd ed.pdf",
    "equity_alternatives": "Equity Alternatives 23rd ed.pdf",
    "gps_publications": "GPS 4-in-1 2024.pdf",
}

# Regexes to extract markers from outline titles
NUMERIC_RE = re.compile(r"^(\d+(?:\.\d+){0,3})(?=[\s\.]|$)")
APPENDIX_RE = re.compile(r"^Appendix\s+([A-Z0-9][A-Z0-9\-]*)", re.IGNORECASE)
EXHIBIT_RE = re.compile(r"^Exhibit\s+(\S+)", re.IGNORECASE)
TABLE_RE = re.compile(r"^Table\s+(\S+)", re.IGNORECASE)
GLOSSARY_RE = re.compile(r"^Glossary\b", re.IGNORECASE)
INDEX_RE = re.compile(r"^Index\b", re.IGNORECASE)


def extract_marker(title: str):
    """Return (marker, kind) or (None, None) for a title."""
    for regex, kind in [
        (NUMERIC_RE, "numeric"),
        (APPENDIX_RE, "appendix"),
        (EXHIBIT_RE, "exhibit"),
        (TABLE_RE, "table"),
    ]:
        m = regex.match(title)
        if m:
            return m.group(1), kind
    if GLOSSARY_RE.match(title):
        return "Glossary", "glossary"
    if INDEX_RE.match(title):
        return "Index", "index"
    return None, None


def get_page_num(reader, dest):
    """Resolve outline destination to 1-indexed page number."""
    try:
        return reader.get_destination_page_number(dest) + 1
    except Exception:
        return None


def walk_outline(reader, items, entries=None):
    """Recursively walk outline, collect (marker, kind, title, page) tuples."""
    if entries is None:
        entries = []
    for item in items:
        if isinstance(item, list):
            walk_outline(reader, item, entries)
        else:
            title = str(getattr(item, "title", "")).strip()
            if not title:
                continue
            page = get_page_num(reader, item)
            if page is None:
                continue
            marker, kind = extract_marker(title)
            if marker:
                entries.append((marker, kind, title, page))
    return entries


def is_descendant(child: str, parent: str) -> bool:
    """True if 'child' (e.g., '1.1.2') is a subsection of 'parent' (e.g., '1.1')."""
    return child.startswith(parent + ".")


def build_map(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path), strict=False)
    if not reader.outline:
        return {}

    entries = walk_outline(reader, reader.outline)
    total_pages = len(reader.pages)
    entries.sort(key=lambda x: x[3])  # by page

    numeric_entries = [e for e in entries if e[1] == "numeric"]
    special_entries = [e for e in entries if e[1] != "numeric"]

    chapter_map: dict = {}

    # Numeric chapters: hierarchical range logic
    # end_page = (page of next non-descendant, non-same-marker entry) - 1
    for i, (num, kind, title, page) in enumerate(numeric_entries):
        end_page = total_pages
        for j in range(i + 1, len(numeric_entries)):
            next_num, _, _, next_page = numeric_entries[j]
            # Skip duplicates: same marker appearing again (some outlines list entries twice)
            if next_num == num:
                continue
            if not is_descendant(next_num, num):
                end_page = max(page, next_page - 1)
                break
        if num not in chapter_map:
            chapter_map[num] = {
                "start": page,
                "end": end_page,
                "title": title,
                "kind": "numeric",
            }

    # Special entries: glossary/index get 30-page cap; others get 5-page cap.
    # If a later entry exists after the special, use (that_page - 1) as end.
    for marker, kind, title, page in special_entries:
        default_span = 30 if kind in ("glossary", "index") else 5
        # Find next entry (any kind) that starts after this page
        next_starts = [e[3] for e in entries if e[3] > page]
        if next_starts:
            end = min(min(next_starts) - 1, page + default_span)
        else:
            end = min(page + default_span, total_pages)

        if kind == "numeric":  # shouldn't happen, but guard
            key = marker
        elif kind == "glossary":
            key = "Glossary"
        elif kind == "index":
            key = "Index"
        else:
            key = f"{kind.capitalize()} {marker}"

        if key not in chapter_map:
            chapter_map[key] = {
                "start": page,
                "end": end,
                "title": title,
                "kind": kind,
            }

    return chapter_map


def main() -> int:
    all_maps: dict = {}
    for book_id, filename in BOOKS.items():
        pdf_path = MATERIALS_DIR / filename
        if not pdf_path.exists():
            print(f"[SKIP] {book_id}: file not found at {pdf_path}", file=sys.stderr)
            continue
        book_map = build_map(pdf_path)
        all_maps[book_id] = book_map
        print(
            f"{book_id:25s}  entries={len(book_map):4d}",
            file=sys.stderr,
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(all_maps, indent=2))
    print(f"\nWritten: {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

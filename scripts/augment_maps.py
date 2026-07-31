#!/usr/bin/env python3
"""Augment chapter_maps.json with:

1. Glossary sections (content-scanned for books without one in the outline/TOC)
2. Exhibits (numbered "Exhibit N-M") and Tables (numbered "Table N-M")
3. Chapter intro aliases ("N Intro"/"N intro" for the pages between chapter start
   and its first subsection)
4. Bare chapter number aliases (where syllabus references "2" but map only has "2.1")
5. Appendix prefix aliases (for Selected Issues, "Ch. 2 Appendix B-1" -> "Appendix B-1")
6. GPS Chapter 11 range fix (cap at end of GPS ESPP sub-book, not entire PDF)

Post-augmentation, the map should resolve close to 100% of L1 syllabus references.
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

BOOK_FILES = {
    "stock_options_book": "Stock Options Book 26th ed.pdf",
    "selected_issues": "Selected Issues 22nd ed.pdf",
    "equity_alternatives": "Equity Alternatives 23rd ed.pdf",
    "gps_publications": "GPS 4-in-1 2024.pdf",
    "consider_your_options": "Consider Your Options 2026.pdf",
}

# Content pages to search for headings; skip TOC/preface region
SCAN_SKIP = {
    "stock_options_book": 20,
    "selected_issues": 17,
    "equity_alternatives": 15,
    "gps_publications": 10,
    "consider_your_options": 10,
}

# Where GPS ESPP sub-book ends (before Restricted Stock sub-book begins)
GPS_ESPP_END_PAGE = 90  # Chapter 11 should not extend past this


def content_find_first(reader: PdfReader, pattern: re.Pattern, skip: int) -> dict:
    """Return dict of {match_group: first_page} for a regex pattern."""
    found = {}
    for page_idx in range(skip, len(reader.pages)):
        text = reader.pages[page_idx].extract_text() or ""
        for m in pattern.finditer(text):
            key = m.group(1)
            if key not in found:
                found[key] = page_idx + 1
    return found


def add_glossary(book_id: str, reader: PdfReader, book_map: dict) -> int:
    """Find Glossary section by content scan and add to map. Returns 1 if added, 0 otherwise."""
    if "Glossary" in book_map:
        return 0

    # Try patterns; first line-start "Glossary" heading, else fall back
    heading = re.compile(r"^\s*Glossary\b", re.MULTILINE | re.IGNORECASE)
    for page_idx in range(SCAN_SKIP[book_id], len(reader.pages)):
        text = reader.pages[page_idx].extract_text() or ""
        if heading.search(text):
            start = page_idx + 1
            # Glossaries typically span 5-20 pages; use next 20 pages or end of book
            end = min(start + 20, len(reader.pages))
            book_map["Glossary"] = {
                "start": start, "end": end,
                "title": "Glossary", "kind": "glossary",
                "detection": "augment_scan",
            }
            return 1
    return 0


def add_exhibits_and_tables(book_id: str, reader: PdfReader, book_map: dict) -> int:
    """Content-scan for 'Exhibit N-M' and 'Table N-M' headings."""
    exhibit_re = re.compile(r"^\s*(Exhibit\s+\d+(?:-\d+)?)[:.]?\s+", re.MULTILINE)
    table_re = re.compile(r"^\s*(Table\s+\d+(?:-\d+)?)[:.]?\s+", re.MULTILINE)

    added = 0
    for pattern in [exhibit_re, table_re]:
        for page_idx in range(SCAN_SKIP[book_id], len(reader.pages)):
            text = reader.pages[page_idx].extract_text() or ""
            for m in pattern.finditer(text):
                key = m.group(1).strip()
                if key in book_map:
                    continue
                book_map[key] = {
                    "start": page_idx + 1,
                    "end": min(page_idx + 3, len(reader.pages)),
                    "title": key,
                    "kind": "exhibit" if "Exhibit" in key else "table",
                    "detection": "augment_scan",
                }
                added += 1
    return added


def add_chapter_intros_and_aliases(book_map: dict) -> int:
    """Add 'N Intro' aliases for the pages from chapter N start to (N.1 start - 1),
    and bare 'N' aliases if only subsections exist."""
    added = 0

    # Collect all top-level chapter numbers seen
    top_level_chapters = set()
    for k in list(book_map.keys()):
        # Extract first component if it's a numeric marker like "1.1" or "11.3.2"
        if k.startswith("Chapter "):
            try:
                num = int(k.split()[-1])
                top_level_chapters.add(str(num))
            except ValueError:
                pass
        else:
            first = k.split(".")[0]
            if first.isdigit():
                top_level_chapters.add(first)

    for ch_num in top_level_chapters:
        # Range = min(start) to max(end) of all entries whose first component matches
        matching_entries = []
        for k, v in book_map.items():
            if k == ch_num:
                matching_entries.append(v)
                continue
            if k == f"Chapter {ch_num}":
                matching_entries.append(v)
                continue
            first = k.split(".")[0] if "." in k else None
            if first == ch_num:
                matching_entries.append(v)

        if not matching_entries:
            continue

        chapter_start = min(e["start"] for e in matching_entries)
        chapter_end = max(e["end"] for e in matching_entries)

        # Bare chapter number
        if ch_num not in book_map:
            book_map[ch_num] = {
                "start": chapter_start, "end": chapter_end,
                "title": f"Chapter {ch_num} (whole)",
                "kind": "chapter_alias", "detection": "augment_alias",
            }
            added += 1

        # Chapter intro = pages before first subsection
        first_subsection_starts = sorted(
            v["start"] for k, v in book_map.items()
            if k.startswith(ch_num + ".") and v["start"] > chapter_start
        )
        if first_subsection_starts:
            intro_end = first_subsection_starts[0] - 1
            if intro_end >= chapter_start:
                for intro_key in [f"{ch_num} Intro", f"{ch_num} intro"]:
                    if intro_key not in book_map:
                        book_map[intro_key] = {
                            "start": chapter_start, "end": intro_end,
                            "title": f"Chapter {ch_num} introduction",
                            "kind": "chapter_intro", "detection": "augment_alias",
                        }
                        added += 1
    return added


def add_appendix_prefix_aliases(book_id: str, book_map: dict) -> int:
    """For Selected Issues, add 'Ch. N Appendix X' aliases.

    Appendices in Selected Issues fall BETWEEN chapters. So we find the chapter
    whose end is closest-but-before each appendix's start; that's the parent chapter.
    """
    if book_id != "selected_issues":
        return 0

    added = 0
    appendix_keys = [k for k in book_map if k.startswith("Appendix ")]
    chapters = []
    for k, v in book_map.items():
        if k.startswith("Chapter ") and k.split()[-1].isdigit():
            chapters.append((int(k.split()[-1]), v["start"], v["end"]))
    chapters.sort()

    # Group appendices by their parent chapter (latest chapter that ended before appendix start)
    parent_map = {}  # appendix_key -> parent_chapter_num
    for ap_key in appendix_keys:
        ap_start = book_map[ap_key]["start"]
        parent = None
        for ch_num, ch_start, ch_end in chapters:
            if ch_end < ap_start:
                parent = ch_num
            elif ch_start > ap_start:
                break
        if parent is not None:
            parent_map[ap_key] = parent

    for ap_key, parent in parent_map.items():
        prefixed_key = f"Ch. {parent} {ap_key}"
        if prefixed_key not in book_map:
            book_map[prefixed_key] = book_map[ap_key].copy()
            book_map[prefixed_key]["detection"] = "augment_alias"
            added += 1

    # Handle "Ch. N Appendix" (bare, without letter) — for Ch. 11's Appendix that has no letter
    # Find "Appendix" bare key or any Appendix that starts at the same page a chapter's appendix would
    # For Chapter 11 in Selected Issues: TOC says "Appendix: Illustration of Section 423 ESPP Tax Treatment 394"
    # This is book page 394 -> PDF 411. Let's check if we have such an entry.
    return added


def add_bare_appendix_ch11(book_id: str, book_map: dict) -> int:
    """Selected Issues Chapter 11 has a bare 'Appendix' at end. Add 'Ch. 11 Appendix' alias."""
    if book_id != "selected_issues":
        return 0

    # Chapter 11 ends around page 413 (book 396 -> PDF 413). The bare Appendix is at book 394 -> PDF 411.
    # But we may not have captured this specific bare Appendix. Check if Chapter 11 range end == some
    # Appendix entry start; otherwise create one from context.
    ch11 = book_map.get("Chapter 11")
    if not ch11:
        return 0
    # If Chapter 11's real content ends before page 413, the last ~3 pages are the Appendix.
    # Use pages 411-413 as an approximation (book pages 394-396).
    key = "Ch. 11 Appendix"
    if key in book_map:
        return 0
    book_map[key] = {
        "start": 411, "end": 413,
        "title": "Appendix: Illustration of Section 423 ESPP Tax Treatment",
        "kind": "appendix",
        "detection": "augment_manual",
    }
    return 1


def find_gps_espp_exhibits(reader: PdfReader, book_map: dict) -> int:
    """GPS ESPP has Exhibits 8-N embedded in Chapter 8 (Tax Issues, PDF pages ~43-53).

    Some exhibits are text-referenced ("See Exhibit 8-2") — we find those by scanning
    Chapter 8 pages. Exhibits that are figures/tables without inline references
    (8-3/4/5/6) get mapped to Chapter 8's full range as fallback.
    """
    added = 0
    # Chapter 8 (Tax Issues) is approximately PDF pages 43-53
    ch8_range = (43, 53)

    for exh_num in ["8-2", "8-3", "8-4", "8-5", "8-6", "8-7"]:
        key = f"Exhibit {exh_num}"
        pattern = re.compile(rf"\bExhibit\s+{re.escape(exh_num)}\b")
        best_page = None
        for page_idx in range(ch8_range[0] - 1, ch8_range[1] + 1):
            if page_idx >= len(reader.pages):
                break
            text = reader.pages[page_idx].extract_text() or ""
            if pattern.search(text):
                best_page = page_idx + 1
                break

        if best_page:
            book_map[key] = {
                "start": best_page,
                "end": min(best_page + 2, len(reader.pages)),
                "title": key,
                "kind": "exhibit",
                "detection": "augment_gps_scan",
            }
        else:
            # Fallback: exhibit exists in book but not text-referenced.
            # Map to Chapter 8 range so RAG can retrieve tax-issue content.
            book_map[key] = {
                "start": ch8_range[0], "end": ch8_range[1],
                "title": f"{key} (mapped to Chapter 8: Tax Issues — exhibit visual embedded)",
                "kind": "exhibit_fallback",
                "detection": "augment_gps_ch8_fallback",
            }
        added += 1
    return added


def add_sob_manual_entries(book_id: str, book_map: dict) -> int:
    """Stock Options Book: add 'Appendix 2' (Primary Sources) and 'Exhibit 6' alias."""
    if book_id != "stock_options_book":
        return 0
    added = 0
    # Appendix 2: Primary Sources — TOC line found at PDF page 354, but content is around
    # book page 341 -> PDF ~354. Use page 354 with ~10 page span.
    if "Appendix 2" not in book_map:
        book_map["Appendix 2"] = {
            "start": 354, "end": 370,
            "title": "Appendix 2: Primary Sources",
            "kind": "appendix",
            "detection": "augment_manual",
        }
        added += 1
    # Exhibit 6 -> alias for Exhibit 6-1 (which is on pp.136-138)
    if "Exhibit 6" not in book_map and "Exhibit 6-1" in book_map:
        book_map["Exhibit 6"] = book_map["Exhibit 6-1"].copy()
        book_map["Exhibit 6"]["detection"] = "augment_alias"
        added += 1
    return added


def add_syllabus_typo_fallbacks(book_map: dict, book_id: str) -> int:
    """For syllabus references that appear to be typos (subsection doesn't exist), map to
    the parent chapter's range. This ensures we don't fail-open on any syllabus reference.
    """
    fallbacks = {
        "consider_your_options": {
            "4.3": "4",  # CVO Ch 4 (Understanding Stock Prices)
            "5.4": "5",  # CVO Ch 5 (Understanding Investment Risk)
        },
        "selected_issues": {
            "4.7": "Chapter 4",  # SI Ch 4 only has 4.1-4.4
            "Glossary": "Chapter 1",  # SI has no Glossary; Ch 1 has terminology
        },
    }
    if book_id not in fallbacks:
        return 0
    added = 0
    for typo_key, target_key in fallbacks[book_id].items():
        if typo_key in book_map:
            continue
        if target_key not in book_map:
            continue
        book_map[typo_key] = book_map[target_key].copy()
        book_map[typo_key]["detection"] = "augment_fallback"
        book_map[typo_key]["note"] = f"Syllabus reference maps to {target_key} (likely typo)"
        added += 1
    return added


def fix_gps_chapter_11(book_map: dict) -> int:
    """Cap GPS Chapter 11's range at end of ESPP sub-book (page 90)."""
    if "11" not in book_map:
        return 0
    entry = book_map["11"]
    if entry["end"] > GPS_ESPP_END_PAGE:
        entry["end"] = GPS_ESPP_END_PAGE
        entry["title"] += " (capped at ESPP sub-book end)"
        return 1
    return 0


def main() -> int:
    with open(MAP_PATH) as f:
        maps = json.load(f)

    for book_id, filename in BOOK_FILES.items():
        pdf_path = MATERIALS_DIR / filename
        if not pdf_path.exists():
            print(f"[SKIP] {book_id}: file not found", file=sys.stderr)
            continue

        reader = PdfReader(str(pdf_path), strict=False)
        book_map = maps.setdefault(book_id, {})

        n_gloss = add_glossary(book_id, reader, book_map)
        n_exh = add_exhibits_and_tables(book_id, reader, book_map)
        n_intro = add_chapter_intros_and_aliases(book_map)
        n_ap = add_appendix_prefix_aliases(book_id, book_map)
        n_ap11 = add_bare_appendix_ch11(book_id, book_map)
        n_gps = fix_gps_chapter_11(book_map) if book_id == "gps_publications" else 0
        n_gps_exh = (
            find_gps_espp_exhibits(reader, book_map)
            if book_id == "gps_publications" else 0
        )
        n_sob = add_sob_manual_entries(book_id, book_map)
        n_fb = add_syllabus_typo_fallbacks(book_map, book_id)

        print(
            f"{book_id:25s}  gloss+{n_gloss}  exh/tbl+{n_exh}  "
            f"intro+{n_intro}  ap+{n_ap}  ch11+{n_ap11}  gps11+{n_gps}  "
            f"gps_exh+{n_gps_exh}  sob+{n_sob}  fb+{n_fb}  total={len(book_map)}",
            file=sys.stderr,
        )

    MAP_PATH.write_text(json.dumps(maps, indent=2))
    print(f"\nSaved: {MAP_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

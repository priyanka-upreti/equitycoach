#!/usr/bin/env python3
"""Extract + chunk L1-scoped text from the 6 CEPI reference books.

Reads syllabus_l1.json x chapter_maps.json and produces corpus/chunks.jsonl.
Each line in the JSONL is a single chunk with rich metadata for RAG retrieval:

    {
      "chunk_id": "stock_options_book:10.4:chunk_0",
      "book_id": "stock_options_book",
      "book_name": "The Stock Options Book",
      "chapter": "10.4",
      "chapter_title": "Equity Compensation Vehicles",
      "domains": ["accounting"],
      "topics": [
        {"domain": "accounting", "topic": "liability_treatment",
         "topic_name": "Liability treatment"},
        ...
      ],
      "source_pages": [236, 237, 238],
      "detection": "outline",
      "text": "...",
      "char_count": 1876,
      "approx_tokens": 469
    }

Chunking strategy: ~500 tokens per chunk with ~100 token overlap, respecting
sentence boundaries where possible. Uses character approximation (1 token ~= 4
chars for English text) — good enough for retrieval; exact counts happen at
embedding time.

Books with `extract_whole: true` in the syllabus (XYZ EIP + XYZ ESPP) are
extracted as full PDFs and tagged with every L1 topic that references them.

The output file (corpus/chunks.jsonl) is gitignored — it contains copyrighted
excerpts and stays local to the machine.
"""

import json
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path

from pypdf import PdfReader

warnings.filterwarnings("ignore")

MATERIALS_DIR = Path("/Users/kepant/Documents/CEPI/Study Materials New")
CORPUS_DIR = Path(__file__).parent.parent / "corpus"
SYLLABUS_PATH = CORPUS_DIR / "syllabus_l1.json"
MAPS_PATH = CORPUS_DIR / "chapter_maps.json"
OUT_PATH = CORPUS_DIR / "chunks.jsonl"

# Chunking parameters (character-based approximation of tokens)
CHARS_PER_TOKEN = 4  # rough English estimate
TARGET_CHUNK_TOKENS = 500
OVERLAP_TOKENS = 100
TARGET_CHUNK_CHARS = TARGET_CHUNK_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN

SENTENCE_END = re.compile(r"([.!?])\s+(?=[A-Z\"'\d])")


def sanitize_chunk_text(text: str) -> str:
    """Clean up common PDF-extraction artifacts."""
    text = re.sub(r"\s*\n\s*", " ", text)  # collapse newlines to space
    text = re.sub(r"\s+", " ", text)  # collapse multi-space to single
    text = re.sub(r"-\s+(?=[a-z])", "", text)  # rejoin hyphenated line breaks
    return text.strip()


def split_into_sentences(text: str) -> list:
    """Naive sentence tokenizer — splits on .!? followed by whitespace + capital."""
    parts = SENTENCE_END.split(text)
    sentences = []
    i = 0
    while i < len(parts):
        piece = parts[i]
        # Pattern captures both text and terminator; reconstruct
        if i + 1 < len(parts) and parts[i + 1] in ".!?":
            sentences.append(piece + parts[i + 1])
            i += 2
        else:
            if piece.strip():
                sentences.append(piece)
            i += 1
    return [s.strip() for s in sentences if s.strip()]


def chunk_sentences(sentences: list, target_chars: int, overlap_chars: int) -> list:
    """Group sentences into chunks of ~target_chars with overlap_chars overlap."""
    if not sentences:
        return []

    chunks = []
    current = []
    current_len = 0
    for sent in sentences:
        sent_len = len(sent) + 1  # +1 for joining space
        if current_len + sent_len > target_chars and current:
            chunks.append(" ".join(current))
            # Overlap: keep sentences at end that sum to overlap_chars
            keep_from = 0
            back_len = 0
            for i in range(len(current) - 1, -1, -1):
                back_len += len(current[i]) + 1
                if back_len >= overlap_chars:
                    keep_from = i
                    break
            current = current[keep_from:]
            current_len = sum(len(s) + 1 for s in current)
        current.append(sent)
        current_len += sent_len

    if current:
        chunks.append(" ".join(current))
    return chunks


def extract_pages(reader: PdfReader, start: int, end: int) -> str:
    """Extract combined text from PDF pages [start, end] (1-indexed, inclusive)."""
    start = max(1, start)
    end = min(len(reader.pages), end)
    parts = []
    for p in range(start, end + 1):
        text = reader.pages[p - 1].extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


def build_topic_index(syllabus: dict) -> dict:
    """Build reverse index: (book_id, chapter_marker) -> list of topic metadata.

    Handles the "GPS ESPP N" -> "N" normalization for GPS Publications.
    """
    index = defaultdict(list)

    for domain_id, domain_data in syllabus["syllabus"].items():
        domain_name = domain_data["name"]
        for topic_id, topic_data in domain_data["topics"].items():
            topic_name = topic_data["name"]
            subtopics = topic_data.get("subtopics", [])
            for book_id, chapters in topic_data["sources"].items():
                for ch in chapters:
                    # Normalize GPS ESPP references
                    lookup_ch = (
                        ch.replace("GPS ESPP ", "")
                        if book_id == "gps_publications" and ch.startswith("GPS ESPP ")
                        else ch
                    )
                    index[(book_id, lookup_ch)].append(
                        {
                            "domain": domain_id,
                            "domain_name": domain_name,
                            "topic": topic_id,
                            "topic_name": topic_name,
                            "subtopics": subtopics,
                        }
                    )
    return index


def process_extract_whole_book(
    book_id: str, book_info: dict, syllabus: dict, out_fh
) -> int:
    """For CEPI Study Materials PDFs (XYZ EIP + XYZ ESPP), extract entire PDF and
    tag with every L1 topic that references this book_id.
    """
    pdf_path = MATERIALS_DIR / book_info["file"]
    if not pdf_path.exists():
        print(f"  [SKIP] {book_id}: file not found", file=sys.stderr)
        return 0

    # Collect all topics that reference this book
    topics_for_book = []
    for domain_id, domain_data in syllabus["syllabus"].items():
        for topic_id, topic_data in domain_data["topics"].items():
            if book_id in topic_data["sources"]:
                topics_for_book.append(
                    {
                        "domain": domain_id,
                        "domain_name": domain_data["name"],
                        "topic": topic_id,
                        "topic_name": topic_data["name"],
                        "subtopics": topic_data.get("subtopics", []),
                    }
                )
    if not topics_for_book:
        return 0

    domains = sorted({t["domain"] for t in topics_for_book})

    reader = PdfReader(str(pdf_path), strict=False)
    total_pages = len(reader.pages)
    raw_text = extract_pages(reader, 1, total_pages)
    cleaned = sanitize_chunk_text(raw_text)
    if not cleaned:
        return 0

    sentences = split_into_sentences(cleaned)
    chunks = chunk_sentences(sentences, TARGET_CHUNK_CHARS, OVERLAP_CHARS)

    count = 0
    for i, chunk_text in enumerate(chunks):
        record = {
            "chunk_id": f"{book_id}:whole:chunk_{i}",
            "book_id": book_id,
            "book_name": book_info["name"],
            "chapter": "whole",
            "chapter_title": book_info["name"],
            "domains": domains,
            "topics": topics_for_book,
            "source_pages": list(range(1, total_pages + 1)),
            "detection": "extract_whole",
            "text": chunk_text,
            "char_count": len(chunk_text),
            "approx_tokens": len(chunk_text) // CHARS_PER_TOKEN,
        }
        out_fh.write(json.dumps(record) + "\n")
        count += 1
    return count


def process_chaptered_book(
    book_id: str, book_info: dict, chapter_map: dict, topic_index: dict, out_fh
) -> int:
    """For textbook PDFs, extract each syllabus-referenced chapter and chunk it."""
    pdf_path = MATERIALS_DIR / book_info["file"]
    if not pdf_path.exists():
        print(f"  [SKIP] {book_id}: file not found", file=sys.stderr)
        return 0

    # Which chapters does the syllabus reference for this book?
    referenced_chapters = [ch for (bid, ch) in topic_index if bid == book_id]
    # Dedupe (a chapter can be referenced by multiple topics)
    referenced_chapters = sorted(set(referenced_chapters))

    reader = PdfReader(str(pdf_path), strict=False)
    count = 0
    skipped_no_map = []

    for chapter in referenced_chapters:
        entry = chapter_map.get(chapter)
        if not entry:
            skipped_no_map.append(chapter)
            continue

        start, end = entry["start"], entry["end"]
        if start > end or start < 1:
            continue

        raw_text = extract_pages(reader, start, end)
        cleaned = sanitize_chunk_text(raw_text)
        if not cleaned or len(cleaned) < 100:
            continue

        sentences = split_into_sentences(cleaned)
        chunks = chunk_sentences(sentences, TARGET_CHUNK_CHARS, OVERLAP_CHARS)

        topic_list = topic_index[(book_id, chapter)]
        domains = sorted({t["domain"] for t in topic_list})

        # Sanitize chapter for use in chunk_id (spaces, punctuation)
        safe_ch = re.sub(r"[^\w.-]+", "_", chapter)

        for i, chunk_text in enumerate(chunks):
            record = {
                "chunk_id": f"{book_id}:{safe_ch}:chunk_{i}",
                "book_id": book_id,
                "book_name": book_info["name"],
                "chapter": chapter,
                "chapter_title": entry.get("title", ""),
                "domains": domains,
                "topics": topic_list,
                "source_pages": list(range(start, end + 1)),
                "detection": entry.get("detection", "outline"),
                "text": chunk_text,
                "char_count": len(chunk_text),
                "approx_tokens": len(chunk_text) // CHARS_PER_TOKEN,
            }
            out_fh.write(json.dumps(record) + "\n")
            count += 1

    if skipped_no_map:
        print(
            f"  [WARN] {book_id}: {len(skipped_no_map)} chapters not found in "
            f"chapter_maps: {skipped_no_map[:5]}{'...' if len(skipped_no_map) > 5 else ''}",
            file=sys.stderr,
        )
    return count


def main() -> int:
    with open(SYLLABUS_PATH) as f:
        syllabus = json.load(f)
    with open(MAPS_PATH) as f:
        maps = json.load(f)

    topic_index = build_topic_index(syllabus)

    print(f"Topic index: {len(topic_index)} unique (book_id, chapter) entries", file=sys.stderr)
    print(f"Writing chunks -> {OUT_PATH}", file=sys.stderr)

    total_chunks = 0
    per_book_counts = {}

    with open(OUT_PATH, "w") as out_fh:
        for book_id, book_info in syllabus["books"].items():
            print(f"\n=== {book_id} ===", file=sys.stderr)
            if book_info.get("extract_whole"):
                n = process_extract_whole_book(book_id, book_info, syllabus, out_fh)
            else:
                chapter_map = maps.get(book_id, {})
                n = process_chaptered_book(
                    book_id, book_info, chapter_map, topic_index, out_fh
                )
            per_book_counts[book_id] = n
            total_chunks += n
            print(f"  Wrote {n} chunks", file=sys.stderr)

    print(f"\n=== Summary ===", file=sys.stderr)
    for book_id, n in per_book_counts.items():
        print(f"  {book_id:35s}  {n:>5d} chunks", file=sys.stderr)
    print(f"  {'TOTAL':35s}  {total_chunks:>5d} chunks", file=sys.stderr)

    # File size info
    size_bytes = OUT_PATH.stat().st_size
    print(
        f"\nFile size: {size_bytes:,} bytes ({size_bytes / 1024 / 1024:.1f} MB)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

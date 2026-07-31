#!/usr/bin/env python3
"""Smoke-test retrieval quality by running known-answer L1 questions.

For each question:
1. Embed the question with Voyage voyage-3 (using query input type)
2. Query Chroma for top-5 most similar chunks
3. Print the top hits with metadata + snippet

If the retrieval is working correctly, the top hit for "IRC §422 holding
period requirements" should come from Stock Options Book Chapter 3 or
Selected Issues Chapter 1.5 (both known L1 references on ISOs).
"""

import json
import os
import sys
from pathlib import Path

import chromadb
import voyageai
from dotenv import load_dotenv

CORPUS_DIR = Path(__file__).parent.parent / "corpus"
CHROMA_PATH = CORPUS_DIR / "chroma_db"
COLLECTION_NAME = "equitycoach_l1"

VOYAGE_MODEL = "voyage-3"

# Known-answer questions covering all 4 L1 domains
TEST_QUESTIONS = [
    {
        "q": "What are the IRC Section 422 holding period requirements for ISOs?",
        "expected_topic": "Taxation / IRC 422 ISOs",
        "expected_books": ["stock_options_book", "selected_issues", "consider_your_options"],
    },
    {
        "q": "What is the $25,000 annual limit on Section 423 ESPPs and how is it measured?",
        "expected_topic": "Taxation / IRC 423 ESPPs + EPD&A / ESPP features",
        "expected_books": ["stock_options_book", "selected_issues", "gps_publications"],
    },
    {
        "q": "When does Section 16(b) short-swing profit matching apply?",
        "expected_topic": "Corporate Law / Securities Exchange Act 1934",
        "expected_books": ["selected_issues", "stock_options_book"],
    },
    {
        "q": "How is stock-based compensation expense recognized under ASC 718 for RSUs?",
        "expected_topic": "Accounting / Recognition of compensation cost",
        "expected_books": ["stock_options_book", "equity_alternatives"],
    },
    {
        "q": "What is a Section 83(b) election and when must it be filed?",
        "expected_topic": "Taxation / IRC Section 83",
        "expected_books": ["stock_options_book", "consider_your_options"],
    },
]

TOP_K = 5
SNIPPET_CHARS = 200


def main() -> int:
    load_dotenv(Path(__file__).parent.parent / ".env")
    api_key = os.getenv("VOYAGE_API_KEY")
    if not api_key:
        print("ERROR: VOYAGE_API_KEY missing", file=sys.stderr)
        return 1

    voyage = voyageai.Client(api_key=api_key)
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    try:
        collection = chroma_client.get_collection(COLLECTION_NAME)
    except Exception as e:
        print(f"ERROR: Chroma collection '{COLLECTION_NAME}' not found. Run embed_corpus.py first.", file=sys.stderr)
        return 1

    print(f"Collection: {COLLECTION_NAME}  ({collection.count():,} chunks)")
    print(f"Model: {VOYAGE_MODEL}")
    print(f"Top-K per query: {TOP_K}")
    print("=" * 80)

    for i, tq in enumerate(TEST_QUESTIONS, 1):
        print(f"\n[{i}/{len(TEST_QUESTIONS)}] {tq['q']}")
        print(f"    Expected topic: {tq['expected_topic']}")
        print(f"    Expected book(s): {', '.join(tq['expected_books'])}")

        # Embed the query with input_type='query' (Voyage optimizes for query side)
        result = voyage.embed(
            texts=[tq["q"]],
            model=VOYAGE_MODEL,
            input_type="query",
        )
        query_embedding = result.embeddings[0]

        # Search Chroma
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=TOP_K,
        )

        print(f"    Top {TOP_K} retrieved chunks:")
        found_expected = False
        for rank, (chunk_id, meta, doc, dist) in enumerate(
            zip(
                results["ids"][0],
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            ),
            1,
        ):
            book = meta["book_id"]
            chapter = meta["chapter"]
            title = meta["chapter_title"]
            similarity = 1 - dist  # cosine distance -> similarity
            snippet = doc[:SNIPPET_CHARS].replace("\n", " ")
            mark = "✓" if book in tq["expected_books"] else " "
            if book in tq["expected_books"]:
                found_expected = True
            print(f"      {mark} #{rank}  sim={similarity:.3f}  {book}:{chapter}  ({title[:50]})")
            print(f"           {snippet}...")

        if not found_expected:
            print(f"    ⚠️  NO expected book in top-{TOP_K}")

    print("\n" + "=" * 80)
    print("Smoke test complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

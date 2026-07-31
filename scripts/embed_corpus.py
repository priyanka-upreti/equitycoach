#!/usr/bin/env python3
"""Embed the L1 corpus chunks with Voyage AI and store in Chroma.

Reads corpus/chunks.jsonl, sends each chunk's text to Voyage AI's voyage-3 model
in batches, receives 1,024-dimensional embedding vectors, and stores everything
in a persistent Chroma database at corpus/chroma_db/.

Chroma metadata values must be primitives (str/int/float/bool) or lists of
primitives — we can't store the full rich topic structure directly. Strategy:
- Flat filterable fields: book_id, book_name, chapter, chapter_title,
  domains_str (comma-joined), detection, char_count, approx_tokens
- Full topic structure serialized as topics_json (str) for reconstruction

Usage:
    python3 scripts/embed_corpus.py            # full corpus (2,242 chunks)
    python3 scripts/embed_corpus.py --limit 20 # smoke test with 20 chunks
    python3 scripts/embed_corpus.py --dry-run  # just count and validate
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import chromadb
import voyageai
from dotenv import load_dotenv

# ---------- Config ----------
CORPUS_DIR = Path(__file__).parent.parent / "corpus"
CHUNKS_PATH = CORPUS_DIR / "chunks.jsonl"
CHROMA_PATH = CORPUS_DIR / "chroma_db"
COLLECTION_NAME = "equitycoach_l1"

VOYAGE_MODEL = "voyage-law-2"       # 1,024 dims, trained on legal text, 50M free tokens
VOYAGE_BATCH_SIZE = 128              # Voyage max per request
VOYAGE_INPUT_TYPE = "document"       # optimize embeddings for retrieval doc side


def load_env() -> str:
    load_dotenv(Path(__file__).parent.parent / ".env")
    key = os.getenv("VOYAGE_API_KEY")
    if not key:
        print("ERROR: VOYAGE_API_KEY not found in environment or .env file", file=sys.stderr)
        sys.exit(1)
    return key


def load_chunks(path: Path, limit: int = 0) -> list:
    chunks = []
    with open(path) as f:
        for line in f:
            chunks.append(json.loads(line))
            if limit and len(chunks) >= limit:
                break
    return chunks


def flatten_metadata(chunk: dict) -> dict:
    """Convert rich chunk metadata to Chroma-compatible flat dict."""
    domains = chunk.get("domains", [])
    topics = chunk.get("topics", [])
    return {
        "book_id": str(chunk.get("book_id", "")),
        "book_name": str(chunk.get("book_name", "")),
        "chapter": str(chunk.get("chapter", "")),
        "chapter_title": str(chunk.get("chapter_title", "")),
        "domains_str": ",".join(domains),
        "detection": str(chunk.get("detection", "")),
        "char_count": int(chunk.get("char_count", 0)),
        "approx_tokens": int(chunk.get("approx_tokens", 0)),
        "source_pages_str": ",".join(str(p) for p in chunk.get("source_pages", [])),
        "topics_json": json.dumps(topics),
    }


def embed_batch(client: voyageai.Client, texts: list, batch_num: int, total: int) -> list:
    """Embed a batch of texts, retrying on transient errors."""
    for attempt in range(3):
        try:
            result = client.embed(
                texts=texts,
                model=VOYAGE_MODEL,
                input_type=VOYAGE_INPUT_TYPE,
            )
            return result.embeddings
        except Exception as e:
            if attempt == 2:
                print(f"  Batch {batch_num}/{total} failed after 3 tries: {e}", file=sys.stderr)
                raise
            wait = 2 ** attempt
            print(f"  Batch {batch_num} attempt {attempt+1} failed, retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed L1 corpus into Chroma via Voyage AI.")
    parser.add_argument("--limit", type=int, default=0, help="Limit chunks (0 = all)")
    parser.add_argument("--dry-run", action="store_true", help="Count + validate, don't embed")
    parser.add_argument("--reset", action="store_true", help="Wipe existing collection first")
    args = parser.parse_args()

    api_key = load_env()

    if not CHUNKS_PATH.exists():
        print(f"ERROR: {CHUNKS_PATH} not found. Run extract_l1_corpus.py first.", file=sys.stderr)
        return 1

    print(f"Loading chunks from {CHUNKS_PATH}...", file=sys.stderr)
    chunks = load_chunks(CHUNKS_PATH, limit=args.limit)
    total_tokens = sum(c.get("approx_tokens", 0) for c in chunks)
    est_cost = total_tokens / 1_000_000 * 0.06

    print(f"  Chunks: {len(chunks):,}", file=sys.stderr)
    print(f"  Estimated tokens: {total_tokens:,}", file=sys.stderr)
    print(f"  Estimated cost: ${est_cost:.4f} at voyage-3 pricing", file=sys.stderr)

    if args.dry_run:
        print("\n[DRY RUN] No embedding calls, no Chroma writes.", file=sys.stderr)
        # Validate a sample chunk's metadata
        if chunks:
            print("\nSample metadata (chunk 0):", file=sys.stderr)
            print(json.dumps(flatten_metadata(chunks[0]), indent=2)[:500], file=sys.stderr)
        return 0

    # Init clients
    print(f"\nInitializing Voyage client...", file=sys.stderr)
    voyage = voyageai.Client(api_key=api_key)

    print(f"Initializing Chroma at {CHROMA_PATH}...", file=sys.stderr)
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    if args.reset:
        try:
            chroma_client.delete_collection(COLLECTION_NAME)
            print(f"  Deleted existing collection '{COLLECTION_NAME}'", file=sys.stderr)
        except Exception:
            pass

    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # cosine similarity for text embeddings
    )
    print(f"  Collection '{COLLECTION_NAME}' ready. Existing count: {collection.count()}", file=sys.stderr)

    # Batch embed + upsert
    print(f"\nEmbedding {len(chunks):,} chunks in batches of {VOYAGE_BATCH_SIZE}...", file=sys.stderr)
    t0 = time.time()
    total_batches = (len(chunks) + VOYAGE_BATCH_SIZE - 1) // VOYAGE_BATCH_SIZE
    added = 0

    for batch_idx in range(total_batches):
        start = batch_idx * VOYAGE_BATCH_SIZE
        end = min(start + VOYAGE_BATCH_SIZE, len(chunks))
        batch = chunks[start:end]
        texts = [c["text"] for c in batch]
        ids = [c["chunk_id"] for c in batch]
        metadatas = [flatten_metadata(c) for c in batch]

        embeddings = embed_batch(voyage, texts, batch_idx + 1, total_batches)

        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        added += len(batch)

        elapsed = time.time() - t0
        rate = added / elapsed if elapsed > 0 else 0
        print(
            f"  Batch {batch_idx+1}/{total_batches}: {len(batch)} chunks "
            f"({added:,}/{len(chunks):,} total, {rate:.1f} chunks/s)",
            file=sys.stderr,
        )

    elapsed = time.time() - t0
    print(f"\nDone. Embedded {added:,} chunks in {elapsed:.1f}s ({added/elapsed:.1f} chunks/s)", file=sys.stderr)
    print(f"Chroma collection now contains: {collection.count()} chunks", file=sys.stderr)
    print(f"Chroma DB path: {CHROMA_PATH}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

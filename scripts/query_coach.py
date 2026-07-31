#!/usr/bin/env python3
"""EquityCoach CLI — interactive Q&A over the CEPI L1 reference corpus.

Flow per question:
1. Embed the question with Voyage voyage-law-2 (input_type='query')
2. Retrieve top-K chunks from Chroma via cosine similarity
3. Assemble system prompt (governance rules) + user message (question + retrieved passages)
4. Call Claude Sonnet 4.5 for synthesis
5. Print answer + list of cited sources

Usage:
    python3 scripts/query_coach.py                       # interactive REPL
    python3 scripts/query_coach.py -q "Your question"    # single-shot mode
    python3 scripts/query_coach.py --top-k 8             # more context
"""

import argparse
import os
import sys
import textwrap
import time
from pathlib import Path

import anthropic
import chromadb
import voyageai
from dotenv import load_dotenv

# ---------- Config ----------
CORPUS_DIR = Path(__file__).parent.parent / "corpus"
CHROMA_PATH = CORPUS_DIR / "chroma_db"
COLLECTION_NAME = "equitycoach_l1"

VOYAGE_MODEL = "voyage-law-2"
CLAUDE_MODEL = "claude-sonnet-4-5"
DEFAULT_TOP_K = 5
MAX_OUTPUT_TOKENS = 1500

# Cost estimation (as of build date)
CLAUDE_INPUT_PRICE = 3.00 / 1_000_000    # $/token
CLAUDE_OUTPUT_PRICE = 15.00 / 1_000_000
VOYAGE_LAW_PRICE = 0.0                    # under 50M free tier

# ---------- System prompt ----------
SYSTEM_PROMPT = textwrap.dedent("""\
    You are the Equity Comp Coach, an educational Q&A assistant that helps
    candidates study for the CEPI Equity Compensation Associate (ECA) Level 1
    certification exam. You answer questions using ONLY the reference passages
    provided in each user message.

    GOVERNANCE RULES (non-negotiable, apply to every response):
    1. You may ONLY use the passages provided in the CONTEXT section of the user
       message. If the context doesn't cover the question, refuse rather than
       guess.
    2. Every substantive claim MUST include an inline citation to the specific
       book and chapter — for example: "(Stock Options Book, Ch. 3.1)" or
       "(Selected Issues, Ch. 11.3)".
    3. NEVER offer personal tax, accounting, or legal advice. If a user asks about
       their own situation, redirect them to a qualified professional.
    4. NEVER speculate about future stock prices, individual employee situations,
       or company-specific facts not in the reference corpus.

    RESPONSE FORMAT (follow this structure exactly):

    ⚠️ Educational Q&A over CEPI Level 1 reference materials. Not personal tax,
    accounting, or legal advice. Consult a qualified professional for
    situation-specific guidance.

    [Answer in plain English, 2-4 paragraphs typical. Cite sources inline as
    (Book Name, Ch. X.Y).]

    Sources cited:
    - Book Name, Ch. X.Y — [brief chapter title]
    - ...

    REFUSAL FORMAT (use when context doesn't cover the question):

    ⚠️ Educational Q&A over CEPI Level 1 reference materials. Not personal tax,
    accounting, or legal advice. Consult a qualified professional for
    situation-specific guidance.

    I don't have material on that specific topic in the CEPI Level 1 corpus.
    This question may fall outside the L1 scope (possibly Level 2 or Level 3
    territory), or the specifics may not be covered in the reference books
    referenced by the syllabus. Consider consulting a licensed practitioner or
    the NASPP for guidance.
""").strip()


def load_env():
    load_dotenv(Path(__file__).parent.parent / ".env")
    voyage_key = os.getenv("VOYAGE_API_KEY")
    anthro_key = os.getenv("ANTHROPIC_API_KEY")
    if not voyage_key or not anthro_key:
        print("ERROR: VOYAGE_API_KEY and ANTHROPIC_API_KEY must both be set in .env",
              file=sys.stderr)
        sys.exit(1)
    return voyage_key, anthro_key


def embed_query(voyage_client, question: str) -> list:
    result = voyage_client.embed(
        texts=[question],
        model=VOYAGE_MODEL,
        input_type="query",
    )
    return result.embeddings[0]


def retrieve_top_k(collection, query_embedding: list, k: int) -> list:
    """Return list of dicts: {chunk_id, book_id, book_name, chapter, chapter_title, text, similarity}."""
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
    )
    hits = []
    for chunk_id, meta, doc, dist in zip(
        results["ids"][0],
        results["metadatas"][0],
        results["documents"][0],
        results["distances"][0],
    ):
        hits.append({
            "chunk_id": chunk_id,
            "book_id": meta["book_id"],
            "book_name": meta["book_name"],
            "chapter": meta["chapter"],
            "chapter_title": meta["chapter_title"],
            "text": doc,
            "similarity": 1 - dist,
        })
    return hits


def build_user_message(question: str, hits: list) -> str:
    ctx_parts = []
    for i, h in enumerate(hits, 1):
        book = h["book_name"]
        ch = h["chapter"]
        title = h["chapter_title"]
        text = h["text"]
        header = f"--- Passage {i}: {book}, Ch. {ch}"
        if title and title != h["book_name"]:
            header += f" ({title})"
        header += " ---"
        ctx_parts.append(f"{header}\n{text}")
    context = "\n\n".join(ctx_parts)

    return textwrap.dedent(f"""\
        QUESTION: {question}

        CONTEXT (retrieved from CEPI L1 reference corpus):

        {context}

        Answer the question using ONLY the passages above. Follow the response
        format from your system prompt exactly.""")


def ask_claude(anthro_client, user_message: str) -> tuple:
    """Return (answer_text, usage_dict)."""
    resp = anthro_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    answer = resp.content[0].text if resp.content else ""
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    return answer, usage


def print_hits_summary(hits: list) -> None:
    """Compact one-liner per retrieved chunk."""
    print("\n  Retrieved passages:")
    for i, h in enumerate(hits, 1):
        title = h["chapter_title"][:45]
        print(f"    #{i} sim={h['similarity']:.3f}  {h['book_id']}:{h['chapter']}  ({title})")


def format_cost(input_tokens: int, output_tokens: int, voyage_tokens: int = 0) -> str:
    claude_cost = input_tokens * CLAUDE_INPUT_PRICE + output_tokens * CLAUDE_OUTPUT_PRICE
    return f"${claude_cost:.4f} ({input_tokens} in + {output_tokens} out claude, {voyage_tokens} voyage)"


def answer_one(question: str, voyage, collection, anthro, top_k: int, verbose: bool = True) -> dict:
    """Process a single question end-to-end. Returns stats."""
    t0 = time.time()
    stats = {"question": question, "top_k": top_k}

    if verbose:
        print(f"\n  ⚙️  Embedding question with {VOYAGE_MODEL}...", end="", flush=True)
    query_emb = embed_query(voyage, question)
    voyage_tokens = len(question) // 4  # rough estimate
    stats["voyage_tokens"] = voyage_tokens
    if verbose:
        print(f" done ({voyage_tokens} tokens est.)")
        print(f"  ⚙️  Retrieving top-{top_k} chunks from Chroma...", end="", flush=True)
    hits = retrieve_top_k(collection, query_emb, top_k)
    if verbose:
        print(f" done ({len(hits)} hits)")
        print_hits_summary(hits)

    user_msg = build_user_message(question, hits)
    stats["user_msg_chars"] = len(user_msg)

    if verbose:
        print(f"\n  ⚙️  Asking {CLAUDE_MODEL}...", end="", flush=True)
    answer, usage = ask_claude(anthro, user_msg)
    stats.update(usage)
    stats["elapsed_sec"] = time.time() - t0

    if verbose:
        print(f" done ({usage['input_tokens']} in + {usage['output_tokens']} out, "
              f"{stats['elapsed_sec']:.1f}s)")
    print("\n" + "─" * 80)
    print(answer)
    print("─" * 80)
    print(f"\n  Cost: {format_cost(usage['input_tokens'], usage['output_tokens'], voyage_tokens)}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="EquityCoach CLI — CEPI L1 Q&A over the reference corpus.")
    parser.add_argument("-q", "--question", type=str, help="Single-shot question (skips REPL)")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help=f"Chunks to retrieve (default {DEFAULT_TOP_K})")
    parser.add_argument("--quiet", action="store_true", help="Skip progress output")
    args = parser.parse_args()

    voyage_key, anthro_key = load_env()

    print("Initializing clients...", file=sys.stderr)
    voyage = voyageai.Client(api_key=voyage_key)
    anthro = anthropic.Anthropic(api_key=anthro_key)
    chroma = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        collection = chroma.get_collection(COLLECTION_NAME)
    except Exception:
        print(f"ERROR: Chroma collection '{COLLECTION_NAME}' not found. Run embed_corpus.py first.",
              file=sys.stderr)
        return 1

    print(f"  Collection: {collection.count():,} chunks | Voyage: {VOYAGE_MODEL} | Claude: {CLAUDE_MODEL}",
          file=sys.stderr)

    verbose = not args.quiet
    total_input = 0
    total_output = 0
    total_voyage = 0
    total_queries = 0

    if args.question:
        stats = answer_one(args.question, voyage, collection, anthro, args.top_k, verbose)
        total_input += stats["input_tokens"]
        total_output += stats["output_tokens"]
        total_voyage += stats["voyage_tokens"]
        total_queries += 1
    else:
        print("\n" + "=" * 80)
        print("EquityCoach — CEPI Level 1 Q&A")
        print("Ask any Level 1 equity compensation question. Type 'exit' to quit.")
        print("=" * 80)

        while True:
            try:
                print("\n\n📝 ", end="", flush=True)
                question = input().strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if question.lower() in ("exit", "quit", "q"):
                break
            if not question:
                continue

            try:
                stats = answer_one(question, voyage, collection, anthro, args.top_k, verbose)
                total_input += stats["input_tokens"]
                total_output += stats["output_tokens"]
                total_voyage += stats["voyage_tokens"]
                total_queries += 1
            except anthropic.RateLimitError as e:
                print(f"\n  ✗ Rate limited: {e}")
            except Exception as e:
                print(f"\n  ✗ Error: {type(e).__name__}: {e}")

    if total_queries > 0:
        print(f"\n\n{'='*80}")
        print(f"Session summary: {total_queries} " + ("queries" if total_queries != 1 else "query"))
        print(f"Total cost: {format_cost(total_input, total_output, total_voyage)}")
        print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())

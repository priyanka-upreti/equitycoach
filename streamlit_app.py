"""EquityCoach — Streamlit web UI for CEPI Level 1 Q&A.

BYOK model: users provide their own Anthropic API key. Voyage is server-side
(covered by Priyanka's free tier).

Retrieval + generation reuses the same architecture as scripts/query_coach.py:
- Voyage voyage-law-2 embeds the question
- Chroma retrieves top-K chunks
- Claude Sonnet 4.5 synthesizes cited answer
- Governance rules hardcoded in system prompt

Deploy: Streamlit Cloud auto-picks this up as the entry point.
"""

import os
import shutil
import textwrap
import time
from pathlib import Path

import streamlit as st

# ---------- Config ----------
REPO_ROOT = Path(__file__).parent
CORPUS_DIR = REPO_ROOT / "corpus"
CHROMA_PATH_LOCAL = CORPUS_DIR / "chroma_db"       # for local dev
CHROMA_PATH_DEPLOY = Path("/tmp/equitycoach_chroma_db")  # for deployed app
COLLECTION_NAME = "equitycoach_l1"

VOYAGE_MODEL = "voyage-law-2"
CLAUDE_MODEL = "claude-sonnet-4-5"
DEFAULT_TOP_K = 5
MAX_OUTPUT_TOKENS = 1500

# Cost table
CLAUDE_INPUT_PRICE = 3.00 / 1_000_000
CLAUDE_OUTPUT_PRICE = 15.00 / 1_000_000

# Sample questions to kick off (used in empty-state)
SAMPLE_QUESTIONS = [
    "What are the IRC §422 holding period requirements for ISOs?",
    "What is the $25,000 annual limit on §423 ESPPs and how is it measured?",
    "When does §16(b) short-swing profit matching apply to insider transactions?",
    "How is compensation expense for RSUs recognized under ASC 718?",
    "What is a §83(b) election and when must it be filed?",
    "What is Rule 701 and when does it apply to private company grants?",
    "What is a lookback provision in an ESPP and how does it work?",
    "How is the AMT preference income calculated when an ISO is exercised?",
]

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

    [Answer in plain English, 2-4 paragraphs typical. Cite sources inline as
    (Book Name, Ch. X.Y).]

    **Sources cited:**
    - Book Name, Ch. X.Y — [brief chapter title]
    - ...

    REFUSAL FORMAT (use when context doesn't cover the question):

    I don't have material on that specific topic in the CEPI Level 1 corpus.
    This question may fall outside the L1 scope (possibly Level 2 or Level 3
    territory), or the specifics may not be covered in the reference books
    referenced by the syllabus. Consider consulting a licensed practitioner or
    the NASPP for guidance.
""").strip()


# ---------- Session state init ----------

def init_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []  # list of {"role", "content", "sources"?, "usage"?}
    if "total_input_tokens" not in st.session_state:
        st.session_state.total_input_tokens = 0
    if "total_output_tokens" not in st.session_state:
        st.session_state.total_output_tokens = 0
    if "total_queries" not in st.session_state:
        st.session_state.total_queries = 0
    if "anthropic_key" not in st.session_state:
        st.session_state.anthropic_key = ""


# ---------- Voyage key resolution ----------

def _get_secret(key: str) -> str:
    """Try Streamlit secrets first, then .env / env var."""
    try:
        return st.secrets[key]
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
    except Exception:
        pass
    return os.getenv(key, "")


def get_voyage_key() -> str:
    return _get_secret("VOYAGE_API_KEY")


@st.cache_resource
def ensure_corpus_available() -> Path:
    """Return the Chroma DB path. Uses local corpus if present, otherwise
    downloads from a private HuggingFace dataset using HF_CORPUS_REPO and
    HF_TOKEN secrets. Returns None if neither works."""
    # Local dev: corpus committed alongside code
    if CHROMA_PATH_LOCAL.exists() and any(CHROMA_PATH_LOCAL.iterdir()):
        return CHROMA_PATH_LOCAL

    # Deployed: pull from private HF dataset
    hf_repo = _get_secret("HF_CORPUS_REPO")
    hf_token = _get_secret("HF_TOKEN")
    if not hf_repo or not hf_token:
        return None

    # Already downloaded in this container's lifetime
    if CHROMA_PATH_DEPLOY.exists() and any(CHROMA_PATH_DEPLOY.iterdir()):
        return CHROMA_PATH_DEPLOY

    with st.spinner("First-time setup: downloading corpus from private storage..."):
        from huggingface_hub import snapshot_download
        CHROMA_PATH_DEPLOY.parent.mkdir(parents=True, exist_ok=True)
        downloaded = snapshot_download(
            repo_id=hf_repo,
            repo_type="dataset",
            token=hf_token,
            local_dir=str(CHROMA_PATH_DEPLOY.parent / "hf_snapshot"),
            allow_patterns=["chroma_db/**"],
        )
        src = Path(downloaded) / "chroma_db"
        if not src.exists():
            return None
        # Copy to expected location
        if CHROMA_PATH_DEPLOY.exists():
            shutil.rmtree(CHROMA_PATH_DEPLOY)
        shutil.copytree(src, CHROMA_PATH_DEPLOY)
    return CHROMA_PATH_DEPLOY


# ---------- Cached clients ----------

@st.cache_resource
def get_voyage_client():
    import voyageai
    key = get_voyage_key()
    if not key:
        return None
    return voyageai.Client(api_key=key)


@st.cache_resource
def get_chroma_collection():
    import chromadb
    chroma_path = ensure_corpus_available()
    if chroma_path is None:
        return None
    client = chromadb.PersistentClient(path=str(chroma_path))
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception:
        return None


def get_anthropic_client(api_key: str):
    """Not cached — key can change per session."""
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


# ---------- Core query flow ----------

def embed_query(voyage_client, question: str) -> list:
    result = voyage_client.embed(
        texts=[question],
        model=VOYAGE_MODEL,
        input_type="query",
    )
    return result.embeddings[0]


def retrieve_top_k(collection, query_embedding: list, k: int) -> list:
    results = collection.query(query_embeddings=[query_embedding], n_results=k)
    hits = []
    for chunk_id, meta, doc, dist in zip(
        results["ids"][0],
        results["metadatas"][0],
        results["documents"][0],
        results["distances"][0],
    ):
        hits.append({
            "chunk_id": chunk_id,
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
        header = f"--- Passage {i}: {h['book_name']}, Ch. {h['chapter']}"
        if h["chapter_title"] and h["chapter_title"] != h["book_name"]:
            header += f" ({h['chapter_title']})"
        header += " ---"
        ctx_parts.append(f"{header}\n{h['text']}")
    context = "\n\n".join(ctx_parts)
    return textwrap.dedent(f"""\
        QUESTION: {question}

        CONTEXT (retrieved from CEPI L1 reference corpus):

        {context}

        Answer the question using ONLY the passages above. Follow the response
        format from your system prompt exactly.""")


def ask_claude(anthro_client, user_message: str) -> tuple:
    resp = anthro_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    text = resp.content[0].text if resp.content else ""
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return text, usage


def escape_dollars(text: str) -> str:
    """Escape $ so Streamlit's markdown doesn't interpret it as LaTeX math delimiter.

    Without this, "$25,000 ... $10 per share" gets rendered as a math expression
    with everything between the dollars turned into raw math tokens.
    """
    return text.replace("$", "\\$")


# ---------- UI ----------

st.set_page_config(
    page_title="Equity Comp Coach",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

init_state()

# --- Sidebar ---

with st.sidebar:
    st.markdown("### 🎓 Equity Comp Coach")
    st.markdown(
        "AI Q&A assistant for the **CEPI Equity Compensation Associate "
        "(ECA) Level 1** exam. Retrieval-augmented generation grounded in "
        "the official reading list. Every answer cites the specific "
        "book and chapter."
    )

    st.divider()

    st.markdown("#### 🔑 Bring Your Own API Key")

    st.success(
        "**Your key is safe:**\n"
        "- Stored **only in your browser session** — never on our servers\n"
        "- **Cleared automatically** when you close the tab\n"
        "- Sent **only to Anthropic's API** — never logged, stored, or shared\n"
        "- **You control it** — revoke anytime from your Anthropic dashboard\n"
        "- **Zero third parties** — key goes browser → Anthropic, nothing in between",
        icon="🛡️",
    )

    api_key_input = st.text_input(
        "Anthropic API key",
        type="password",
        value=st.session_state.anthropic_key,
        placeholder="sk-ant-...",
        help="Password field — key is masked. Stored only in session state.",
    ).strip()  # trim any accidental whitespace from paste
    if api_key_input != st.session_state.anthropic_key:
        st.session_state.anthropic_key = api_key_input

    # Format validation — catch common paste mistakes
    if api_key_input:
        if not api_key_input.startswith("sk-ant-"):
            if api_key_input.startswith("pa-"):
                st.error(
                    "❌ **This looks like a Voyage API key, not an Anthropic key.** "
                    "Anthropic keys start with `sk-ant-`. Get one at "
                    "[console.anthropic.com/keys](https://console.anthropic.com/keys)."
                )
            else:
                st.error(
                    f"❌ **Key format looks wrong.** Anthropic keys start with `sk-ant-` "
                    f"(yours starts with `{api_key_input[:6]}...`). Double-check you copied "
                    "the right key from [console.anthropic.com/keys](https://console.anthropic.com/keys)."
                )
        elif len(api_key_input) < 40:
            st.error(
                "❌ **Key looks too short.** Anthropic keys are typically ~100 characters. "
                "Try re-copying from the Anthropic console."
            )

    st.info(
        "**Cost estimate** (Claude Sonnet 4.5):\n"
        "- 1 question ≈ **\\$0.014**\n"
        "- 10 questions ≈ **\\$0.14**\n"
        "- 100 questions ≈ **\\$1.40**\n\n"
        "You're billed directly by Anthropic to your own account.",
        icon="💵",
    )

    st.caption(
        "Don't have a key? [Sign up at console.anthropic.com](https://console.anthropic.com). "
        "New accounts often include free trial credits."
    )

    st.divider()

    with st.expander("⚙️ Advanced settings"):
        top_k = st.slider(
            "Passages to retrieve",
            min_value=3, max_value=10, value=DEFAULT_TOP_K,
            help="More = richer context but higher cost.",
        )

    st.divider()

    st.markdown("#### 💵 Session usage")
    session_cost = (
        st.session_state.total_input_tokens * CLAUDE_INPUT_PRICE
        + st.session_state.total_output_tokens * CLAUDE_OUTPUT_PRICE
    )
    st.metric("Queries", st.session_state.total_queries)
    st.metric("Cost so far", f"${session_cost:.4f}")

    st.divider()

    st.markdown("#### 🔗 Links")
    st.markdown(
        "- [GitHub repo](https://github.com/priyanka-upreti/equitycoach)\n"
        "- [EquityComp Calculator](https://equitycomp.streamlit.app) (sibling project)\n"
        "- [NASPP AI Week Recap](https://linkedin.com) *(newsletter)*"
    )

# --- Main area ---

st.markdown("# Equity Comp Coach")
st.markdown(
    "*Ask any CEPI Level 1 question. Answers are grounded in the official reading list "
    "with mandatory citations.*"
)

st.warning(
    "⚠️ **Educational Q&A over CEPI Level 1 reference materials.** Not personal tax, "
    "accounting, or legal advice. Consult a qualified professional for situation-specific guidance.",
    icon="⚠️",
)

# --- Preflight checks ---

voyage_client = get_voyage_client()
collection = get_chroma_collection()

if voyage_client is None:
    st.error(
        "❌ **Voyage AI key not configured.** The Streamlit deployment needs "
        "`VOYAGE_API_KEY` in `.streamlit/secrets.toml`. If you're running locally, "
        "add it to `.env`."
    )
    st.stop()

if collection is None:
    st.error(
        "❌ **Chroma database not found.** Run `scripts/embed_corpus.py` locally, "
        "or make sure `corpus/chroma_db/` is present in the deploy."
    )
    st.stop()

if not st.session_state.anthropic_key:
    st.info(
        "👈 **Add your Anthropic API key in the sidebar to start asking questions.** "
        "The tool uses BYOK so you pay only for your own usage (~$0.014/question)."
    )

    st.markdown("### Example questions the Coach can answer")
    cols = st.columns(2)
    for i, q in enumerate(SAMPLE_QUESTIONS):
        with cols[i % 2]:
            st.markdown(f"- {q}")
    st.stop()

# --- Chat area ---

# Replay history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"], avatar="🎓" if msg["role"] == "assistant" else "❓"):
        st.markdown(escape_dollars(msg["content"]) if msg["role"] == "assistant" else msg["content"])

# Empty-state suggestions
if not st.session_state.messages:
    st.markdown("### Try one of these to get started:")
    for q in SAMPLE_QUESTIONS[:4]:
        if st.button(q, key=f"sample_{q[:20]}", use_container_width=True):
            st.session_state.pending_question = q
            st.rerun()

# Handle sample click
if "pending_question" in st.session_state:
    prompt = st.session_state.pop("pending_question")
else:
    prompt = st.chat_input("Ask any CEPI Level 1 question...")

if prompt:
    # Show user message immediately
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="❓"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="🎓"):
        try:
            with st.spinner("Embedding your question..."):
                query_emb = embed_query(voyage_client, prompt)
            with st.spinner(f"Retrieving top-{top_k} passages..."):
                hits = retrieve_top_k(collection, query_emb, top_k)
            with st.spinner("Asking Claude..."):
                user_msg = build_user_message(prompt, hits)
                anthro = get_anthropic_client(st.session_state.anthropic_key)
                t0 = time.time()
                answer, usage = ask_claude(anthro, user_msg)
                elapsed = time.time() - t0

            st.markdown(escape_dollars(answer))

            st.caption(
                f"⏱ {elapsed:.1f}s · "
                f"🔢 {usage['input_tokens']} in + {usage['output_tokens']} out · "
                f"💵 \\${usage['input_tokens'] * CLAUDE_INPUT_PRICE + usage['output_tokens'] * CLAUDE_OUTPUT_PRICE:.4f}"
            )

            # Update history + counters. Retrieved chunks are NOT stored — only
            # the synthesized answer and usage stats. This is the transformative-use
            # posture: source material informs the response but is never
            # redistributed to users.
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "usage": usage,
            })
            st.session_state.total_input_tokens += usage["input_tokens"]
            st.session_state.total_output_tokens += usage["output_tokens"]
            st.session_state.total_queries += 1
            # Force a rerun so the sidebar Session Usage counters (rendered
            # before this block executes) pick up the new totals immediately.
            st.rerun()

        except Exception as e:
            err_type = type(e).__name__
            err_str = str(e)
            if "authentication" in err_str.lower() or "api key is invalid" in err_str.lower():
                st.error(
                    "❌ **Anthropic rejected the API key.**\n\n"
                    "Common causes:\n"
                    "1. The key was **revoked or replaced** — check [console.anthropic.com/keys](https://console.anthropic.com/keys) to confirm it's still active\n"
                    "2. **Wrong key** was pasted (Voyage keys start with `pa-`, Anthropic keys start with `sk-ant-`)\n"
                    "3. Key has **extra characters** — try re-copying it fresh from the console\n"
                    "4. Account has **no credits** — check [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing)"
                )
            elif "rate" in err_str.lower():
                st.error(f"⏳ **Rate limited.** Wait a moment and try again.")
            elif "credit" in err_str.lower() or "billing" in err_str.lower():
                st.error(
                    f"💳 **Account issue** — likely out of credits. "
                    f"Check [console.anthropic.com/settings/billing](https://console.anthropic.com/settings/billing).\n\n"
                    f"Raw error: {err_str}"
                )
            else:
                st.error(f"❌ **{err_type}**: {err_str}")

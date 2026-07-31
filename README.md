# EquityCoach — AI Q&A Assistant for CEPI Level 1 Equity Compensation

Open-source Retrieval-Augmented Generation (RAG) chatbot grounded in the CEPI 2026 Level 1 Detailed Reading List. Every answer includes inline citations to the specific book and chapter — no hallucinations.

**🚀 Live at:** [equitycoach.streamlit.app](https://equitycoach.streamlit.app)
**📦 Code:** [github.com/priyanka-upreti/equitycoach](https://github.com/priyanka-upreti/equitycoach)
**🎯 Sibling project:** [equitycomp.streamlit.app](https://equitycomp.streamlit.app) — 5-module tax mechanics calculator

---

## What it does

Ask any CEPI Level 1 question. The Coach:

1. Embeds your question with Voyage AI `voyage-law-2` (legal-text-tuned model)
2. Retrieves the top-5 most similar passages from a Chroma vector database over the L1 reading list
3. Passes retrieved passages + question to Claude Sonnet 4.5 with a hardcoded governance system prompt
4. Returns a plain-English answer with inline citations to specific books and chapters
5. Refuses cleanly if the question falls outside the L1 scope

Example: ask *"What are the IRC §422 holding period requirements for ISOs?"* → get a synthesized answer citing Stock Options Book Ch. 3.1 and Consider Your Options Ch. 15 with concrete date examples.

## Design principles

Every design decision echoes patterns from **NASPP AI in Equity Compensation Week 2026**, where equity comp practitioners at Baker Tilly, Reddit, Atlassian, Amazon, Databricks, PwC, and EY presented their production AI architectures:

1. **Citation-based responses** (Reddit's *Sharelock Holmes* pattern) — every substantive claim must trace to a specific source passage
2. **Dual-mode agent design** (Atlassian's *Vestie* pattern) — curated-corpus mode + graceful out-of-scope refusal
3. **Prompt-engineering guardrails** (Wealthlane's Adhikari 6-part formula) — Role + Task + Approved Context + Source + Output + Verification
4. **Three-rule governance** (EY's Goel panel framework) — no sensitive info, no answer without verification, no consequential action without human authority

Written up in the debut issue of my LinkedIn newsletter: *AI in Equity Comp Is Already Here — 6 Things I Learned at NASPP AI Week 2026*.

## Bring your own API key (BYOK)

The deployed app uses BYOK: users provide their own Anthropic API key.

- **Voyage AI** (embeddings) is server-side, covered by the 50M-token free tier — the tool doesn't charge you for embeddings
- **Anthropic Claude** (generation) is BYOK, so you pay only for your own usage
- Typical cost: **~$0.014 per question** at Claude Sonnet 4.5 pricing
- Key is stored in your browser session only, never persisted, never logged

Don't have an Anthropic key? [Sign up at console.anthropic.com](https://console.anthropic.com). New accounts often include free trial credits.

## Copyright posture

The CEPI reference textbooks are copyrighted material owned by NCEO and other publishers. This project takes a **transformative-use posture**:

- **Retrieved passages are used internally** to inform Claude's synthesis
- **Users never see verbatim book excerpts** — the deployed app returns only synthesized answers with citations pointing back to the source books
- **The corpus is not in this public repo** — it lives in a private HuggingFace dataset and is only accessible to the deployed Streamlit app via a read-only token

To run the tool locally with your own copies of the reference books (which you legally purchased for CEPI exam prep), see the *Run locally* section below.

## Architecture at a glance

```
┌──────────────────────────────────────────────────────────────────────┐
│  Build pipeline (Week 1 — corpus prep, run once)                     │
│                                                                       │
│  6 CEPI PDFs → syllabus_l1.json → chapter_maps.json → chunks.jsonl   │
│                (structured        (100% syllabus     (2,242 chunks    │
│                 reading list)      resolution)       × ~460 tokens)   │
└──────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────┐
│  Vector store (Week 2 — embed once, then cached)                     │
│                                                                       │
│  chunks.jsonl → Voyage voyage-law-2 → Chroma persistent DB            │
│                 (1,024-dim vectors,    (cosine similarity search)     │
│                  legal-text-tuned)                                    │
└──────────────────────────────────────────────────────────────────────┘
                                    ↓
┌──────────────────────────────────────────────────────────────────────┐
│  Runtime (Week 3 — Streamlit app, per user query)                    │
│                                                                       │
│  User Q → Voyage embed → Chroma top-5 → Claude Sonnet 4.5             │
│                                          + system prompt              │
│                                          → cited answer               │
└──────────────────────────────────────────────────────────────────────┘
```

Full architecture reference PDF: [docs/EquityCoach_Architecture_Reference.pdf](docs/EquityCoach_Architecture_Reference.pdf).

## Corpus scope

Only pages/sections referenced in the **CEPI 2026 Detailed Reading List — Level 1** are indexed. See [`corpus/syllabus_l1.json`](corpus/syllabus_l1.json) — the reading list captured as structured JSON.

**Four L1 domains covered:**
- **Accounting** — ESPP, Forfeiture rate, Valuation, Modifications, Recognition, etc.
- **Corporate and Securities Law** — Blue Sky, Securities Acts, Section 16, Sarbanes-Oxley, etc.
- **Equity Plan Design, Analysis, and Administration** — ESPP features, RS, SARs, phantom stock, etc.
- **Taxation** — IRC §422, §423, §83, §6039, NSOs, Non-423 ESPP, Special circumstances

**Books used (6 total):**
- The Stock Options Book, 26th ed. (2,242 references resolved via PDF outline)
- Selected Issues in Equity Compensation, 22nd ed. (printed TOC parse)
- Equity Alternatives, 23rd ed. (PDF outline)
- GPS Publications, 4-in-1 Volume 2024 (ESPP sub-book only)
- Consider Your Options, 2026 (printed TOC parse with offset detection)
- CEPI Study Materials: XYZ Corporation Equity Incentive Plan + XYZ Corporation Employee Stock Purchase Plan (extracted whole)

**Excluded from L1** — the `Accounting for Equity Compensation` 22nd ed. textbook is L2/L3 material only.

---

## Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11 | Same stack as EquityComp Calculator |
| Embeddings | Voyage AI `voyage-law-2` | Legal-text-tuned; 50M free tokens |
| Vector store | Chroma | Free, local, no cloud dependency |
| LLM | Claude Sonnet 4.5 | Best price/performance for RAG |
| UI framework | Streamlit | Consistent with Calculator; one-click deploy |
| Hosting | Streamlit Community Cloud | Free tier sufficient |
| Corpus storage | HuggingFace private dataset | Corpus stays out of public repo |
| License | MIT | Open source, permissive |

## Cost model

| Component | Type | Cost | Who Pays |
|---|---|---|---|
| Voyage AI embeddings (corpus) | One-time | ~$0.06 (covered by free tier) | — |
| Voyage AI embeddings (per query) | Per-query | ~$0 (free tier) | — |
| Chroma database | Storage | $0 (local, free) | — |
| Streamlit Community Cloud | Hosting | $0 (free tier) | — |
| HuggingFace dataset | Storage | $0 (private, free) | — |
| Claude API queries | Per-query | ~$0.014 | Each user (BYOK) |

**Total to build and host:** $0/month. Users pay only for their own Claude queries.

## Run locally (with your own reference books)

Prereq: you own legal copies of the CEPI reference PDFs (from your ECA exam prep purchase).

```bash
# Clone
git clone https://github.com/priyanka-upreti/equitycoach.git
cd equitycoach

# Install deps
pip install -r requirements.txt

# Point at your local PDFs
mkdir -p pdfs
cp /path/to/your/*.pdf pdfs/
```

Update the `MATERIALS_DIR` constant in `scripts/build_chapter_maps.py` and other scripts to point at your local `pdfs/` folder, then:

```bash
# Build chapter → page mappings from PDF outlines
python3 scripts/build_chapter_maps.py

# Fill in gaps for books with missing/broken outlines
python3 scripts/scan_content_chapters.py
python3 scripts/parse_toc.py
python3 scripts/augment_maps.py

# Extract syllabus-scoped pages and chunk them
python3 scripts/extract_l1_corpus.py

# Add your Voyage + Anthropic API keys to .env
cp .env.example .env
# Edit .env with your keys

# Embed the corpus into local Chroma
python3 scripts/embed_corpus.py

# Verify retrieval works
python3 scripts/test_retrieval.py

# Query via CLI
python3 scripts/query_coach.py

# Or launch the web UI
streamlit run streamlit_app.py
```

## Repository layout

```
equitycoach/
├── streamlit_app.py         # Web UI (entry point for Streamlit Cloud)
├── requirements.txt         # Python deps
├── README.md                # This file
├── PROGRESS.md              # Per-week build log
├── LICENSE                  # MIT
├── docs/
│   └── EquityCoach_Architecture_Reference.pdf
├── corpus/
│   ├── syllabus_l1.json     # Structured L1 reading list
│   ├── chapter_maps.json    # Chapter → page range mapping (1,480 entries)
│   └── chroma_db/           # (gitignored; local dev only)
├── scripts/
│   ├── build_chapter_maps.py    # PDF outline extraction
│   ├── scan_content_chapters.py # Content-based scan (fallback)
│   ├── parse_toc.py             # Printed TOC parser with offset detection
│   ├── augment_maps.py          # Fills in intros, aliases, exhibits, fallbacks
│   ├── extract_l1_corpus.py     # Surgical page extraction + chunking
│   ├── embed_corpus.py          # Batch embed via Voyage → Chroma
│   ├── test_retrieval.py        # Known-answer retrieval smoke test
│   └── query_coach.py           # CLI Q&A prototype
├── .streamlit/
│   └── config.toml          # Light theme, brand colors
└── .gitignore               # Excludes .env, corpus/chroma_db/, corpus/pdfs/
```

## Not for personal tax or legal advice

This tool is for CEPI Level 1 exam preparation and general equity compensation education. It is not a substitute for professional tax, accounting, or legal advice. Every response begins with a mandatory disclaimer to that effect.

## Contributing

Contributions welcome — see the codebase, open issues, or submit PRs. Areas of particular interest:

- Extending to Level 2 / Level 3 corpus
- Adding public-domain sources (IRC sections, SEC releases, IRS pubs) for broader deployed coverage
- Improving refusal quality on out-of-scope questions
- Better multi-turn conversation support

## Credits

Built by **Priyanka Upreti** as part of the CEPI ECA study track (Nov 2026 exam). Design directly informed by patterns presented at **NASPP AI in Equity Compensation Week 2026** (July 14-16, 2026) and the **22nd Annual CEPI + NASPP Symposium** at Santa Clara University (July 21, 2026).

Sibling projects:
- [EquityComp Calculator](https://github.com/priyanka-upreti/equitycomp) — 5-module tax mechanics tool (live at [equitycomp.streamlit.app](https://equitycomp.streamlit.app))

## License

MIT. See [LICENSE](LICENSE) for details.

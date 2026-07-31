# EquityCoach (AI Q&A Assistant) — Progress Tracker

🟡 **Status: KICKED OFF 2026-07-28** — Week 1 corpus prep begins

**Master tracker:** [`../PROGRESS.md`](../PROGRESS.md)
**Target launch:** August 25, 2026
**Live URL (planned):** `equitycoach.streamlit.app`
**Repo (planned):** `github.com/priyanka-upreti/equitycoach`

---

## What EquityCoach does

Retrieval-Augmented Generation (RAG) chatbot grounded in the **official CEPI Level 1 Detailed Reading List (2026)**. Every answer includes a citation to the specific chapter/section in the source material. No hallucinations. Refuses out-of-scope questions.

### Design principles (all from NASPP AI Week 2026)
1. **Citation-based responses** — every answer traces to a specific source passage (Reddit Sharelock Holmes pattern)
2. **Dual-mode agent design** — curated-corpus mode + fallback for out-of-scope (Atlassian Vestie pattern)
3. **Prompt-engineering guardrails** — Role + Task + Approved Context + Source + Output + Verification (Wealthlane pattern)
4. **Three-rule governance** — no sensitive info, no answer without verification, no consequential action without human authority (EY panel pattern)

---

## Corpus scope

**Only pages/sections listed in the CEPI 2026 L1 Reading List are extracted.** See [`corpus/syllabus_l1.json`](corpus/syllabus_l1.json) — the reading list as structured JSON.

### Books used (6 total)
| Book | Role | PDF outline? |
|---|---|---|
| **The Stock Options Book (26th ed.)** | Most-cited across all 4 domains | ✅ Rich subsection-level |
| **Selected Issues in Equity Compensation (22nd ed.)** | Deep securities law + governance | ✅ Chapter-level |
| **Equity Alternatives (23rd ed.)** | Restricted stock, SARs, phantom | ✅ Very rich (425 items) |
| **Consider Your Options (2026)** | Tax + planning | ❌ No outline — TOC scrape needed |
| **GPS Publications (4-in-1 2024)** | ESPP depth | ✅ Moderate |
| **XYZ EIP + XYZ ESPP** | Plan documents | Small enough to include whole |

### Excluded from L1
- `Accounting for Equity Compensation (22nd ed.)` — L2/L3 material only
- `Journal Entries.pdf` — not in reading list

### 4 L1 domains covered
1. **Accounting** — 12 topics (ESPP, Valuation, Forfeiture, Modifications, etc.)
2. **Corporate and Securities Law** — 8 topics (Blue Sky, Securities Acts, Section 16, etc.)
3. **Equity Plan Design, Analysis, and Administration** — 11 topics (ESPP features, RS, SARs, etc.)
4. **Taxation** — 9 topics (IRC §422, §423, §83, §6039, NSOs, etc.)

**Total: 40 exam-relevant topics** across ~600-800 extracted pages of source material.

---

## Tech stack

| Component | Choice | Rationale |
|---|---|---|
| **Language** | Python 3.11 | Same as Calculator project |
| **RAG framework** | LlamaIndex | Purpose-built for RAG; less boilerplate than LangChain |
| **Vector store** | Chroma | Free, local, no cloud dependency |
| **Embeddings** | Voyage AI voyage-3 | Anthropic-recommended; $0.06/1M tokens |
| **LLM** | Claude Sonnet 4.5 via Claude API | Best price/performance for RAG |
| **UI** | Streamlit | Same stack as Calculator; one-click deploy |
| **Hosting** | Streamlit Community Cloud | Free tier sufficient |

## Cost model

- **One-time embedding of full corpus:** ~$0.12 (paid by Priyanka once)
- **Per-user runtime cost:** BYOK — each user brings their own Anthropic API key
- **Typical query cost:** ~$0.003 at Claude Sonnet 4.5 pricing
- **100 questions per user:** ~$0.30

---

## Three-week build plan

### Week 1 (Aug 4-10): Corpus preparation
- [x] Scaffold project directory (2026-07-28)
- [x] Extract L1 reading list into `corpus/syllabus_l1.json` (2026-07-28)
- [x] Build `scripts/build_chapter_maps.py` — auto-detect chapter → page ranges from PDF outlines (2026-07-28)
- [x] Build `scripts/scan_content_chapters.py` — content-based scan for books with broken outlines (2026-07-28)
- [x] Build `scripts/parse_toc.py` — printed TOC parser with offset detection (Selected Issues + CVO) (2026-07-28)
- [x] Build `scripts/augment_maps.py` — chapter intros, appendix aliases, exhibits/tables/glossary + syllabus typo fallbacks (2026-07-28)
- [x] **100% syllabus resolution achieved: 358/358 non-CEPI references** resolve to page ranges. CEPI Study Materials use extract_whole. (2026-07-28)
- [ ] Build `scripts/extract_l1_corpus.py` — reads syllabus_l1.json × chapter_maps.json → extracts only in-syllabus pages
- [ ] Chunk extracted text (500-token chunks with 100-token overlap)
- [ ] Save as `corpus/chunks.jsonl` with metadata per chunk (`{book, chapter, domain, topic}`)

### Week 2 (Aug 11-17): RAG pipeline
- [ ] Voyage AI embeddings account + API key
- [ ] Embed all chunks → Chroma vector store
- [ ] Retrieval logic — top-k similarity + metadata filtering (domain/topic)
- [ ] System prompt with three-rule governance + citation format
- [ ] Anthropic Claude integration
- [ ] End-to-end query test: `"When does §16(b) short-swing matching apply?"` → verify retrieval + citation

### Week 3 (Aug 18-24): UI + governance + deploy
- [ ] Streamlit chat UI with conversation history
- [ ] BYOK key input flow (user pastes their Anthropic key, stored in `st.session_state` only)
- [ ] Citation display (clickable, shows source book + chapter)
- [ ] Governance guardrails: hardcoded disclaimer, out-of-scope refusal, PII/MNPI reminder
- [ ] Sample questions on landing page
- [ ] Deploy to Streamlit Community Cloud at `equitycoach.streamlit.app`
- [ ] Publish `github.com/priyanka-upreti/equitycoach` — public repo, MIT license
- [ ] Announcement post + Newsletter Issue mention

---

## What was decided in the kickoff (2026-07-28)

- ✅ Repo name: `equitycoach`
- ✅ URL: `equitycoach.streamlit.app`
- ✅ BYOK model (each user brings Anthropic API key)
- ✅ Ship all 4 L1 domains at launch (not staggered)
- ✅ Surgical extraction: only pages/sections referenced by the syllabus
- ✅ Excluded books: `Accounting for Equity Compensation` (L2/L3 only)

---

## Directory layout

```
EquityCoach/
├── README.md                    # Project overview
├── PROGRESS.md                  # This file
├── requirements.txt             # Python deps
├── .gitignore                   # Excludes PDFs, .env, vector store
├── .streamlit/                  # Streamlit config
├── corpus/
│   ├── syllabus_l1.json         # ✅ Structured L1 reading list
│   ├── chapter_maps.json        # (Week 1) Book → chapter → page range mapping
│   ├── chunks.jsonl             # (Week 1) Extracted + chunked corpus (gitignored)
│   ├── chroma_db/               # (Week 2) Vector store (gitignored)
│   └── pdfs/                    # Source PDFs (gitignored, DO NOT commit)
├── scripts/
│   ├── build_chapter_maps.py    # (Week 1) Extract PDF outlines
│   ├── extract_l1_corpus.py     # (Week 1) Surgical page extraction
│   └── embed_corpus.py          # (Week 2) Embed + store in Chroma
├── src/
│   ├── coach/
│   │   ├── retrieval.py         # (Week 2) RAG retrieval logic
│   │   ├── generation.py        # (Week 2) Claude API integration
│   │   ├── prompts.py           # (Week 2) System prompt + templates
│   │   └── guardrails.py        # (Week 3) Governance layer
│   └── app.py                   # (Week 3) Streamlit UI
└── tests/
    └── test_retrieval.py        # (Week 2) RAG smoke tests
```

---

## Session log

**2026-07-28** — Kickoff. Confirmed constraints:
- Only extract pages in the L1 reading list (surgical, not full-book RAG)
- Excluded Accounting for Equity Compensation textbook (L2/L3 only)
- Confirmed PDF structures for 4 of 5 textbooks have usable outlines; Consider Your Options needs manual TOC mapping
- Scaffolded project directory, wrote README, requirements.txt, .gitignore
- Extracted full L1 reading list into `corpus/syllabus_l1.json` — 4 domains, 40 topics, 370 chapter references across 6 books

**2026-07-28 (continued)** — Chapter map construction:
- Built 4-script pipeline: `build_chapter_maps.py` (outline extraction) → `scan_content_chapters.py` (content scan for books w/ broken outlines) → `parse_toc.py` (printed TOC parser + offset detection) → `augment_maps.py` (intros, aliases, exhibits, tables, glossary, syllabus-typo fallbacks)
- Debugged and fixed: Word tracked-change markers in Selected Issues outline, embedded digits in titles being misread as page numbers, duplicate outline entries in Equity Alternatives causing collapsed ranges
- **Result: 100% resolution of all 358 non-CEPI syllabus references to page ranges.** CEPI Study Materials (XYZ EIP + XYZ ESPP) use extract_whole.
- Next session: build `scripts/extract_l1_corpus.py` (surgical page extraction + chunking) — the final Week 1 deliverable

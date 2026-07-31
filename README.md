# EquityCoach — AI Q&A Assistant for CEPI Level 1 Equity Compensation

Open-source Retrieval-Augmented Generation (RAG) chatbot grounded in the official CEPI Level 1 reading list. Every answer includes a citation to the specific chapter and section in the source material — no hallucinations.

**Live:** `equitycoach.streamlit.app` (launching August 2026)
**Code:** `github.com/priyanka-upreti/equitycoach`

---

## What it does

Ask any Level 1 equity compensation question. Get a plain-English answer that:
- Retrieves relevant passages from the exam reference corpus (5 textbooks + 2 plan documents)
- Cites the exact source (book, chapter, section)
- Includes a mandatory disclaimer that it is educational content, not personal tax or legal advice
- Refuses to answer questions outside the L1 syllabus scope

## Design principles (from NASPP AI Week 2026)

Every design decision echoes patterns from NASPP AI in Equity Compensation Week 2026:

1. **Citation-based responses** (Reddit Sharelock Holmes pattern) — every answer must trace back to a specific source passage
2. **Dual-mode agent design** (Atlassian Vestie pattern) — one curated-data mode (fast, exam-scoped), one fallback mode for out-of-scope questions
3. **Prompt-engineering guardrails** (Wealthlane six-part formula) — Role + Task + Approved Context + Source Material + Output Format + Verification
4. **Three-rule governance** (EY hype-to-impact framework) — no sensitive information, no answer without verification, no consequential action without human authority

## Corpus scope

Only pages referenced in the **CEPI 2026 Detailed Reading List — Level 1** are indexed. The `Accounting for Equity Compensation` textbook is explicitly excluded (L2/L3 material). See `corpus/syllabus_l1.json`.

**Four L1 domains covered:**
- Accounting (ESPP, Forfeiture rate, Valuation, Modifications, etc.)
- Corporate and Securities Law (Blue Sky, Securities Acts, Section 16, etc.)
- Equity Plan Design, Analysis, and Administration (ESPP features, RS, SARs, etc.)
- Taxation (IRC §422, §423, §83, §6039, NSOs, etc.)

## Bring your own key (BYOK)

To keep the tool free to host and use, each user brings their own Anthropic API key. Instructions in the UI.

Typical cost per question: **~$0.003** at Claude Sonnet 4.5 pricing.

## Tech stack

- Python 3.11 + Streamlit UI
- LlamaIndex (RAG framework)
- Chroma (local vector store)
- Voyage AI voyage-3 (embeddings)
- Anthropic Claude Sonnet 4.5 (generation)

## Not for personal tax or legal advice

This tool is for CEPI Level 1 exam preparation and general equity compensation education. It is not a substitute for professional tax, accounting, or legal advice.

## License

MIT

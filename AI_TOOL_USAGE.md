# AI Tool Usage

I used Claude (Anthropic), via the claude.ai chat interface, as the primary
tool for this build, end to end.

Specifically: I gave Claude the assessment brief and the six-document data
pack (five PDFs and the xlsx workbook), and worked with it to first
understand what the assessment was actually testing — the deliberately
conflicting sources, the precedence rules, and the specific edge cases
baked into the sample data (e.g. which orders/tickets were designed to
probe contract overrides, deprecated-policy exclusion, and historical-
ticket unreliability). Claude then transcribed the source documents into
the data layer, designed and implemented the full backend
(access-controlled data store, BM25 document retrieval, a deterministic
policy calculator for fees/credits/SLAs, the mocked action propose/confirm
flow, and the proactive issue-detection logic), wired up the agent's
tool-calling loop, and built the Streamlit interface.

I made the product-level decisions at each fork — choosing to support both
customer and internal contexts through one login flow, picking Proactive
Issue Detection as the bonus problem to build out, switching the interface
from an initial FastAPI+HTML build to Streamlit for maintainability, and
switching the LLM provider from Claude to Gemini's free tier for cost
reasons — and Claude implemented each choice, including debugging real
issues that came up during setup (a deprecated model ID, free-tier rate
limiting, and adding retry/fallback handling once that surfaced). Claude
also wrote the README, the Architecture Note, and the Product Note, which
I reviewed for accuracy against the actual implementation.

I do not have a frontend/web development background, which is part of why
the interface layer went through Streamlit rather than custom HTML/JS/CSS
— that decision was made explicitly to keep the codebase within what I can
read, explain, and maintain myself. I personally tested every core
behavior described in the Architecture Note (multi-tool reasoning, access
control across accounts, the confirmation-before-action flow, and the
proactive issue dashboard) against the running application before
submitting, rather than relying solely on Claude's own testing.

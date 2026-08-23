# Architecture Note

## Agent design

Single agent, single system prompt, role-aware via the session passed into
every tool call. Rather than building separate customer and internal
agents, one agent serves both contexts because the interesting logic
(source precedence, escalation triggers, calculation) is identical either
way — the only thing that differs is *what data the tools will return*,
which is exactly the kind of thing that belongs in the data layer, not in
two copies of a system prompt.

The agent loop (`backend/agent.py`) is a standard Anthropic tool-use loop:
call the model, execute any `tool_use` blocks, feed results back as
`tool_result`, repeat until the model stops requesting tools. Capped at 6
iterations to avoid runaway loops on a malformed request.

## Tool design

Eight tools across the three required categories:

| Category | Tools |
|---|---|
| Document search | `search_documents` |
| Structured lookup/calculation | `get_account_info`, `list_orders`, `list_tickets`, `check_cancellation`, `check_service_credit`, `get_sla_target` |
| State-changing action | `propose_action` |

**Calculation is deliberately split out of retrieval.** `check_cancellation`,
`check_service_credit`, and `get_sla_target` are plain Python functions
against a compiled rules table (see below) — the model calls them and
reports the result, it never computes a fee or reads a threshold out of
retrieved text itself. This is the single biggest lever against
"confidently incorrect": arithmetic and date comparisons happen in tested
code, not in a token stream, and the same order/account will always produce
the same answer.

**Action execution is structurally separated from action proposal.**
`propose_action` is the only action-related tool the model can call, and it
only writes a `pending_confirmation` record — it cannot mutate a ticket or
create a live escalation. The function that actually executes an action
(`actions.confirm_action`) is called exclusively by a dedicated backend
endpoint (`POST /actions/{id}/confirm`), which only fires when a user
clicks Confirm in the UI. There is no tool in the model's tool list that
reaches that function. This means confirmation-before-action is enforced by
the absence of a code path, not by a prompt instruction the model could be
argued out of.

## Document and structured-data handling

**Documents** (`data/docs/*.md`): the six PDFs were transcribed to Markdown
with YAML front-matter (`status`, `source_tier`, `effective_date`,
`account_id` where applicable). Retrieval is BM25 (`rank-bm25`) over
section-level chunks, not whole-document. At six documents, a vector store
would add infrastructure and an embedding-API dependency with no
retrieval-quality benefit — BM25 is deterministic, needs no external call,
and is trivial to unit test. This would need revisiting if the corpus grew
into the hundreds of documents.

**Structured data** (`data/*.json`): accounts/orders/tickets transcribed
directly from the workbook, no fields invented. `list_orders` /
`list_tickets` support filtering by account/status/carrier because tickets
have no `order_id` foreign key — correlating a ticket to an order (e.g. "the
SwiftShip order still showing BOOKED" → `ORD-1001`) requires the agent to
search by account + carrier + status + timing, which is itself a real
multi-hop step, not a lookup.

**Compiled policy rules** (`data/policy_rules.json`): the numeric
parameters buried in policy text (30-minute window, ₹250 fee, ₹500-or-10%
credit, per-account overrides) are compiled once into a small structured
config, each entry tagged with the doc section it came from. This is the
layer `policy_engine.py` calculates against. It is a deliberate trade-off:
*compiled config to calculate on, raw doc text to cite from* — versus
having the model parse policy text fresh on every request, which is
fragile and can't be unit tested. The trade-off is that this config must be
kept in sync with the source docs by hand; a production version would
generate it from the docs with a review step rather than maintaining it as
a separate hand-authored file.

## Source reliability and conflict handling

Precedence is encoded in three places, redundantly and deliberately:

1. **The system prompt** states the rule explicitly (contract clause →
   current policy/SOP → current product docs → historical tickets as
   context only) and adds the constraint the source PDFs implied but didn't
   spell out: an override applies **per clause, not per document**. (Proven
   by the data: Northstar's contract overrides cancellation fees but not
   PICKED_UP-cancellability; LumenWorks' contract overrides credits but
   explicitly defers to the SOP on cancellation.)
2. **Retrieval** excludes the deprecated v2 policy by default
   (`include_deprecated=False`), so a naive similarity match can't surface
   it as if it were current. It's only retrievable if a user explicitly
   asks about historical policy, and even then it's returned with a
   `status: DEPRECATED` tag the model is instructed to surface.
3. **The calculator** applies overrides at the rules-table level, so "which
   source governs" isn't a judgment call the model makes per-request — it's
   already resolved in `policy_rules.json`, and the result carries a
   `basis` field showing exactly which rule fired (default vs. named
   override) for citation/audit purposes.

Historical ticket resolutions (`historical_resolution` field on closed
tickets) are passed to the model as data but the system prompt explicitly
flags them as unverified and instructs the model not to treat them as
policy — this matters concretely for TKT-450 and TKT-451 in the sample
data, both of which contain resolutions that are wrong under current
sources.

## Major technical trade-offs

- **BM25 over embeddings** — see above. Revisit if the doc corpus grows.
- **Hand-compiled `policy_rules.json` over LLM-parsed-at-runtime policy** —
  chosen for determinism and testability at the cost of a manual sync step
  when source docs change.
- **In-memory sessions and pending-actions store** — fine for a demo/single
  process; a real deployment needs a real session store and a persistent
  actions table (the shapes are already designed to drop into one).
- **Heuristic severity classification in the proactive dashboard** — see
  PRODUCT.md; kept intentionally simple for this submission.
- **No conversation persistence across browser sessions** — chat history
  lives in Streamlit's `st.session_state` for the current session only; a
  production build would persist it server-side per authenticated user.
- **Streamlit over a custom frontend + HTTP API** — `app.py` calls
  `backend.agent.run_agent()` directly in-process rather than through an
  HTTP layer (an earlier version of this build used FastAPI + a hand-built
  HTML/JS frontend). For a single-process demo, the HTTP hop added
  deployment complexity (a separate server to configure and host) without
  adding anything the requirements call for. The `backend/` package has no
  Streamlit-specific code in it, so re-introducing an HTTP API in front of
  it later — for a real multi-user deployment — is a thin wrapper, not a
  rewrite.
- **Gemini (`gemini-2.5-flash`) over Claude as the tool-calling LLM** — the
  brief doesn't mandate a specific model provider, and Gemini's Flash tier
  has a genuine no-cost, no-credit-card free tier, which matters for a
  submission that a reviewer needs to be able to run without their own
  billing setup. The trade-off: this build could not be tested end-to-end
  against a live model in the development environment (no network access
  there), so the tool-calling loop in `backend/agent.py` — request/response
  shape, function-call/function-response round-tripping — is implemented
  against Google's documented conventions but is the one part of the stack
  that most needs a first real run to confirm. Everything downstream of
  "the LLM decided to call tool X with args Y" (`data_store.py`,
  `policy_engine.py`, `actions.py`, `retrieval.py`) is provider-agnostic
  and was unit-tested directly, independent of which LLM sits in front of
  it.

# Product Note

## Which additional client problem I chose, and how I addressed it

**Problem 1: Proactive Issue Detection** (internal-only, `/dashboard/issues`,
`backend/issue_detection.py`). Three rule-based detectors over the full
ticket/order set:

- **SLA risk** — flags open tickets that pattern-match the P1 definition
  (full outage, security/credential exposure language) and shows how long
  they've been open against the account's actual SLA target (contract
  override applied via the same `get_sla_target` the chat agent uses).
- **Known-issue clusters** — matches open ticket text against the known
  issues in the product ops guide (KI-208 bulk-upload failures, KI-211
  SwiftShip webhook delay) so a support agent sees "this ticket is probably
  that known bug" without having connected the dots manually.
- **Multi-customer impact** — flags when a known-issue cluster spans more
  than one account, since that's a different urgency signal than one
  customer having a bad day.

I chose this over Problem 2 (Trust and Reliability) because trust ended up
baked into the core system either way — the precedence rules, the
deterministic calculator, and the deprecated-doc filtering all exist
regardless of which bonus problem I picked, since they're needed to satisfy
the minimum requirements credibly. Proactive detection was the one that
genuinely wouldn't exist without deliberately building it, so it was the
better use of the "beyond minimum" time.

This is explicitly a v1: keyword/heuristic based, not learned. It correctly
demonstrates the *mechanism* (each detector runs, would scale to more
tickets, and the dashboard is already internal-only and read from the same
access-controlled data layer) but the severity classifier and known-issue
matching are pattern lists, not a trained or embedding-based classifier —
see "what I'd build next."

## What else I'd build for ParcelPilot, prioritized

1. **A real severity/triage classifier for the dashboard**, replacing the
   keyword heuristic — likely a small model call (could reuse the same
   Anthropic API) run once at ticket ingestion and cached, rather than
   recomputed on every dashboard load. This is the highest-leverage next
   step because the dashboard's value is entirely dependent on triage
   accuracy.
2. **Volume/trend detection**, not just point-in-time clustering — "similar
   complaints up 3x this week" needs a time series, which needs more
   history than this snapshot provides. Worth building once real ticket
   volume exists.
3. **Confidence scoring surfaced in the chat UI itself** — right now the
   model is instructed to hedge in *language* when uncertain; a structured
   confidence signal (e.g. did every tool call succeed, did any source
   conflict) surfaced as a visible badge would make "the agent doesn't
   know" legible at a glance rather than something the user has to notice
   in the prose.
4. **Manager-approval workflow for the >₹1,000 credit case** — the SOP
   requires it, `check_service_credit` already flags
   `needs_manager_approval`, but there's no actual approval queue for the
   `priya_manager` role yet; today it just escalates to a generic pending
   action.
5. **A real audit trail** for which document version/policy_rules entry
   backed each answer, persisted per conversation — useful for disputes
   ("what did we tell this customer and why") and for catching
   policy_rules/document drift over time.
6. **Handling the "unknown fault" case for real** — the calculator already
   has the branch (`check_service_credit` refuses to promise a credit when
   fault is null), but none of the sample orders exercise it, so it's
   untested against real data shapes; worth deliberately generating test
   cases for it.

## What I intentionally left out

- **A vector/embedding retrieval pipeline** — not worth it at 6 documents;
  see ARCHITECTURE.md.
- **Real authentication** — mocked login by design, per the assessment's
  explicit allowance to mock auth/roles.
- **Persistent storage (database)** — JSON files + in-memory session/action
  stores are sufficient for the assessment's scope and make the repo
  runnable with zero setup beyond `pip install`.
- **Multi-turn conversation persistence across sessions/reloads** — history
  lives client-side in the browser tab for this build.
- **The manager-approval queue itself** (see #4 above) — the trigger exists,
  the workflow doesn't yet.
- **Rate limiting / abuse protection** on the API — out of scope for a
  local assessment build, would be required before any real deployment.

## One metric I'd use to judge whether the product is useful

**% of customer-facing queries resolved without human escalation *and*
without a subsequent correction from a support agent within 24 hours.**

I'd track escalation rate alone, but escalation rate by itself rewards the
system for escalating everything — a system that escalates 100% of queries
has a perfect "never wrong" record and zero usefulness. Pairing it with a
post-hoc correction rate (did a human have to fix what the agent told a
customer) is what actually distinguishes "confidently wrong" from
"appropriately cautious," which is the exact failure mode the assessment
brief calls out as the biggest adoption risk. A system that escalates the
genuinely ambiguous cases (unknown fault, conflicting sources) while
answering the clear-cut ones directly and correctly is the target; this
metric is sensitive to both halves of that, where escalation rate alone is
sensitive to neither.

"""
Agent loop: system prompt + tool schemas + the tool-calling cycle against
the Google Gemini API (google-genai SDK), chosen specifically for its
genuinely free, no-credit-card tier on Flash-class models.

Tool categories (per the assessment's minimum requirement of >=3 distinct
tool types) are unchanged from the original Anthropic-backed version:
  - Document search:      search_documents
  - Structured lookup/calc: get_account_info, list_orders, list_tickets,
                             check_cancellation, check_service_credit,
                             get_sla_target
  - State-changing action:  propose_action (never executes directly --
                             see actions.py for why)

Only the LLM-calling mechanics differ from an Anthropic-backed build: tool
schema format, the request/response loop shape, and message roles
("model" instead of "assistant"). backend/data_store.py, retrieval.py,
policy_engine.py, and actions.py are completely provider-agnostic and were
not touched by this swap.
"""
import json
import time
from google import genai
from google.genai import types
from .config import GEMINI_API_KEY, MODEL_NAME
from . import data_store, retrieval, policy_engine, actions

_client = genai.Client(api_key=GEMINI_API_KEY)


def _is_rate_limit_error(e):
    msg = str(e)
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


def _generate_with_retry(model, contents, config, max_retries=2, backoff_seconds=12):
    """The free tier has a tight per-minute request limit, and this agent's
    tool loop makes several API calls per user question -- so a burst of
    quick questions can trip it. Rather than surface the raw 429 to the
    user, wait out the (short, per-minute) window and retry a couple of
    times before giving up."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return _client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            last_error = e
            if _is_rate_limit_error(e) and attempt < max_retries:
                time.sleep(backoff_seconds)
                continue
            raise
    raise last_error

# Same JSON-schema shape used for the Anthropic version's input_schema --
# Gemini's FunctionDeclaration.parameters accepts the same OpenAPI-subset
# dict, so these were carried over unchanged.
_TOOL_SCHEMAS = [
    {
        "name": "search_documents",
        "description": (
            "Search ParcelPilot's policies, SOPs, product documentation, and "
            "customer agreements. Returns the most relevant sections with "
            "metadata (status, source_tier, effective_date). Excludes the "
            "deprecated v2 support policy unless include_deprecated=true "
            "(only set true if the user explicitly asks about historical / "
            "old / superseded policy)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer"},
                "include_deprecated": {"type": "boolean"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_account_info",
        "description": "Get account details: plan, status, CSM, whether a custom contract exists. Customer sessions may only fetch their own account.",
        "parameters": {
            "type": "object",
            "properties": {"account_id": {"type": "string", "description": "Omit for customer sessions -- their own account is used automatically."}},
        },
    },
    {
        "name": "list_orders",
        "description": "List/filter orders. Customer sessions are automatically restricted to their own account regardless of account_id passed.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "order_id": {"type": "string"},
                "status": {"type": "string", "enum": ["DRAFT", "BOOKED", "PICKED_UP", "DELIVERED"]},
                "carrier": {"type": "string"},
            },
        },
    },
    {
        "name": "list_tickets",
        "description": "List/filter support tickets. Customer sessions are automatically restricted to their own account. historical_resolution field on closed tickets is unverified context, not policy authority -- never treat it as current guidance.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "ticket_id": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "closed"]},
            },
        },
    },
    {
        "name": "check_cancellation",
        "description": "Deterministically compute whether an order is cancellable right now and what fee (if any) applies, applying any account-specific contract override. Always use this instead of computing a fee yourself.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "check_service_credit",
        "description": "Deterministically compute whether an order is eligible for a failed-pickup service credit and the amount, applying any account-specific override. Always use this instead of computing a credit yourself.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "get_sla_target",
        "description": "Get the applicable first-response SLA target for an account and severity (P1/P2/P3), applying any contract override.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
            },
            "required": ["account_id", "severity"],
        },
    },
    {
        "name": "propose_action",
        "description": (
            "Prepare (but do NOT execute) a state-changing action: an escalation, "
            "a ticket update, or a follow-up task. This only creates a pending "
            "record for the user to review -- it never takes effect on its own. "
            "Tell the user what you're proposing and that they need to confirm it "
            "in the UI before anything actually happens. Only call this once you "
            "have enough information to state a clear reason."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["escalation", "ticket_update", "task"]},
                "target_id": {"type": "string", "description": "e.g. a ticket_id or order_id"},
                "payload": {"type": "object", "description": "Free-form details of the action, e.g. {'new_severity': 'P1', 'note': '...'}"},
                "reason": {"type": "string"},
            },
            "required": ["action_type", "target_id", "payload", "reason"],
        },
    },
]

_GEMINI_TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"])
        for t in _TOOL_SCHEMAS
    ])
]


def _system_prompt(session):
    role = session["role"]
    if role == "customer":
        identity = f"You are talking to a logged-in customer from account {session['account_id']}. They can only see their own account's data -- this is enforced by the tools themselves, not just by these instructions."
    else:
        identity = f"You are talking to an authorised ParcelPilot internal user ({session.get('display_name', role)}). They may query any account."

    return f"""You are the ParcelPilot support assistant.

{identity}

SOURCE PRECEDENCE (apply per topic/clause, not per document):
1. A signed customer agreement (contract) -- but only for the specific clause it addresses.
2. Current support policy / current SOP.
3. Current product documentation.
4. Historical tickets / past resolutions -- context only, MAY BE WRONG. Never
   treat historical_resolution text on a closed ticket as current policy,
   even if it looks confident or specific. If it conflicts with current
   sources, current sources win and you should say so.
The deprecated v2 support policy must never be used as authority for a
current question.

A contract overriding one clause (e.g. cancellation fee) does NOT mean it
overrides other clauses (e.g. SLA, or PICKED_UP cancellability) unless the
contract explicitly says so. Check what the override actually covers.

CALCULATIONS: never compute a fee, credit, or SLA target yourself from
retrieved text. Always call check_cancellation / check_service_credit /
get_sla_target and report their result. These tools already apply the
correct precedence and overrides.

ESCALATE (propose_action, type=escalation) rather than answering directly when:
- The situation matches the P1 definition (full outage, confirmed/suspected
  security incident or credential exposure, or other no-workaround business risk).
- An SLA target is already breached.
- check_cancellation or check_service_credit returns needs_escalation=true
  (e.g. unknown fault data, credit needing manager approval, unrecognized status).
- Sources conflict and you cannot resolve which governs.
- The request needs an action outside your tools (e.g. changing a billing
  contact -- propose a follow-up task instead of guessing how to do it).
When you propose an action, clearly tell the user what you're proposing and
that nothing happens until they confirm it in the UI -- do not imply the
action is already done.

UNCERTAINTY: if you don't have enough information (e.g. missing fault data,
ambiguous order match), say so plainly and ask for verification or propose
an escalation. Do not guess and present it as fact. If asked something you
cannot answer from the supplied sources, say that plainly rather than
inventing an answer.

Be concise. When you state a policy fact or number, briefly name the source
(e.g. "per Northstar's agreement" / "per the current Cancellation SOP").
"""


def _execute_tool(session, name, tool_input):
    try:
        if name == "search_documents":
            account_id = session.get("account_id") if session["role"] == "customer" else tool_input.get("account_id")
            return retrieval.search_documents(
                query=tool_input["query"],
                top_k=tool_input.get("top_k", 4),
                include_deprecated=tool_input.get("include_deprecated", False),
                account_id=account_id,
            )
        elif name == "get_account_info":
            return data_store.get_account(session, tool_input.get("account_id"))
        elif name == "list_orders":
            return data_store.list_orders(
                session,
                account_id=tool_input.get("account_id"),
                order_id=tool_input.get("order_id"),
                status=tool_input.get("status"),
                carrier=tool_input.get("carrier"),
            )
        elif name == "list_tickets":
            return data_store.list_tickets(
                session,
                account_id=tool_input.get("account_id"),
                ticket_id=tool_input.get("ticket_id"),
                status=tool_input.get("status"),
            )
        elif name == "check_cancellation":
            return policy_engine.check_cancellation(session, tool_input["order_id"])
        elif name == "check_service_credit":
            return policy_engine.check_service_credit(session, tool_input["order_id"])
        elif name == "get_sla_target":
            return policy_engine.get_sla_target(session, tool_input["account_id"], tool_input["severity"])
        elif name == "propose_action":
            return actions.propose_action(
                session,
                action_type=tool_input["action_type"],
                target_id=tool_input["target_id"],
                payload=tool_input.get("payload", {}),
                reason=tool_input["reason"],
            )
        else:
            return {"error": f"Unknown tool {name}"}
    except data_store.AccessDenied as e:
        return {"error": f"Access denied: {e}"}
    except Exception as e:
        return {"error": str(e)}


def user_turn(text):
    """Build a Gemini-format 'user' turn for persisted cross-turn history."""
    return {"role": "user", "parts": [{"text": text}]}


def model_turn(text):
    """Build a Gemini-format 'model' turn for persisted cross-turn history."""
    return {"role": "model", "parts": [{"text": text}]}


def run_agent(session, conversation, max_iterations=6):
    """conversation: list of Gemini-format turns, e.g. [{"role": "user",
    "parts": [{"text": "..."}]}, {"role": "model", "parts": [{"text": "..."}]}, ...]
    -- use user_turn()/model_turn() above to build these.

    Returns (final_text, tool_trace, pending_action) where tool_trace is a
    list of {"tool": name, "input": ..., "result": ...} for the UI to show
    "using tool: X", and pending_action is the last propose_action result
    (if any) so the UI can render a Confirm/Reject card.

    Only the final user/model text turns get persisted back into the
    caller's conversation history (app.py does this) -- the intermediate
    function-call/function-response turns used to resolve THIS message stay
    local to this function call, the same way the original Anthropic
    version only persisted final text across turns.
    """
    contents = list(conversation)
    tool_trace = []
    pending_action = None

    config = types.GenerateContentConfig(
        system_instruction=_system_prompt(session),
        tools=_GEMINI_TOOLS,
    )

    for _ in range(max_iterations):
        response = _generate_with_retry(MODEL_NAME, contents, config)

        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        function_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not function_calls:
            final_text = response.text or ""
            return final_text, tool_trace, pending_action

        contents.append(candidate.content)  # the model's turn, incl. function_call parts

        response_parts = []
        for fc in function_calls:
            name = fc.name
            args = dict(fc.args) if fc.args else {}
            result = _execute_tool(session, name, args)
            tool_trace.append({"tool": name, "input": args, "result": result})
            if name == "propose_action" and "error" not in result:
                pending_action = result
            response_parts.append(
                types.Part.from_function_response(name=name, response={"result": json.loads(json.dumps(result, default=str))})
            )
        contents.append(types.Content(role="user", parts=response_parts))

    return "I'm having trouble completing this request -- please rephrase or contact support directly.", tool_trace, pending_action
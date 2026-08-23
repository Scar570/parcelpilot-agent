"""
ParcelPilot Support Assistant -- Streamlit interface.

Pure Python UI. Calls the same backend/ package (agent, data_store,
policy_engine, actions, issue_detection) that was originally wired up
behind a FastAPI HTTP layer -- none of that logic changed, only the
interface layer did. See ARCHITECTURE.md for why.
"""
import os
import sys
from pathlib import Path

import streamlit as st

# Streamlit Cloud secrets -> environment variable. Must happen BEFORE
# importing backend.agent, since backend/config.py reads GEMINI_API_KEY
# from os.environ at import time. Wrapped in try/except because st.secrets
# raises if no secrets.toml exists at all, which is the normal case for a
# local run where the key is exported directly.
try:
    if "GEMINI_API_KEY" in st.secrets:
        os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.config import MOCK_USERS
from backend import agent, actions, issue_detection

st.set_page_config(page_title="ParcelPilot Support Assistant", page_icon="📦", layout="centered")

EXAMPLES_CUSTOMER = [
    "Can I cancel my booked order without a fee?",
    "Am I eligible for a service credit on my delayed pickup?",
    "What's your P1 SLA for my plan?",
]
EXAMPLES_INTERNAL = [
    "Can Northstar cancel ORD-1001 without a cancellation fee? Explain why.",
    "Is ORD-2002 eligible for a service credit?",
    "What did we tell the customer in TKT-450, and was it correct?",
]

# ---------------------------------------------------------------- state ---
defaults = {
    "session": None,
    "chat_history": [],   # Gemini-format turns, fed to agent.run_agent
    "display_log": [],    # what actually gets rendered
    "page": "Chat",
    "pending_prompt": None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def do_login(username):
    user = dict(MOCK_USERS[username])
    user["username"] = username
    st.session_state.session = user
    st.session_state.chat_history = []
    st.session_state.display_log = [
        {"type": "assistant", "text": "Hi, I'm the ParcelPilot support assistant. How can I help?"}
    ]
    st.rerun()


def do_logout():
    st.session_state.session = None
    st.session_state.chat_history = []
    st.session_state.display_log = []
    st.rerun()


# ------------------------------------------------------------ login page --
if st.session_state.session is None:
    st.title("📦 ParcelPilot Support Assistant")
    st.caption("Mocked login for the assessment demo — pick a user to see how access is scoped.")
    for username, info in MOCK_USERS.items():
        label = f"{info['display_name']}  ·  {info['role']}"
        if info.get("account_id"):
            label += f"  ·  {info['account_id']}"
        if st.button(label, use_container_width=True, key=f"login-{username}"):
            do_login(username)
    st.stop()

session = st.session_state.session

# ---------------------------------------------------------------- sidebar --
with st.sidebar:
    st.markdown(f"**{session['display_name']}**")
    caption = session["role"]
    if session.get("account_id"):
        caption += f" · {session['account_id']}"
    st.caption(caption)
    if st.button("Switch user"):
        do_logout()
    st.divider()
    pages = ["Chat"]
    if session["role"] in ("internal", "internal_manager"):
        pages.append("Proactive Issues")
    idx = pages.index(st.session_state.page) if st.session_state.page in pages else 0
    st.session_state.page = st.radio("View", pages, index=idx)

# ------------------------------------------------------ proactive issues --
if st.session_state.page == "Proactive Issues":
    st.title("Proactive Issue Detection")
    if st.button("Refresh"):
        st.rerun()

    data = issue_detection.run_all(session)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.subheader(f"SLA Risk ({len(data['sla_risk'])})")
        if not data["sla_risk"]:
            st.caption("Nothing flagged right now.")
        for f in data["sla_risk"]:
            st.warning(f"**{f['severity']}** — {f['message']}")

    with col2:
        st.subheader(f"Known-Issue Clusters ({len(data['known_issue_clusters'])})")
        if not data["known_issue_clusters"]:
            st.caption("Nothing flagged right now.")
        for f in data["known_issue_clusters"]:
            st.info(f["message"])

    with col3:
        st.subheader(f"Multi-Customer Impact ({len(data['multi_customer_impact'])})")
        if not data["multi_customer_impact"]:
            st.caption("Nothing flagged right now.")
        for f in data["multi_customer_impact"]:
            st.error(f["message"])

    st.stop()

# -------------------------------------------------------------- chat page --
st.title("📦 ParcelPilot Support Assistant")

examples = EXAMPLES_CUSTOMER if session["role"] == "customer" else EXAMPLES_INTERNAL
cols = st.columns(len(examples))
for i, ex in enumerate(examples):
    if cols[i].button(ex, key=f"ex-{i}", use_container_width=True):
        st.session_state.pending_prompt = ex

for event in st.session_state.display_log:
    if event["type"] == "user":
        with st.chat_message("user"):
            st.write(event["text"])

    elif event["type"] == "assistant":
        with st.chat_message("assistant"):
            if event.get("tool_trace"):
                tools_used = ", ".join(t["tool"] for t in event["tool_trace"])
                st.caption(f"🔧 used: {tools_used}")
            st.write(event["text"])

    elif event["type"] == "action":
        action = event["action"]
        with st.chat_message("assistant"):
            summary = (
                f"**Proposed {action['action_type'].replace('_', ' ')}** "
                f"— target `{action['target_id']}`\n\n{action['reason']}"
            )
            if action["status"] == "pending_confirmation":
                st.warning(summary + "\n\n*Nothing happens until you confirm below.*")
                c1, c2 = st.columns(2)
                if c1.button("Confirm", key=f"confirm-{action['action_id']}", type="primary", use_container_width=True):
                    actions.confirm_action(action["action_id"], session.get("display_name"))
                    action["status"] = "executed"
                    st.rerun()
                if c2.button("Reject", key=f"reject-{action['action_id']}", use_container_width=True):
                    actions.reject_action(action["action_id"], session.get("display_name"))
                    action["status"] = "rejected"
                    st.rerun()
            elif action["status"] == "executed":
                st.success(summary + "\n\n✅ Confirmed and executed.")
            else:
                st.error(summary + "\n\n❌ Rejected.")

prompt = st.chat_input("Ask a question...")
if st.session_state.pending_prompt:
    prompt = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

if prompt:
    st.session_state.display_log.append({"type": "user", "text": prompt})
    st.session_state.chat_history.append(agent.user_turn(prompt))

    with st.spinner("Thinking..."):
        try:
            final_text, tool_trace, pending_action = agent.run_agent(session, st.session_state.chat_history)
        except Exception as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                final_text = "Free-tier rate limit hit (this agent makes a few API calls per question). Please wait about 20-30 seconds and try again."
            else:
                final_text = f"Something went wrong: {e}"
            tool_trace, pending_action = [], None

    st.session_state.chat_history.append(agent.model_turn(final_text))
    st.session_state.display_log.append({"type": "assistant", "text": final_text, "tool_trace": tool_trace})
    if pending_action:
        st.session_state.display_log.append({"type": "action", "action": pending_action})

    st.rerun()
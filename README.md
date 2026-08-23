# ParcelPilot Support Agent

An AI agent for ParcelPilot support, built for the First-Round Assessment.
Supports **both** customer-facing and internal/ops user contexts through a
single mocked login screen. Pure Python end to end — Streamlit for the
interface, no HTML/CSS/JS.

## Quick start (local)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEY=your-gemini-key-here
streamlit run app.py
```

Opens at http://localhost:8501 — pick a mock user from the login screen.

- `northstar_user`, `lumenworks_user`, `beacon_user`, `axis_user` — customer
  sessions, each scoped to that account only.
- `rohit`, `maya` — internal support agents (any account, "Proactive Issues"
  view visible in the sidebar).
- `priya_manager` — internal manager (same access as above; role exists for
  future manager-approval gating, see PRODUCT.md).

No database is required — everything loads from the JSON/Markdown files in
`data/`, which were compiled from the six supplied PDFs and the xlsx
workbook.

## Deploying (free, ~5 minutes)

1. Push this repo to a **public** GitHub repo.
2. Go to [share.streamlit.io](https://share.streamlit.io) (Streamlit
   Community Cloud), sign in with GitHub, click "New app".
3. Point it at your repo, branch `main`, main file path `app.py`.
4. Under **Advanced settings → Secrets**, add:
   ```toml
   GEMINI_API_KEY = "your-gemini-key-here"
   ```
5. Deploy. You'll get a public `*.streamlit.app` URL to submit.

No Dockerfile, no server config, no build step — `app.py` reads the key
from Streamlit's secrets manager automatically (see the top of `app.py`).

## Project layout

```
app.py                 # Streamlit entrypoint — the entire interface, pure Python
.streamlit/config.toml # theme only, no custom CSS
data/
  docs/*.md            # PDF contents, transcribed with YAML front-matter
                        # (status, source_tier, effective_date, account_id)
  accounts.json        # from ParcelPilot_Assessment_Data.xlsx
  orders.json
  tickets.json
  meta.json            # dataset snapshot time, currency
  policy_rules.json    # numeric policy parameters compiled from the current
                        # docs + per-account overrides, used for deterministic
                        # calculation (see ARCHITECTURE.md)
backend/
  data_store.py         # structured lookups; ACCESS CONTROL enforced here
  retrieval.py           # BM25 document search over data/docs
  policy_engine.py       # deterministic fee/credit/SLA calculator
  actions.py              # mocked state-changing action, propose/confirm split
  issue_detection.py      # Bonus Problem 1: proactive issue detection
  agent.py                 # system prompt, tool schemas, tool-calling loop
ARCHITECTURE.md
PRODUCT.md
```

`backend/agent.py` is UI-agnostic — it's called directly by `app.py` in the
same Python process (no HTTP layer). That's a deliberate simplification
over an earlier FastAPI-backed version: at this scale, a network hop
between "the UI" and "the agent" bought nothing but hosting complexity.

## Re-deriving the data files (optional)

`data/docs/*.md` were transcribed directly from the six supplied PDFs and
`data/*.json` from the xlsx workbook, with no invented facts. If the
underlying PDFs/xlsx change, these files should be regenerated the same way
rather than hand-edited independently, since `policy_rules.json` (used for
calculation) was compiled from them and would go stale otherwise.

## Testing without running the UI

The policy engine and access-control logic have no external dependencies
and can be exercised directly:

```python
from backend import policy_engine
session = {"role": "internal", "account_id": None, "display_name": "test"}
print(policy_engine.check_cancellation(session, "ORD-1001"))
```

The full chat loop requires `GEMINI_API_KEY` and network access to the
Gemini API. The deterministic sub-components (policy engine, access
control, actions propose/confirm) were unit tested directly; the end-to-end
model conversation should be your first smoke test after cloning.

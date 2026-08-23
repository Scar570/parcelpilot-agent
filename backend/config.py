import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = os.environ.get("PARCELPILOT_MODEL", "gemini-3.6-flash")

# Mocked users for the login screen. In a real system this would be replaced
# by a real auth provider (SSO / customer portal session), but the shape
# (role + account_id OR role=internal + staff permissions) would stay the
# same -- that boundary is what the data layer enforces on every call.
MOCK_USERS = {
    "northstar_user": {"role": "customer", "account_id": "ACCT-001", "display_name": "Northstar Logistics user"},
    "lumenworks_user": {"role": "customer", "account_id": "ACCT-002", "display_name": "LumenWorks user"},
    "beacon_user": {"role": "customer", "account_id": "ACCT-003", "display_name": "Beacon Retail user"},
    "axis_user": {"role": "customer", "account_id": "ACCT-004", "display_name": "Axis Labs user"},
    "rohit": {"role": "internal", "account_id": None, "display_name": "Rohit (Support Agent)"},
    "maya": {"role": "internal", "account_id": None, "display_name": "Maya (Support Agent)"},
    "priya_manager": {"role": "internal_manager", "account_id": None, "display_name": "Priya Mehta (Manager)"},
}

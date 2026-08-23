"""
State-changing action tool, mocked locally (no real ticketing system).

CRITICAL DESIGN POINT: the model-facing tool is `propose_action` ONLY. It
writes a PENDING record and returns its id -- it never mutates ticket/order
state and is not capable of doing so. The only function that actually
executes an action is `confirm_action`, and it is called exclusively by a
dedicated backend endpoint (/actions/{id}/confirm) that fires when the user
clicks Confirm in the UI. The model has no tool that reaches confirm_action.
This means "requires human confirmation" is enforced by the absence of a
code path, not by an instruction the model could be talked out of.
"""
import uuid
import json
from datetime import datetime, timezone
from .config import DATA_DIR

_ACTIONS_LOG = DATA_DIR / "actions_log.json"
if not _ACTIONS_LOG.exists():
    _ACTIONS_LOG.write_text("[]")

_PENDING = {}  # id -> action dict, in-memory (demo-scope; would be a DB table in production)

VALID_TYPES = {"escalation", "ticket_update", "task"}


def propose_action(session, action_type, target_id, payload, reason):
    if action_type not in VALID_TYPES:
        raise ValueError(f"Unknown action type: {action_type}")
    action_id = str(uuid.uuid4())[:8]
    action = {
        "action_id": action_id,
        "action_type": action_type,
        "target_id": target_id,
        "payload": payload,
        "reason": reason,
        "proposed_by": session.get("display_name", session.get("role")),
        "proposed_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending_confirmation",
    }
    _PENDING[action_id] = action
    return action


def get_pending(action_id):
    action = _PENDING.get(action_id)
    if action is None:
        raise ValueError(f"No pending action with id {action_id} (already confirmed, rejected, or never existed).")
    return action


def confirm_action(action_id, confirmed_by):
    action = get_pending(action_id)
    action["status"] = "executed"
    action["confirmed_by"] = confirmed_by
    action["executed_at"] = datetime.now(timezone.utc).isoformat()
    _append_to_log(action)
    del _PENDING[action_id]
    return action


def reject_action(action_id, rejected_by):
    action = get_pending(action_id)
    action["status"] = "rejected"
    action["rejected_by"] = rejected_by
    action["rejected_at"] = datetime.now(timezone.utc).isoformat()
    _append_to_log(action)
    del _PENDING[action_id]
    return action


def _append_to_log(action):
    log = json.loads(_ACTIONS_LOG.read_text())
    log.append(action)
    _ACTIONS_LOG.write_text(json.dumps(log, indent=2))


def list_log():
    return json.loads(_ACTIONS_LOG.read_text())

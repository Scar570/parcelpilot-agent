"""
Structured-data layer: accounts / orders / tickets / meta.

CRITICAL DESIGN POINT: access control is enforced HERE, not via prompt
instructions. Every function takes a `session` dict and, if the caller is a
customer, silently forces account_id to the session's own account_id
regardless of what argument was requested. Internal callers may pass any
account_id. There is no code path that lets a customer session read another
account's data -- this is a hard filter in the data layer, so even if the
model is tricked/confused by a prompt injection, the underlying function
call cannot leak cross-account data.
"""
import json
from .config import DATA_DIR


class AccessDenied(Exception):
    pass


def _load(name):
    with open(DATA_DIR / f"{name}.json") as f:
        return json.load(f)


_ACCOUNTS = _load("accounts")
_ORDERS = _load("orders")
_TICKETS = _load("tickets")
_META = _load("meta")

_ACCOUNTS_BY_ID = {a["account_id"]: a for a in _ACCOUNTS}


def get_snapshot_meta():
    return dict(_META)


def _enforce_account_scope(session, requested_account_id):
    """Returns the account_id that should actually be used for this query,
    given the session's role. Raises AccessDenied if a customer explicitly
    asked for another account (rather than silently swapping it, we want the
    denial to be visible/logged -- silent substitution can mask bugs)."""
    role = session.get("role")
    if role == "customer":
        own = session["account_id"]
        if requested_account_id is not None and requested_account_id != own:
            raise AccessDenied(
                f"Session is scoped to {own}; cannot access {requested_account_id}."
            )
        return own
    # internal / internal_manager may query any account, including None (=all)
    return requested_account_id


def get_account(session, account_id=None):
    scoped_id = _enforce_account_scope(session, account_id)
    if scoped_id is None:
        raise ValueError("account_id is required for this session role.")
    acct = _ACCOUNTS_BY_ID.get(scoped_id)
    if acct is None:
        raise ValueError(f"No such account: {scoped_id}")
    return acct


def list_accounts(session):
    role = session.get("role")
    if role == "customer":
        return [_ACCOUNTS_BY_ID[session["account_id"]]]
    return list(_ACCOUNTS)


def list_orders(session, account_id=None, order_id=None, status=None, carrier=None):
    scoped_id = _enforce_account_scope(session, account_id)
    results = _ORDERS
    if scoped_id is not None:
        results = [o for o in results if o["account_id"] == scoped_id]
    if order_id is not None:
        results = [o for o in results if o["order_id"] == order_id]
    if status is not None:
        results = [o for o in results if o["status"] == status]
    if carrier is not None:
        results = [o for o in results if o["carrier"].lower() == carrier.lower()]
    return results


def get_order(session, order_id):
    # Look up without account filter first so we can raise AccessDenied
    # with a clear signal rather than a confusing "not found" if a
    # customer probes another account's order id.
    matches = [o for o in _ORDERS if o["order_id"] == order_id]
    if not matches:
        raise ValueError(f"No such order: {order_id}")
    order = matches[0]
    _enforce_account_scope(session, order["account_id"])  # raises if out of scope
    return order


def list_tickets(session, account_id=None, ticket_id=None, status=None):
    scoped_id = _enforce_account_scope(session, account_id)
    results = _TICKETS
    if scoped_id is not None:
        results = [t for t in results if t["account_id"] == scoped_id]
    if ticket_id is not None:
        results = [t for t in results if t["ticket_id"] == ticket_id]
    if status is not None:
        results = [t for t in results if t["status"] == status]
    return results


def get_ticket(session, ticket_id):
    matches = [t for t in _TICKETS if t["ticket_id"] == ticket_id]
    if not matches:
        raise ValueError(f"No such ticket: {ticket_id}")
    ticket = matches[0]
    _enforce_account_scope(session, ticket["account_id"])
    return ticket


def all_tickets_unscoped():
    """Internal-only helper for the proactive issue-detection dashboard.
    Callers must check session role themselves before using this."""
    return list(_TICKETS)


def all_orders_unscoped():
    return list(_ORDERS)


def all_accounts_unscoped():
    return list(_ACCOUNTS)

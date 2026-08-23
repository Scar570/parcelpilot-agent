"""
Deterministic calculation over policy_rules.json + order data.

CRITICAL DESIGN POINT: the LLM never computes a fee or credit amount itself.
It calls check_cancellation() / check_service_credit() and reports the
result. This is the main lever against "confidently incorrect": arithmetic
and threshold comparisons happen in tested Python, not in a token stream.

Every result includes a `basis` list showing exactly which rule fired and
which source it came from (default SOP vs. a specific account override), so
the chat UI / agent can cite provenance rather than asserting an answer.
"""
import json
from datetime import datetime
from .config import DATA_DIR
from . import data_store

with open(DATA_DIR / "policy_rules.json") as f:
    RULES = json.load(f)


def _now():
    meta = data_store.get_snapshot_meta()
    return datetime.fromisoformat(meta["snapshot_time"])


def _parse(ts):
    return datetime.fromisoformat(ts) if ts else None


def check_cancellation(session, order_id):
    order = data_store.get_order(session, order_id)  # access-controlled
    account = data_store.get_account(
        {"role": "internal", "account_id": None}, order["account_id"]
    )  # internal call: we already proved the caller may see this order

    status = order["status"]
    default_rule = RULES["default_cancellation"]
    override = RULES["account_overrides"].get(order["account_id"], {}).get("cancellation", {})

    basis = [f"default: {default_rule['source']}"]

    if status not in default_rule:
        return {
            "order_id": order_id,
            "eligible": False,
            "fee_inr": None,
            "reason": f"Unrecognized order status '{status}'.",
            "basis": basis,
            "needs_escalation": True,
        }

    base = default_rule[status]

    if status == "BOOKED":
        # check for an account override on this specific status
        status_override = override.get("BOOKED")
        if status_override:
            basis.append(f"override: {RULES['account_overrides'][order['account_id']]['source']}")
            free_window = status_override.get("free_window_minutes", base.get("free_window_minutes"))
            fee_after = status_override.get("fee_after_window_inr", base.get("fee_after_window_inr"))
            note = status_override.get("note")
        else:
            free_window = base.get("free_window_minutes")
            fee_after = base.get("fee_after_window_inr")
            note = None

        cancel_requested_at = order.get("cancellation_requested_at") or _now().isoformat()
        requested_dt = _parse(cancel_requested_at)
        booked_dt = _parse(order["booked_at"])

        if free_window is None:
            # override removes the time limit entirely
            fee = 0
            minutes_elapsed = (requested_dt - booked_dt).total_seconds() / 60
            reason = f"BOOKED, not yet picked up. Contract override waives the fee with no time limit ({minutes_elapsed:.0f} min since booking)."
        else:
            minutes_elapsed = (requested_dt - booked_dt).total_seconds() / 60
            if minutes_elapsed <= free_window:
                fee = 0
                reason = f"BOOKED, cancellation requested {minutes_elapsed:.0f} min after booking -- within the {free_window}-min free window."
            else:
                fee = fee_after
                reason = f"BOOKED, cancellation requested {minutes_elapsed:.0f} min after booking -- past the {free_window}-min free window, fee applies."

        if note:
            reason += f" ({note})"

        return {
            "order_id": order_id,
            "eligible": True,
            "fee_inr": fee,
            "reason": reason,
            "basis": basis,
            "needs_escalation": False,
        }

    # DRAFT / PICKED_UP / DELIVERED -- overrides don't apply per the data we
    # were given (Northstar's override is scoped to BOOKED only), so we use
    # the default rule directly. This is intentional: an override only
    # replaces the specific clause it names, not the whole document.
    return {
        "order_id": order_id,
        "eligible": base["cancellable"],
        "fee_inr": base.get("fee_inr", 0) if base["cancellable"] else None,
        "reason": base.get("note", f"Status is {status}."),
        "basis": basis,
        "needs_escalation": False,
    }


def check_service_credit(session, order_id):
    order = data_store.get_order(session, order_id)
    default_rule = RULES["default_service_credit"]
    override = RULES["account_overrides"].get(order["account_id"], {}).get("service_credit", {})

    basis = [f"default: {default_rule['source']}"]

    if order["carrier_fault"] is None or order["customer_fault"] is None:
        return {
            "order_id": order_id,
            "eligible": None,
            "credit_inr": None,
            "reason": "Carrier fault / customer fault is unknown for this order. Do not promise a credit -- verify with the carrier before responding.",
            "basis": basis,
            "needs_escalation": True,
        }

    if not order["carrier_fault"] or order["customer_fault"]:
        return {
            "order_id": order_id,
            "eligible": False,
            "credit_inr": 0,
            "reason": "Not eligible: carrier is not at fault, or customer is at fault.",
            "basis": basis,
            "needs_escalation": False,
        }

    threshold_hours = override.get("threshold_hours_late", default_rule["threshold_hours_late"])
    if "source" in RULES["account_overrides"].get(order["account_id"], {}):
        basis.append(f"override: {RULES['account_overrides'][order['account_id']]['source']}")

    window_end = _parse(order["pickup_window_end"])
    actual = _parse(order["pickup_actual_at"])
    reference_time = actual if actual else _now()
    hours_late = (reference_time - window_end).total_seconds() / 3600

    if hours_late <= threshold_hours:
        return {
            "order_id": order_id,
            "eligible": False,
            "credit_inr": 0,
            "reason": f"Pickup is {hours_late:.1f}h past the window end -- at or under the {threshold_hours}h threshold, not yet eligible.",
            "basis": basis,
            "needs_escalation": False,
        }

    formula = override.get("credit_formula", default_rule["credit_formula"])
    if formula.strip().isdigit():
        credit = float(formula)
    else:
        # only the specific whitelisted default formula is supported --
        # deliberately NOT a general eval() of arbitrary text for safety
        if formula == "min(500, 0.10 * shipment_fee_inr)":
            credit = min(500.0, 0.10 * order["shipment_fee_inr"])
        else:
            return {
                "order_id": order_id,
                "eligible": True,
                "credit_inr": None,
                "reason": f"Eligible, but credit formula '{formula}' is not recognized by the calculator. Escalate for manual calculation.",
                "basis": basis,
                "needs_escalation": True,
            }

    needs_approval = credit > default_rule["manager_approval_above_inr"]

    return {
        "order_id": order_id,
        "eligible": True,
        "credit_inr": round(credit, 2),
        "reason": f"Pickup is {hours_late:.1f}h past the window end (threshold {threshold_hours}h), carrier at fault, customer not at fault.",
        "basis": basis,
        "needs_escalation": needs_approval,
        "needs_manager_approval": needs_approval,
    }


def get_sla_target(session, account_id, severity):
    account = data_store.get_account(session, account_id)
    override = RULES["account_overrides"].get(account_id, {}).get("sla")
    if override and severity in override:
        return {
            "target": override[severity],
            "source": RULES["account_overrides"][account_id]["source"],
            "coverage_note": override.get("coverage_note"),
        }
    default = RULES["default_sla"].get(account["plan"], {}).get(severity)
    return {"target": default, "source": RULES["default_sla"]["source"], "coverage_note": None}

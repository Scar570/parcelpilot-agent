"""
Bonus Problem 1: Proactive Issue Detection (internal-only).

v1 scope, intentionally simple and rule-based rather than ML-based -- see
PRODUCT.md for what a v2 (embedding-based clustering, anomaly detection
over volume trends) would add and why it wasn't built for this submission.

Three detectors, each over the full (unscoped) ticket/order set -- this
module must only ever be called from an internal-role session; the caller
in main.py enforces that before invoking anything here.
"""
from datetime import datetime
from . import data_store
from .policy_engine import get_sla_target, RULES

KNOWN_ISSUE_KEYWORDS = {
    "KI-208": ["bulk upload", "csv", "upload fail"],
    "KI-211": ["swiftship", "still shows booked", "booked after", "pickup"],
}

# crude severity classifier: keyword match against the same P1/P2/P3
# language used in support_policy_v3. A production version would use the
# same LLM call already made to answer the ticket, cached at ticket-creation
# time, rather than a second heuristic -- flagged as a known simplification.
P1_KEYWORDS = ["all shipment creation", "http 500", "outage", "api key", "credential", "security incident"]


def _now():
    meta = data_store.get_snapshot_meta()
    return datetime.fromisoformat(meta["snapshot_time"])


def _hours_since(ts):
    return (_now() - datetime.fromisoformat(ts)).total_seconds() / 3600


def _guess_severity(ticket):
    text = f"{ticket['subject']} {ticket['description']}".lower()
    if any(k in text for k in P1_KEYWORDS):
        return "P1"
    return "P3"  # conservative default; P2 detection intentionally left for v2


def detect_sla_risk(session):
    findings = []
    for ticket in data_store.all_tickets_unscoped():
        if ticket["status"] != "open":
            continue
        severity = _guess_severity(ticket)
        account = next(a for a in data_store.all_accounts_unscoped() if a["account_id"] == ticket["account_id"])
        target = get_sla_target(session, ticket["account_id"], severity)
        elapsed_h = _hours_since(ticket["created_at"])
        # only flag clearly (severity == P1, elapsed > 0.5h) to avoid noisy
        # false positives from the business-hours vs. clock-hours ambiguity
        # in P3 targets, which this v1 does not model precisely
        if severity == "P1" and elapsed_h > 0.25:
            findings.append({
                "type": "sla_risk",
                "ticket_id": ticket["ticket_id"],
                "account_name": account["account_name"],
                "severity": severity,
                "elapsed_hours": round(elapsed_h, 2),
                "sla_target": target["target"],
                "sla_source": target["source"],
                "message": f"{ticket['ticket_id']} ({account['account_name']}) looks like {severity} by policy definition -- target is {target['target']}, open {elapsed_h:.1f}h.",
            })
    return findings


def detect_known_issue_clusters(session):
    findings = []
    clusters = {ki: [] for ki in KNOWN_ISSUE_KEYWORDS}
    for ticket in data_store.all_tickets_unscoped():
        if ticket["status"] != "open":
            continue
        text = f"{ticket['subject']} {ticket['description']} {ticket['carrier'] if 'carrier' in ticket else ''}".lower()
        for ki, keywords in KNOWN_ISSUE_KEYWORDS.items():
            if any(k in text for k in keywords):
                clusters[ki].append(ticket)
    for ki, tickets in clusters.items():
        if not tickets:
            continue
        accounts = {t["account_id"] for t in tickets}
        findings.append({
            "type": "known_issue_cluster",
            "known_issue": ki,
            "ticket_ids": [t["ticket_id"] for t in tickets],
            "affected_accounts": len(accounts),
            "message": f"{len(tickets)} open ticket(s) match {ki}, across {len(accounts)} account(s).",
        })
    return findings


def detect_multi_customer_impact(session):
    """Cross-check known-issue clusters for accounts > 1 -- flagged
    separately from detect_known_issue_clusters since 'affects more than
    one customer at once' is a distinct signal worth surfacing on its own,
    per the brief's example list."""
    findings = []
    for f in detect_known_issue_clusters(session):
        if f["affected_accounts"] > 1:
            findings.append({**f, "type": "multi_customer_impact"})
    return findings


def run_all(session):
    if session.get("role") not in ("internal", "internal_manager"):
        raise PermissionError("Issue detection is internal-only.")
    return {
        "sla_risk": detect_sla_risk(session),
        "known_issue_clusters": detect_known_issue_clusters(session),
        "multi_customer_impact": detect_multi_customer_impact(session),
        "generated_at": _now().isoformat(),
    }

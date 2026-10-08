"""
Mock PayPal sandbox server for the AI Invoice Collector (InvoicePilot).

Implements just enough of the PayPal REST surface for a full local test
loop with ZERO credentials and ZERO network:

  POST /v1/oauth2/token                 -> mock OAuth2 access token
  POST /v2/invoicing/invoices           -> 201, creates a fake invoice
  POST /v2/invoicing/invoices/<id>/send -> 200, marks it SENT
  GET  /v2/invoicing/invoices/<id>      -> tracked status (default UNPAID)
  POST /simulate/pay/<id>               -> marks invoice PAID and POSTs a
                                          INVOICING.INVOICE.PAID webhook
                                          event to WEBHOOK_URL

Point paypal_invoice.py at this server with:
  PAYPAL_BASE_URL=http://127.0.0.1:5001

Run:  python mock_paypal_server.py   (port from MOCK_PORT, default 5001)
"""

import os
import random
import string
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

MOCK_PORT = int(os.environ.get("MOCK_PORT", "5001"))
# Where the mock POSTs INVOICING.INVOICE.PAID webhook events when a
# simulated payment happens (see /simulate/pay/<id>).
WEBHOOK_URL = os.environ.get(
    "WEBHOOK_URL", "http://127.0.0.1:5002/webhooks/paypal"
)

# In-memory invoice store: id -> {"status", "currency", "amount",
# "item_name", "customer_email", "customer_name", "due_date"}
_invoices = {}


def _new_invoice_id():
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"INV2-MOCK-{suffix}"


@app.post("/v1/oauth2/token")
def oauth_token():
    """Mock client-credentials token endpoint (accepts anything)."""
    grant = request.form.get("grant_type", "")
    app.logger.info("mock token request (grant_type=%s)", grant)
    return jsonify(
        {
            "access_token": "mock-A21AA" + "".join(
                random.choices(string.ascii_letters + string.digits, k=24)
            ),
            "token_type": "Bearer",
            "expires_in": 3600,
            "app_id": "MOCK-APP",
        }
    )


@app.post("/v2/invoicing/invoices")
def create_invoice():
    """Mock invoice creation -> 201 with a fake INV2-MOCK-XXXX id.

    Echoes the key request fields (currency, amount, item, customer)
    back in the response and stores them for GET / simulate flows.
    """
    payload = request.get_json(force=True, silent=True) or {}
    detail = payload.get("detail", {}) or {}
    items = payload.get("items", []) or [{}]
    recipients = payload.get("primary_recipients", []) or [{}]
    billing = (recipients[0].get("billing_info") or {}) if recipients else {}

    invoice_id = _new_invoice_id()
    currency = detail.get("currency_code", "USD")
    unit = (items[0].get("unit_amount") or {}) if items else {}
    _invoices[invoice_id] = {
        "status": "DRAFT",
        "currency": unit.get("currency_code", currency),
        "amount": unit.get("value", "0.00"),
        "item_name": items[0].get("name", "") if items else "",
        "customer_email": billing.get("email_address", ""),
        "customer_name": (billing.get("name") or {}).get("given_name", ""),
        "due_date": (detail.get("payment_term") or {}).get("due_date"),
        "invoice_number": f"MOCK-{invoice_id[-4:]}",
    }
    app.logger.info("mock created invoice %s", invoice_id)
    return (
        jsonify(
            {
                "id": invoice_id,
                "status": "DRAFT",
                "detail": {
                    "currency_code": currency,
                    "invoice_number": _invoices[invoice_id]["invoice_number"],
                },
            }
        ),
        201,
    )


@app.post("/v2/invoicing/invoices/<invoice_id>/send")
def send_invoice(invoice_id):
    """Mock send -> 200, status becomes SENT."""
    inv = _invoices.get(invoice_id)
    if inv is None:
        return jsonify({"name": "RESOURCE_NOT_FOUND",
                        "message": f"Unknown invoice {invoice_id}"}), 404
    inv["status"] = "SENT"
    app.logger.info("mock sent invoice %s", invoice_id)
    return jsonify({"id": invoice_id, "status": "SENT"})


@app.get("/v2/invoicing/invoices/<invoice_id>")
def get_invoice(invoice_id):
    """Mock invoice details -> tracked status (UNPAID once sent)."""
    inv = _invoices.get(invoice_id)
    if inv is None:
        return jsonify({"name": "RESOURCE_NOT_FOUND",
                        "message": f"Unknown invoice {invoice_id}"}), 404
    # PayPal reports sent-but-unpaid invoices as UNPAID on GET.
    status = "UNPAID" if inv["status"] in ("SENT", "UNPAID") else inv["status"]
    return jsonify(
        {
            "id": invoice_id,
            "status": status,
            "detail": {
                "currency_code": inv["currency"],
                "invoice_number": inv["invoice_number"],
            },
            "due_amount": {"currency_code": inv["currency"],
                           "value": inv["amount"]},
        }
    )


@app.post("/simulate/pay/<invoice_id>")
def simulate_pay(invoice_id):
    """Test helper (NOT a PayPal endpoint): pretend the customer paid.

    Marks the invoice PAID and POSTs an INVOICING.INVOICE.PAID webhook
    event to WEBHOOK_URL, exactly like PayPal's sandbox would.
    """
    inv = _invoices.get(invoice_id)
    if inv is None:
        return jsonify({"name": "RESOURCE_NOT_FOUND",
                        "message": f"Unknown invoice {invoice_id}"}), 404
    inv["status"] = "PAID"

    event = {
        "id": "WH-MOCK-" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)),
        "event_version": "1.0",
        "create_time": datetime.now(timezone.utc).isoformat(),
        "resource_type": "invoices",
        "event_type": "INVOICING.INVOICE.PAID",
        "summary": f"Invoice {invoice_id} was paid",
        "resource": {
            "id": invoice_id,
            "status": "PAID",
            "detail": {
                "currency_code": inv["currency"],
                "invoice_number": inv["invoice_number"],
            },
            "amount": {
                "currency_code": inv["currency"],
                "value": inv["amount"],
            },
        },
    }
    delivered = False
    try:
        r = requests.post(WEBHOOK_URL, json=event, timeout=10)
        delivered = r.status_code < 300
        app.logger.info("webhook POST -> %s (%s)", WEBHOOK_URL, r.status_code)
    except Exception as exc:  # webhook receiver not running -> not fatal
        app.logger.warning("webhook POST failed: %s", exc)

    return jsonify({"id": invoice_id, "status": "PAID",
                    "webhook_delivered": delivered})


if __name__ == "__main__":
    print(f"Mock PayPal server on http://127.0.0.1:{MOCK_PORT}")
    print(f"Webhook target: {WEBHOOK_URL}")
    app.run(host="127.0.0.1", port=MOCK_PORT)

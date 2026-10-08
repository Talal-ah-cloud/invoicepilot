"""
PayPal Invoicing API v2 — sandbox scaffold for the AI Invoice Collector.

Hackathon project: PayPal AI Hackathon 2026 (submissions close Nov 12, 2026).
Flow: owner messages WhatsApp bot -> bot creates PayPal invoice via API ->
customer gets payment link on WhatsApp -> PayPal webhook reports payment ->
bot confirms + follows up on unpaid invoices.

Dependencies: only `requests` (pip install requests). Nothing exotic.

Credentials: read from environment variables PAYPAL_CLIENT_ID and
PAYPAL_CLIENT_SECRET. NEVER hard-code or commit real credentials.
Sandbox is the default; pass sandbox=False for live (not needed for the
hackathon — judges test against sandbox).

Endpoints used (all verified against PayPal Developer docs, Oct 2026):
  POST {base}/v1/oauth2/token                      -> OAuth2 access token
  POST {base}/v2/invoicing/invoices                 -> create draft invoice (201)
  POST {base}/v2/invoicing/invoices/{id}/send       -> send invoice (200)
  GET  {base}/v2/invoicing/invoices/{id}            -> invoice details

Relevant webhook events (official docs, "Invoicing webhooks", Sep 2026):
  INVOICING.INVOICE.CREATED / PAID / CANCELLED / REFUNDED / SCHEDULED / UPDATED
(Webhook receiver is a Week-3 task — not implemented here.)
"""

import os
import time
import uuid
from datetime import date, timedelta

import requests
from requests.auth import HTTPBasicAuth

# PAYPAL_BASE_URL overrides the sandbox endpoint for local testing, e.g.
# PAYPAL_BASE_URL=http://127.0.0.1:5001 runs this same code against the mock
# PayPal server (see mock_paypal_server.py). Unset = real PayPal sandbox.
SANDBOX_BASE = os.environ.get("PAYPAL_BASE_URL") or "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"

# Well-known PayPal payer-view URL pattern (the same URL PayPal puts in its
# own invoice notification emails, e.g. <a href="https://www.paypal.com/
# invoice/payerView/details/INV2-...">). It is NOT returned by the API, so
# we construct it from the invoice id. Verified against multiple live PayPal
# invoice emails. If PayPal ever changes this pattern, fall back to the
# payment link PayPal emails to the payer, or use the invoice "qr-code"
# endpoint (POST /v2/invoicing/invoices/{id}/generate-qr-code).
PAYER_VIEW_URL = {
    True: "https://www.sandbox.paypal.com/invoice/payerView/details/{invoice_id}",
    False: "https://www.paypal.com/invoice/payerView/details/{invoice_id}",
}


class PayPalError(Exception):
    """Raised when a PayPal API call fails (non-2xx response)."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


# --- token cache: access tokens live up to ~8h (read expires_in, don't assume)
_token_cache = {"token": None, "expires_at": 0.0}


def get_access_token(client_id=None, client_secret=None, sandbox=True):
    """Return a cached OAuth2 access token (client-credentials flow).

    POST {base}/v1/oauth2/token with HTTP Basic Auth (client_id:secret)
    and form body grant_type=client_credentials.
    Token is cached until ~60s before its expires_in; refresh is automatic.
    """
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID")
    client_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise PayPalError(
            "Missing credentials. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET "
            "environment variables (see SANDBOX_SETUP.md)."
        )

    base = SANDBOX_BASE if sandbox else LIVE_BASE
    resp = requests.post(
        f"{base}/v1/oauth2/token",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise PayPalError(
            f"Token request failed ({resp.status_code})",
            status_code=resp.status_code,
            response_body=resp.text,
        )
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    # expires_in is documented as up to 28800s (8h); read it, don't assume.
    _token_cache["expires_at"] = now + int(data.get("expires_in", 28800))
    return _token_cache["token"]


def _headers(sandbox=True):
    token = get_access_token(sandbox=sandbox)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # PayPal recommends a unique request id on every POST for idempotency
        # (re-using the same id on retry prevents duplicate invoices).
        "PayPal-Request-Id": str(uuid.uuid4()),
    }


def _check(resp, ok_codes, action):
    if resp.status_code not in ok_codes:
        raise PayPalError(
            f"{action} failed ({resp.status_code}): {resp.text[:500]}",
            status_code=resp.status_code,
            response_body=resp.text,
        )
    return resp.json() if resp.text else {}


def create_draft_invoice(
    customer_email,
    item_name,
    amount,
    currency="USD",
    due_days=7,
    customer_name=None,
    note=None,
    sandbox=True,
):
    """Create a DRAFT invoice. Returns the invoice id (e.g. INV2-XXXX-...).

    Minimal payload verified against PayPal's official "Integrate invoicing"
    guide: detail (currency_code), invoicer name, one primary_recipient
    (billing_info.email_address), and an items array with name/quantity/
    unit_amount. The API computes totals server-side.

    due_days: mapped to payment_term DUE_ON_DATE_SPECIFIED with a computed
    due_date (verified in official docs example). If due_days <= 0 the
    payment_term is omitted (PayPal default applies).
    NOTE: other term_type values (NET_10, NET_30, DUE_ON_RECEIPT, ...)
    exist in the API reference but were not individually verified here —
    stick to due_days for now.
    """
    payload = {
        "detail": {"currency_code": currency},
        "invoicer": {"name": {"given_name": "MT AI", "surname": "Solutions"}},
        "primary_recipients": [
            {"billing_info": {"email_address": customer_email}}
        ],
        "items": [
            {
                "name": item_name,
                "quantity": "1",
                "unit_amount": {
                    "currency_code": currency,
                    "value": f"{float(amount):.2f}",
                },
            }
        ],
    }
    if customer_name:
        # billing_info.name is optional; email alone is enough to create.
        parts = customer_name.split(None, 1)
        payload["primary_recipients"][0]["billing_info"]["name"] = {
            "given_name": parts[0],
            "surname": parts[1] if len(parts) > 1 else "",
        }
    if note:
        payload["detail"]["note"] = note
    if due_days and due_days > 0:
        due_date = (date.today() + timedelta(days=due_days)).isoformat()
        payload["detail"]["payment_term"] = {
            "term_type": "DUE_ON_DATE_SPECIFIED",
            "due_date": due_date,
        }

    base = SANDBOX_BASE if sandbox else LIVE_BASE
    resp = requests.post(
        f"{base}/v2/invoicing/invoices",
        headers=_headers(sandbox=sandbox),
        json=payload,
        timeout=30,
    )
    data = _check(resp, (201,), "Create draft invoice")
    # 201 response body contains the invoice id (INV2-XXXX-XXXX-XXXX-XXXX).
    return data["id"]


def send_invoice(invoice_id, send_to_recipient=True, sandbox=True):
    """Send a draft invoice -> status becomes SENT/UNPAID. Returns True.

    POST /v2/invoicing/invoices/{invoice_id}/send with
    {"send_to_recipient": ..., "send_to_invoicer": ...} (200 OK on success).
    send_to_recipient=True also makes PayPal email the payer the secure
    payment link. For our WhatsApp flow the customer gets the link on
    WhatsApp, so this may later be set False (share-link mode).
    """
    base = SANDBOX_BASE if sandbox else LIVE_BASE
    resp = requests.post(
        f"{base}/v2/invoicing/invoices/{invoice_id}/send",
        headers=_headers(sandbox=sandbox),
        json={"send_to_recipient": send_to_recipient, "send_to_invoicer": True},
        timeout=30,
    )
    _check(resp, (200, 202), "Send invoice")
    return True


def payment_link_for(invoice_id, sandbox=True):
    """Build the payer's payment link for an invoice.

    See PAYER_VIEW_URL note above: well-known PayPal pattern, not an API
    field. Works for sandbox and live invoice ids.
    """
    return PAYER_VIEW_URL[sandbox].format(invoice_id=invoice_id)


def get_invoice(invoice_id, sandbox=True):
    """Fetch invoice details. Returns dict with id, status, payment_link, etc.

    GET /v2/invoicing/invoices/{invoice_id}. Status values include
    DRAFT, SENT, UNPAID, PAID, CANCELLED (full list in API reference).
    """
    base = SANDBOX_BASE if sandbox else LIVE_BASE
    resp = requests.get(
        f"{base}/v2/invoicing/invoices/{invoice_id}",
        headers={
            "Authorization": f"Bearer {get_access_token(sandbox=sandbox)}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    data = _check(resp, (200,), "Get invoice details")
    return {
        "id": data.get("id"),
        "status": data.get("status"),
        "payment_link": payment_link_for(invoice_id, sandbox=sandbox),
        "currency": (data.get("detail") or {}).get("currency_code"),
        "due_amount": ((data.get("due_amount") or {}).get("value")),
        "invoice_number": (data.get("detail") or {}).get("invoice_number"),
        "raw": data,
    }


def create_and_send_invoice(
    customer_email,
    item_name,
    amount,
    currency="USD",
    due_days=7,
    customer_name=None,
    note=None,
    sandbox=True,
):
    """Convenience wrapper: draft + send. Returns (invoice_id, payment_link)."""
    invoice_id = create_draft_invoice(
        customer_email=customer_email,
        item_name=item_name,
        amount=amount,
        currency=currency,
        due_days=due_days,
        customer_name=customer_name,
        note=note,
        sandbox=sandbox,
    )
    send_invoice(invoice_id, sandbox=sandbox)
    return invoice_id, payment_link_for(invoice_id, sandbox=sandbox)


if __name__ == "__main__":
    # Self-test: creates + sends a $1.00 sandbox invoice and prints the
    # payment link. Requires PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET env vars.
    # The test recipient should be a SANDBOX buyer email (see SANDBOX_SETUP.md).
    # NOTE: each run creates a REAL sandbox invoice (sandbox only — no real
    # money moves). Delete test invoices from the sandbox dashboard if needed.
    TEST_EMAIL = os.environ.get("PAYPAL_TEST_BUYER_EMAIL", "buyer@example.com")

    print("PayPal Invoicing API v2 — sandbox self-test")
    print("=" * 55)
    try:
        token = get_access_token()
        print(f"[ok] access token acquired (starts with {token[:6]}...)")

        inv_id, link = create_and_send_invoice(
            customer_email=TEST_EMAIL,
            item_name="AI Invoice Collector — test invoice",
            amount=1.00,
            currency="USD",
            due_days=7,
            customer_name="Test Buyer",
            note="Hackathon sandbox test — please ignore.",
        )
        print(f"[ok] invoice created + sent: {inv_id}")

        info = get_invoice(inv_id)
        print(f"[ok] status: {info['status']}")
        print()
        print("Payment link (open as the sandbox buyer to pay):")
        print(link)
    except PayPalError as e:
        print(f"[FAIL] {e}")
        raise SystemExit(1)

"""
PayPal webhook receiver for the AI Invoice Collector (InvoicePilot).

  POST /webhooks/paypal -> handles PayPal webhook events. On
  INVOICING.INVOICE.PAID it prints a PAYMENT RECEIVED confirmation and
  appends {"invoice_id","amount","currency","received_at_utc"} to
  payments_log.json (same directory as this file). Other event types
  are logged and acknowledged.

Signature verification: verify_paypal_signature() below implements the
real PayPal verify-webhook-signature flow (POST
/v1/notifications/verify-webhook-signature) for production use. It is
SKIPPED while MOCK_MODE=true (the default), because the mock server
signs nothing.

Run:  MOCK_MODE=true python webhook_server.py
      (port from WEBHOOK_PORT, default 5002)
"""

import json
import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "5002"))
# "true" (default) = skip signature verification (mock server mode).
MOCK_MODE = os.environ.get("MOCK_MODE", "true").lower() == "true"

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "payments_log.json")


def verify_paypal_signature(payload_bytes, headers, webhook_id=None,
                            sandbox=True):
    """Verify a PayPal webhook signature (production use).

    Implements PayPal's documented flow: POST
    {base}/v1/notifications/verify-webhook-signature with the transmission
    id/time, cert url, auth algorithm, the raw transmission signature, the
    webhook id, and the raw event body. Returns True only when PayPal
    answers verification_status == "SUCCESS".

    Requires env: PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_WEBHOOK_ID
    (the webhook id from developer.paypal.com -> Webhooks). Raises
    RuntimeError when credentials are missing so a production deploy fails
    loudly instead of silently accepting unsigned events.
    """
    from requests.auth import HTTPBasicAuth

    client_id = os.environ.get("PAYPAL_CLIENT_ID")
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET")
    webhook_id = webhook_id or os.environ.get("PAYPAL_WEBHOOK_ID")
    if not (client_id and client_secret and webhook_id):
        raise RuntimeError(
            "Cannot verify webhook signature: set PAYPAL_CLIENT_ID, "
            "PAYPAL_CLIENT_SECRET and PAYPAL_WEBHOOK_ID."
        )

    base = ("https://api-m.sandbox.paypal.com" if sandbox
            else "https://api-m.paypal.com")
    token_resp = requests.post(
        f"{base}/v1/oauth2/token",
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]

    verify_resp = requests.post(
        f"{base}/v1/notifications/verify-webhook-signature",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={
            "transmission_id": headers.get("Paypal-Transmission-Id"),
            "transmission_time": headers.get("Paypal-Transmission-Time"),
            "cert_url": headers.get("Paypal-Cert-Url"),
            "auth_algo": headers.get("Paypal-Auth-Algo"),
            "transmission_sig": headers.get("Paypal-Transmission-Sig"),
            "webhook_id": webhook_id,
            "webhook_event": json.loads(payload_bytes.decode("utf-8")),
        },
        timeout=30,
    )
    verify_resp.raise_for_status()
    return (verify_resp.json().get("verification_status") == "SUCCESS")


def _append_payment(entry):
    """Append one payment record to payments_log.json (tolerant of a
    missing or corrupt log file)."""
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            log = json.load(f)
            if not isinstance(log, list):
                log = []
    except (FileNotFoundError, json.JSONDecodeError):
        log = []
    log.append(entry)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


@app.post("/webhooks/paypal")
def paypal_webhook():
    raw = request.get_data()
    try:
        event = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        return jsonify({"error": "invalid JSON"}), 400

    if not MOCK_MODE:
        # Production path: reject anything PayPal didn't sign.
        try:
            ok = verify_paypal_signature(raw, request.headers)
        except Exception as exc:
            app.logger.error("signature verification error: %s", exc)
            return jsonify({"error": "verification failed"}), 500
        if not ok:
            app.logger.warning("rejected webhook: bad signature")
            return jsonify({"error": "bad signature"}), 401
    # MOCK_MODE=true: accept unsigned events (the mock signs nothing).

    event_type = event.get("event_type", "UNKNOWN")
    resource = event.get("resource") or {}

    if event_type == "INVOICING.INVOICE.PAID":
        invoice_id = resource.get("id", "?")
        amount = (resource.get("amount") or {}).get("value", "?")
        currency = ((resource.get("amount") or {}).get("currency_code")
                    or (resource.get("detail") or {}).get("currency_code")
                    or "?")
        entry = {
            "invoice_id": invoice_id,
            "amount": amount,
            "currency": currency,
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _append_payment(entry)
        print("=" * 60, flush=True)
        print("  PAYMENT RECEIVED", flush=True)
        print(f"  invoice : {invoice_id}", flush=True)
        print(f"  amount  : {amount} {currency}", flush=True)
        print("=" * 60, flush=True)
    else:
        app.logger.info("webhook event %s (no action)", event_type)

    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"PayPal webhook receiver on http://127.0.0.1:{WEBHOOK_PORT}/webhooks/paypal")
    print(f"MOCK_MODE={str(MOCK_MODE).lower()} (signature check "
          f"{'SKIPPED' if MOCK_MODE else 'ENFORCED'})")
    print(f"Payment log: {LOG_PATH}")
    app.run(host="127.0.0.1", port=WEBHOOK_PORT)

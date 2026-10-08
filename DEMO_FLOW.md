# InvoicePilot — Full Local Demo Flow (no credentials, no network)

Tests the entire loop — AI extraction → PayPal invoice create/send →
payment link → simulated customer payment → webhook PAYMENT RECEIVED —
using a local mock of the PayPal sandbox. Verified end-to-end 2026-10-08.

## Setup (once)

```bash
pip install flask requests
cd ~/workspace/goals/paypal-ai-hackathon-ai-invoice-collector/hidden_files
```

## Terminal 1 — mock PayPal server (port 5001)

```bash
cd ~/workspace/goals/paypal-ai-hackathon-ai-invoice-collector/hidden_files
python mock_paypal_server.py
```

Leave running. Implements: `POST /v1/oauth2/token`,
`POST /v2/invoicing/invoices` (→ 201), `POST .../send` (→ 200),
`GET /v2/invoicing/invoices/<id>`, plus `POST /simulate/pay/<id>`
(test helper that marks an invoice PAID and fires the webhook).

## Terminal 2 — webhook receiver (port 5002)

```bash
cd ~/workspace/goals/paypal-ai-hackathon-ai-invoice-collector/hidden_files
MOCK_MODE=true python webhook_server.py
```

Leave running. Listens on `/webhooks/paypal`; on
`INVOICING.INVOICE.PAID` prints `PAYMENT RECEIVED` and appends to
`payments_log.json`. (`MOCK_MODE=true` skips signature verification;
production would enforce it via `verify_paypal_signature()`.)

## Terminal 3 — the demo

```bash
cd ~/workspace/goals/paypal-ai-hackathon-ai-invoice-collector/hidden_files
PAYPAL_BASE_URL=http://127.0.0.1:5001 \
PAYPAL_CLIENT_ID=mock-id \
PAYPAL_CLIENT_SECRET=mock-secret \
python3 << 'EOF'
from invoice_ai import extract_invoice_details
from paypal_invoice import create_and_send_invoice, get_invoice

# 1. Owner's WhatsApp message -> structured invoice details
#    (uses OpenAI if OPENAI_API_KEY is set, else offline regex)
details = extract_invoice_details(
    "Send Ahmed a $500 invoice for website work, due in 7 days")
print("parsed:", details)

# 2. Create + send the PayPal invoice (talks to the mock server)
invoice_id, link = create_and_send_invoice(
    customer_email="ahmed.buyer@example.com",
    customer_name=details["customer"],
    item_name=details["item"],
    amount=details["amount"],
    currency=details["currency"],
    due_days=details["due_in_days"],
    sandbox=True,
)
print("invoice id :", invoice_id)
print("payer link :", link)
print("status     :", get_invoice(invoice_id)["status"])
EOF
```

Expected: `parsed: {'customer': 'Ahmed', 'amount': 500.0, 'currency': 'USD',
'item': 'website work', 'due_in_days': 7}`, an `INV2-MOCK-XXXX` id, and
`status: UNPAID`.

## Simulate the customer paying

In Terminal 3 (replace the id with yours):

```bash
curl -X POST http://127.0.0.1:5001/simulate/pay/INV2-MOCK-XXXX
```

Watch **Terminal 2** — it prints:

```
============================================================
  PAYMENT RECEIVED
  invoice : INV2-MOCK-XXXX
  amount  : 500.00 USD
============================================================
```

And `payments_log.json` gains:

```json
[{"invoice_id": "INV2-MOCK-XXXX", "amount": "500.00",
  "currency": "USD", "received_at_utc": "..."}]
```

Confirm the status flipped:

```bash
PAYPAL_BASE_URL=http://127.0.0.1:5001 \
PAYPAL_CLIENT_ID=mock-id PAYPAL_CLIENT_SECRET=mock-secret \
python3 -c "from paypal_invoice import get_invoice;
print(get_invoice('INV2-MOCK-XXXX')['status'])"
# -> PAID
```

## Notes

- `PAYPAL_BASE_URL` is the only env override; unset it and
  `paypal_invoice.py` talks to the real PayPal sandbox again.
- The mock's credentials check accepts anything — `mock-id`/`mock-secret`
  are placeholders, never real secrets.
- Swap the sample message for Roman Urdu to exercise the parser, e.g.
  `"Bilal ko 200 dollar ka invoice bhejo design ke liye, 3 din mein due"`.

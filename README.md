InvoicePilot
AI payment-collection agent for WhatsApp — turns a chat message into a PayPal invoice, sends the payment link, confirms on payment, and auto-follows-up on unpaid invoices.
Built for the PayPal AI Hackathon 2026.
How it works
The owner writes a plain message, e.g. "Send Ahmed a $500 invoice for website work, due in 7 days."
The AI extracts customer, item, amount, currency, and due date.
paypal_invoice.py creates and sends a real PayPal invoice via the PayPal Invoicing API v2.
The customer receives a PayPal payment link (over WhatsApp).
PayPal's INVOICING.INVOICE.PAID webhook confirms payment → owner gets notified instantly.
Unpaid invoices get automatic, stage-appropriate follow-up reminders in the customer's language.
Quickstart (PayPal sandbox — free, no real money)
Everything runs in PayPal's sandbox (test mode). No real money moves, ever.
1. Create a sandbox app
Go to developer.paypal.com → Log in or Sign up (free).
Open Apps & Credentials → select the Sandbox tab (not Live).
Create App → name it AI Invoice Collector → Create.
Copy the Client ID and Secret (click Show).
⚠️ These are secrets. Never share them, never commit them. The .gitignore in this repo already excludes .env.
2. Set credentials (Windows)
Command Prompt:
set PAYPAL_CLIENT_ID=your_client_id_here
set PAYPAL_CLIENT_SECRET=your_secret_here
PowerShell:
$env:PAYPAL_CLIENT_ID="your_client_id_here"
$env:PAYPAL_CLIENT_SECRET="your_secret_here"
3. Install + run the self-test
pip install requests
python paypal_invoice.py
You should see:
[ok] access token acquired
[ok] invoice created + sent: INV2-XXXX-XXXX-XXXX-XXXX
[ok] status: UNPAID

Payment link (open as the sandbox buyer to pay):
https://www.sandbox.paypal.com/invoice/payerView/details/INV2-XXXX-...
This creates a $1 test invoice (fake sandbox dollars — costs you nothing).
4. Pay it as a test buyer (verifies the full loop)
developer.paypal.com/dashboard/accounts → Create Account → Personal → Country: United States.
Open the payment link from step 3 → log in with the test buyer → pay the invoice.
Check the invoice status again — it should read PAID.
Project structure
invoicepilot/
├── paypal_invoice.py   # PayPal Invoicing API v2 client (sandbox): OAuth2,
│                       # create/send/get invoice, payer-view link, $1 self-test
├── .gitignore          # excludes .env and other secrets
├── README.md
└── LICENSE             # MIT
Roadmap: webhook receiver for INVOICING.INVOICE.PAID, AI invoice-detail extraction from natural language, multilingual follow-up engine, owner dashboard (collected vs. outstanding).
Disclosure
The multilingual WhatsApp business-bot engine this project plugs into was built before October 1, 2026 and had no payment functionality. Every PayPal-related line of code here — the OAuth flow, invoice create/send/status, payer-link generation, and webhook handling — was written during the hackathon submission period.
License
MIT — see LICENSE.

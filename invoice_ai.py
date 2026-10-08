"""
Natural-language invoice detail extraction for the AI Invoice Collector.

  extract_invoice_details(message) -> {
      "customer": str, "amount": float, "currency": "USD"|...,
      "item": str, "due_in_days": int
  }

Two paths:
  1. OpenAI chat API (env OPENAI_API_KEY) with a strict JSON-only system
     prompt. Any failure (no key, network error, bad JSON) falls through.
  2. Rule-based regex fallback — no network at all. Handles English and
     Roman Urdu, e.g.:
       "Send Ahmed a $500 invoice for website work, due in 7 days"
       "Bilal ko 200 dollar ka invoice bhejo design ke liye, 3 din mein due"

Currency words map to ISO codes (dollar->USD, rupees/rs->PKR,
dirham->AED, ...); default USD. Default due_in_days = 7.

Dependencies: only `requests` (pip install requests) — and only used when
OPENAI_API_KEY is set. The regex path is fully offline.

Self-test:  python invoice_ai.py
"""

import json
import os
import re

import requests

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

CURRENCY_WORDS = {
    "dollar": "USD", "dollars": "USD", "usd": "USD", "bucks": "USD",
    "rupee": "PKR", "rupees": "PKR", "rs": "PKR", "pkr": "PKR",
    "dirham": "AED", "dirhams": "AED", "aed": "AED", "dhs": "AED",
    "euro": "EUR", "euros": "EUR", "eur": "EUR",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP",
}
CURRENCY_SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP"}  # $, euro, pound signs

DEFAULT_CURRENCY = "USD"
DEFAULT_DUE_DAYS = 7

# --- regexes (compiled once) ---------------------------------------------
_AMOUNT_RE = re.compile(
    r"(?:"
    r"(?P<sym>[$\u20ac\u00a3])\s*(?P<sym_amt>[\d,]+(?:\.\d{1,2})?)"  # $500 / €50
    r"|(?P<plain_amt>[\d,]+(?:\.\d{1,2})?)\s*"
    r"(?P<curword>dollars?|usd|bucks|rupees?|rs\.?|pkr|dirhams?|aed|dhs"
    r"|euros?|eur|pounds?|gbp)"                                    # 200 dollar
    r")",
    re.IGNORECASE,
)
_CUSTOMER_EN_RE = re.compile(r"\bsend\s+([A-Z][\w.\-']+)", re.IGNORECASE)
_CUSTOMER_EN2_RE = re.compile(r"\binvoice\s+(?:to|for)\s+([A-Z][\w.\-']+)",
                              re.IGNORECASE)
_CUSTOMER_UR_RE = re.compile(r"\b([A-Z][\w.\-']+)\s+ko\b")
_ITEM_EN_RE = re.compile(r"\bfor\s+(.+?)(?:,|\s+due\b|[.?!]\s*|$)",
                         re.IGNORECASE)
_ITEM_UR_RE = re.compile(r"(?:bhej\w*|send)\s+(.+?)\s+k[ae]\s+liy[eai]\b",
                         re.IGNORECASE)
_ITEM_UR2_RE = re.compile(r"((?:\w+\s+){1,4})k[ae]\s+liy[eai]\b", re.IGNORECASE)
_DUE_EN_RE = re.compile(r"\bdue\s+in\s+(\d+)\s*days?", re.IGNORECASE)
_DUE_UR_RE = re.compile(r"(\d+)\s*din(?:o|on)?\s*(?:mein|main)?\s*due\b",
                        re.IGNORECASE)
_VERB_NOISE_RE = re.compile(r"^(bhej\w*|send|invoice|ka|ki|ko)\s+",
                            re.IGNORECASE)


def _clean_num(text):
    return float(text.replace(",", ""))


def _regex_extract(message):
    """Offline rule-based extraction. Returns the details dict."""
    details = {
        "customer": None,
        "amount": None,
        "currency": DEFAULT_CURRENCY,
        "item": "Services",
        "due_in_days": DEFAULT_DUE_DAYS,
    }

    # Amount + currency.
    m = _AMOUNT_RE.search(message)
    if m:
        if m.group("sym_amt"):
            details["amount"] = _clean_num(m.group("sym_amt"))
            details["currency"] = CURRENCY_SYMBOLS.get(m.group("sym"), "USD")
        else:
            details["amount"] = _clean_num(m.group("plain_amt"))
            details["currency"] = CURRENCY_WORDS.get(
                m.group("curword").lower().rstrip("."), DEFAULT_CURRENCY)

    # Customer name.
    for rx in (_CUSTOMER_EN_RE, _CUSTOMER_UR_RE, _CUSTOMER_EN2_RE):
        cm = rx.search(message)
        if cm:
            details["customer"] = cm.group(1)
            break

    # Item description.
    im = _ITEM_EN_RE.search(message)
    if im and im.group(1).strip():
        details["item"] = im.group(1).strip()
    else:
        iu = _ITEM_UR_RE.search(message) or _ITEM_UR2_RE.search(message)
        if iu:
            item = iu.group(1).strip()
            # strip leftover verbs ("bhejo", "ka", ...) from the front
            while True:
                cleaned = _VERB_NOISE_RE.sub("", item).strip()
                if cleaned == item:
                    break
                item = cleaned
            if item:
                details["item"] = item

    # Due date.
    dm = _DUE_EN_RE.search(message) or _DUE_UR_RE.search(message)
    if dm:
        details["due_in_days"] = int(dm.group(1))

    if details["customer"] is None:
        details["customer"] = "Customer"
    if details["amount"] is None:
        raise ValueError(f"Could not find an amount in: {message!r}")
    return details


def _openai_extract(message):
    """Extract via OpenAI chat API. Returns details dict or raises."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    system = (
        "You extract invoice details from a user message. Reply with JSON "
        "ONLY — no markdown, no explanation. Keys: "
        '{"customer": "<person name>", "amount": <number>, '
        '"currency": "<3-letter ISO code>", "item": "<short description>", '
        '"due_in_days": <integer>}. '
        "Defaults: currency USD, due_in_days 7. "
        "Currency words map like: dollar->USD, rupees/rs->PKR, dirham->AED."
    )
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": OPENAI_MODEL,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):  # tolerate fenced JSON
        text = re.sub(r"^```\w*\n?|```$", "", text).strip()
    data = json.loads(text)
    return {
        "customer": str(data.get("customer") or "Customer"),
        "amount": float(data["amount"]),
        "currency": str(data.get("currency") or DEFAULT_CURRENCY).upper(),
        "item": str(data.get("item") or "Services"),
        "due_in_days": int(data.get("due_in_days") or DEFAULT_DUE_DAYS),
    }


def extract_invoice_details(message):
    """Extract invoice details from a natural-language message.

    Tries the OpenAI path first when OPENAI_API_KEY is set; falls back to
    the offline regex parser on any failure (missing key, network error,
    unparsable reply). Always returns the details dict or raises
    ValueError if no amount can be found.
    """
    if os.environ.get("OPENAI_API_KEY"):
        try:
            return _openai_extract(message)
        except Exception as exc:
            print(f"[invoice_ai] OpenAI path failed ({exc}); "
                  "using regex fallback.")
    return _regex_extract(message)


if __name__ == "__main__":
    examples = [
        # English
        "Send Ahmed a $500 invoice for website work, due in 7 days",
        # Roman Urdu
        "Bilal ko 200 dollar ka invoice bhejo design ke liye, 3 din mein due",
        # Mixed
        "Send Fatima 1500 rupees ka invoice for logo design, due in 5 days",
    ]
    path = "openai" if os.environ.get("OPENAI_API_KEY") else "regex"
    print(f"invoice_ai self-test (engine path: {path})")
    print("=" * 60)
    for msg in examples:
        print(f"in : {msg}")
        try:
            print("out:", json.dumps(extract_invoice_details(msg),
                                    ensure_ascii=False))
        except Exception as exc:
            print(f"out: [FAIL] {exc}")
        print("-" * 60)

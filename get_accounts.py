# -*- coding: utf-8 -*-
"""Standalone helper: list DNSE sub-accounts using the project's shared signing.

Run directly (`python get_accounts.py`) to print account/sub-account info, or
import `fetch_accounts()` from the UI to populate the account picker.
HMAC signing is reused from core.dnse_signature; .env loading from core.env_utils.
"""

import json
import http.client

from core.dnse_signature import generate_signature_header
from core.env_utils import apply_env, get_env_value

BASE_URL = "openapi.dnse.com.vn"
PATH = "/accounts"
METHOD = "GET"


def fetch_accounts(api_key=None, api_secret=None, version=None):
    """Call GET /accounts and return (status_code, parsed_json_or_text)."""
    apply_env()
    api_key = api_key or get_env_value("DNSE_API_KEY")
    api_secret = api_secret or get_env_value("DNSE_API_SECRET")
    version = version or get_env_value("DNSE_API_VERSION", "2026-05-07")

    if not api_key or not api_secret:
        raise RuntimeError("Thiếu DNSE_API_KEY hoặc DNSE_API_SECRET (đặt trong .env hoặc truyền vào).")

    x_signature, date_value = generate_signature_header(api_key, api_secret, METHOD, PATH)
    headers = {
        "Accept": "application/json",
        "X-API-Key": api_key,
        "X-Signature": x_signature,
        "X-Aux-Date": date_value,
        "version": version,
    }

    conn = http.client.HTTPSConnection(BASE_URL, timeout=15)
    conn.request(METHOD, PATH, body="", headers=headers)
    res = conn.getresponse()
    raw = res.read().decode("utf-8")
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = raw
    return res.status, parsed


def main():
    status, data = fetch_accounts()
    print("HTTP:", status)
    print(data)

    if status == 200 and isinstance(data, dict):
        print("\n=== DNSE ACCOUNT INFO ===")
        print("Name:", data.get("name"))
        print("Custody Code:", data.get("custodyCode"))
        print("Investor ID:", data.get("investorId"))

        print("\n=== TRADING ACCOUNTS ===")
        for acc in data.get("accounts", []):
            print("Account ID:", acc.get("id"))
            print("Deal Account:", acc.get("dealAccount"))
            print("Derivative Account:", acc.get("derivativeAccount"))
            print("Derivative Status:", acc.get("derivative", {}).get("status"))
            print("---")


if __name__ == "__main__":
    main()

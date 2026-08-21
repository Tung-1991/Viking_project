from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
from urllib.parse import quote
import uuid


def generate_nonce() -> str:
    return uuid.uuid4().hex


def generate_date() -> str:
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def generate_signature_header(
    api_key: str,
    api_secret: str,
    method: str,
    path: str,
    *,
    date_value: str | None = None,
    nonce: str | None = None,
) -> tuple[str, str]:
    date_value = date_value or generate_date()
    nonce = nonce or generate_nonce()
    # DNSE signs the custom X-Aux-Date header.  Using ``date`` here produces a
    # syntactically valid HMAC, but DNSE rejects the Authorization fields.
    signing = (
        f"(request-target): {method.lower()} {path}\n"
        f"x-aux-date: {date_value}\n"
        f"nonce: {nonce}"
    )
    digest = hmac.new(api_secret.encode("utf-8"), signing.encode("utf-8"), hashlib.sha256).digest()
    encoded = quote(base64.b64encode(digest).decode("ascii"), safe="")
    signature = (
        f'Signature keyId="{api_key}",algorithm="hmac-sha256",'
        f'headers="(request-target) x-aux-date",signature="{encoded}",nonce="{nonce}"'
    )
    return signature, date_value

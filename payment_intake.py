# -*- coding: utf-8 -*-
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass


class SepaySignatureError(ValueError):
    """Raised when a SePay webhook signature cannot be trusted."""


class SepayPayloadError(ValueError):
    """Raised when a SePay webhook payload cannot be parsed."""


@dataclass(frozen=True)
class SepayPayment:
    data: dict
    raw_body_text: str
    amount: int
    content: str
    payment_code: str
    transaction_id: str
    is_incoming: bool


def parse_sepay_webhook_payload(raw_body: bytes) -> SepayPayment:
    try:
        raw_body_text = raw_body.decode("utf-8")
        data = json.loads(raw_body_text)
    except UnicodeDecodeError as error:
        raise SepayPayloadError("Invalid UTF-8 payload") from error
    except json.JSONDecodeError as error:
        raise SepayPayloadError("Invalid JSON payload") from error

    amount = extract_payment_amount(data)
    content = extract_payment_content(data)
    payment_code = extract_payment_code(data)
    return SepayPayment(
        data=data,
        raw_body_text=raw_body.decode("utf-8", errors="replace"),
        amount=amount,
        content=content,
        payment_code=payment_code,
        transaction_id=make_sepay_transaction_id(data, amount, content),
        is_incoming=is_incoming_payment(data),
    )


def verify_sepay_hmac(
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: str,
    *,
    now: float | None = None,
) -> None:
    if not secret:
        return

    signature = headers.get("x-sepay-signature", "")
    timestamp_raw = headers.get("x-sepay-timestamp", "0")

    try:
        timestamp = int(timestamp_raw)
    except ValueError as error:
        raise SepaySignatureError("Invalid SePay timestamp") from error

    current_time = time.time() if now is None else now
    if abs(current_time - timestamp) > 300:
        raise SepaySignatureError("SePay request expired")

    signed_payload = str(timestamp).encode("utf-8") + b"." + raw_body
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        raise SepaySignatureError("Invalid SePay signature")


def flatten_sepay_payload(data: dict) -> dict:
    flat = dict(data)
    if isinstance(data.get("transaction"), dict):
        flat.update(data["transaction"])
    return flat


def extract_payment_amount(data: dict) -> int:
    flat = flatten_sepay_payload(data)

    for key in ["transferAmount", "amount", "money", "transfer_amount", "creditAmount"]:
        value = flat.get(key)
        if value not in (None, ""):
            try:
                return int(float(str(value).replace(",", "").replace(" ", "")))
            except ValueError:
                pass

    return 0


def extract_payment_content(data: dict) -> str:
    flat = flatten_sepay_payload(data)
    keys = [
        "content",
        "transfer_content",
        "description",
        "transaction_content",
        "gateway_content",
        "addInfo",
        "payment_code",
        "code",
    ]
    return " ".join(str(flat[key]) for key in keys if flat.get(key))


def extract_payment_code(data: dict) -> str:
    content = extract_payment_content(data).upper()
    match = re.search(r"\bMUA\s+[A-Z0-9]{6,20}\b", content)
    if not match:
        return ""
    return " ".join(match.group(0).split())


def is_incoming_payment(data: dict) -> bool:
    transfer_type = str(data.get("transferType") or data.get("transfer_type") or "").lower()
    if not transfer_type:
        return True
    return transfer_type in {"in", "credit", "deposit", "incoming"}


def make_sepay_transaction_id(data: dict, amount: int, content: str) -> str:
    explicit_id = data.get("id") or data.get("sepay_id")
    if explicit_id not in (None, ""):
        return str(explicit_id)

    reference = data.get("referenceCode") or data.get("reference_code") or data.get("ref")
    if reference not in (None, ""):
        transfer_type = data.get("transferType") or data.get("transfer_type") or ""
        return f"ref:{reference}:{transfer_type}:{amount}"

    transaction_date = data.get("transactionDate") or data.get("transaction_date") or ""
    account_number = data.get("accountNumber") or data.get("account_number") or ""
    if content or transaction_date or account_number:
        payload = "|".join([str(transaction_date), str(account_number), str(amount), str(content)])
        return "hash:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return ""

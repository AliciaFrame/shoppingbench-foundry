"""Server-opted-in, signed evaluation capture; signing keys never enter requests."""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from functools import lru_cache
from urllib.parse import urlparse

_USED_NONCES = {}
_LOCK = threading.Lock()
MAX_AGE_SECONDS = 300


def redact_trace(value):
    if isinstance(value, dict):
        return {
            key: "<redacted>"
            if key.lower().replace("-", "_")
            in {
                "authorization",
                "headers",
                "api_key",
                "apikey",
                "access_token",
                "refresh_token",
                "client_secret",
                "password",
                "secret",
                "token",
            }
            else redact_trace(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_trace(item) for item in value]
    return value


def validate_secret_uri(uri):
    parsed = urlparse(uri)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or len(parts) != 3
        or parts[0] != "secrets"
    ):
        raise ValueError("Evaluation signing key requires a versioned HTTPS Key Vault secret URI")
    return f"https://{parsed.netloc}", parts[1], parts[2]


@lru_cache(maxsize=4)
def _vault_key(uri):
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    vault_url, name, version = validate_secret_uri(uri)
    with (
        DefaultAzureCredential() as credential,
        SecretClient(
            vault_url=vault_url,
            credential=credential,
        ) as client,
    ):
        return client.get_secret(name, version).value


def evaluation_key():
    uri = os.getenv("HILLCLIMB_EVALUATION_KEY_SECRET_URI")
    key = _vault_key(uri) if uri else os.getenv("HILLCLIMB_EVALUATION_KEY")
    if not key or len(key) < 32:
        raise RuntimeError("Evaluation capture requires a signing key of at least 32 characters")
    return key


def _signature(text, timestamp, nonce, key):
    payload = json.dumps(
        {"input": text, "timestamp": timestamp, "nonce": nonce},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def evaluation_metadata(text):
    timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
    return {
        "hillclimb_capture": "v1",
        "hillclimb_timestamp": timestamp,
        "hillclimb_nonce": nonce,
        "hillclimb_signature": _signature(text, timestamp, nonce, evaluation_key()),
    }


def authorize_capture(text, metadata):
    if metadata.get("hillclimb_capture") != "v1":
        return False
    if os.getenv("HILLCLIMB_ENABLE_EVALUATION") != "1":
        raise PermissionError("Evaluation capture is disabled on this deployment")
    timestamp = metadata.get("hillclimb_timestamp", "")
    nonce = metadata.get("hillclimb_nonce", "")
    signature = metadata.get("hillclimb_signature", "")
    if (
        not isinstance(timestamp, str)
        or not 1 <= len(timestamp) <= 12
        or not timestamp.isascii()
        or not timestamp.isdecimal()
        or not isinstance(nonce, str)
        or len(nonce) != 32
        or any(character not in "0123456789abcdef" for character in nonce)
        or not isinstance(signature, str)
        or len(signature) != 64
        or any(character not in "0123456789abcdef" for character in signature)
    ):
        raise PermissionError("Invalid evaluation authorization")
    now = int(time.time())
    if not -30 <= now - int(timestamp) <= MAX_AGE_SECONDS:
        raise PermissionError("Expired evaluation authorization")
    expected = _signature(text, timestamp, nonce, evaluation_key())
    if not hmac.compare_digest(signature, expected):
        raise PermissionError("Invalid evaluation authorization")
    with _LOCK:
        for used, expires in list(_USED_NONCES.items()):
            if expires < now:
                del _USED_NONCES[used]
        if nonce in _USED_NONCES or len(_USED_NONCES) >= 10000:
            raise PermissionError(
                "Replayed evaluation authorization or exhausted authorization cache"
            )
        _USED_NONCES[nonce] = int(timestamp) + MAX_AGE_SECONDS
    return True

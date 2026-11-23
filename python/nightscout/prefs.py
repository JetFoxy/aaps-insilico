#!/usr/bin/env python3
"""Decrypt AAPS settings exports; passwords and plaintext never reach error text."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_ENCRYPTED_FORMAT = "aaps_encrypted"
_PBKDF2_ITERATIONS = 50_000
_KEY_LENGTH = 32
_DECRYPT_ERROR = "cannot decrypt export: wrong password or damaged file"


class PrefsDecryptError(ValueError):
    """Raised when an AAPS settings export cannot be parsed or decrypted."""


def _parse_json(text: str | bytes) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise PrefsDecryptError("export is not valid JSON") from None


def _decode_envelope(security: dict, content: str) -> tuple[bytes, bytes, bytes]:
    salt_hex = security.get("salt")
    if not isinstance(salt_hex, str):
        raise PrefsDecryptError("export is missing security metadata")
    try:
        salt = bytes.fromhex(salt_hex)
        envelope = base64.b64decode(content, validate=True)
    except (ValueError, binascii.Error):
        raise PrefsDecryptError(_DECRYPT_ERROR) from None
    if len(envelope) < 2:
        raise PrefsDecryptError(_DECRYPT_ERROR)
    iv_length = envelope[0]
    iv = envelope[1:1 + iv_length]
    sealed = envelope[1 + iv_length:]
    if not iv or not sealed:
        raise PrefsDecryptError(_DECRYPT_ERROR)
    return salt, iv, sealed


def _decrypt(payload: dict, password: str | None) -> dict[str, object]:
    security = payload.get("security")
    content = payload.get("content")
    if not isinstance(security, dict) or not isinstance(content, str):
        raise PrefsDecryptError("export is missing security metadata")
    if password is None:
        raise PrefsDecryptError("password required for this export")
    salt, iv, sealed = _decode_envelope(security, content)
    key = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=_KEY_LENGTH,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    ).derive(password.encode())
    try:
        raw = AESGCM(key).decrypt(iv, sealed, None)
    except InvalidTag:
        raise PrefsDecryptError(_DECRYPT_ERROR) from None
    if hashlib.sha256(raw).hexdigest() != security.get("content_hash"):
        raise PrefsDecryptError("export content hash mismatch")
    prefs = _parse_json(raw)
    if not isinstance(prefs, dict):
        raise PrefsDecryptError("export content is not a settings object")
    return prefs


def decode_export(text: str, password: str | None) -> dict[str, object]:
    """Return the settings object of an AAPS export, decrypting it when needed."""
    payload = _parse_json(text)
    if not isinstance(payload, dict):
        raise PrefsDecryptError("export is not a settings object")
    has_envelope = "security" in payload or "content" in payload
    if payload.get("format") != _ENCRYPTED_FORMAT:
        if has_envelope:
            raise PrefsDecryptError("unsupported export format")
        return payload
    return _decrypt(payload, password)

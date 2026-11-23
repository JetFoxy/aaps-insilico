import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from nightscout.prefs import PrefsDecryptError, decode_export


def encode(prefs: dict, password: str) -> str:
    raw = json.dumps(prefs, sort_keys=True)
    salt = os.urandom(32)
    key = PBKDF2HMAC(algorithm=hashes.SHA1(), length=32, salt=salt, iterations=50000).derive(password.encode())
    iv = os.urandom(12)
    sealed = AESGCM(key).encrypt(iv, raw.encode(), None)
    content = base64.b64encode(bytes([len(iv)]) + iv + sealed).decode()
    return json.dumps({
        "metadata": {}, "format": "aaps_encrypted",
        "security": {"salt": salt.hex(), "algorithm": "v1",
                     "content_hash": hashlib.sha256(raw.encode()).hexdigest()},
        "content": content,
    })


def test_round_trip():
    text = encode({"a": "1", "insulin_configuration": "{}"}, "pw")
    assert decode_export(text, "pw") == {"a": "1", "insulin_configuration": "{}"}


def test_wrong_password_does_not_echo_it():
    text = encode({"a": "1"}, "pw")
    with pytest.raises(PrefsDecryptError) as exc:
        decode_export(text, "nope-secret")
    assert "nope-secret" not in str(exc.value)


def test_missing_password_is_reported():
    text = encode({"a": "1"}, "pw")
    with pytest.raises(PrefsDecryptError, match="password"):
        decode_export(text, None)


def test_plain_prefs_are_returned_as_is():
    assert decode_export('{"a": "1"}', None) == {"a": "1"}

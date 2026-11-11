#!/usr/bin/env python3
"""Decrypt an AAPS settings export (`"format": "aaps_encrypted"`).

Scheme, taken from implementation/.../maintenance/formats/PrefsFormatCodec.kt:
PBKDF2-HMAC-SHA1 over the hex salt, 50000 rounds, 256-bit key, AES-GCM with a
128-bit tag. `content` is base64 of [1 byte iv length][iv][ciphertext || tag].
The plaintext is JSON and its SHA-256 is stored as `security.content_hash`.

    tools/decrypt_prefs.py ~/Downloads/2026-11-10_125705_full.json -o data/raw/settings.json

The password is read with getpass: it is never passed in argv, written to disk,
or echoed. Requires `cryptography` (already present in this environment).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PBKDF2_ROUNDS = 50_000
AES_KEY_BITS = 256
TAG_BITS = 128


def decrypt(export: dict, password: str) -> tuple[str, bool]:
    security = export.get("security") or {}
    if security.get("algorithm") != "v1":
        raise SystemExit(f"unsupported algorithm: {security.get('algorithm')!r}")
    salt = bytes.fromhex(security["salt"])
    envelope = base64.b64decode(export["content"])
    iv_len = envelope[0]
    if iv_len <= 0 or len(envelope) < 1 + iv_len:
        raise SystemExit("malformed envelope")
    iv = envelope[1:1 + iv_len]
    ciphertext = envelope[1 + iv_len:]
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), salt, PBKDF2_ROUNDS, AES_KEY_BITS // 8)
    plain = AESGCM(key).decrypt(iv, ciphertext, None)
    text = plain.decode("utf-8")
    expected = (security.get("content_hash") or "").lower()
    actual = hashlib.sha256(plain).hexdigest()
    return text, (not expected or expected == actual)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="AAPS settings export .json")
    ap.add_argument("-o", "--out", default="data/raw/settings.json")
    ap.add_argument("--password-stdin", action="store_true", help="read the password from stdin instead of a prompt")
    args = ap.parse_args()

    export = json.load(open(args.export))
    if export.get("format") != "aaps_encrypted":
        raise SystemExit(f"not an encrypted export: format={export.get('format')!r}")
    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass("Export password: ")

    text, hash_ok = decrypt(export, password)
    if not hash_ok:
        print("WARNING: content_hash does not match, the file may be truncated", file=sys.stderr)
    data = json.loads(text)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    meta = export.get("metadata") or {}
    print(f"decrypted to {args.out}")
    print(f"  aaps {meta.get('aaps_version')} {meta.get('aaps_flavour')}, written {meta.get('created_at')}")
    print(f"  {len(data)} preference groups" if isinstance(data, dict) else f"  {len(data)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

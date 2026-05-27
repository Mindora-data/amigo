from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

PASSWORD_PREFIX = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("empty_password")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return ":".join(
        [
            PASSWORD_PREFIX,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        prefix, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded.split(":", 5)
        if prefix != PASSWORD_PREFIX:
            return False
        salt = base64.b64decode(raw_salt.encode("ascii"))
        expected = base64.b64decode(raw_digest.encode("ascii"))
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(raw_n),
            r=int(raw_r),
            p=int(raw_p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def token_hash(token: str) -> str:
    pepper = os.environ.get("NINO_SESSION_PEPPER", "local-dev-session-pepper").encode("utf-8")
    return hmac.new(pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()

"""Password hashing (PBKDF2-HMAC-SHA256, stdlib-only) and opaque token helpers.

Stdlib PBKDF2 is used instead of passlib/argon2 to avoid extra native-build
dependencies on the Ubuntu host; iteration count follows current OWASP
guidance for PBKDF2-SHA256.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations_str, salt, hash_hex = encoded.split("$", 3)
        if algo != _ALGO:
            return False
        iterations = int(iterations_str)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(digest.hex(), hash_hex)


def generate_token() -> str:
    """A high-entropy opaque secret, e.g. for device tokens or session ids."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are already high-entropy random, so a plain fast hash is fine for lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

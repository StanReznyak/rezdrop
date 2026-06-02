from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from typing import Mapping

from fastapi import HTTPException, Request, status

_PASSWORD_SCHEME = "scrypt"
_USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,40}$")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=64)
    return f"{_PASSWORD_SCHEME}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    try:
        algo, salt_b64, digest_b64 = stored_hash.split("$", 2)
        if algo != _PASSWORD_SCHEME:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=64)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def validate_username(username: str) -> bool:
    return bool(_USERNAME_RE.fullmatch(username or ""))


def is_strong_enough_password(password: str) -> bool:
    """Simple password check for local accounts."""
    if len(password or "") < 8:
        return False
    if password.lower() in {"password", "password123", "qwerty123", "12345678", "11111111"}:
        return False
    return True


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return str(token)


def validate_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not hmac.compare_digest(str(expected), str(token)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token invalid or missing")


def verify_admin_password(candidate: str, *, plain_password: str, password_hash: str | None = None) -> bool:
    if password_hash:
        return verify_password(candidate, password_hash)
    return hmac.compare_digest(candidate, plain_password)


def build_security_headers(*, https_enabled: bool = False) -> Mapping[str, str]:
    headers: dict[str, str] = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "form-action 'self'"
        ),
    }
    if https_enabled:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers

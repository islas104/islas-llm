import os
import hashlib
import secrets
from starlette.requests import HTTPConnection


_sessions: set[str] = set()


def hash_password(password: str, salt: str) -> str:
    return hashlib.scrypt(
        password.encode(), salt=salt.encode(), n=2**14, r=8, p=1
    ).hex()


def verify_password(password: str) -> bool:
    stored = os.getenv("PASSWORD_HASH", "")
    if not stored:
        return True
    try:
        _, salt, expected = stored.split("$", 2)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), expected)


def create_session() -> str:
    token = secrets.token_hex(32)
    _sessions.add(token)
    return token


def revoke_session(token: str) -> None:
    _sessions.discard(token)


def is_authenticated(conn: HTTPConnection) -> bool:
    if not os.getenv("PASSWORD_HASH"):
        return True
    token = conn.cookies.get("forge_session", "")
    return bool(token and token in _sessions)

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from api import db
from api.auth import verify_password, create_session, revoke_session

router = APIRouter()

# Sliding-window rate limiter: max 5 failed attempts per 60 s per IP
_failed: dict[str, deque] = defaultdict(deque)
_MAX_FAILURES = 5
_WINDOW = 60  # seconds


def _is_blocked(ip: str) -> bool:
    now = time.time()
    q = _failed[ip]
    while q and now - q[0] > _WINDOW:
        q.popleft()
    return len(q) >= _MAX_FAILURES


def _record_failure(ip: str) -> None:
    _failed[ip].append(time.time())


@router.post("/login")
async def login(request: Request, password: str = Form(...)):
    ip = request.client.host
    if _is_blocked(ip):
        return JSONResponse(
            {"error": "Too many failed attempts — try again in a minute"},
            status_code=429,
        )
    if not verify_password(password):
        _record_failure(ip)
        return JSONResponse({"error": "Invalid password"}, status_code=401)

    _failed.pop(ip, None)  # clear on success
    token = create_session()
    await db.save_session(token)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "forge_session", token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get("forge_session", "")
    revoke_session(token)
    await db.delete_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("forge_session")
    return resp

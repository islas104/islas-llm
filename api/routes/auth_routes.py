from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from api.auth import verify_password, create_session, revoke_session

router = APIRouter()


@router.post("/login")
async def login(password: str = Form(...)):
    if not verify_password(password):
        return JSONResponse({"error": "Invalid password"}, status_code=401)
    token = create_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "forge_session", token,
        httponly=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 7,
    )
    return resp


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get("forge_session", "")
    revoke_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("forge_session")
    return resp

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api import db
from api.auth import get_session_token

router = APIRouter()
_NOT_FOUND = "Not found"


class CreateBody(BaseModel):
    title: str = "New Chat"
    system_prompt: str = ""


class UpdateBody(BaseModel):
    title: str | None = None
    system_prompt: str | None = None


@router.get("")
async def list_convs(request: Request):
    return await db.list_conversations(get_session_token(request))


@router.post("")
async def create_conv(request: Request, body: CreateBody):
    return await db.create_conversation(get_session_token(request), body.title, body.system_prompt)


@router.get("/{cid}")
async def get_conv(cid: str, request: Request):
    conv = await db.get_conversation(cid, get_session_token(request))
    if not conv:
        raise HTTPException(404, _NOT_FOUND)
    messages = await db.get_messages(cid)
    return {**conv, "messages": messages}


@router.patch("/{cid}")
async def update_conv(cid: str, request: Request, body: UpdateBody):
    token = get_session_token(request)
    if not await db.get_conversation(cid, token):
        raise HTTPException(404, _NOT_FOUND)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        await db.update_conversation(cid, **updates)
    return await db.get_conversation(cid)


@router.delete("/{cid}")
async def delete_conv(cid: str, request: Request):
    await db.delete_conversation(cid, get_session_token(request))
    return {"ok": True}


@router.delete("/{cid}/messages/{mid}/from")
async def truncate_from(cid: str, mid: str, request: Request):
    token = get_session_token(request)
    if not await db.get_conversation(cid, token):
        raise HTTPException(404, _NOT_FOUND)
    await db.truncate_from_message(cid, mid)
    return {"ok": True}

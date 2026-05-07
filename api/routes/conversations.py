from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api import db

router = APIRouter()


class CreateBody(BaseModel):
    title: str = "New Chat"
    system_prompt: str = ""


class UpdateBody(BaseModel):
    title: str | None = None
    system_prompt: str | None = None


@router.get("")
async def list_convs():
    return await db.list_conversations()


@router.post("")
async def create_conv(body: CreateBody):
    return await db.create_conversation(body.title, body.system_prompt)


@router.get("/{cid}")
async def get_conv(cid: str):
    conv = await db.get_conversation(cid)
    if not conv:
        raise HTTPException(404, "Not found")
    messages = await db.get_messages(cid)
    return {**conv, "messages": messages}


@router.patch("/{cid}")
async def update_conv(cid: str, body: UpdateBody):
    if not await db.get_conversation(cid):
        raise HTTPException(404, "Not found")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if updates:
        await db.update_conversation(cid, **updates)
    return await db.get_conversation(cid)


@router.delete("/{cid}")
async def delete_conv(cid: str):
    await db.delete_conversation(cid)
    return {"ok": True}


@router.delete("/{cid}/messages/{mid}/from")
async def truncate_from(cid: str, mid: str):
    await db.truncate_from_message(cid, mid)
    return {"ok": True}

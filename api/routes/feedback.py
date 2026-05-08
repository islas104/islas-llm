from fastapi import APIRouter
from pydantic import BaseModel

from api import db

router = APIRouter()


class FeedbackBody(BaseModel):
    message: str


@router.post("")
async def submit_feedback(body: FeedbackBody):
    msg = body.message.strip()[:2000]
    if not msg:
        return {"ok": False}
    await db.save_feedback(msg)
    return {"ok": True}

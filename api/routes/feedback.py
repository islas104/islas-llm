import asyncio
import logging
import os
import smtplib
import time
from collections import defaultdict, deque
from email.message import EmailMessage

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api import db

router = APIRouter()
logger = logging.getLogger(__name__)

_fb_times: dict[str, deque] = defaultdict(deque)
_FB_LIMIT = 5
_FB_WINDOW = 300  # 5 per 5 minutes per IP


def _feedback_rate_limited(ip: str) -> bool:
    now = time.time()
    q = _fb_times[ip]
    while q and now - q[0] > _FB_WINDOW:
        q.popleft()
    if len(q) >= _FB_LIMIT:
        return True
    q.append(now)
    return False


def _send_email(message: str) -> None:
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    to_addr = os.getenv("FEEDBACK_TO", "islas104@gmail.com")
    if not smtp_user or not smtp_pass:
        return
    msg = EmailMessage()
    msg["Subject"] = "Islas AI — Issue Report"
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.set_content(message)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(smtp_user, smtp_pass)
        smtp.send_message(msg)


class FeedbackBody(BaseModel):
    message: str


@router.post("")
async def submit_feedback(request: Request, body: FeedbackBody):
    if _feedback_rate_limited(request.client.host):
        return JSONResponse({"error": "Too many reports"}, status_code=429)
    msg = body.message.strip()[:2000]
    if not msg:
        return {"ok": False}
    await db.save_feedback(msg)
    try:
        await asyncio.to_thread(_send_email, msg)
    except Exception:
        logger.warning("Failed to send feedback email", exc_info=True)
    return {"ok": True}

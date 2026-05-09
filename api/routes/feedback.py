import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api import db

router = APIRouter()

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
    return {"ok": True}

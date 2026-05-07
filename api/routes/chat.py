import os
import asyncio
import time
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator, model_validator

from model.loader import get_model_and_tokenizer

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_MESSAGES = 50
MAX_CONTENT_LEN = 8000
MAX_NEW_TOKENS_LIMIT = 2048

RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
_rate_store: dict[str, list[float]] = defaultdict(list)

_executor = ThreadPoolExecutor(max_workers=1)
_generation_lock = asyncio.Lock()

DEFAULT_MAX_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "512"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))


def _check_rate(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_store[ip] = [t for t in _rate_store[ip] if t > cutoff]
    if len(_rate_store[ip]) >= RATE_LIMIT_REQUESTS:
        return False
    _rate_store[ip].append(now)
    return True


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("content cannot be empty")
        if len(v) > MAX_CONTENT_LEN:
            raise ValueError(f"content exceeds {MAX_CONTENT_LEN} characters")
        return v


class ChatRequest(BaseModel):
    messages: list[Message]
    max_new_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE

    @model_validator(mode="after")
    def validate_request(self):
        if not self.messages:
            raise ValueError("messages cannot be empty")
        if len(self.messages) > MAX_MESSAGES:
            raise ValueError(f"too many messages (max {MAX_MESSAGES})")
        if not (0.0 <= self.temperature <= 2.0):
            raise ValueError("temperature must be 0–2")
        if not (1 <= self.max_new_tokens <= MAX_NEW_TOKENS_LIMIT):
            raise ValueError(f"max_new_tokens must be 1–{MAX_NEW_TOKENS_LIMIT}")
        return self


@router.post("/chat")
async def chat(request: ChatRequest, http_request: Request):
    client_ip = http_request.client.host if http_request.client else "unknown"

    if not _check_rate(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded — slow down")

    if _generation_lock.locked():
        raise HTTPException(status_code=503, detail="Model busy — try again shortly")

    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = get_model_and_tokenizer()
    conversation = [{"role": m.role, "content": m.content} for m in request.messages]
    prompt = tokenizer.apply_chat_template(
        conversation, tokenize=False, add_generation_prompt=True
    )
    sampler = make_sampler(temp=request.temperature)

    async def token_stream():
        async with _generation_lock:
            loop = asyncio.get_event_loop()
            gen = stream_generate(
                model, tokenizer,
                prompt=prompt,
                max_tokens=request.max_new_tokens,
                sampler=sampler,
            )
            try:
                while True:
                    resp = await loop.run_in_executor(_executor, lambda: next(gen, None))
                    if resp is None:
                        break
                    yield resp.text
            except Exception:
                logger.exception("Generation error for %s", client_ip)

    return StreamingResponse(token_stream(), media_type="text/plain")


@router.get("/health")
async def health():
    try:
        get_model_and_tokenizer()
        return {"status": "ok", "model_ready": True}
    except RuntimeError:
        return {"status": "loading", "model_ready": False}

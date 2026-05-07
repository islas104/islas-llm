import os
import asyncio
import torch
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from transformers import TextIteratorStreamer
from threading import Thread
from model.loader import get_model_and_tokenizer

router = APIRouter()


class Message(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    max_new_tokens: int = int(os.getenv("MAX_NEW_TOKENS", 512))
    temperature: float = float(os.getenv("TEMPERATURE", 0.7))


@router.post("/chat")
async def chat(request: ChatRequest):
    model, tokenizer = get_model_and_tokenizer()

    conversation = [{"role": m.role, "content": m.content} for m in request.messages]

    prompt = tokenizer.apply_chat_template(
        conversation, tokenize=False, add_generation_prompt=True
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    streamer = TextIteratorStreamer(
        tokenizer, skip_prompt=True, skip_special_tokens=True
    )

    generation_kwargs = dict(
        **inputs,
        streamer=streamer,
        max_new_tokens=request.max_new_tokens,
        temperature=request.temperature,
        do_sample=request.temperature > 0,
    )

    thread = Thread(target=model.generate, kwargs=generation_kwargs)
    thread.start()

    async def token_stream():
        loop = asyncio.get_event_loop()
        for token in streamer:
            yield token
            await asyncio.sleep(0)

    return StreamingResponse(token_stream(), media_type="text/plain")


@router.get("/health")
async def health():
    return {"status": "ok"}

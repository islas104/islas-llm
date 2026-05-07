import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api import db
from api.auth import is_authenticated
from model.loader import get_model_and_tokenizer, make_cache

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_CONTENT_LEN = int(os.getenv("MAX_CONTENT_LEN", "8000"))
MAX_MESSAGES = 100
MAX_NEW_TOKENS_LIMIT = 2048
DEFAULT_MAX_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "512"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))

_executor = ThreadPoolExecutor(max_workers=1)
_generation_lock = asyncio.Lock()


def _parse_request(data: dict) -> dict | None:
    content = str(data.get("content", "")).strip()
    if not content or len(content) > MAX_CONTENT_LEN:
        return None
    return {
        "content": content,
        "max_tokens": max(1, min(int(data.get("max_tokens", DEFAULT_MAX_TOKENS)), MAX_NEW_TOKENS_LIMIT)),
        "temperature": max(0.0, min(float(data.get("temperature", DEFAULT_TEMPERATURE)), 2.0)),
        "truncate_from_id": data.get("truncate_from_id"),
    }


def _build_conversation(history: list[dict], system_prompt: str) -> list[dict]:
    conversation = []
    if system_prompt:
        conversation.append({"role": "system", "content": system_prompt})
    conversation.extend({"role": m["role"], "content": m["content"]} for m in history)
    if len(conversation) > MAX_MESSAGES:
        conversation = conversation[:1] + conversation[-(MAX_MESSAGES - 1):]
    return conversation


async def _stream_response(
    websocket: WebSocket,
    prompt: str,
    max_tokens: int,
    temperature: float,
    kv_cache,
    stop_event: asyncio.Event,
    msg_queue: asyncio.Queue,
) -> str:
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = get_model_and_tokenizer()
    sampler = make_sampler(temp=temperature)
    gen_kwargs = {"model": model, "tokenizer": tokenizer,
                  "prompt": prompt, "max_tokens": max_tokens, "sampler": sampler}
    if kv_cache is not None:
        gen_kwargs["prompt_cache"] = kv_cache

    gen = stream_generate(**gen_kwargs)
    loop = asyncio.get_event_loop()
    full_text = ""

    while True:
        if stop_event.is_set():
            break
        try:
            ctrl = msg_queue.get_nowait()
            if ctrl.get("type") == "stop":
                stop_event.set()
                break
        except asyncio.QueueEmpty:
            pass

        resp = await loop.run_in_executor(_executor, lambda: next(gen, None))
        if resp is None:
            break
        full_text += resp.text
        await websocket.send_json({"type": "token", "content": resp.text})

    return full_text


async def _handle_message(
    websocket: WebSocket,
    data: dict,
    conversation_id: str,
    conv: dict,
    kv_cache,
    stop_event: asyncio.Event,
    msg_queue: asyncio.Queue,
) -> tuple[dict, any]:
    req = _parse_request(data)
    if req is None:
        await websocket.send_json({"type": "error", "message": "Invalid content"})
        return conv, kv_cache

    if req["truncate_from_id"]:
        await db.truncate_from_message(conversation_id, req["truncate_from_id"])
        kv_cache = make_cache()

    user_msg = await db.add_message(conversation_id, "user", req["content"])
    await websocket.send_json({"type": "message_saved", "message": user_msg})

    history = await db.get_messages(conversation_id)
    conversation = _build_conversation(history, conv.get("system_prompt", ""))
    prompt = get_model_and_tokenizer()[1].apply_chat_template(
        conversation, tokenize=False, add_generation_prompt=True
    )

    stop_event.clear()
    full_response = ""

    async with _generation_lock:
        try:
            full_response = await _stream_response(
                websocket, prompt, req["max_tokens"], req["temperature"],
                kv_cache, stop_event, msg_queue,
            )
        except Exception:
            logger.exception("Generation error in %s", conversation_id)
            await websocket.send_json({"type": "error", "message": "Generation failed"})
            return conv, kv_cache

    if full_response:
        asst_msg = await db.add_message(conversation_id, "assistant", full_response)

        if conv.get("title") == "New Chat" and len(history) <= 1:
            new_title = req["content"][:50] + ("…" if len(req["content"]) > 50 else "")
            await db.update_conversation(conversation_id, title=new_title)
            conv = {**conv, "title": new_title}
            await websocket.send_json({"type": "title_updated", "title": new_title})

        await websocket.send_json({"type": "done", "message": asst_msg})

    return conv, kv_cache


@router.websocket("/ws/{conversation_id}")
async def ws_chat(websocket: WebSocket, conversation_id: str):
    await websocket.accept()

    if not is_authenticated(websocket):
        await websocket.send_json({"type": "error", "message": "Unauthorised"})
        await websocket.close(code=4401)
        return

    conv = await db.get_conversation(conversation_id)
    if not conv:
        await websocket.send_json({"type": "error", "message": "Conversation not found"})
        await websocket.close()
        return

    kv_cache = make_cache()
    msg_queue: asyncio.Queue = asyncio.Queue()
    stop_event = asyncio.Event()

    async def _receiver():
        try:
            while True:
                await msg_queue.put(await websocket.receive_json())
        except (WebSocketDisconnect, Exception):
            await msg_queue.put({"type": "_disconnect"})

    receiver_task = asyncio.create_task(_receiver())

    try:
        while True:
            data = await msg_queue.get()
            msg_type = data.get("type")

            if msg_type == "_disconnect":
                break
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "stop":
                stop_event.set()
            elif msg_type == "message":
                conv, kv_cache = await _handle_message(
                    websocket, data, conversation_id,
                    conv, kv_cache, stop_event, msg_queue,
                )
    except WebSocketDisconnect:
        logger.info("Client disconnected: %s", conversation_id)
    finally:
        receiver_task.cancel()


@router.get("/health")
async def health():
    try:
        get_model_and_tokenizer()
        return {"status": "ok", "model_ready": True}
    except RuntimeError:
        return {"status": "loading", "model_ready": False}

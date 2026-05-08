import asyncio
import logging
import os
from threading import Thread

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api import db
from api.auth import is_authenticated, get_session_token
from model.loader import get_model_and_tokenizer, make_cache

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_CONTENT_LEN = int(os.getenv("MAX_CONTENT_LEN", "8000"))
MAX_NEW_TOKENS_LIMIT = 2048
DEFAULT_MAX_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "512"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_PROMPT_TOKENS = 3500   # ~500 buffer for response within Mistral's 4096 limit
TOKEN_BATCH = 6            # tokens to accumulate before each WebSocket send
FLUSH_INTERVAL = 0.03      # seconds — flush partial buffer if no token arrives within this window
GENERATION_TIMEOUT = 120   # seconds before a hung generation is cancelled

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


def _build_prompt(history: list[dict], system_prompt: str) -> tuple[str, bool]:
    """Build prompt string, trimming oldest messages if needed to fit token budget.
    Returns (prompt, was_trimmed)."""
    _, tokenizer = get_model_and_tokenizer()
    messages = list(history)

    while messages:
        conversation = []
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})
        conversation.extend({"role": m["role"], "content": m["content"]} for m in messages)
        prompt = tokenizer.apply_chat_template(
            conversation, tokenize=False, add_generation_prompt=True
        )
        if len(tokenizer.encode(prompt)) <= MAX_PROMPT_TOKENS:
            return prompt, len(messages) < len(history)
        messages.pop(0)

    # Absolute fallback: system prompt + latest message only
    conversation = []
    if system_prompt:
        conversation.append({"role": "system", "content": system_prompt})
    if history:
        m = history[-1]
        conversation.append({"role": m["role"], "content": m["content"]})
    prompt = tokenizer.apply_chat_template(
        conversation, tokenize=False, add_generation_prompt=True
    )
    return prompt, True


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

    token_queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def generate():
        try:
            for resp in stream_generate(**gen_kwargs):
                if stop_event.is_set():
                    break
                loop.call_soon_threadsafe(token_queue.put_nowait, resp.text)
        finally:
            loop.call_soon_threadsafe(token_queue.put_nowait, None)

    Thread(target=generate, daemon=True).start()

    async def flush(buf: list[str]) -> list[str]:
        if buf:
            await websocket.send_json({"type": "token", "content": "".join(buf)})
        return []

    full_text = ""
    buffer: list[str] = []

    while True:
        try:
            ctrl = msg_queue.get_nowait()
            if ctrl.get("type") == "stop":
                stop_event.set()
        except asyncio.QueueEmpty:
            pass

        try:
            token = await asyncio.wait_for(token_queue.get(), timeout=FLUSH_INTERVAL)
        except asyncio.TimeoutError:
            buffer = await flush(buffer)
            continue

        if token is None:
            await flush(buffer)
            break
        full_text += token
        buffer.append(token)
        if len(buffer) >= TOKEN_BATCH:
            buffer = await flush(buffer)

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
    prompt, was_trimmed = _build_prompt(history, conv.get("system_prompt", ""))
    if was_trimmed:
        kv_cache = make_cache()

    stop_event.clear()
    full_response = ""

    async with _generation_lock:
        try:
            full_response = await asyncio.wait_for(
                _stream_response(
                    websocket, prompt, req["max_tokens"], req["temperature"],
                    kv_cache, stop_event, msg_queue,
                ),
                timeout=GENERATION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            stop_event.set()
            logger.warning("Generation timed out in %s", conversation_id)
            await websocket.send_json({"type": "error", "message": "Generation timed out"})
            return conv, make_cache()  # reset — thread may still be writing to old cache
        except Exception:
            logger.exception("Generation error in %s", conversation_id)
            await websocket.send_json({"type": "error", "message": "Generation failed"})
            return conv, make_cache()  # reset — cache state is unknown after an error

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

    conv = await db.get_conversation(conversation_id, get_session_token(websocket))
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

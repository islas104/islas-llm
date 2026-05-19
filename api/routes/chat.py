import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from threading import Thread

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from api import db
from api.auth import is_authenticated, get_session_token
from model.loader import get_model_and_tokenizer, make_cache

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_CONTENT_LEN = int(os.getenv("MAX_CONTENT_LEN", "8000"))
_DEFAULT_SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "You are a helpful assistant. Be concise and accurate.")
MAX_NEW_TOKENS_LIMIT = 2048
DEFAULT_MAX_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "1024"))
DEFAULT_TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_PROMPT_TOKENS = 8192   # Qwen2.5 supports 32k context; 8k leaves headroom for the response
TOKEN_BATCH = 1            # tokens to accumulate before each WebSocket send
FLUSH_INTERVAL = 0.01      # seconds — flush partial buffer if no token arrives within this window
GENERATION_TIMEOUT = 120   # seconds before a hung generation is cancelled
LOCK_WAIT_TIMEOUT = 30    # seconds to wait for the generation lock before giving up

_MSG_RATE_LIMIT = 10       # max messages per user per minute
_MSG_WINDOW = 60           # seconds
_WS_CONN_LIMIT = 30        # max WebSocket connections per IP per minute
_WS_CONN_WINDOW = 60

_generation_lock = asyncio.Lock()
_user_msg_times: dict[str, deque] = defaultdict(deque)
_ip_conn_times: dict[str, deque] = defaultdict(deque)


def cleanup_rate_limits() -> None:
    now = time.time()
    for d, window in [(_user_msg_times, _MSG_WINDOW), (_ip_conn_times, _WS_CONN_WINDOW)]:
        stale = [k for k, q in d.items() if not q or now - q[-1] > window]
        for k in stale:
            del d[k]


def _is_rate_limited(uid: str) -> bool:
    now = time.time()
    q = _user_msg_times[uid]
    while q and now - q[0] > _MSG_WINDOW:
        q.popleft()
    if len(q) >= _MSG_RATE_LIMIT:
        return True
    q.append(now)
    return False


def _is_conn_rate_limited(ip: str) -> bool:
    now = time.time()
    q = _ip_conn_times[ip]
    while q and now - q[0] > _WS_CONN_WINDOW:
        q.popleft()
    if len(q) >= _WS_CONN_LIMIT:
        return True
    q.append(now)
    return False


async def _safe_send(websocket: WebSocket, data: dict) -> None:
    """Send JSON on a WebSocket, silently dropping if the connection is already closed."""
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.send_json(data)
        except Exception:
            pass


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

    # Pre-count per-message token cost (+6 for role/template framing around each message)
    # so we only re-encode the full prompt once rather than once per trim iteration.
    def _approx(m: dict) -> int:
        return len(tokenizer.encode(m["content"])) + 6

    sys_cost = (len(tokenizer.encode(system_prompt)) + 6) if system_prompt else 0
    messages = list(history)
    costs = [_approx(m) for m in messages]

    while messages and sys_cost + sum(costs) > MAX_PROMPT_TOKENS:
        messages.pop(0)
        costs.pop(0)

    was_trimmed = len(messages) < len(history)

    if not messages and history:
        messages = [history[-1]]
        was_trimmed = True

    def _apply(msgs: list[dict]) -> str:
        conv = []
        if system_prompt:
            conv.append({"role": "system", "content": system_prompt})
        conv.extend({"role": m["role"], "content": m["content"]} for m in msgs)
        return tokenizer.apply_chat_template(conv, tokenize=False, add_generation_prompt=True)

    prompt = _apply(messages)

    # Single verification pass — estimate may be slightly off due to template overhead
    while messages and len(tokenizer.encode(prompt)) > MAX_PROMPT_TOKENS:
        messages.pop(0)
        was_trimmed = True
        prompt = _apply(messages)

    return prompt, was_trimmed


def _start_generate_thread(gen_kwargs: dict, stop_event: asyncio.Event, token_queue: asyncio.Queue, loop) -> None:
    from mlx_lm import stream_generate

    def generate():
        try:
            for resp in stream_generate(**gen_kwargs):
                if stop_event.is_set():
                    break
                loop.call_soon_threadsafe(token_queue.put_nowait, resp.text)
        finally:
            loop.call_soon_threadsafe(token_queue.put_nowait, None)

    Thread(target=generate, daemon=True).start()


async def _collect_tokens(
    websocket: WebSocket,
    token_queue: asyncio.Queue,
    msg_queue: asyncio.Queue,
    stop_event: asyncio.Event,
) -> str:
    async def flush(buf: list[str]) -> list[str]:
        if buf:
            await _safe_send(websocket, {"type": "token", "content": "".join(buf)})
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


async def _stream_response(
    websocket: WebSocket,
    prompt: str,
    max_tokens: int,
    temperature: float,
    kv_cache,
    stop_event: asyncio.Event,
    msg_queue: asyncio.Queue,
) -> str:
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = get_model_and_tokenizer()
    sampler = make_sampler(temp=temperature)
    gen_kwargs = {"model": model, "tokenizer": tokenizer,
                  "prompt": prompt, "max_tokens": max_tokens, "sampler": sampler}
    if kv_cache is not None:
        gen_kwargs["prompt_cache"] = kv_cache

    token_queue: asyncio.Queue = asyncio.Queue()
    _start_generate_thread(gen_kwargs, stop_event, token_queue, asyncio.get_running_loop())
    return await _collect_tokens(websocket, token_queue, msg_queue, stop_event)


async def _run_generation(
    websocket: WebSocket,
    prompt: str,
    req: dict,
    kv_cache,
    stop_event: asyncio.Event,
    msg_queue: asyncio.Queue,
    conversation_id: str,
) -> str | None:
    """Acquire the generation lock and stream a response. Returns full text, or None on failure."""
    try:
        await asyncio.wait_for(_generation_lock.acquire(), timeout=LOCK_WAIT_TIMEOUT)
    except asyncio.TimeoutError:
        await _safe_send(websocket, {"type": "error", "message": "Server is busy — try again in a moment."})
        return None

    try:
        await _safe_send(websocket, {"type": "thinking"})
        return await asyncio.wait_for(
            _stream_response(websocket, prompt, req["max_tokens"], req["temperature"], kv_cache, stop_event, msg_queue),
            timeout=GENERATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        stop_event.set()
        logger.warning("Generation timed out in %s", conversation_id)
        await _safe_send(websocket, {"type": "error", "message": "Generation timed out"})
        return None
    except Exception:
        logger.exception("Generation error in %s", conversation_id)
        await _safe_send(websocket, {"type": "error", "message": "Generation failed"})
        return None
    finally:
        _generation_lock.release()


async def _handle_message(
    websocket: WebSocket,
    data: dict,
    conversation_id: str,
    conv: dict,
    kv_cache,
    stop_event: asyncio.Event,
    msg_queue: asyncio.Queue,
    uid: str = "",
) -> tuple[dict, any]:
    req = _parse_request(data)
    if req is None:
        await _safe_send(websocket, {"type": "error", "message": "Invalid content"})
        return conv, kv_cache

    if uid and _is_rate_limited(uid):
        await _safe_send(websocket, {"type": "error", "message": "Slow down — you're sending too many messages."})
        return conv, kv_cache

    if req["truncate_from_id"]:
        await db.truncate_from_message(conversation_id, req["truncate_from_id"])
        kv_cache = make_cache()

    user_msg = await db.add_message(conversation_id, "user", req["content"])
    await _safe_send(websocket, {"type": "message_saved", "message": user_msg})

    history = await db.get_messages(conversation_id)
    system_prompt = conv.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT
    prompt, was_trimmed = _build_prompt(history, system_prompt)
    if was_trimmed:
        kv_cache = make_cache()

    stop_event.clear()

    full_response = await _run_generation(websocket, prompt, req, kv_cache, stop_event, msg_queue, conversation_id)
    if full_response is None:
        return conv, make_cache()

    asst_msg = await db.add_message(conversation_id, "assistant", full_response)

    if conv.get("title") == "New Chat" and len(history) <= 1:
        _fire(_generate_title(websocket, conversation_id, req["content"], full_response))

    await _safe_send(websocket, {"type": "done", "message": asst_msg})
    return conv, kv_cache


async def _ws_guard(websocket: WebSocket, conversation_id: str):
    """Validate an already-accepted WebSocket. Returns (conv, uid) or None."""
    ip = websocket.client.host if websocket.client else "unknown"
    if _is_conn_rate_limited(ip):
        await _safe_send(websocket, {"type": "error", "message": "Too many connections"})
        await websocket.close(code=4429)
        return None
    if not is_authenticated(websocket):
        await _safe_send(websocket, {"type": "error", "message": "Unauthorised"})
        await websocket.close(code=4401)
        return None
    uid = get_session_token(websocket)
    conv = await db.get_conversation(conversation_id, uid)
    if not conv:
        await _safe_send(websocket, {"type": "error", "message": "Conversation not found"})
        await websocket.close()
        return None
    return conv, uid


_background_tasks: set = set()


def _fire(coro) -> None:
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)


async def _generate_title(websocket: WebSocket, conversation_id: str, user_content: str, asst_content: str) -> None:
    """Generate a short title using the model after the first exchange."""
    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = get_model_and_tokenizer()
    messages = [{"role": "user", "content": (
        "Write a title of 5 words or fewer for this conversation. "
        "Reply with only the title — no punctuation, no quotes.\n\n"
        f"User: {user_content[:300]}\nAssistant: {asst_content[:300]}"
    )}]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    gen_kwargs = {
        "model": model, "tokenizer": tokenizer,
        "prompt": prompt, "max_tokens": 12,
        "sampler": make_sampler(temp=0.3),
    }

    try:
        await asyncio.wait_for(_generation_lock.acquire(), timeout=30)
    except asyncio.TimeoutError:
        return

    try:
        parts: list[str] = []
        def _run():
            for resp in stream_generate(**gen_kwargs):
                parts.append(resp.text)
        await asyncio.to_thread(_run)
    except Exception:
        return
    finally:
        _generation_lock.release()

    title = "".join(parts).strip().strip("\"'").strip()[:60]
    if not title:
        return
    await db.update_conversation(conversation_id, title=title)
    await _safe_send(websocket, {"type": "title_updated", "title": title})


async def _prefill_history(conv: dict, kv_cache) -> None:
    """Pre-warm the KV cache with existing history so the first response has no prefill delay."""
    history = await db.get_messages(conv["id"])
    if not history:
        return

    from mlx_lm import stream_generate
    from mlx_lm.sample_utils import make_sampler

    system_prompt = conv.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT
    prompt, _ = _build_prompt(history, system_prompt)
    model, tokenizer = get_model_and_tokenizer()
    gen_kwargs = {
        "model": model, "tokenizer": tokenizer,
        "prompt": prompt, "max_tokens": 1,
        "sampler": make_sampler(temp=0.0),
        "prompt_cache": kv_cache,
    }

    try:
        await asyncio.wait_for(_generation_lock.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        return  # Model is busy — skip prefill; first message will prefill naturally

    try:
        def _run():
            for _ in stream_generate(**gen_kwargs):
                return  # one token fills the cache; that's all we need
        await asyncio.to_thread(_run)
    except Exception:
        logger.debug("Cache pre-warm failed", exc_info=True)
    finally:
        _generation_lock.release()


@router.websocket("/ws/{conversation_id}")
async def ws_chat(websocket: WebSocket, conversation_id: str):
    await websocket.accept()

    result = await _ws_guard(websocket, conversation_id)
    if result is None:
        return
    conv, uid = result

    kv_cache = make_cache()
    msg_queue: asyncio.Queue = asyncio.Queue()
    stop_event = asyncio.Event()

    prefill_task = asyncio.create_task(_prefill_history(conv, kv_cache))

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
                    conv, kv_cache, stop_event, msg_queue, uid,
                )
    except WebSocketDisconnect:
        logger.info("Client disconnected: %s", conversation_id)
    finally:
        receiver_task.cancel()
        prefill_task.cancel()


@router.get("/health")
async def health():
    try:
        get_model_and_tokenizer()
        return {"status": "ok", "model_ready": True}
    except RuntimeError:
        return {"status": "loading", "model_ready": False}

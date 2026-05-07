import os
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from model.loader import get_model_and_tokenizer

router = APIRouter()


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    max_new_tokens: int = int(os.getenv("MAX_NEW_TOKENS", 512))
    temperature: float = float(os.getenv("TEMPERATURE", 0.7))


@router.post("/chat")
async def chat(request: ChatRequest):
    from mlx_lm import stream_generate

    model, tokenizer = get_model_and_tokenizer()

    conversation = [{"role": m.role, "content": m.content} for m in request.messages]
    prompt = tokenizer.apply_chat_template(
        conversation, tokenize=False, add_generation_prompt=True
    )

    from mlx_lm.sample_utils import make_sampler

    sampler = make_sampler(temp=request.temperature)

    async def token_stream():
        for response in stream_generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=request.max_new_tokens,
            sampler=sampler,
        ):
            yield response.text

    return StreamingResponse(token_stream(), media_type="text/plain")


@router.get("/health")
async def health():
    return {"status": "ok"}

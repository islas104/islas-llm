# ⚡ Islas LLM

A fully custom LLM product built from the ground up — fine-tuned Mistral 7B running locally on Apple Silicon, with a FastAPI backend, WebSocket streaming, persistent conversations, and a polished chat UI.

---

![Islas LLM – Empty State](screenshots/ui.png)

![Islas LLM – Chat View](screenshots/ui-chat.png)

---

## Features

- **Local inference** — Mistral 7B Instruct (4-bit) via Apple MLX on-device, no API costs
- **WebSocket streaming** — real-time token-by-token responses with auto-reconnect and ping keepalive
- **Token batching** — tokens are buffered and flushed every 6 tokens or 30 ms, reducing WebSocket frame overhead
- **KV cache** — per-session KV cache so only new tokens are prefilled each turn
- **Token-aware context trimming** — oldest messages are dropped to stay within Mistral's 4096-token limit; no silent overflow
- **Generation timeout** — hung generations are cancelled after 120 s with a clean error message
- **Persistent conversations** — all chats saved to SQLite (WAL mode, 32 MB page cache, persistent connection)
- **Startup warm-up** — dummy inference at boot compiles MLX compute graphs so every response has consistent latency
- **Edit & regenerate** — edit any past message and regenerate from that point
- **System prompt** — configurable per conversation via the settings panel
- **Temperature & max tokens** — adjustable per session
- **Password auth** — optional password protection with scrypt hashing and HTTP-only session cookies
- **Fine-tuning** — QLoRA fine-tuning script included via HuggingFace PEFT + TRL
- **Security** — CSP headers, input validation, rate limiting, GZip compression

## Stack

| Layer | Technology |
|-------|-----------|
| Model | Mistral 7B Instruct (4-bit, MLX) |
| Backend | FastAPI + WebSockets + SQLite |
| Frontend | Vanilla JS, marked.js, highlight.js, DOMPurify |
| Training | HuggingFace Transformers, PEFT, TRL |
| Platform | Apple Silicon (MLX) |

## Quick Start

**Requirements:** Python 3.12+, Apple Silicon Mac (M1/M2/M3/M4)

```bash
# Clone
git clone https://github.com/islas104/islas-llm.git
cd islas-llm

# Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Add your HuggingFace token to .env

# (Optional) Set a password
python setup.py

# Run
python run.py
```

Open [http://localhost:8000](http://localhost:8000)

## Fine-tuning

Prepare a JSONL file where each line has a `text` field:

```json
{"text": "<s>[INST] Your prompt [/INST] Your response </s>"}
```

Then run:

```bash
python scripts/finetune.py --data data/train.jsonl --output checkpoints/islas-v1
```

## Project Structure

```
islas-llm/
├── model/
│   └── loader.py          # MLX model loading, KV cache, warm-up
├── api/
│   ├── auth.py            # Password auth, session management
│   ├── db.py              # SQLite — persistent connection, WAL, indexes
│   ├── main.py            # FastAPI app, middleware, security headers
│   └── routes/
│       ├── chat.py        # WebSocket handler — streaming, token batching, timeout
│       ├── conversations.py
│       └── auth_routes.py
├── ui/
│   ├── index.html         # Chat interface
│   ├── login.html         # Auth page
│   ├── app.js             # WebSocket client, markdown rendering, mobile sidebar
│   └── style.css          # Dark theme with gradient accents
├── screenshots/           # UI screenshots
├── scripts/
│   └── finetune.py        # QLoRA fine-tuning
├── setup.py               # First-run auth setup
├── run.py                 # Start the server
└── .env.example
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MODEL_ID` | HuggingFace model ID | `mlx-community/Mistral-7B-Instruct-v0.3-4bit` |
| `HF_TOKEN` | HuggingFace access token | — |
| `PORT` | Server port | `8000` |
| `PASSWORD_HASH` | scrypt password hash (run `setup.py`) | — |
| `MAX_NEW_TOKENS` | Max tokens per response | `512` |
| `TEMPERATURE` | Default sampling temperature | `0.7` |
| `MAX_CONTENT_LEN` | Max characters per user message | `8000` |

---

Built by [Islas Nawaz](https://github.com/islas104)

# Islas LLM

A fully custom LLM product built from the ground up — fine-tuned Mistral 7B running locally on Apple Silicon, with a FastAPI backend, WebSocket streaming, persistent conversations, and a clean chat UI.

---

## Features

- **Local inference** — Mistral 7B Instruct (8-bit) running via Apple MLX on-device, no API costs
- **WebSocket streaming** — real-time token-by-token responses with auto-reconnect
- **KV cache** — conversation context is cached per session so only new tokens are processed each turn
- **Persistent conversations** — all chats saved to SQLite, survive restarts
- **Edit & regenerate** — edit any past message and regenerate from that point
- **System prompt** — configurable per conversation via the settings panel
- **Password auth** — optional password protection with scrypt hashing
- **Fine-tuning** — QLoRA fine-tuning script included via HuggingFace PEFT + TRL
- **Security** — CSP headers, input validation, rate limiting, HTTP-only session cookies

## Stack

| Layer | Technology |
|-------|-----------|
| Model | Mistral 7B Instruct (8-bit, MLX) |
| Backend | FastAPI + WebSockets + SQLite |
| Frontend | Vanilla JS, marked.js, highlight.js |
| Training | HuggingFace Transformers, PEFT, TRL |
| Platform | Apple Silicon (MPS / MLX) |

## Quick Start

**Requirements:** Python 3.12+, Apple Silicon Mac (M1/M2/M3/M4)

```bash
# Clone
git clone https://github.com/islas104/forge-llm.git
cd forge-llm

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

Prepare a JSONL file where each line has a `text` field with your training data:

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
│   └── loader.py          # MLX model loading + KV cache
├── api/
│   ├── auth.py            # Password auth, session management
│   ├── db.py              # SQLite conversation storage
│   ├── main.py            # FastAPI app, middleware, security
│   └── routes/
│       ├── chat.py        # WebSocket chat handler
│       ├── conversations.py
│       └── auth_routes.py
├── ui/
│   ├── index.html         # Chat interface
│   ├── login.html         # Auth page
│   ├── app.js             # WebSocket client, markdown rendering
│   └── style.css
├── scripts/
│   └── finetune.py        # QLoRA fine-tuning
├── setup.py               # First-run auth setup
├── run.py                 # Start the server
└── .env.example
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MODEL_ID` | HuggingFace model ID | `mlx-community/Mistral-7B-Instruct-v0.3-8bit` |
| `HF_TOKEN` | HuggingFace access token | — |
| `PORT` | Server port | `8000` |
| `PASSWORD_HASH` | scrypt password hash (run `setup.py`) | — |
| `MAX_NEW_TOKENS` | Max tokens per response | `512` |
| `TEMPERATURE` | Default sampling temperature | `0.7` |

---

Built by [Islas Nawaz](https://github.com/islas104)

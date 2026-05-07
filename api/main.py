import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from api.routes.chat import router as chat_router
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Forge LLM", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api")
app.mount("/static", StaticFiles(directory="ui"), name="static")


@app.get("/")
async def root():
    return FileResponse("ui/index.html")

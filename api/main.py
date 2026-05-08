import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from api.auth import is_authenticated

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_PUBLIC = {"/login", "/api/auth/login", "/api/auth/logout", "/static"}
_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self' ws: wss: https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "font-src 'self' https://cdn.jsdelivr.net;"
)
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": _CSP,
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    from api.db import init_db, close_db, load_sessions
    from api.auth import _sessions
    from model.loader import load_model
    await init_db()
    _sessions.update(await load_sessions())
    await asyncio.to_thread(load_model)
    yield
    await close_db()


app = FastAPI(title="Islas LLM", version="0.2.0", lifespan=lifespan,
              docs_url=None, redoc_url=None)

_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_origins,
                   allow_methods=["GET", "POST", "PATCH", "DELETE"],
                   allow_headers=["Content-Type"])
app.add_middleware(GZipMiddleware, minimum_size=500)


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    is_ws = request.headers.get("upgrade", "").lower() == "websocket"
    is_public = any(path.startswith(p) for p in _PUBLIC)

    if not is_public and not is_authenticated(request):
        if is_ws:
            pass  # WS auth handled inside the handler
        elif path.startswith("/api/"):
            return JSONResponse({"error": "Unauthorised"}, status_code=401)
        else:
            return FileResponse("ui/login.html")

    response = await call_next(request)
    response.headers.update(_SECURITY_HEADERS)
    return response


@app.exception_handler(Exception)
async def unhandled(_: Request, _exc: Exception):
    logger.exception("Unhandled error")
    return JSONResponse({"error": "Internal server error"}, status_code=500)


from api.routes.auth_routes import router as auth_router
from api.routes.chat import router as chat_router
from api.routes.conversations import router as conv_router

app.include_router(auth_router, prefix="/api/auth")
app.include_router(chat_router, prefix="/api")
app.include_router(conv_router, prefix="/api/conversations")
app.mount("/static", StaticFiles(directory="ui"), name="static")


@app.get("/login")
async def login_page():
    return FileResponse("ui/login.html")


@app.get("/")
async def root():
    return FileResponse("ui/index.html")

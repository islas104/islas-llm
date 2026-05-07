import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_model = None
_tokenizer = None


def load_model() -> None:
    global _model, _tokenizer
    if _model is not None:
        return
    from mlx_lm import load
    model_id = os.getenv("MODEL_ID", "mlx-community/Mistral-7B-Instruct-v0.3-4bit")
    logger.info("Loading model: %s", model_id)
    _model, _tokenizer = load(model_id)
    logger.info("Model ready")


def get_model_and_tokenizer():
    if _model is None:
        raise RuntimeError("Model not initialised — server still starting up")
    return _model, _tokenizer


def make_cache():
    if _model is None:
        return None
    try:
        return _model.make_cache()
    except AttributeError:
        return None


def count_tokens(text: str) -> int:
    if _tokenizer is None:
        return len(text) // 4
    return len(_tokenizer.encode(text))

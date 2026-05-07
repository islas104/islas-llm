import os
from dotenv import load_dotenv

load_dotenv()

_model = None
_tokenizer = None


def get_model_and_tokenizer():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    from mlx_lm import load

    model_id = os.getenv("MODEL_ID", "mlx-community/Mistral-7B-Instruct-v0.3-4bit")
    print(f"Loading model: {model_id}")

    _model, _tokenizer = load(model_id)
    print("Model loaded.")
    return _model, _tokenizer

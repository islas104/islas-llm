import os
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from dotenv import load_dotenv

load_dotenv()

_model = None
_tokenizer = None


def get_model_and_tokenizer():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    model_id = os.getenv("MODEL_ID", "mistralai/Mistral-7B-Instruct-v0.3")
    hf_token = os.getenv("HF_TOKEN")

    print(f"Loading model: {model_id}")

    _tokenizer = AutoTokenizer.from_pretrained(model_id, token=hf_token)

    # 4-bit quantization so it runs on consumer GPUs / MPS / CPU
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
    )

    device_map = "auto"
    if torch.backends.mps.is_available():
        # MPS (Apple Silicon) doesn't support bitsandbytes yet — load in fp16
        _model = AutoModelForCausalLM.from_pretrained(
            model_id,
            token=hf_token,
            torch_dtype=torch.float16,
            device_map={"": "mps"},
        )
    elif torch.cuda.is_available():
        _model = AutoModelForCausalLM.from_pretrained(
            model_id,
            token=hf_token,
            quantization_config=bnb_config,
            device_map=device_map,
        )
    else:
        # CPU fallback — slow but works
        _model = AutoModelForCausalLM.from_pretrained(
            model_id,
            token=hf_token,
            torch_dtype=torch.float32,
            device_map="cpu",
        )

    _model.eval()
    print("Model loaded.")
    return _model, _tokenizer

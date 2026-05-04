import json
import time
import ollama
from config import OLLAMA_MODEL, OLLAMA_FAST_MODEL, OLLAMA_TIMEOUT, LLM_KEEP_ALIVE


def _build_options(num_predict: int = 4096):
    return {
        "num_ctx": 16384,
        "num_predict": num_predict,
        "temperature": 0.0,
        "top_p": 1.0,
    }


def _call(messages: list, json_mode: bool = False, retries: int = 2,
          model: str = None, num_predict: int = 4096) -> str:
    """Low-level Ollama call with retry logic."""
    options = _build_options(num_predict=num_predict)
    fmt = "json" if json_mode else ""
    use_model = model or OLLAMA_MODEL

    for attempt in range(retries + 1):
        try:
            resp = ollama.chat(
                model=use_model,
                messages=messages,
                format=fmt if fmt else None,
                options=options,
                keep_alive=LLM_KEEP_ALIVE,
            )
            return resp["message"]["content"]
        except ollama.ResponseError as e:
            if "not found" in str(e).lower():
                raise RuntimeError(
                    f"Model '{use_model}' not found. "
                    f"Run: ollama pull {use_model}"
                ) from e
            if attempt < retries:
                time.sleep(2)
                continue
            raise
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                continue
            raise RuntimeError(
                f"Ollama connection failed. Is Ollama running? (ollama serve)\n{e}"
            ) from e


def chat(prompt: str, system_prompt: str = "", fast: bool = False) -> str:
    """Send a prompt and get a text response. fast=True uses the smaller model."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    model = OLLAMA_FAST_MODEL if fast else None
    return _call(messages, json_mode=False, model=model)


def chat_json(prompt: str, system_prompt: str = "", fast: bool = False,
              num_predict: int = 4096) -> dict:
    """Send a prompt and get a parsed JSON response.

    fast=True routes to OLLAMA_FAST_MODEL (defaults to the same model unless overridden
    via the OLLAMA_FAST_MODEL env var).
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    model = OLLAMA_FAST_MODEL if fast else None
    raw = _call(messages, json_mode=True, model=model, num_predict=num_predict)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise ValueError(f"LLM returned invalid JSON:\n{raw[:500]}")


def embed_text(text: str) -> list:
    """Embedding vector for RAG (Ollama). Model: OLLAMA_EMBED_MODEL."""
    from config import OLLAMA_EMBED_MODEL

    t = (text or "").strip()
    if not t:
        return []
    t = t[:8000]
    resp = ollama.embeddings(model=OLLAMA_EMBED_MODEL, prompt=t)
    vec = resp.get("embedding")
    if not vec:
        raise RuntimeError(
            f"No embedding returned. Install model: ollama pull {OLLAMA_EMBED_MODEL}"
        )
    return vec


def warmup(model: str = None):
    """Send a tiny request so the model is loaded into memory and stays via keep_alive."""
    try:
        _call(
            [{"role": "user", "content": "ok"}],
            json_mode=False, retries=0, model=model, num_predict=8,
        )
    except Exception:
        pass


def is_available() -> bool:
    """Check if Ollama is running and the model is available."""
    try:
        ollama.list()
        return True
    except Exception:
        return False

import json
import time
import ollama
from config import OLLAMA_MODEL, OLLAMA_TIMEOUT


def _call(messages: list, json_mode: bool = False, retries: int = 2) -> str:
    """Low-level Ollama call with retry logic."""
    options = {"num_ctx": 4096}
    fmt = "json" if json_mode else ""

    for attempt in range(retries + 1):
        try:
            resp = ollama.chat(
                model=OLLAMA_MODEL,
                messages=messages,
                format=fmt if fmt else None,
                options=options,
            )
            return resp["message"]["content"]
        except ollama.ResponseError as e:
            if "not found" in str(e).lower():
                raise RuntimeError(
                    f"Model '{OLLAMA_MODEL}' not found. "
                    f"Run: ollama pull {OLLAMA_MODEL}"
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


def chat(prompt: str, system_prompt: str = "") -> str:
    """Send a prompt and get a text response."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return _call(messages, json_mode=False)


def chat_json(prompt: str, system_prompt: str = "") -> dict:
    """Send a prompt and get a parsed JSON response."""
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    raw = _call(messages, json_mode=True)

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


def is_available() -> bool:
    """Check if Ollama is running and the model is available."""
    try:
        ollama.list()
        return True
    except Exception:
        return False

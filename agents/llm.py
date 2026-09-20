"""
Groq LLM adapter.

Every agent calls chat_json(). To switch provider or model later, edit only this file
(or set GROQ_MODEL in the .env file).
"""
import json
import os
import time

from dotenv import load_dotenv

from kb.config import BASE_DIR

load_dotenv(BASE_DIR / ".env")

# Models that need no paid/enterprise access. Check console.groq.com/docs/models for changes.
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
_client = None


class LLMConfigError(RuntimeError):
    """Problems retrying will not fix: missing/invalid key, unknown model."""


def _get_client():
    global _client
    if _client is None:
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise LLMConfigError(
                "GROQ_API_KEY not found. Create a file named .env in the project root "
                "containing the line: GROQ_API_KEY=your_key_here")
        from groq import Groq
        _client = Groq(api_key=key)
    return _client


def chat_json(system, user, model=None, retries=3):
    """Send a prompt and return the JSON object the model replies with (as a dict)."""
    model = model or MODEL
    extra = {}
    if "gpt-oss" in model:
        # Reasoning models spend tokens "thinking"; keep it short so the JSON is not cut off.
        extra["extra_body"] = {"reasoning_effort": "low"}
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = _get_client().chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"},
                **extra,
            )
            data = json.loads(response.choices[0].message.content)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data
        except LLMConfigError:
            raise
        except Exception as error:
            if getattr(error, "status_code", None) in (401, 403, 404):
                raise LLMConfigError(
                    f"Groq rejected the request: {error}\n"
                    "If this says model_not_found, set GROQ_MODEL in your .env file to a model "
                    "your account can use (see console.groq.com/docs/models).") from error
            last_error = error
            if attempt < retries:
                time.sleep(2 * (attempt + 1) ** 2)  # waits 2s, 8s, 18s (helps with rate limits)
    raise RuntimeError(f"LLM call failed after {retries + 1} attempts: {last_error}")


if __name__ == "__main__":
    # Connection test:  python -m agents.llm
    print("Model:", MODEL)
    print("Reply:", chat_json('Reply with a JSON object like {"status": "ready"}.', "Are you connected?"))
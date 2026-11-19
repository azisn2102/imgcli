"""Provider: Google Gemini image generation via the REST API (API key)."""

from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request

from ..config import get_api_key

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/images/generations"
USER_AGENT = "imgcli/2.0"
MAX_RETRIES = 3


def _eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def _http_post(url: str, payload: dict, headers: dict, timeout: int, retries: int = MAX_RETRIES) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=body, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}"
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == retries:
                break
            time.sleep(2 ** attempt)
            _eprint(f"  retry {attempt}/{retries - 1} ({last})")
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last = str(exc)
            if attempt == retries:
                break
            time.sleep(2 ** attempt)
            _eprint(f"  retry {attempt}/{retries - 1} ({last})")
    raise RuntimeError(last)


def generate(prompt: str, model: str, width: int, height: int, negative: str | None, timeout: int = 120) -> bytes:
    key = get_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set (get one at Google AI Studio)")

    final = prompt + (f". Avoid: {negative}" if negative else "")
    payload = {
        "model": model,
        "prompt": final,
        "response_format": "b64_json",
        "n": 1,
        "size": f"{width}x{height}",
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    doc = json.loads(_http_post(GEMINI_URL, payload, headers, timeout).decode("utf-8", "replace"))

    for item in doc.get("data") or []:
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])

    for cand in doc.get("candidates", []):
        for part in (cand.get("content", {}) or {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return base64.b64decode(inline["data"])

    raise RuntimeError(f"no image in response: {json.dumps(doc)[:300]}")

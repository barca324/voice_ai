"""Shared Sarvam helpers: chat (optionally JSON-schema constrained), TTS, key loading."""
import base64
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "sarvam-105b"


def key():
    if os.environ.get("SARVAM_API_KEY"):
        return os.environ["SARVAM_API_KEY"]
    for line in open(os.path.join(ROOT, ".env")):
        if line.startswith("SARVAM_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("SARVAM_API_KEY missing in .env")


def _post(url, body, timeout=300):
    k = key()
    data = json.dumps(body).encode()
    for attempt in range(4):
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", "api-subscription-key": k, "Authorization": "Bearer " + k})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:  # rate limit / transient: back off and retry
                time.sleep(2 * (attempt + 1) ** 2)
                continue
            raise RuntimeError(f"Sarvam API {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"Sarvam API unreachable: {e}") from None


def chat(system, user, schema=None, temperature=0.0, max_tokens=4000, name="out", attempts=3):
    """One chat call. With `schema`, returns a parsed dict (retrying on malformed JSON); else returns text."""
    if schema:  # the model sometimes pads finished JSON with blank lines until the token limit; ask for one line
        system += "\n\nOutput the JSON minified on a single line: no newlines, no indentation, nothing after the closing brace."
    body = {"model": MODEL, "temperature": temperature, "max_tokens": max_tokens,
            "reasoning_effort": None,  # reasoning otherwise consumes the whole token budget
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    last = None
    for i in range(attempts if schema else 1):
        body["temperature"] = temperature + 0.2 * i
        r = _post("https://api.sarvam.ai/v1/chat/completions", body)
        content = (r["choices"][0]["message"].get("content") or "").strip()
        if not schema:
            return content
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            last = f"finish={r['choices'][0].get('finish_reason')}: {e}"
    raise RuntimeError("model returned invalid JSON after retries: " + str(last))


def tts(text, speaker, path_or_none=None, rate=8000, model="bulbul:v3", lang="hi-IN", pace=1.0):
    """Text to speech. Returns WAV bytes."""
    r = _post("https://api.sarvam.ai/text-to-speech",
              {"text": text[:1400], "language_code": lang, "model": model, "speaker": speaker, "pace": pace,
               "speech_sample_rate": rate, "output_audio_codec": "wav"}, timeout=120)
    return base64.b64decode(r["audios"][0])
